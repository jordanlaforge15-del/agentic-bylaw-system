"""Test the /v1/_test/evaluate-bylaws endpoint exposed by the e2e server.

The endpoint is exercised end-to-end through TestClient (FastAPI's
in-process HTTP test harness), so the chain is:

    HTTP request -> Pydantic body validation -> EvaluatorService ->
    RetrievalService -> sqlite session -> JSON response

Pinned behaviours:

* Compliant body returns overall_status=compliant.
* Non-compliant body surfaces a shortfall.
* Bad payload (missing attributes) returns 422 (Pydantic validation).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# The e2e server module reads DATABASE_URL at session_scope() time, so
# we point it at a temp sqlite file before importing. The fixture
# below resets the env per-test to avoid leaking state.


@pytest.fixture
def e2e_client(tmp_path: Path, monkeypatch):
    """Boot the e2e FastAPI app against a fresh sqlite DB.

    Bypasses Clerk + Postgres entirely; the app's MockGateway is the
    one wired by build_e2e_app(). We swap DATABASE_URL via env so
    every layer of the stack — case routes, evaluator endpoint, etc.
    — opens sessions against the same file.
    """
    db_path = tmp_path / "e2e_evaluator.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("LAYER1_DATABASE_URL", db_url)

    # Force layer1 settings to re-read the env var. The settings
    # object is cached per-process in some configurations; this lets
    # the test point at the new DB without a process restart.
    import layer1.config as _layer1_config  # noqa: WPS433 — local import to delay

    try:
        _layer1_config.get_settings.cache_clear()
    except AttributeError:
        pass

    from layer1.db.init_db import create_all

    create_all(db_url)

    from layer1.db.base import Document, SourceFragment
    from layer1.db.session import session_scope
    from layer1.models.enums import FragmentType, ParseStatus

    with session_scope(db_url) as session:
        doc = Document(
            municipality="HRM",
            bylaw_name="Test Bylaw",
            source_path="t.pdf",
            file_hash="evaluator-endpoint-test",
            mime_type="application/pdf",
            page_count=1,
            parser_version="test",
        )
        session.add(doc)
        session.flush()
        session.add(
            SourceFragment(
                document_id=doc.id,
                fragment_type=FragmentType.CLAUSE,
                citation_path="4.2.1",
                page_start=1,
                page_end=1,
                text="The minimum front yard shall not be less than 4.5 metres.",
                parse_status=ParseStatus.PARSED,
                confidence=1.0,
                attribute_tags=["front_setback_m"],
            )
        )

    # Import the app AFTER env mutation so build_e2e_app() reads the
    # right DATABASE_URL.
    from fastapi.testclient import TestClient
    from advisor.api.e2e_server import build_e2e_app

    app = build_e2e_app()
    with TestClient(app) as client:
        yield client


def test_evaluate_endpoint_compliant(e2e_client) -> None:
    response = e2e_client.post(
        "/v1/_test/evaluate-bylaws",
        json={
            "attributes": [
                {"attribute_key": "front_setback_m", "value": 6.0, "unit": "m"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overall_status"] == "compliant"
    assert body["evaluator_version"].startswith("phase1-")
    assert body["attribute_results"][0]["verdict"] == "pass"
    assert body["attribute_results"][0]["applicable_clauses"][0]["citation_path"] == "4.2.1"


def test_evaluate_endpoint_non_compliant_surfaces_shortfall(e2e_client) -> None:
    response = e2e_client.post(
        "/v1/_test/evaluate-bylaws",
        json={
            "attributes": [
                {"attribute_key": "front_setback_m", "value": 3.0, "unit": "m"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overall_status"] == "non_compliant"
    result = body["attribute_results"][0]
    assert result["verdict"] == "fail"
    assert result["delta"]["shortfall"] == pytest.approx(1.5, rel=1e-6)


def test_evaluate_endpoint_rejects_empty_attributes(e2e_client) -> None:
    response = e2e_client.post(
        "/v1/_test/evaluate-bylaws",
        json={"attributes": []},
    )
    # Pydantic v2 returns 422 for min_length violations.
    assert response.status_code == 422


def test_evaluate_endpoint_rejects_missing_attribute_key(e2e_client) -> None:
    response = e2e_client.post(
        "/v1/_test/evaluate-bylaws",
        json={
            "attributes": [
                {"value": 6.0, "unit": "m"},
            ],
        },
    )
    assert response.status_code == 422
