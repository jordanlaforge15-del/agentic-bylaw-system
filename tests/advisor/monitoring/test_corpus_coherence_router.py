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
from bylaw_retrieval.retrieval import (
    CorpusCoherenceReport,
    E2eContaminationMarker,
    E2eContaminationReport,
    EnabledDocumentRef,
    EnabledNameCollision,
    EnabledNameCollisionReport,
    GoverningBylawCoverageReport,
    MissingOverlayRole,
    UnheldGoverningBylaw,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    router_module._cached_coherence_body = None
    router_module._cached_coherence_status = 200
    router_module._cached_coherence_at = 0.0
    yield
    router_module._cached_coherence_body = None
    router_module._cached_coherence_status = 200
    router_module._cached_coherence_at = 0.0


@pytest.fixture(autouse=True)
def _clean_contamination_by_default(monkeypatch: pytest.MonkeyPatch):
    """ABS-432/ABS-434: the endpoint now also runs the e2e-contamination
    sweep and the enabled-name-collision audit via the same lazy import
    pattern. Default every test to clean results in a non-test deployment;
    check-specific tests override."""
    monkeypatch.delenv("ADVISOR_E2E_MARKERS_EXPECTED", raising=False)
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_e2e_contamination",
        lambda *a, **k: _clean_contamination(),
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_enabled_name_collisions",
        lambda *a, **k: _collision_free_report(),
    )
    # ABS-472: same lazy-import pattern; default to fully-covered so the
    # status-code tests below stay about the checks they name.
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_governing_bylaw_coverage",
        lambda *a, **k: _complete_coverage(),
    )


def _complete_coverage() -> GoverningBylawCoverageReport:
    return GoverningBylawCoverageReport(
        complete=True,
        datasets_checked=1,
        features_checked=3119,
        covered_features=3119,
        unheld_features=0,
        unheld=[],
    )


def _incomplete_coverage() -> GoverningBylawCoverageReport:
    """The real HRM shape: 7,950 of 11,069 zoning features are governed by
    by-laws the corpus does not hold."""
    return GoverningBylawCoverageReport(
        complete=False,
        datasets_checked=1,
        features_checked=11069,
        covered_features=3119,
        unheld_features=7950,
        unheld=[
            UnheldGoverningBylaw(
                dataset_name="halifax_zoning_boundaries",
                governing_bylaw="Downtown Halifax Land Use By-law",
                governing_bylaw_code="hrm:DHFX",
                feature_count=28,
                detail="28 feature(s) are governed by a by-law not in the corpus",
            )
        ],
    )


def _clean_contamination() -> E2eContaminationReport:
    return E2eContaminationReport(
        contaminated=False,
        marker_counts={
            "document_parser_version": 0,
            "document_file_hash": 0,
            "external_dataset_name": 0,
        },
        markers=[],
    )


def _dirty_contamination() -> E2eContaminationReport:
    return E2eContaminationReport(
        contaminated=True,
        marker_counts={
            "document_parser_version": 1,
            "document_file_hash": 1,
            "external_dataset_name": 0,
        },
        markers=[
            E2eContaminationMarker(
                table="document",
                row_id=99,
                marker_kinds=["document_parser_version", "document_file_hash"],
                detail="document 99: 'Corpus Coherence Test Bylaw', file_hash='e2e-doc-1'",
            )
        ],
    )


def _collision_free_report() -> EnabledNameCollisionReport:
    return EnabledNameCollisionReport(
        collision_free=True, enabled_documents=3, identities_checked=3, collisions=[]
    )


def _colliding_report() -> EnabledNameCollisionReport:
    return EnabledNameCollisionReport(
        collision_free=False,
        enabled_documents=4,
        identities_checked=3,
        collisions=[
            EnabledNameCollision(
                normalized_municipality="hrm",
                normalized_bylaw_name="regionalcentrelandusebylaw",
                document_ids=[15, 38],
                documents=[
                    EnabledDocumentRef(
                        id=15, municipality="HRM", bylaw_name="Regional Centre Land Use By-law"
                    ),
                    EnabledDocumentRef(
                        id=38, municipality="HRM", bylaw_name="Regional Centre Land Use By-Law"
                    ),
                ],
                detail=(
                    "2 enabled documents share the normalized bylaw identity "
                    "('hrm', 'regionalcentrelandusebylaw'): 15, 38"
                ),
            )
        ],
    )


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
    assert body["e2e_contamination"]["status"] == "ok"
    assert body["e2e_contamination"]["contaminated"] is False


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
    body = response.json()
    assert body["status"] == "error"
    # ABS-420: name the failure. "error" alone cannot tell an operator mid-
    # rollout whether the database is down or the image cannot read its
    # dataset configs.
    assert "RuntimeError" in body["detail"]
    assert "db unreachable" in body["detail"]


def test_an_audit_that_checked_nothing_is_an_error_not_a_green(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ABS-420 — the shape production served for months.

    With no dataset configs on disk the audit loads zero declarations, finds
    zero missing roles, and reports itself coherent. That is
    ``{"status":"ok","checked_roles":0}``: a green that would survive every
    degradation this endpoint exists to catch.
    """
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence",
        lambda *a, **k: CorpusCoherenceReport(
            coherent=True, checked_roles=0, bylaws_checked=0, missing=[]
        ),
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checked_roles"] == 0
    assert "no overlay declarations loaded" in body["detail"]


def test_returns_503_contaminated_when_e2e_markers_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ABS-432: a marker row in a non-test deployment turns the endpoint red,
    naming the offending row — green only when zero."""
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _coherent_report()
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_e2e_contamination",
        lambda *a, **k: _dirty_contamination(),
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "contaminated"
    contamination = body["e2e_contamination"]
    assert contamination["status"] == "contaminated"
    assert contamination["contaminated"] is True
    assert contamination["marker_counts"]["document_parser_version"] == 1
    assert contamination["markers"][0]["row_id"] == 99
    assert "e2e-doc-1" in contamination["markers"][0]["detail"]


def test_markers_expected_deployment_reports_fixtures_without_going_red(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ABS-432: the e2e stack itself (ADVISOR_E2E_MARKERS_EXPECTED=1, set by
    advisor.api.e2e_server) legitimately hosts seeded marker rows — they are
    reported informationally and the endpoint stays green."""
    monkeypatch.setenv("ADVISOR_E2E_MARKERS_EXPECTED", "1")
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _coherent_report()
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_e2e_contamination",
        lambda *a, **k: _dirty_contamination(),
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    contamination = body["e2e_contamination"]
    assert contamination["status"] == "expected_test_fixtures"
    assert contamination["contaminated"] is True


def test_incoherent_takes_priority_over_contaminated_in_top_level_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _incoherent_report()
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_e2e_contamination",
        lambda *a, **k: _dirty_contamination(),
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "incoherent"
    assert body["e2e_contamination"]["status"] == "contaminated"


def test_returns_503_name_collision_when_two_enabled_docs_share_a_normalized_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ABS-434: the doc-15/38 double-enable turns the endpoint red, naming
    both ids — no expected-fixtures exemption exists for this check."""
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _coherent_report()
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_enabled_name_collisions",
        lambda *a, **k: _colliding_report(),
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "name_collision"
    collisions = body["enabled_name_collisions"]
    assert collisions["status"] == "collision"
    assert collisions["collision_free"] is False
    assert collisions["collisions"][0]["document_ids"] == [15, 38]
    assert {d["bylaw_name"] for d in collisions["collisions"][0]["documents"]} == {
        "Regional Centre Land Use By-law",
        "Regional Centre Land Use By-Law",
    }


def test_name_collision_reported_in_body_when_collision_free(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _coherent_report()
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["enabled_name_collisions"]["status"] == "ok"
    assert body["enabled_name_collisions"]["collision_free"] is True
    assert body["enabled_name_collisions"]["collisions"] == []


def test_name_collision_goes_red_even_where_e2e_markers_are_expected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ABS-434: unlike the contamination sweep, the e2e stack gets no pass —
    a fragmented enabled corpus is never legitimate, even among fixtures."""
    monkeypatch.setenv("ADVISOR_E2E_MARKERS_EXPECTED", "1")
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _coherent_report()
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_enabled_name_collisions",
        lambda *a, **k: _colliding_report(),
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 503
    assert response.json()["status"] == "name_collision"


def test_incoherent_and_contaminated_outrank_name_collision_in_top_level_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_enabled_name_collisions",
        lambda *a, **k: _colliding_report(),
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_e2e_contamination",
        lambda *a, **k: _dirty_contamination(),
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _incoherent_report()
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "incoherent"
    # The per-check statuses still tell the whole story.
    assert body["e2e_contamination"]["status"] == "contaminated"
    assert body["enabled_name_collisions"]["status"] == "collision"


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


# --- ABS-472: governing-by-law coverage is informational, never red -------


def test_incomplete_governing_bylaw_coverage_does_not_turn_the_endpoint_red(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A municipality publishes far more by-law areas than any corpus
    ingests, so incomplete coverage is the steady state. Failing on it would
    leave this endpoint permanently 503 and train operators to ignore it —
    the answers for that ground are already refused at request time."""
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _coherent_report()
    )
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_governing_bylaw_coverage",
        lambda *a, **k: _incomplete_coverage(),
    )

    response = client.get("/v1/monitoring/corpus-coherence")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    coverage = body["governing_bylaw_coverage"]
    assert coverage["complete"] is False
    assert coverage["unheld_features"] == 7950
    assert coverage["unheld"][0]["governing_bylaw"] == "Downtown Halifax Land Use By-law"


def test_full_governing_bylaw_coverage_is_reported_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The section is always present, so an operator can tell "covered" from
    "not measured"."""
    monkeypatch.setattr(
        "bylaw_retrieval.retrieval.audit_corpus_coherence", lambda *a, **k: _coherent_report()
    )

    body = client.get("/v1/monitoring/corpus-coherence").json()

    assert body["governing_bylaw_coverage"]["complete"] is True
    assert body["governing_bylaw_coverage"]["unheld"] == []
