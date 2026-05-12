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
    confirm it accepts a query, returns the compact JSON shape, and
    the JSON parses back to a structure with the LLM-essential
    top-level keys. The ``request`` echo is intentionally absent —
    every byte of tool_result content gets replayed on every
    subsequent turn, so the compact shape drops the request echo to
    save tokens.
    """
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)
    output = await handlers["search_bylaw_evidence"](
        {"query": "residential zones", "document_id": document_id, "limit": 3}
    )
    parsed = json.loads(output)
    assert "matches" in parsed
    assert "total_matches" in parsed
    assert "shown_matches" in parsed
    assert "request" not in parsed


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
    bubbles up as a ValidationError. The compact response no longer
    echoes the request payload, so we confirm the call did not raise
    and that the response shape is intact — the LocationSlot parse
    happens inside the handler before the service is touched, so a
    successful response is the signal that parsing worked.
    """
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
    assert "matches" in parsed
    assert "total_matches" in parsed


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


@pytest.mark.asyncio
async def test_search_bylaw_evidence_response_drops_noise_fields(seeded_service):
    """Compact mode strips internal/verbose fields from every match.

    These fields are not needed to produce a citation-grounded answer
    and replaying them on every tool turn wastes input tokens.
    """
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)
    raw = await handlers["search_bylaw_evidence"](
        {"query": "residential zones", "document_id": document_id, "limit": 3}
    )
    parsed = json.loads(raw)
    assert parsed["matches"], "fixture should return at least one match"
    top = parsed["matches"][0]
    # Citation + content fields the LLM does use:
    assert "text" in top
    assert "page_start" in top
    assert "municipality" in top
    # Noise fields the LLM does not use and that should be dropped:
    for noisy in (
        "fragment_type",
        "parse_status",
        "confidence",
        "metadata_json",
    ):
        assert noisy not in top, f"compact match should not carry {noisy}"


@pytest.mark.asyncio
async def test_search_bylaw_evidence_paginates_beyond_cap(
    monkeypatch, seeded_service
):
    """When the underlying search returns more matches than the
    compact-mode cap, the handler ships the top K and notes how many
    were dropped — the LLM doesn't need to wade through long tails.
    """
    monkeypatch.setenv("ADVISOR_COMPACT_MAX_MATCHES", "2")
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)
    raw = await handlers["search_bylaw_evidence"](
        {"query": "zone", "document_id": document_id, "limit": 25}
    )
    parsed = json.loads(raw)
    if parsed["total_matches"] > 2:
        assert parsed["shown_matches"] == 2
        assert len(parsed["matches"]) == 2
        assert "truncation_note" in parsed
        assert str(parsed["total_matches"] - 2) in parsed["truncation_note"]
    else:
        # Fixture is too small to exercise truncation; still confirm
        # the shape stays consistent.
        assert parsed["shown_matches"] == parsed["total_matches"]
        assert "truncation_note" not in parsed


@pytest.mark.asyncio
async def test_lookup_citation_returns_compact_match(seeded_service):
    """``lookup_citation``'s output is a single match. The compact
    shape drops the same noise fields the search shape drops.
    """
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)
    outline_raw = await handlers["get_document_outline"]({"document_id": document_id})
    outline = json.loads(outline_raw)
    cited = next(item for item in outline["fragments"] if item.get("citation_path"))

    raw = await handlers["lookup_citation"](
        {"citation_path": cited["citation_path"], "document_id": document_id}
    )
    parsed = json.loads(raw)
    assert parsed["citation_path"] == cited["citation_path"]
    assert "text" in parsed
    assert "fragment_type" not in parsed
    assert "metadata_json" not in parsed
    assert "parse_status" not in parsed


@pytest.mark.asyncio
async def test_get_document_outline_compact_shape(seeded_service):
    """Outline drops fragment_id + fragment_type per fragment — the
    LLM looks up by citation_path, not by internal fragment_id.
    """
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)
    raw = await handlers["get_document_outline"](
        {"document_id": document_id, "max_fragments": 5}
    )
    parsed = json.loads(raw)
    assert parsed["document"]["id"] == document_id
    # Compact document summary drops parser_version + ingestion_timestamp:
    assert "parser_version" not in parsed["document"]
    assert "ingestion_timestamp" not in parsed["document"]
    for item in parsed["fragments"]:
        assert "fragment_id" not in item
        assert "fragment_type" not in item


def test_compact_linked_dataset_keeps_canonical_values():
    """Whitebox: the LLM needs the canonical attribute values from a
    spatial match (e.g. {"max_height_m": 25.0}) and the geocoder
    confidence — but not the verbose dataset summary_text,
    feature_count, crs, or internal feature_id / feature_key /
    overlap_metric. This test pins the projection so future edits
    can't silently re-add noise.
    """
    from advisor.chat.compact import compact_linked_dataset
    from bylaw_retrieval.retrieval.schemas import (
        DatasetFeatureMatch,
        LinkedDataset,
    )

    ds = LinkedDataset(
        dataset_id=42,
        name="halifax_height_precincts",
        publisher="HRM",
        feature_count=137,
        crs="EPSG:4326",
        summary_text="A long verbose summary the LLM does not need. " * 8,
        source_image_id=99,
        location_resolver="google_maps",
        location_confidence=0.95,
        feature_matches=[
            DatasetFeatureMatch(
                feature_id=7,
                feature_key="GlobalID-abc",
                canonical_attributes={"max_height_m": 25.0},
                contains_input=True,
                overlap_metric=0.42,
            )
        ],
    )
    out = compact_linked_dataset(ds)
    assert out == {
        "dataset_id": 42,
        "name": "halifax_height_precincts",
        "location_resolver": "google_maps",
        "location_confidence": 0.95,
        "feature_matches": [
            {
                "canonical_attributes": {"max_height_m": 25.0},
                "contains_input": True,
            }
        ],
    }


def test_compact_search_response_keeps_notes_and_drops_request():
    """Whitebox: server-side notes on RetrievalResponse remain visible
    in compact mode (the LLM is supposed to read them and re-issue),
    but the ``request`` echo is dropped — the LLM already knows what
    it sent and that field is pure cache bloat.
    """
    from advisor.chat.compact import compact_search_response
    from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalResponse

    req = RetrievalRequest(query="anything", limit=5)
    response = RetrievalResponse(
        request=req,
        total_matches=0,
        matches=[],
        notes=["The query contains a civic address but no location field"],
    )
    out = compact_search_response(response)
    assert "request" not in out
    assert out["notes"] == [
        "The query contains a civic address but no location field"
    ]
    assert out["total_matches"] == 0
    assert out["shown_matches"] == 0
    assert "truncation_note" not in out
