"""SemanticResolver — определяет стратегию geo-резолюции по контексту сообщения.

Два уровня:
  Phase 1 [Pre-filter]  — быстрые правила, без модели.
  Phase 2 [TypeValidator] — zero-shot BERT валидация типов.
  Phase 3 [Model]       — Ollama/llama-cpp-python для сложных конфликтов.

Модель возвращает ТОЛЬКО стратегию + список geo_ids. PostGIS вычисляет геометрию.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Fallback defaults (overridden from DB on initialize)
_DEFAULT_MIDPOINT_TYPES: Tuple[str, ...] = ('street', 'market', 'station', 'park', 'landmark')
_DEFAULT_TYPE_MARKERS: Dict[str, str] = {
    "сквер": "park",
    "парк": "park",
    "сад": "park",
    "улица": "street",
    "проспект": "street",
    "переулок": "street",
    "бульвар": "street",
    "набережная": "embankment",
    "село": "village",
    "деревня": "village",
    "посёлок": "village",
    "город": "town",
    "площадь": "square",
    "мост": "bridge",
    "станция": "station",
    "остановка": "stop",
    "рынок": "market",
}
_DEFAULT_TYPE_HINTS: Dict[str, str] = {
    'село': 'village', 'селения': 'village',
    'пгт': 'town', 'город': 'town', 'города': 'town',
    'станция': 'station', 'станции': 'station',
    'район': 'district', 'района': 'district',
    'парк': 'park', 'сквер': 'park',
    'рынок': 'market', 'рынка': 'market',
}


def _build_prompt(text: str, candidates: List[Dict]) -> str:
    """Сформировать промпт для модели."""
    lines = [
        "Ты — анализатор geo-данных. По тексту сообщения и списку кандидатов",
        "определи релевантные объекты и выбери стратегию:",
        "- single_match — один объект, вернуть его полную геометрию",
        "- intersection — пересечение двух или более объектов",
        "- midpoint — средняя точка между 2+ объектами",
        "",
        "Правила:",
        "- Если в тексте есть предлоги 'от X до Y' или 'между X и Y' → midpoint",
        "- Если контекст указывает на конкретный объект → single_match",
        "- Если объекты пересекаются по смыслу → intersection",
        "- Не придумывай объекты, которых нет в списке кандидатов",
        "",
        "Сообщение:",
        text,
        "",
        "Кандидаты (id, name, type):",
    ]
    for c in candidates:
        c_type = c.get('validated_type') or c.get('type', '?')
        lines.append(f"  {c.get('geo_id')}: {c.get('matched_name', '?')} ({c_type})")
    lines.extend([
        "",
        'Ответь ТОЛЬКО JSON в одну строку:',
        '{"geo_ids": [<id1>, <id2>], "strategy": "<strategy>", "reasoning": "<кратко>"}',
    ])
    return '\n'.join(lines)


class SemanticResolver:
    """Определяет стратегию геолокации по семантике текста.

    Используется после GeoMatcher: получает список кандидатов и решает,
    какой SQL-метод PostGIS применить.
    """

    def __init__(self, morph, index, type_validator=None):
        self._morph = morph
        self._index = index
        self._type_validator = type_validator
        self._initialized = False
        self._stopwords: Set[str] = set()
        self._ollama_base: Optional[str] = None

        self._midpoint_types: Tuple[str, ...] = _DEFAULT_MIDPOINT_TYPES
        self._type_markers: Dict[str, str] = dict(_DEFAULT_TYPE_MARKERS)
        self._type_hints: Dict[str, str] = dict(_DEFAULT_TYPE_HINTS)

    async def initialize(self, pg_pool) -> None:
        """Загрузить стоп-слова и конфигурацию из БД."""
        try:
            async with pg_pool.acquire() as conn:
                sw_rows = await conn.fetch("SELECT word FROM stopwords")
                self._stopwords = {row['word'].strip().lower() for row in sw_rows if row['word']}

                stf_rows = await conn.fetch("SELECT strategy, allowed_types FROM strategy_type_filters")
                for row in stf_rows:
                    if row['strategy'] == 'midpoint':
                        self._midpoint_types = tuple(row['allowed_types'])

            ollama = settings.ollama if settings else None
            if ollama and ollama.enabled:
                self._ollama_base = ollama.base_url.rstrip('/')
                logger.info(f"[Resolver] Ollama enabled at {self._ollama_base}")
            else:
                self._ollama_base = None
                logger.info("[Resolver] Ollama disabled, pre-filter only")

            if self._type_validator:
                await self._type_validator.initialize()

            self._initialized = True
            logger.info(
                f"[Resolver] Initialized, {len(self._stopwords)} stopwords, "
                f"{len(self._midpoint_types)} midpoint types"
            )
        except Exception as exc:
            logger.warning(f"[Resolver] Init failed: {exc}")
            self._initialized = True

    async def resolve(
        self,
        text: str,
        tokens: list,
        lemmas: list,
        candidates: List[Dict],
    ) -> Optional[Dict[str, Any]]:
        """Определить стратегию для списка кандидатов.

        Args:
            text: исходный текст
            tokens: токены
            lemmas: леммы
            candidates: список кандидатов от GeoMatcher

        Returns:
            Dict с geo_ids, strategy, reasoning или None (fallback).
        """
        if not self._initialized or not candidates:
            return None

        # Phase 0: Type validation (enrich candidates with validated type)
        if self._type_validator:
            candidates = self._type_validator.validate(candidates, text, tokens)

        # Phase 1: Pre-filter rules
        result = self._pre_filter(text, candidates)
        if result is not None:
            logger.debug(f"[Resolver] Pre-filter: {result['strategy']} ({result.get('reasoning')})")
            return result

        # Phase 2: Model call (Ollama)
        if self._ollama_base is not None:
            result = await self._model_call(text, candidates)
            if result is not None:
                logger.debug(f"[Resolver] Model: {result['strategy']} ({result.get('reasoning')})")
                return result

        return None

    def _pre_filter(self, text: str, candidates: List[Dict]) -> Optional[Dict[str, Any]]:
        """Быстрые правила без вызова модели."""
        text_lower = text.lower()

        # Use validated_type if available, fall back to type
        def _get_type(c):
            return c.get('validated_type') or c.get('type', '')

        candidate_types = {_get_type(c) for c in candidates}
        candidate_names = {c.get('matched_name', '').lower() for c in candidates}

        # ── Правило 1: предлоги направления → midpoint ──────────────────────────
        dir_prepositions = ('от', 'до', 'в сторону', 'по направлению к', 'из', 'в сторону от')
        has_from_to = any(p in text_lower for p in dir_prepositions) and len(candidates) >= 2
        has_between = 'между' in text_lower and len(candidates) >= 2
        if has_from_to or has_between:
            midpoint_types = set(self._midpoint_types) & candidate_types
            if midpoint_types:
                return {
                    'geo_ids': [c['geo_id'] for c in candidates],
                    'strategy': 'midpoint',
                    'reasoning': 'prepositional_construction',
                }

        # ── Правило 2: тип объекта явно указан в тексте ─────────────────────────
        for hint_word, target_type in self._type_hints.items():
            if hint_word in text_lower:
                typed = [c for c in candidates if _get_type(c) == target_type]
                if len(typed) == 1:
                    return {
                        'geo_ids': [typed[0]['geo_id']],
                        'strategy': 'single_match',
                        'reasoning': f'type_hint:{target_type}',
                    }
                if len(typed) > 1:
                    return {
                        'geo_ids': [c['geo_id'] for c in typed],
                        'strategy': 'midpoint' if set(self._midpoint_types) & {target_type} else 'single_match',
                        'reasoning': f'type_hint_multiple:{target_type}',
                    }

        # ── Правило 3: уточнение типа по контекстным маркерам (_TYPE_MARKERS) ──
        for marker_word, target_type in self._type_markers.items():
            if marker_word in text_lower:
                typed = [c for c in candidates if _get_type(c) == target_type]
                if typed:
                    return {
                        'geo_ids': [c['geo_id'] for c in typed],
                        'strategy': 'single_match' if len(typed) == 1 else 'intersection',
                        'reasoning': f'marker_refine:{target_type}',
                    }

        # ── Правило 4a: "/" — пересечение улиц ────────────────────────────────────
        if '/' in text:
            parts = [p.strip() for p in text.split('/')]
            match_values = []
            for c in candidates:
                match_values.append(c.get('matched_name', '').lower())
                match_values.append(c.get('text', '').lower())
            match_values = [v for v in match_values if v]
            for part in parts:
                pl = part.lower()
                if any(mv in pl or pl in mv for mv in match_values):
                    return {
                        'geo_ids': [c['geo_id'] for c in candidates],
                        'strategy': 'intersection',
                        'reasoning': 'slash_intersection',
                    }

        # ── Правило 4b: одноимённые объекты, в тексте есть второй топоним ────────
        name_to_ids: Dict[str, List[int]] = {}
        for c in candidates:
            name = (c.get('matched_name') or '').lower()
            name_to_ids.setdefault(name, []).append(c['geo_id'])

        for name, ids in name_to_ids.items():
            if len(ids) > 1 and len(candidates) > len(ids):
                return None

        return None

    async def _model_call(self, text: str, candidates: List[Dict]) -> Optional[Dict[str, Any]]:
        """Вызвать Ollama для определения стратегии."""
        if not self._ollama_base:
            return None

        prompt = _build_prompt(text, candidates)

        sim = settings.similarity if settings and settings.similarity else None
        timeout_s = getattr(sim, 'semantic_timeout_s', 10) if sim else 10
        temperature = getattr(sim, 'semantic_temperature', 0.0) if sim else 0.0
        model_name = getattr(sim, 'semantic_model', 'qwen2.5:0.5b') if sim else 'qwen2.5:0.5b'

        try:
            import aiohttp

            payload = {
                'model': model_name,
                'prompt': prompt,
                'temperature': temperature,
                'max_tokens': 128,
                'stream': False,
                'format': 'json',
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f'{self._ollama_base}/api/generate',
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[Resolver] Ollama returned {resp.status}")
                        return None
                    data = await resp.json()

            raw = (data.get('response') or '').strip()
            result = json.loads(raw)

            strategy = result.get('strategy')
            geo_ids = result.get('geo_ids', [])
            reasoning = result.get('reasoning', '')

            if strategy not in ('single_match', 'intersection', 'midpoint'):
                logger.warning(f"[Resolver] Unknown strategy from model: {strategy}")
                return None

            valid_ids = {c['geo_id'] for c in candidates}
            geo_ids = [gid for gid in geo_ids if gid in valid_ids]
            if not geo_ids:
                logger.warning("[Resolver] Model returned no valid geo_ids")
                return None

            return {
                'geo_ids': geo_ids,
                'strategy': strategy,
                'reasoning': reasoning,
            }

        except ImportError:
            logger.warning("[Resolver] aiohttp not installed, skipping model call")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"[Resolver] Model returned invalid JSON: {e}")
            return None
        except Exception as e:
            logger.warning(f"[Resolver] Model call failed: {e}")
            return None

    async def close(self) -> None:
        self._initialized = False
        if self._type_validator:
            await self._type_validator.close()
        logger.info("[Resolver] Closed")
