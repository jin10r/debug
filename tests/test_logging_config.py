"""Tests for core/utils/logging_config.py."""
import json
import logging
from io import StringIO
from unittest.mock import patch

import pytest

from conftest import load_module_by_path

lc = load_module_by_path("_logging_config_under_test", "core/utils/logging_config.py")
JSONFormatter = lc.JSONFormatter
ContextLogger = lc.ContextLogger
setup_logging = lc.setup_logging
get_logger_with_context = lc.get_logger_with_context
log_with_extra = lc.log_with_extra


# ============================================================
# JSONFormatter
# ============================================================

class TestJSONFormatter:
    def test_format_produces_valid_json(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello %s", args=("world",), exc_info=None
        )
        out = formatter.format(record)
        data = json.loads(out)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert "timestamp" in data

    def test_format_includes_exc_info(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = (ValueError, ValueError("boom"), None)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="error", args=(), exc_info=exc_info
        )
        out = formatter.format(record)
        data = json.loads(out)
        assert "exception" in data
        assert data["exception"]["type"] == "ValueError"
        assert data["exception"]["message"] == "boom"

    def test_format_includes_extra_fields(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None
        )
        record.request_id = "req-123"
        record.user_id = 42
        record.extra_data = {"correlation_id": "abc"}
        out = formatter.format(record)
        data = json.loads(out)
        assert data["request_id"] == "req-123"
        assert data["user_id"] == 42
        assert data["correlation_id"] == "abc"

    def test_format_without_extra_fields(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None
        )
        out = json.loads(formatter.format(record))
        assert "request_id" not in out
        assert "user_id" not in out
        assert "extra_data" not in out


# ============================================================
# ContextLogger
# ============================================================

class TestContextLogger:
    def test_process_merges_context_into_extra(self):
        logger = logging.getLogger("ctx_test")
        adapter = ContextLogger(logger, {"request_id": "r1", "user_id": 10})
        msg, kwargs = adapter.process("hello", {})
        extra = kwargs.get("extra", {})
        assert extra["request_id"] == "r1"
        assert extra["user_id"] == 10
        assert msg == "hello"


# ============================================================
# setup_logging
# ============================================================

class TestSetupLogging:
    def test_setup_configures_root_handler(self):
        # Remove existing handlers to avoid pollution
        logging.root.handlers = []
        setup_logging(level=logging.DEBUG, json_format=False)
        assert len(logging.root.handlers) == 1
        handler = logging.root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert logging.root.level == logging.DEBUG

    def test_setup_json_format_uses_json_formatter(self):
        logging.root.handlers = []
        setup_logging(level=logging.INFO, json_format=True)
        handler = logging.root.handlers[0]
        assert isinstance(handler.formatter, JSONFormatter)


# ============================================================
# log_exceptions decorator
# ============================================================

class TestLogExceptions:
    def test_sync_wrapper_logs_and_raises(self, caplog):
        @lc.log_exceptions(logging.getLogger("sync_test"))
        def bad_func():
            raise RuntimeError("sync boom")

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError, match="sync boom"):
                bad_func()
        assert "Exception in bad_func" in caplog.text

    @pytest.mark.asyncio
    async def test_async_wrapper_logs_and_raises(self, caplog):
        @lc.log_exceptions(logging.getLogger("async_test"))
        async def bad_async():
            raise RuntimeError("async boom")

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError, match="async boom"):
                await bad_async()
        assert "Exception in bad_async" in caplog.text


# ============================================================
# get_logger_with_context / log_with_extra
# ============================================================

class TestLoggerHelpers:
    def test_get_logger_with_context_returns_adapter(self):
        logger = get_logger_with_context("helper_test", request_id="r99")
        assert isinstance(logger, ContextLogger)
        assert logger.extra["request_id"] == "r99"

    def test_log_with_extra_calls_logger(self, caplog):
        caplog.set_level(logging.INFO, logger="helper_test2")
        log_with_extra(
            logging.getLogger("helper_test2"),
            logging.INFO,
            "event occurred",
            event_id=7,
        )
        assert "event occurred" in caplog.text
