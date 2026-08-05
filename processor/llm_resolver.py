"""LLM-based resolvers: layer classification + strategy selection.

Заменяют LayerClassifier (для сложных случаев) и SemanticResolver._model_call
(для multi-candidate geo-резолюции). Используют LLMBackend для локального
in-process инференса через llama-cpp-python.

Каждый резолвер имеет два режима:
  - Прямой вызов: llm.infer(messages, grammar) — синхронный, через to_thread.
  - Батч-вызов: batch_processor.submit(data) — асинхронный, с батчированием.
"""

import logging
from typing import Any, Dict, List, Optional

from .llm_backend import LLMBackend

logger = logging.getLogger(__name__)


# ─── Layer-only resolver ─────────────────────────────────────────────────────

_LAYER_SYSTEM_PROMPT = (
    "Ты — классификатор событий для карты Одессы. Определи слой события "
    "по тексту сообщения.\n\n"
    "Слои:\n"
    "- cops: полиция, патруль, мусора, ДПС, блокпост, ТЦК, тцкашники\n"
    "- traffic: ДТП, авария, пробка, перекрытие, ремонт, светофор\n"
    "- bus: автобусы, маршрутки, троллейбусы, транспорт\n"
    "- pig: кабаны, свиньи, дикие животные\n"
    "- junk: реклама, спам, ссылки, не релевантно\n\n"
    "Правила:\n"
    "- Если упомянуты и 'копы' и 'автобус' — выбирай cops\n"
    "- Если упомянуты и 'автобус' и 'авария' — выбирай traffic\n"
    "- Сленг: тцк/тцкашники/тцкашный → cops\n"
    "- Реклама, ссылки, подписки → junk\n"
    "- Если не уверен — выбирай pig"
)


class LLMLayerResolver:
    """LLM-based layer classification для сложных случаев.

    Вызывается только когда LayerClassifier вернул 'pig' при наличии
    подозрительных ключевых слов в тексте.
    """

    def __init__(self, llm: LLMBackend) -> None:
        self._llm = llm

    def classify(self, text: str) -> Optional[Dict[str, Any]]:
        """Определить слой сообщения через LLM.

        Returns:
            {"layer": "cops"|"traffic"|"bus"|"pig"|"junk", "reasoning": "..."}
            или None при ошибке инференса.
        """
        messages = [
            {"role": "system", "content": _LAYER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Сообщение: {text}"},
        ]
        return self._llm.infer(messages, grammar_name='layer')


# ─── Strategy-only resolver ──────────────────────────────────────────────────

_STRATEGY_SYSTEM_PROMPT = (
    "Ты — анализатор geo-данных. По тексту сообщения и списку кандидатов "
    "определи релевантные объекты и выбери стратегию геолокации.\n\n"
    "Стратегии:\n"
    "- single_match: один объект, вернуть его полную геометрию\n"
    "- intersection: пересечение двух или более объектов\n"
    "- midpoint: средняя точка между 2+ объектами (только для village/town)\n\n"
    "Правила:\n"
    "- Если в тексте есть 'от X до Y' или 'между X и Y' → midpoint\n"
    "- Если контекст указывает на конкретный объект → single_match\n"
    "- Если объекты пересекаются по смыслу → intersection\n"
    "- Не придумывай объекты, которых нет в списке кандидатов"
)


class LLMStrategyResolver:
    """LLM-based strategy selection для multi-candidate случаев.

    Заменяет SemanticResolver._model_call (который использовал Ollama).
    """

    def __init__(self, llm: LLMBackend) -> None:
        self._llm = llm

    def resolve(
        self,
        text: str,
        candidates: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Определить стратегию и релевантные geo_id.

        Args:
            text: Исходный текст сообщения.
            candidates: Список кандидатов от GeoMatcher.find_geo().

        Returns:
            {"geo_ids": [int, ...], "strategy": "...", "reasoning": "..."}
            или None при ошибке.
        """
        candidate_lines = [
            f"  {c.get('geo_id')}: {c.get('matched_name', '?')} ({c.get('type', '?')})"
            for c in candidates
        ]
        user_content = (
            f"Сообщение: {text}\n\n"
            f"Кандидаты (id, name, type):\n"
            + "\n".join(candidate_lines)
        )

        messages = [
            {"role": "system", "content": _STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        result = self._llm.infer(messages, grammar_name='strategy')

        if result is None:
            return None

        # Validate returned geo_ids against candidates
        valid_ids = {c['geo_id'] for c in candidates}
        geo_ids = [gid for gid in result.get('geo_ids', []) if gid in valid_ids]
        if not geo_ids:
            logger.warning("[LLMResolver] No valid geo_ids in model output")
            return None

        strategy = result.get('strategy')
        if strategy not in ('single_match', 'intersection', 'midpoint'):
            logger.warning(f"[LLMResolver] Unknown strategy: {strategy}")
            return None

        return {
            'geo_ids': geo_ids,
            'strategy': strategy,
            'reasoning': result.get('reasoning', ''),
        }


# ─── Unified resolver (layer + strategy in one call) ─────────────────────────

_UNIFIED_SYSTEM_PROMPT = (
    "Ты — анализатор сообщений для карты событий Одессы. "
    "Определи слой события, стратегию геолокации и релевантные geo_id "
    "по тексту сообщения и списку geo-кандидатов.\n\n"
    "Слои:\n"
    "- cops: полиция, патруль, ДПС, блокпост, ТЦК\n"
    "- traffic: ДТП, авария, пробка, ремонт\n"
    "- bus: автобусы, маршрутки, транспорт\n"
    "- pig: кабаны, свиньи\n"
    "- junk: реклама, спам, не релевантно\n\n"
    "Стратегии:\n"
    "- single_match: один объект → полная геометрия\n"
    "- intersection: перекрёсток (2+ улицы)\n"
    "- midpoint: от X до Y (только village/town)\n\n"
    "Правила:\n"
    "- 'копы на остановке автобуса' → layer=cops, strategy=single_match\n"
    "- 'ДТП с автобусом' → layer=traffic, strategy=single_match\n"
    "- 'между Старосенной и Тираспольской' → layer=traffic, strategy=intersection\n"
    "- 'от Александровки до Визирки' → layer=traffic, strategy=midpoint\n"
    "- Ссылки, реклама → layer=junk\n"
    "- Не придумывай объекты вне списка кандидатов"
)


class UnifiedLLMResolver:
    """Единый LLM-вызов: слой + стратегия + geo_id фильтрация.

    Заменяет LayerClassifier.classify() + SemanticResolver.resolve()
    для сложных случаев. Экономит один inference по сравнению с
    раздельными вызовами.
    """

    def __init__(self, llm: LLMBackend) -> None:
        self._llm = llm

    def resolve(
        self,
        text: str,
        candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Единый вызов: слой + стратегия + geo_ids.

        Args:
            text: Исходный текст сообщения.
            candidates: Список geo-кандидатов (может быть пустым/None).

        Returns:
            {"layer": "...", "strategy": "...", "geo_ids": [...], "reasoning": "..."}
            или None при ошибке.
        """
        if candidates:
            candidate_lines = [
                f"  {c.get('geo_id')}: {c.get('matched_name', '?')} ({c.get('type', '?')})"
                for c in candidates
            ]
            user_content = (
                f"Сообщение: {text}\n\n"
                f"Geo-кандидаты (id, name, type):\n"
                + "\n".join(candidate_lines)
            )
        else:
            user_content = f"Сообщение: {text}\n\nGeo-кандидаты: нет"

        messages = [
            {"role": "system", "content": _UNIFIED_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        result = self._llm.infer(messages, grammar_name='unified')

        if result is None:
            return None

        # Validate layer
        layer = result.get('layer')
        if layer not in ('cops', 'traffic', 'bus', 'pig', 'junk'):
            logger.warning(f"[LLMResolver] Unknown layer: {layer}")
            return None

        # Validate strategy (only if there are geo candidates)
        strategy = result.get('strategy')
        if strategy and strategy not in ('single_match', 'intersection', 'midpoint'):
            strategy = None

        # Validate geo_ids against candidates
        if candidates:
            valid_ids = {c['geo_id'] for c in candidates}
            geo_ids = [gid for gid in result.get('geo_ids', []) if gid in valid_ids]
        else:
            geo_ids = []

        return {
            'layer': layer,
            'strategy': strategy,
            'geo_ids': geo_ids,
            'reasoning': result.get('reasoning', ''),
        }
