"""SemanticResolver — определяет стратегию geo-резолюции по контексту сообщения.

Pre-filter только: быстрые правила (только предлоги), без вызова LLM/модели.
PostGIS вычисляет геометрию по выбранной стратегии.
"""

import logging
from typing import Any, Dict, List, Optional

from core.settings import settings

logger = logging.getLogger(__name__)

class SemanticResolver:
    """Определяет стратегию геолокации по семантике текста.

    Упрощённая версия: только правило предлогов направления -> midpoint.
    Остальные стратегии (single_match, intersection) определяются в PostGIS.
    """

    def __init__(self, morph, index):
        """Инициализация резолвера с морфологией и индексом."""
        self._morph = morph
        self._index = index
        self._initialized = False

    async def initialize(self, pg_pool) -> None:
        """Установка флага готовности резолвера."""
        self._initialized = True
        logger.info("[Resolver] Initialized (simplified: prepositional midpoint only)")

    async def resolve(
        self,
        text: str,
        tokens: list,
        lemmas: list,
        candidates: List[Dict],
    ) -> Optional[Dict[str, Any]]:
        """Определение стратегии геолокации по тексту и кандидатам."""
        if not self._initialized or not candidates:
            return None

        result = self._pre_filter(text, candidates)
        if result is not None:
            logger.debug(f"[Resolver] Pre-filter: {result['strategy']} ({result.get('reasoning')})")
            return result

        return None

    def _pre_filter(self, text: str, candidates: List[Dict]) -> Optional[Dict[str, Any]]:
        """Быстрая предфильтрация: поиск предлогов направления."""
        text_lower = text.lower()

        has_from_to = any(p in text_lower for p in ('от', 'до')) and len(candidates) >= 2
        has_between = 'между' in text_lower and len(candidates) >= 2

        if has_from_to or has_between:
            return {
                'geo_ids': [c['geo_id'] for c in candidates],
                'strategy': 'midpoint',
                'reasoning': 'prepositional_construction',
            }

        return None

    async def close(self) -> None:
        """Сброс флага инициализации резолвера."""
        self._initialized = False
        logger.info("[Resolver] Closed")
