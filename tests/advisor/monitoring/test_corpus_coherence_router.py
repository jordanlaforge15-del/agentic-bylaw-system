"""ABS-356 — GET /v1/monitoring/corpus-coherence.

Exercises the router's status-code mapping and 30s result cache without
needing a full real-corpus seed (that end-to-end path — real dataset
configs, real DB, real scoping — is covered by
``tests/test_corpus_coherence_audit.py`` and the Playwright spec). Here we
monkeypatch ``bylaw_retrieval.retrieval.audit_corpus_coherence`` (the lazy
import the endpoint resolves at call time) with a stub report, so these
tests are about the HTTP/caching contract, not the audit logic itself.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from advisor.monitoring import router as router_module
from bylaw_retrieval.retrieval import CorpusCoherenceReport, MissingOverlayRole


@pytest.fixture(autouse=True)
def _reset_cache():
    router_module._cached_coherence_body = None
    router_module._cached_coherence_status = 200
    router_module._cached_coherence_at = 0.0
    yield
    router_module._cached_coherence_body = None
    router_module._cached_coherence_status = 200
    router_module._cached_coherence_at = 0.0


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router_module.router)
    return TestClient(app)


def _coherent_report() -> CorpusCoherenceReport:
    return CorpusCoherenceReport(coherent=True, checked_roles=7, bylaws_checked=1, missing=[])


def _incoherent_report() -> CorpusCoherenceReport:
    return CorpusCoherenceReport(
        coherent=False,
        checked_roles=7,
        bylaws_checked=1,
        missing=[
            MissingOverlayRole(
                role="shadow_impact",
                dataset_name="halifax_shadow_impact_areas",
                municipality="HRM",
                bylaw_name="Regional Centre Land Use By-Law",
                fragment_citation="Schedule 51",
                reason="unlinked",
                detail="no dataset named 'halifax_shadow_impact_areas' has ever been ingested",
            )
        ],
    )


def test_returns_200_when_coherent(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _coherent_report()
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["missing"] == []
    assert body["checked_roles"] == 7


def test_returns_503_with_missing_role_when_incoherent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _incoherent_report()
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "incoherent"
    assert len(body["missing"]) == 1
    assert body["missing"][0]["role"] == "shadow_impact"
    assert body["missing"][0]["reason"] == "unlinked"


def test_returns_503_when_the_audit_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr("bylaw_retrieval.retrieval.audit_corpus_coherence", _boom)

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_caches_the_result_within_the_ttl(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_audit = MagicMock(return_value=_coherent_report())
    monkeypatch.setattr("bylaw_retrieval.retrieval.audit_corpus_coherence", fake_audit)

    first = client.get("/v1/monitoring/corpus-coherence")
    second = client.get("/v1/monitoring/corpus-coherence")

    assert first.status_code == 200
    assert second.status_code == 200
    fake_audit.assert_called_once()


def test_recomputes_after_the_cache_expires(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_audit = MagicMock(return_value=_coherent_report())
    monkeypatch.setattr("bylaw_retrieval.retrieval.audit_corpus_coherence", fake_audit)
    monkeypatch.setattr(router_module, "_coherence_cache_ttl", 0.0)

    client.get("/v1/monitoring/corpus-coherence")
    client.get("/v1/monitoring/corpus-coherence")

    assert fake_audit.call_count == 2
