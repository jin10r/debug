"""Pytest bootstrap.

- Puts the repo root on sys.path so `core.*` imports work.
- Provides lightweight stubs for pure-python deps that may be absent in a bare
  dev/CI box (environs, pybreaker) so the modules under test import. When the
  real packages ARE installed (full env / CI with requirements), the stubs are
  NOT used — find_spec finds the real package first. Heavy C-ext deps
  (asyncpg/rapidfuzz/pymorphy3) are NOT stubbed here; tests needing them skip.
"""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _missing(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ValueError):
        return True


# --- environs stub (core.settings builds Env at import) -----------------------
if _missing("environs"):
    _m = types.ModuleType("environs")

    class _Env:  # minimal surface used by core/settings.py
        def __init__(self, *a, **k):
            pass

        def read_env(self, *a, **k):
            return None

        def str(self, name, default=None):
            return default

        def bool(self, name, default=None):
            return default

        def int(self, name, default=None):
            return default

        def float(self, name, default=None):
            return default

    _m.Env = _Env
    sys.modules["environs"] = _m


# --- pybreaker stub (telegram_validation decorates with a CircuitBreaker) -----
if _missing("pybreaker"):
    _m = types.ModuleType("pybreaker")

    class _CircuitBreaker:
        def __init__(self, *a, **k):
            pass

        def __call__(self, func):  # used as a decorator -> pass-through
            return func

    class _CircuitBreakerError(Exception):
        pass

    _m.CircuitBreaker = _CircuitBreaker
    _m.CircuitBreakerError = _CircuitBreakerError
    sys.modules["pybreaker"] = _m


# --- prometheus_client stub (metrics import) ---------------------------------
if _missing("prometheus_client"):
    _m = types.ModuleType("prometheus_client")

    class _Counter:
        def __init__(self, *a, **k):
            pass
        def labels(self, **kw):
            return self
        def inc(self, *a, **k):
            pass

    class _Histogram:
        def __init__(self, *a, **k):
            pass
        def labels(self, **kw):
            return self
        def observe(self, *a, **k):
            pass

    class _Gauge:
        def __init__(self, *a, **k):
            pass
        def set(self, *a, **k):
            pass

    class _Info:
        def __init__(self, *a, **k):
            pass
        def info(self, *a, **k):
            pass

    def _generate_latest(*a, **k):
        return b""

    _m.Counter = _Counter
    _m.Histogram = _Histogram
    _m.Gauge = _Gauge
    _m.Info = _Info
    _m.generate_latest = _generate_latest
    _m.CONTENT_TYPE_LATEST = "text/plain"
    _m.REGISTRY = type("Registry", (), {"collect": lambda self: []})()
    sys.modules["prometheus_client"] = _m


def load_module_by_path(name: str, relpath: str):
    """Import a single module file directly, bypassing its package __init__.

    Needed for self-contained submodules (core/text_preprocessor,
    processor/word_tokenizer), которые тестируются без тяжёлых зависимостей,
    подтягиваемых их пакетами (asyncpg/rapidfuzz/pymorphy3).
    """
    path = ROOT / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
