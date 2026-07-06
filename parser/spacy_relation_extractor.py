"""SpaCyRelationExtractor — семантический анализ пространственных отношений между кандидатами.

Модуль выполняет:
  - Связывание кандидатов с токенами spaCy
  - Уточнение типа объекта на основе синтаксически связанных маркеров
  - Определение пространственных отношений между сущностями
  - Генерацию плана вызовов PostGIS инструментов

Вход: query + candidates (с fuzzy-сопоставления)
Выход: план с tool/args (совместимый с process_candidates)
"""

import logging
from typing import Any, Dict, List, Optional

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Type markers: lemma → gazetteer type
TYPE_MARKERS = {
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


class SpaCyRelationExtractor:
    """Извлекает пространственные отношения между кандидатами на основе spaCy."""

    def __init__(self):
        self._nlp = None
        self._initialized = False
        self._enabled = False
        self._model_name = None
        self._custom_attributes_registered = False

    async def initialize(self) -> bool:
        """Инициализация spaCy конфигурации (lazy loading модели при первом запросе)."""
        try:
            # Check if spaCy is enabled in settings
            if settings and hasattr(settings, 'spacy') and not settings.spacy.enabled:
                logger.info("[SpaCy] Disabled in settings")
                self._enabled = False
                return True

            self._enabled = True
            self._model_name = (
                settings.spacy.model_name
                if settings and hasattr(settings, 'spacy') and hasattr(settings.spacy, 'model_name')
                else 'ru_core_news_sm'
            )

            # Register custom attributes (can be done without loading model)
            self._register_custom_attributes()

            logger.info(f"[SpaCy] Configuration ready (model: {self._model_name}, lazy loading enabled)")
            self._initialized = True
            return True

        except Exception as e:
            logger.error(f"[SpaCy] ❌ Initialization failed: {e}")
            self._enabled = False
            return False

    def _register_custom_attributes(self) -> None:
        """Зарегистрировать custom attributes для Token (однократно)."""
        if self._custom_attributes_registered:
            return

        try:
            import spacy
            from spacy.tokens import Token

            if not Token.has_extension('candidate_id'):
                Token.set_extension('candidate_id', default=None)
            if not Token.has_extension('candidate_type'):
                Token.set_extension('candidate_type', default=None)
            if not Token.has_extension('is_location'):
                Token.set_extension('is_location', default=False)

            self._custom_attributes_registered = True
            logger.debug("[SpaCy] Custom attributes registered")
        except ImportError:
            logger.warning("[SpaCy] spacy not installed, cannot register attributes")
        except Exception as e:
            logger.warning(f"[SpaCy] Failed to register custom attributes: {e}")

    def _ensure_model_loaded(self) -> bool:
        """Загрузить модель spaCy при первом использовании (lazy loading)."""
        if self._nlp is not None:
            return True

        if not self._enabled:
            return False

        try:
            import spacy
            logger.info(f"[SpaCy] Lazy loading model: {self._model_name}")
            self._nlp = spacy.load(self._model_name)
            logger.info("[SpaCy] ✅ Model loaded")
            return True
        except ImportError:
            logger.warning("[SpaCy] spacy not installed, cannot load model")
            self._enabled = False
            return False
        except Exception as e:
            logger.error(f"[SpaCy] ❌ Failed to load model: {e}")
            self._enabled = False
            return False

    def extract_plan(
        self,
        query: str,
        candidates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Извлечь план пространственных операций из query и candidates.

        Args:
            query: исходный текст пользователя
            candidates: список кандидатов от GeoMatcher с полями:
                - id: уникальный идентификатор
                - name: название объекта (из газеттира)
                - type: тип объекта (village, street, park, ...)
                - geom_type: point|linestring|polygon|multilinestring
                - span: [start_char, end_char] - позиции в тексте

        Returns:
            Dict с plan (список tool/args) или {"plan": []} если план не определён
        """
        if not self._enabled or not self._initialized:
            logger.debug("[SpaCy] Module not enabled or initialized, returning empty plan")
            return {"plan": []}

        if not candidates:
            logger.debug("[SpaCy] No candidates, returning empty plan")
            return {"plan": []}

        # Lazy load model on first use
        if not self._ensure_model_loaded():
            logger.debug("[SpaCy] Model not available, returning empty plan")
            return {"plan": []}

        try:
            import time
            start_time = time.time()

            # Phase 1: Process text with spaCy
            doc = self._nlp(query)

            # Phase 2: Link candidates to tokens
            self._link_candidates_to_tokens(doc, candidates)

            # Phase 3: Refine types by context
            refined_candidates = self._refine_types_by_context(doc, candidates)

            # Phase 4: Extract spatial relations
            plan = self._extract_spatial_relations(doc, refined_candidates)

            elapsed_ms = (time.time() - start_time) * 1000
            timeout_ms = (
                settings.spacy.timeout_ms
                if settings and hasattr(settings, 'spacy') and hasattr(settings.spacy, 'timeout_ms')
                else 50
            )

            if elapsed_ms > timeout_ms:
                logger.warning(
                    f"[SpaCy] Processing time {elapsed_ms:.1f}ms exceeded timeout {timeout_ms}ms"
                )
            else:
                logger.debug(f"[SpaCy] Processing time: {elapsed_ms:.1f}ms")

            logger.debug(f"[SpaCy] Generated plan: {plan}")
            return plan

        except Exception as e:
            logger.error(f"[SpaCy] Extract plan failed: {e}")
            return {"plan": []}

    def _link_candidates_to_tokens(
        self,
        doc,
        candidates: List[Dict[str, Any]]
    ) -> None:
        """Связать кандидатов с токенами spaCy по их span позициям.

        Для каждого кандидата найти токены, чьи индексы попадают в его span,
        и установить custom attributes (candidate_id, candidate_type, is_location).
        """
        for candidate in candidates:
            span = candidate.get('span')
            if not span or len(span) != 2:
                logger.warning(f"[SpaCy] Candidate {candidate.get('id')} missing or invalid span")
                continue

            start_char, end_char = span

            # Try char_span first (handles tokenization offsets)
            char_span = doc.char_span(start_char, end_char)

            if char_span:
                # Span found - mark all tokens in it
                for token in char_span:
                    token._.candidate_id = str(candidate.get('id'))
                    token._.candidate_type = candidate.get('type')
                    token._.is_location = True
            else:
                # Fallback: manual token-by-token matching
                for token in doc:
                    token_start = token.idx
                    token_end = token.idx + len(token.text_with_ws)
                    if token_start >= start_char and token_end <= end_char:
                        token._.candidate_id = str(candidate.get('id'))
                        token._.candidate_type = candidate.get('type')
                        token._.is_location = True

                # Log warning if no tokens matched
                matched = any(t._.is_location for t in doc if t._.candidate_id == str(candidate.get('id')))
                if not matched:
                    logger.warning(
                        f"[SpaCy] Could not link candidate {candidate.get('id')} "
                        f"('{candidate.get('name')}') to any tokens (span={span})"
                    )

    def _refine_types_by_context(
        self,
        doc,
        candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Уточнить типы кандидатов на основе синтаксически связанных маркеров.

        Обходит все токены с is_location=True и проверяет их синтаксическую связь
        с существительными из TYPE_MARKERS. Если маркер найден, фильтрует кандидатов
        по типу.
        """
        # Group candidates by name
        name_to_candidates: Dict[str, List[Dict]] = {}
        for cand in candidates:
            name = cand.get('name', '')
            name_to_candidates.setdefault(name, []).append(cand)

        refined_candidates = []

        for token in doc:
            if not token._.is_location:
                continue

            # Check head and children for type markers
            related_tokens = [token.head] + list(token.children)

            for related in related_tokens:
                if related.lemma_ in TYPE_MARKERS and related.pos_ == 'NOUN':
                    target_type = TYPE_MARKERS[related.lemma_]
                    candidate_name = None

                    # Find the candidate name for this token
                    if token._.candidate_id:
                        for cand in candidates:
                            if str(cand.get('id')) == token._.candidate_id:
                                candidate_name = cand.get('name')
                                break

                    if candidate_name and candidate_name in name_to_candidates:
                        # Filter candidates by type
                        typed_candidates = [
                            c for c in name_to_candidates[candidate_name]
                            if c.get('type') == target_type
                        ]

                        if typed_candidates:
                            logger.debug(
                                f"[SpaCy] Type refinement: '{candidate_name}' → {target_type} "
                                f"(marker: '{related.lemma_}')"
                            )
                            # Use typed candidates, remove non-typed ones
                            for cand in typed_candidates:
                                if cand not in refined_candidates:
                                    refined_candidates.append(cand)
                            # Mark that we've processed this name
                            name_to_candidates[candidate_name] = typed_candidates

        # Add candidates that weren't refined (no marker found)
        for name, cands in name_to_candidates.items():
            for cand in cands:
                if cand not in refined_candidates:
                    refined_candidates.append(cand)

        return refined_candidates if refined_candidates else candidates

    def _extract_spatial_relations(
        self,
        doc,
        candidates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Извлечь пространственные отношения и сгенерировать план.

        Определяет паттерны:
          - "между X и Y" → bounds → intersection
          - "от X в направлении Y" → from, to → midpoint
          - "в квадрате A, B, C, D" → bounds → intersection
          - "недалеко от A, B, C" → objects → single_match/intersection

        Returns:
            Dict с plan: [{"tool": "...", "args": {...}}]
        """
        plan = []

        # Build ID to candidate mapping
        id_to_candidate = {str(c.get('id')): c for c in candidates}

        # Pattern 1: "между X и Y" (between X and Y)
        bounds = self._extract_between_pattern(doc, id_to_candidate)
        if bounds and len(bounds) >= 2:
            plan.append({
                "tool": "intersection",
                "args": {"bounds": bounds[:2]}  # Take first 2 if more
            })
            logger.debug(f"[SpaCy] Pattern 'between': bounds={bounds[:2]}")
            return {"plan": plan}

        # Pattern 2: "от X в направлении Y" (from X to Y)
        from_to = self._extract_from_to_pattern(doc, id_to_candidate)
        if from_to and from_to.get('from') and from_to.get('to'):
            plan.append({
                "tool": "midpoint",
                "args": {
                    "from": from_to['from'],
                    "to": from_to['to']
                }
            })
            logger.debug(f"[SpaCy] Pattern 'from-to': {from_to}")
            return {"plan": plan}

        # Pattern 3: "в квадрате A, B, C, D" (in square A, B, C, D)
        square_bounds = self._extract_square_pattern(doc, id_to_candidate)
        if square_bounds and len(square_bounds) == 4:
            plan.append({
                "tool": "intersection",
                "args": {"bounds": square_bounds}
            })
            logger.debug(f"[SpaCy] Pattern 'square': bounds={square_bounds}")
            return {"plan": plan}

        # Pattern 4: "недалеко от A, B, C" (near A, B, C)
        near_objects = self._extract_near_pattern(doc, id_to_candidate)
        if near_objects:
            if len(near_objects) == 1:
                plan.append({
                    "tool": "single_match",
                    "args": {"object": near_objects[0]}
                })
            else:
                plan.append({
                    "tool": "intersection",
                    "args": {"bounds": near_objects}
                })
            logger.debug(f"[SpaCy] Pattern 'near': objects={near_objects}")
            return {"plan": plan}

        # Fallback: if multiple candidates but no pattern, suggest intersection
        if len(candidates) >= 2:
            candidate_ids = [str(c.get('id')) for c in candidates]
            plan.append({
                "tool": "intersection",
                "args": {"bounds": candidate_ids}
            })
            logger.debug(f"[SpaCy] Fallback: intersection for {len(candidates)} candidates")
            return {"plan": plan}

        # Single candidate: single_match
        if len(candidates) == 1:
            plan.append({
                "tool": "single_match",
                "args": {"object": str(candidates[0].get('id'))}
            })
            logger.debug(f"[SpaCy] Single candidate: single_match")
            return {"plan": plan}

        # No pattern matched
        logger.debug("[SpaCy] No spatial pattern matched")
        return {"plan": []}

    def _extract_between_pattern(
        self,
        doc,
        id_to_candidate: Dict[str, Dict]
    ) -> Optional[List[str]]:
        """Извлечь паттерн 'между X и Y'."""
        for token in doc:
            if token.lemma_ in ('между', 'промеж'):
                # Look for location tokens in subtree
                bounds = []
                for child in token.children:
                    self._collect_location_ids(child, id_to_candidate, bounds)
                if len(bounds) >= 2:
                    return bounds
        return None

    def _extract_from_to_pattern(
        self,
        doc,
        id_to_candidate: Dict[str, Dict]
    ) -> Optional[Dict[str, str]]:
        """Извлечь паттерн 'от X в направлении Y' / 'от X к Y'."""
        from_id = None
        to_id = None

        for token in doc:
            # Find 'от' (from)
            if token.lemma_ == 'от':
                for child in token.children:
                    ids = []
                    self._collect_location_ids(child, id_to_candidate, ids)
                    if ids:
                        from_id = ids[0]
                        break

            # Find direction markers
            if token.lemma_ in ('направление', 'сторона', 'к'):
                for child in token.children:
                    ids = []
                    self._collect_location_ids(child, id_to_candidate, ids)
                    if ids:
                        to_id = ids[0]
                        break

        if from_id and to_id:
            return {"from": from_id, "to": to_id}
        return None

    def _extract_square_pattern(
        self,
        doc,
        id_to_candidate: Dict[str, Dict]
    ) -> Optional[List[str]]:
        """Извлечь паттерн 'в квадрате A, B, C, D'."""
        for token in doc:
            if token.lemma_ == 'квадрат':
                bounds = []
                for child in token.children:
                    self._collect_location_ids(child, id_to_candidate, bounds)
                if len(bounds) == 4:
                    return bounds
        return None

    def _extract_near_pattern(
        self,
        doc,
        id_to_candidate: Dict[str, Dict]
    ) -> Optional[List[str]]:
        """Извлечь паттерн 'недалеко от A, B, C' / 'рядом с A, B, C'."""
        for token in doc:
            if token.lemma_ in ('недалеко', 'рядом', 'возле', 'около'):
                objects = []
                for child in token.children:
                    self._collect_location_ids(child, id_to_candidate, objects)
                if objects:
                    return objects
        return None

    def _collect_location_ids(
        self,
        token,
        id_to_candidate: Dict[str, Dict],
        collected: List[str]
    ) -> None:
        """Рекурсивно собрать ID всех location токенов в поддереве."""
        if token._.is_location and token._.candidate_id:
            if token._.candidate_id not in collected:
                collected.append(token._.candidate_id)

        for child in token.children:
            self._collect_location_ids(child, id_to_candidate, collected)

    async def close(self) -> None:
        """Очистить ресурсы."""
        self._nlp = None
        self._initialized = False
        logger.info("[SpaCy] Closed")
