"""Tests for core/middlewares/ratelimit.RateLimiter fixed-window logic."""
import importlib

rl = importlib.import_module("core.middlewares.ratelimit")


def test_allows_up_to_limit_then_blocks():
    r = rl.RateLimiter(default_limit=3, window_seconds=60)
    allowed = [r.check("1.1.1.1", "/x")[0] for _ in range(4)]
    assert allowed == [True, True, True, False]


def test_counters_are_per_ip():
    r = rl.RateLimiter(default_limit=1, window_seconds=60)
    assert r.check("a", "/x")[0] is True
    assert r.check("a", "/x")[0] is False
    assert r.check("b", "/x")[0] is True  # different IP → fresh quota


def test_counters_are_per_path():
    r = rl.RateLimiter(default_limit=1, window_seconds=60)
    assert r.check("a", "/x")[0] is True
    assert r.check("a", "/y")[0] is True  # different path → fresh quota


def test_endpoint_override_raises_limit():
    r = rl.RateLimiter(default_limit=2, window_seconds=60)
    # /api/events overridden to (120, 60) → 5 requests all allowed
    assert all(r.check("a", "/api/events")[0] for _ in range(5))


def test_window_reset_restores_quota():
    r = rl.RateLimiter(default_limit=1, window_seconds=60)
    assert r.check("a", "/x")[0] is True
    assert r.check("a", "/x")[0] is False
    # force the window to expire by ageing the stored window_start
    r._counters[("a", "/x")][1] -= 61
    assert r.check("a", "/x")[0] is True


def test_remaining_count_decrements():
    r = rl.RateLimiter(default_limit=5, window_seconds=60)
    _, limit, remaining, _ = r.check("a", "/x")
    assert limit == 5 and remaining == 4


class _FakeReq:
    def __init__(self, headers=None, remote=None):
        self.headers = headers or {}
        self.remote = remote


def test_client_ip_prefers_x_real_ip():
    r = rl.RateLimiter()
    req = _FakeReq(
        headers={"X-Real-IP": "9.9.9.9", "X-Forwarded-For": "1.2.3.4, 9.9.9.9"},
        remote="10.0.0.1",
    )
    assert r._get_client_ip(req) == "9.9.9.9"


def test_client_ip_xff_uses_rightmost_not_spoofable_leftmost():
    r = rl.RateLimiter()
    # attacker-supplied leftmost "1.2.3.4" must be ignored; trust proxy-appended rightmost
    req = _FakeReq(headers={"X-Forwarded-For": "1.2.3.4, 9.9.9.9"}, remote="10.0.0.1")
    assert r._get_client_ip(req) == "9.9.9.9"


def test_client_ip_falls_back_to_remote():
    r = rl.RateLimiter()
    assert r._get_client_ip(_FakeReq(headers={}, remote="10.0.0.1")) == "10.0.0.1"
    assert r._get_client_ip(_FakeReq(headers={}, remote=None)) == "127.0.0.1"
