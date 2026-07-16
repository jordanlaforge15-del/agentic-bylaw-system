"""Unit tests for the SLI/SLO metrics instrumentation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from advisor.api import InMemorySessionStore, create_app
from advisor.api.metrics import (
    SLO_AVAILABILITY_TARGET,
    SLO_CHAT_LATENCY_P95_SECONDS,
    SLO_ERROR_RATE_TARGET,
    compute_sli_compliance,
)
from advisor.api.metrics_middleware import MetricsMiddleware
from advisor.llm.mock import MockGateway, text_response


def _make_app():
    app = create_app(
        gateway=MockGateway(scripted=[text_response("hello back")]),
        retrieval_service_factory=lambda: None,
        session_store=InMemorySessionStore(),
        persona_text="You are a senior urban planner.",
    )
    app.add_middleware(MetricsMiddleware)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app())


class TestSloConstants:
    def test_availability_target(self):
        assert SLO_AVAILABILITY_TARGET == 0.995

    def test_chat_latency_target(self):
        assert SLO_CHAT_LATENCY_P95_SECONDS == 10.0

    def test_error_rate_target(self):
        assert SLO_ERROR_RATE_TARGET == 0.01


class TestSliCompliance:
    def test_compliance_report_structure(self):
        report = compute_sli_compliance()
        assert report["window"] == "since_startup"
        assert "total_requests" in report
        assert "availability" in report
        assert "chat_latency_p95_seconds" in report
        assert "error_rate" in report

    def test_availability_has_target(self):
        report = compute_sli_compliance()
        assert report["availability"]["target"] == SLO_AVAILABILITY_TARGET

    def test_error_rate_has_target(self):
        report = compute_sli_compliance()
        assert report["error_rate"]["target"] == SLO_ERROR_RATE_TARGET


class TestHealthzSli:
    def test_healthz_includes_sli(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert "sli" in body
        assert body["sli"]["availability"]["target"] == SLO_AVAILABILITY_TARGET
        assert body["sli"]["error_rate"]["target"] == SLO_ERROR_RATE_TARGET

    def test_readyz_unchanged(self, client):
        resp = client.get("/readyz")
        body = resp.json()
        assert "sli" not in body
