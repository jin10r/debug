"""Prometheus metrics for core service.

Used by:
  - core/api/websocket.py: ws_connections_total, ws_connections_rejected_total,
                           ws_messages_sent_total, ws_broadcast_duration_seconds
  - common/logging_config.py: http_requests_total, http_request_duration_seconds
"""

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# WebSocket metrics
# ---------------------------------------------------------------------------

ws_connections_total = Counter(
    "ws_connections_total",
    "Total WebSocket connections accepted",
)

ws_connections_rejected_total = Counter(
    "ws_connections_rejected_total",
    "Total WebSocket connections rejected (limit / auth)",
)

ws_messages_sent_total = Counter(
    "ws_messages_sent_total",
    "Total WebSocket messages sent to clients",
)

ws_broadcast_duration_seconds = Histogram(
    "ws_broadcast_duration_seconds",
    "Duration of WebSocket broadcast operations",
    ["layer"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# ---------------------------------------------------------------------------
# HTTP metrics
# ---------------------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
