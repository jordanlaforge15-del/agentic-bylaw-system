"""Tests for advisor.logging — structured JSON output + correlation IDs."""
from __future__ import annotations

import json
import logging
import uuid

import pytest

from advisor.logging import (
    CorrelationIdMiddleware,
    JsonFormatter,
    TextFormatter,
    _CorrelationIdFilter,
    correlation_id,
    setup_logging,
)


class TestJsonFormatter:
    def test_basic_output_is_valid_json(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        record.correlation_id = None  # type: ignore[attr-defined]
        line = formatter.format(record)
        data = json.loads(line)
        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "hello world"
        assert data["correlation_id"] is None
        assert "timestamp" in data

    def test_correlation_id_included(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="x", level=logging.WARNING, pathname="", lineno=0,
            msg="warn", args=(), exc_info=None,
        )
        record.correlation_id = "abc-123"  # type: ignore[attr-defined]
        data = json.loads(formatter.format(record))
        assert data["correlation_id"] == "abc-123"

    def test_exception_field(self):
        formatter = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="x", level=logging.ERROR, pathname="", lineno=0,
                msg="oops", args=(), exc_info=sys.exc_info(),
            )
            record.correlation_id = None  # type: ignore[attr-defined]
        data = json.loads(formatter.format(record))
        assert "ValueError: boom" in data["exception"]


class TestCorrelationIdFilter:
    def test_injects_context_var(self):
        filt = _CorrelationIdFilter()
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="hi", args=(), exc_info=None,
        )
        cid = uuid.uuid4().hex
        token = correlation_id.set(cid)
        try:
            assert filt.filter(record) is True
            assert record.correlation_id == cid  # type: ignore[attr-defined]
        finally:
            correlation_id.reset(token)

    def test_default_is_none(self):
        filt = _CorrelationIdFilter()
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=0,
            msg="hi", args=(), exc_info=None,
        )
        assert filt.filter(record) is True
        assert record.correlation_id is None  # type: ignore[attr-defined]


class TestSetupLogging:
    def test_json_mode(self):
        setup_logging(level="WARNING", json_output=True)
        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)

    def test_text_mode(self):
        setup_logging(level="DEBUG", json_output=False)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert isinstance(root.handlers[0].formatter, TextFormatter)

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        monkeypatch.setenv("LOG_FORMAT", "text")
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.ERROR
        assert isinstance(root.handlers[0].formatter, TextFormatter)

    def test_idempotent(self):
        setup_logging(level="INFO", json_output=True)
        setup_logging(level="INFO", json_output=True)
        assert len(logging.getLogger().handlers) == 1

    def test_noisy_loggers_quieted(self):
        setup_logging(level="DEBUG", json_output=True)
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING


class TestTextFormatter:
    def test_format_includes_correlation_id(self):
        formatter = TextFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        record.correlation_id = "cid-abc"  # type: ignore[attr-defined]
        formatted = formatter.format(record)
        assert "cid-abc" in formatted
        assert "hello" in formatted
