"""ABS-479 — the test-only endpoint the Playwright spec drives.

``/v1/_test/advisor-search-attribute-tag-filter`` runs the production chat
handler (``advisor.chat.tools.search_bylaw_evidence``) against the server's
own database, so the Playwright spec can assert that ``attribute_tag_filter``
survives the whole chain inside the deployed FastAPI process.

That endpoint is itself test infrastructure, and infrastructure that lies is
worse than none: if it swallowed the filter, or turned the empty-list
rejection into a 500, the spec would go green (or red) for the wrong reason.
This module pins its contract in-process against sqlite so the e2e run only
has the Postgres-specific behaviour left to prove.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "attr_tag_filter_endpoint.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("LAYER1_DATABASE_URL", db_url)

    import layer1.config as _layer1_config  # noqa: PLC0415

    try:
        _layer1_config.get_settings.cache_clear()
    except AttributeError:
        pass

    from layer1.db.base import Document, SourceFragment  # noqa: PLC0415
    from layer1.db.init_db import create_all  # noqa: PLC0415
    from layer1.db.session import session_scope  # noqa: PLC0415
    from layer1.models.enums import FragmentType, ParseStatus  # noqa: PLC0415

    create_all(db_url)
    with session_scope(db_url) as session:
        doc = Document(
            municipality="HRM",
            bylaw_name="Tag Filter Bylaw",
            source_path="t.pdf",
            file_hash="attr-tag-filter-endpoint",
            mime_type="application/pdf",
            page_count=2,
            parser_version="test",
        )
        session.add(doc)
        session.flush()
        # Both clauses share the query vocabulary, so only the tag
        # pre-filter can separate them.
        for citation_path, page, tags in (
            ("4.2.1", 1, ["front_setback_m"]),
            ("4.3.1", 2, ["building_height_m"]),
        ):
            session.add(
                SourceFragment(
                    document_id=doc.id,
                    fragment_type=FragmentType.CLAUSE,
                    citation_path=citation_path,
                    page_start=page,
                    page_end=page,
                    text=(
                        "The maximum building height and the minimum front "
                        f"yard are set by clause {citation_path}."
                    ),
                    parse_status=ParseStatus.PARSED,
                    confidence=1.0,
                    attribute_tags=tags,
                )
            )

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from advisor.api.e2e_server import (  # noqa: PLC0415
        _mount_advisor_search_attribute_tag_filter_endpoint,
        build_e2e_app,
    )

    # The e2e server mounts its test-only endpoints onto the module-level
    # app after construction, so a freshly built app needs the same call.
    app = build_e2e_app()
    _mount_advisor_search_attribute_tag_filter_endpoint(app)
    with TestClient(app) as test_client:
        yield test_client


def _post(client, **body):
    response = client.post(
        "/v1/_test/advisor-search-attribute-tag-filter",
        json={"query": "building height front yard", "limit": 20, **body},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_endpoint_returns_every_clause_without_a_filter(client) -> None:
    body = _post(client)
    assert body["ok"] is True
    paths = {m["citation_path"] for m in body["result"]["matches"]}
    assert paths == {"4.2.1", "4.3.1"}


def test_endpoint_narrows_to_the_tagged_clause(client) -> None:
    body = _post(client, attribute_tag_filter=["building_height_m"])
    assert body["ok"] is True
    paths = {m["citation_path"] for m in body["result"]["matches"]}
    assert paths == {"4.3.1"}


def test_endpoint_returns_a_tool_error_for_an_empty_filter(client) -> None:
    """The rejection must arrive as a readable error at HTTP 200.

    A 500 here would mean the chat surface leaks an unhandled exception
    instead of handing the model something it can correct.
    """
    body = _post(client, attribute_tag_filter=[])
    assert body["ok"] is False
    assert "attribute_tag_filter must be non-empty" in body["error"]


def test_endpoint_rejects_a_body_without_a_query(client) -> None:
    response = client.post(
        "/v1/_test/advisor-search-attribute-tag-filter",
        json={"attribute_tag_filter": ["building_height_m"]},
    )
    assert response.status_code == 422
