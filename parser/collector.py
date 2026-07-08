"""Collector — сбор реальных примеров (input → target) для обучения GeoIntentSeq2Seq.

Формат вход/выход — по ТЗ GeoIntentSeq2Seq.
Встраивается в message_processor после SemanticResolver.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_STRATEGY_MAP = {
    'single_match': 'single',
    'intersection': 'intersection',
    'midpoint': 'midpoint',
}


def build_input(query: str, candidates: List[Dict]) -> str:
    """Собрать входную строку модели."""
    lines = [f"запрос: {query}", "кандидаты:"]
    for c in candidates:
        gtype = c.get('geom_type', 'ST_Point')
        gtype_clean = gtype.replace('ST_', '').lower()
        lines.append(
            f"  [id: {c['geo_id']}] название: {c['matched_name']}, "
            f"тип: {c.get('type', 'street')} ({gtype_clean})"
        )
    return '\n'.join(lines)


def build_target(strategy: str, geo_ids: List[int], candidates: List[Dict]) -> str:
    """Собрать целевую строку — компактный формат модели."""
    clean_strategy = _STRATEGY_MAP.get(strategy, strategy)
    if clean_strategy == 'single':
        return f"single: obj={geo_ids[0]}"
    ids_str = ','.join(str(i) for i in geo_ids)
    return f"{clean_strategy}: objs={ids_str}"
