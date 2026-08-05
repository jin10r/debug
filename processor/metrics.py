"""Prometheus-метрики NLP-процессора (processor).

Экспортируются на /metrics health-сервера (processor/health.py, порт 8765).
Отдельный процесс → свой REGISTRY, конфликтов с core (core/utils/metrics.py) нет.

Метрики:
  • semantic_checked_total    — кандидатов серой зоны проверила ONNX-модель;
  • semantic_accepted_total   — принято моделью (semantic_score >= порога);
  • semantic_rejected_total   — отклонено моделью (ложные позитивы отсеяны);
  • semantic_missing_embedding_total — эмбеддинга нет, кандидат сохранён (keep);
  • geo_matches_total{source} — матчи по источнику (stem_exact / surface_typo /
    surface_typo+semantic) — из них считается доля surface_typo-матчей.
"""

import logging

from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST, REGISTRY
from aiohttp import web

logger = logging.getLogger(__name__)

# ============================================
# Semantic filter (ONNX rubert-tiny2)
# ============================================

semantic_checked_total = Counter(
    'semantic_checked_total',
    'Total gray-zone candidates validated by the semantic model'
)

semantic_accepted_total = Counter(
    'semantic_accepted_total',
    'Total gray-zone candidates accepted by the semantic model'
)

semantic_rejected_total = Counter(
    'semantic_rejected_total',
    'Total gray-zone candidates rejected by the semantic model (false positives)'
)

semantic_missing_embedding_total = Counter(
    'semantic_missing_embedding_total',
    'Gray-zone candidates with no precomputed embedding (conservatively kept)'
)

# ============================================
# Geo matches by source
# ============================================

geo_matches_total = Counter(
    'geo_matches_total',
    'Total geo matches by source',
    ['source']  # 'stem_exact', 'stem_reorder', 'surface_typo', 'surface_typo+semantic'
)

# Pre-warm серий: без этого панели показывают "No data" до первого матча.
# Метрики существуют с момента старта процесса (как semantic_* счётчики).
for _src in ('stem_exact', 'stem_reorder', 'surface_typo', 'surface_typo+semantic', 'unknown'):
    geo_matches_total.labels(source=_src).inc(0)


# Источники ограничены фиксированным набором (см. _link_span/filter_candidates),
# поэтому .inc() не может упасть на неизвестном label — try/except не нужен.
def record_geo_matches(results) -> None:
    """Инкремент geo_matches_total по source для каждого результата."""
    for r in results:
        src = r.get('source') or 'unknown'
        geo_matches_total.labels(source=src).inc()


# ============================================
# Metrics endpoint
# ============================================

async def metrics_handler(request: web.Request):
    """Prometheus-эндпоинт: отдаёт метрики процессора в text-формате."""
    return web.Response(
        body=generate_latest(REGISTRY),
        headers={'Content-Type': CONTENT_TYPE_LATEST}
    )
