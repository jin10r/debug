"""Shared Prometheus metrics for cross-service observability.

Definitions live in `common/` — the only package guaranteed to be present in
every service image (parser, nlp_processor, core). The `prometheus_client`
dependency is only guaranteed in the `core` image, so the import is guarded:
images that don't ship it (e.g. parser/processor builds without
prometheus_client) get no-op counters instead of an import error.
"""

try:
    from prometheus_client import Counter
except Exception:  # pragma: no cover - optional dependency
    Counter = None


if Counter is not None:
    layer_classification_fallback_total = Counter(
        "layer_classification_fallback_total",
        "Total layer classifications by resulting layer (bus/cops/traffic/pig)",
        ["layer"],
    )

    geo_match_tier_total = Counter(
        "geo_match_tier_total",
        "Total geo-matching results by tier (tier1 stem / tier2 surface typo / none)",
        ["tier"],
    )
else:
    class _NoopCounter:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, amount=1):
            return None

    layer_classification_fallback_total = _NoopCounter()
    geo_match_tier_total = _NoopCounter()
