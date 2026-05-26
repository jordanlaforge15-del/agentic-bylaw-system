"""Unit tests for the Prometheus metrics module."""
from __future__ import annotations

import pytest

from advisor.api.metrics import (
    SLO_AVAILABILITY_TARGET,
    SLO_CHAT_LATENCY_P95_SECONDS,
    SLO_ERROR_RATE_CEILING,
    _normalize_path,
)


class TestSLOConstants:
    def test_availability_target(self):
        assert SLO_AVAILABILITY_TARGET == 0.995

    def test_chat_latency_target(self):
        assert SLO_CHAT_LATENCY_P95_SECONDS == 10.0

    def test_error_rate_ceiling(self):
        assert SLO_ERROR_RATE_CEILING == 0.01


class TestNormalizePath:
    def test_root(self):
        assert _normalize_path("/") == "/"

    def test_healthz(self):
        assert _normalize_path("/healthz") == "/healthz"

    def test_chat_endpoint(self):
        assert _normalize_path("/v1/chat") == "/v1/chat"

    def test_session_id_collapsed(self):
        assert _normalize_path("/v1/chat/sessions/42") == "/v1/chat/sessions/{id}"

    def test_session_uuid_collapsed(self):
        assert _normalize_path("/v1/chat/sessions/abc-def") == "/v1/chat/sessions/{id}"

    def test_numeric_id_collapsed(self):
        assert _normalize_path("/v1/cases/123") == "/v1/cases/{id}"

    def test_trailing_slash_stripped(self):
        assert _normalize_path("/healthz/") == "/healthz"

    def test_cases_base(self):
        assert _normalize_path("/v1/cases") == "/v1/cases"
