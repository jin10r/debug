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
  • processor_match_time_seconds — гистограмма latency обработки сообщения;
  • processor_tier_distribution{tier} — распределение матчей по тирам.
"""

import logging

try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST, REGISTRY
except ImportError:
    Counter = None
    Histogram = None
    generate_latest = None
    CONTENT_TYPE_LATEST = None
    REGISTRY = None

from aiohttp import web

logger = logging.getLogger(__name__)


# ============================================
# No-op stubs when prometheus_client is absent
# ============================================

class _NoopMetric:
    def __init__(self, *a, **k):
        pass
    def labels(self, **kw):
        return self
    def inc(self, *a, **k):
        pass
    def observe(self, *a, **k):
        pass
    def set(self, *a, **k):
        pass
    def info(self, *a, **k):
        pass


def _metric_cls(base):
    return base if base is not None else _NoopMetric


Counter = _metric_cls(Counter)
Histogram = _metric_cls(Histogram)


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
for _src in ('stem_exact', 'stem_reorder', 'surface_typo', 'surface_typo+semantic', 'unknown'):
    geo_matches_total.labels(source=_src).inc(0)


# ============================================
# Processor latency + tier distribution
# ============================================

processor_match_time_seconds = Histogram(
    'processor_match_time_seconds',
    'Histogram of message processing latency (find_geo + insert)',
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

processor_tier_distribution = Counter(
    'processor_tier_distribution',
    'Distribution of geo matches by tier',
    ['tier']
)

for _tier in ('tier1_stem', 'tier2_typo', 'tier3_semantic', 'no_match'):
    processor_tier_distribution.labels(tier=_tier).inc(0)


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
    if generate_latest is None or REGISTRY is None:
        return web.Response(text="", content_type='text/plain')
    return web.Response(
        body=generate_latest(REGISTRY),
        headers={'Content-Type': CONTENT_TYPE_LATEST or 'text/plain'}
    )
