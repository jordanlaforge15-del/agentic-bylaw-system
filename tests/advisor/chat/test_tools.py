"""Bylaw retrieval tool definitions and handlers.

Each handler must:
- Translate the LLM's input dict into the right pydantic request model.
- Call the appropriate ``RetrievalService`` method.
- Return a JSON string so the LLM gets structured data back.

We verify against a real ``RetrievalService`` bound to a sqlite
in-memory db rather than mocking, so any drift in the request models
fails here loudly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from advisor.chat.tools import build_bylaw_tools
from bylaw_retrieval.retrieval import RetrievalService
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.pipeline.ingest import ingest_file


@pytest.fixture()
def seeded_service(tmp_path: Path):
    """A RetrievalService bound to a fresh sqlite DB with one
    synthetic bylaw ingested. Tests use this directly rather than
    mocking the service so the JSON-Schema -> request-model
    translation is exercised end-to-end.
    """
    db_url = f"sqlite:///{tmp_path / 'tools.db'}"
    create_all(db_url)
    fixture_path = Path("tests/fixtures/synthetic_bylaw.txt")
    with session_scope(db_url) as session:
        document, _ = ingest_file(
            session,
            fixture_path,
            municipality="Sampleton",
            bylaw_name="Synthetic Zoning Bylaw",
        )
        document_id = document.id

    # Open a fresh, long-lived session for the tests to use. We
    # don't wrap the assertions in session_scope because the test
    # assertions are read-only against the materialised JSON output.
    session_cm = session_scope(db_url)
    session = session_cm.__enter__()
    service = RetrievalService(session)

    yield service, document_id

    session_cm.__exit__(None, None, None)


def test_build_bylaw_tools_returns_four_tools(seeded_service):
    service, _ = seeded_service
    tool_defs, handlers = build_bylaw_tools(service)
    names = [t.name for t in tool_defs]
    # Order matters less than the exact set: callers can rely on
    # this set being complete because mismatched name <-> handler
    # pairs would silently break tool dispatch.
    assert set(names) == {
        "list_documents",
        "get_document_outline",
        "lookup_citation",
        "search_bylaw_evidence",
    }
    assert set(handlers.keys()) == set(names)


@pytest.mark.asyncio
async def test_search_bylaw_evidence_handler_returns_json(seeded_service):
    """search_bylaw_evidence is the bread-and-butter handler. We
    confirm it accepts a query, returns JSON, and the JSON parses
    back to a structure with the expected top-level keys."""
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)
    output = await handlers["search_bylaw_evidence"](
        {"query": "residential zones", "document_id": document_id, "limit": 3}
    )
    parsed = json.loads(output)
    assert "matches" in parsed
    assert "total_matches" in parsed
    assert "request" in parsed


@pytest.mark.asyncio
async def test_lookup_citation_handler_returns_json(seeded_service):
    """lookup_citation must round-trip through CitationLookupRequest;
    a missing citation_path raises validation error before we hit
    the service."""
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)

    # First, find a real citation path from the document outline.
    outline_raw = await handlers["get_document_outline"]({"document_id": document_id})
    outline = json.loads(outline_raw)
    cited = next(
        item for item in outline["fragments"] if item.get("citation_path")
    )

    raw = await handlers["lookup_citation"](
        {"citation_path": cited["citation_path"], "document_id": document_id}
    )
    parsed = json.loads(raw)
    assert parsed["citation_path"] == cited["citation_path"]
    assert "text" in parsed


@pytest.mark.asyncio
async def test_list_documents_handler_returns_json(seeded_service):
    service, _ = seeded_service
    _, handlers = build_bylaw_tools(service)
    raw = await handlers["list_documents"]({"limit": 5})
    parsed = json.loads(raw)
    assert "documents" in parsed
    assert len(parsed["documents"]) >= 1
    # Verify the seed doc is present:
    municipalities = [doc["municipality"] for doc in parsed["documents"]]
    assert "Sampleton" in municipalities


@pytest.mark.asyncio
async def test_get_document_outline_handler_returns_json(seeded_service):
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)
    raw = await handlers["get_document_outline"](
        {"document_id": document_id, "max_fragments": 50}
    )
    parsed = json.loads(raw)
    assert parsed["document"]["id"] == document_id
    assert isinstance(parsed["fragments"], list)
    assert len(parsed["fragments"]) > 0


@pytest.mark.asyncio
async def test_search_bylaw_evidence_handler_with_location_slot(seeded_service):
    """The location dict must be parsed into a LocationSlot and
    forwarded to RetrievalService.search; a malformed location
    bubbles up as a ValidationError. We confirm the request
    survives serialisation back through the response payload."""
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)
    raw = await handlers["search_bylaw_evidence"](
        {
            "query": "residential zones",
            "document_id": document_id,
            "location": {
                "civic_number": "6321",
                "street": "Quinpool Road",
            },
            "limit": 3,
        }
    )
    parsed = json.loads(raw)
    # The response echoes the request, including the location slot
    # we passed in. That's how we confirm it was carried through
    # rather than dropped silently.
    assert parsed["request"]["location"]["civic_number"] == "6321"
    assert parsed["request"]["location"]["street"] == "Quinpool Road"


@pytest.mark.asyncio
async def test_factory_callable_resolved_per_call(seeded_service):
    """When build_bylaw_tools is given a zero-arg factory, the
    factory is invoked on each handler call — this is what lets
    production open a fresh session_scope per tool use without
    leaking sessions across calls."""
    service, _ = seeded_service
    call_count = {"n": 0}

    def factory():
        call_count["n"] += 1
        return service

    _, handlers = build_bylaw_tools(factory)
    await handlers["list_documents"]({"limit": 5})
    await handlers["list_documents"]({"limit": 5})
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_factory_context_manager_exits_after_each_call(seeded_service):
    """Regression: a context-manager factory must have ``__exit__``
    called after each handler returns, otherwise SQLAlchemy sessions
    leak and the underlying transaction sits 'idle in transaction'
    until a Postgres timeout fires. The earlier implementation called
    ``__enter__`` and threw the cm away — which wedged Postgres
    connections after a few tool calls and required
    ``idle_in_transaction_session_timeout`` to self-heal.
    """
    service, _ = seeded_service
    enters = {"n": 0}
    exits = {"n": 0}

    class TrackingCm:
        def __enter__(self):
            enters["n"] += 1
            return service

        def __exit__(self, exc_type, exc, tb):
            exits["n"] += 1
            return False

    def factory():
        return TrackingCm()

    _, handlers = build_bylaw_tools(factory)
    await handlers["list_documents"]({"limit": 5})
    await handlers["list_documents"]({"limit": 5})

    assert enters["n"] == 2
    assert exits["n"] == 2, (
        "context manager exit was skipped — sessions leak per tool call"
    )


@pytest.mark.asyncio
async def test_factory_context_manager_exits_on_handler_exception(
    seeded_service,
):
    """If a handler raises (e.g. malformed input), the cm must still
    exit so the SQLAlchemy session rolls back rather than leaking.
    """
    service, _ = seeded_service
    exits = {"n": 0}

    class TrackingCm:
        def __enter__(self):
            return service

        def __exit__(self, exc_type, exc, tb):
            exits["n"] += 1
            return False

    def factory():
        return TrackingCm()

    _, handlers = build_bylaw_tools(factory)
    # lookup_citation requires citation_path; omitting it raises
    # ValidationError BEFORE the service is touched. Even so the cm
    # never gets entered, so we exercise the exception path with a
    # request that does enter the service:
    with pytest.raises(Exception):
        await handlers["lookup_citation"](
            {"citation_path": "this-path-does-not-exist", "document_id": 999_999}
        )
    assert exits["n"] == 1


def test_search_bylaw_evidence_schema_has_location_slot():
    """The tool's input_schema must explicitly document the location
    slot — that's what the LLM reads to know how to populate it.
    Without this assertion, a refactor that drops the schema field
    would silently disable the address-aware path."""
    tool_defs, _ = build_bylaw_tools(lambda: None)  # no service needed for schema inspection
    search = next(t for t in tool_defs if t.name == "search_bylaw_evidence")
    location = search.input_schema["properties"]["location"]
    assert "civic_number" in location["properties"]
    assert "parcel_id" in location["properties"]
    assert "geometry" in location["properties"]
