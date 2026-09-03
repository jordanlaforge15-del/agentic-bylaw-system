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
from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalResponse, RetrievalService
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


def test_build_bylaw_tools_returns_full_tool_set(seeded_service):
    service, _ = seeded_service
    tool_defs, handlers = build_bylaw_tools(service)
    names = [t.name for t in tool_defs]
    # Order matters less than the exact set: callers can rely on
    # this set being complete because mismatched name <-> handler
    # pairs would silently break tool dispatch. ``bylaw_query`` is the
    # Phase 4 intent-routed mega-tool (ABS-274) that composes over the
    # thick tools. ``request_tier_upgrade`` was UNREGISTERED in ABS-383:
    # the beta pivot bills the account token wallet, not per-case tier
    # credits, so the agent is no longer offered a tool to prompt a
    # tier upgrade.
    assert set(names) == {
        "list_documents",
        "get_document_outline",
        "lookup_citation",
        "search_bylaw_evidence",
        "get_address_profile",
        "get_adjacent_zoning",
        "get_zone_profile",
        "evaluate_submission_against_bylaws",
        "bylaw_query",
    }
    assert "request_tier_upgrade" not in set(names)
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
    # ABS-261: handler now returns a match-or-suggestions envelope.
    # On a hit, ``match`` is populated and the canonical citation_path
    # lives under it.
    assert parsed["match"]["citation_path"] == cited["citation_path"]
    assert "text" in parsed["match"]


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
async def test_get_address_profile_handler_returns_json(seeded_service):
    """The get_address_profile handler must round-trip through the service
    and emit the compact projection. The seed corpus has no spatial datasets
    or geocode cache, so a free-text address resolves to the graceful
    unresolvable shape (never an exception) with the fall-back instruction.
    """
    service, _ = seeded_service
    _, handlers = build_bylaw_tools(service)
    raw = await handlers["get_address_profile"]({"address": "100 Robie Street"})
    parsed = json.loads(raw)
    assert parsed["address"]
    assert parsed["unresolvable"] is True
    assert "instruction" in parsed


@pytest.mark.asyncio
async def test_get_adjacent_zoning_handler_returns_json(seeded_service):
    """The get_adjacent_zoning handler (ABS-375) round-trips through the
    service and emits the compact projection. The seed corpus has no
    geocode cache, so a free-text address resolves to the graceful
    unresolvable shape (never an exception) with the fall-back instruction.
    """
    service, _ = seeded_service
    _, handlers = build_bylaw_tools(service)
    raw = await handlers["get_adjacent_zoning"]({"address": "1250 Robie Street"})
    parsed = json.loads(raw)
    assert parsed["address"]
    assert parsed["unresolvable"] is True
    assert "instruction" in parsed


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
    """If the wrapped service call raises mid-handler, the cm must still
    exit so the SQLAlchemy session rolls back rather than leaking.

    ABS-261 changed the contract: ``lookup_citation`` no longer raises on
    path-not-found (it returns ``match=None`` + suggestions). The
    exception path inside the cm now belongs to:

    * search/lookup with an exception raised by the underlying ORM
      (e.g. transient DB failure), or
    * the still-raising ambiguous-across-documents case in
      ``lookup_citation``.

    We simulate that by yielding a stub service whose ``lookup_citation``
    method raises — that puts the failure squarely inside the cm scope
    (after ``model_validate`` succeeds, after ``__enter__`` returns)
    which is where we need to prove ``__exit__`` still runs.
    """
    exits = {"n": 0}

    class RaisingService:
        def lookup_citation(self, request):  # noqa: ARG002
            raise RuntimeError("simulated DB blip inside cm scope")

    class TrackingCm:
        def __enter__(self):
            return RaisingService()

        def __exit__(self, exc_type, exc, tb):
            exits["n"] += 1
            return False

    def factory():
        return TrackingCm()

    _, handlers = build_bylaw_tools(factory)
    with pytest.raises(RuntimeError, match="simulated DB blip"):
        await handlers["lookup_citation"](
            {"citation_path": "Part I > 9", "document_id": 1}
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
    # ABS-261: handler now returns an envelope { match: {...} } on hit.
    # The compact noise-stripping rules still apply to the wrapped match.
    match = parsed["match"]
    assert match["citation_path"] == cited["citation_path"]
    assert "text" in match
    assert "fragment_type" not in match
    assert "metadata_json" not in match
    assert "parse_status" not in match


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
    from bylaw_retrieval.retrieval import RetrievalResponse

    response = RetrievalResponse(
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


def test_coerce_stringified_object_arg():
    """ABS-280: a nested object arg serialized as a JSON string is parsed back
    to a dict; everything else passes through untouched so normal validation
    still runs."""
    from advisor.chat.tools import _coerce_stringified_object_arg

    inner = {"kind": "permitted_use", "use": "home occupation use", "zone": "HR-2"}
    # stringified nested object -> parsed dict
    assert _coerce_stringified_object_arg(
        {"structured": json.dumps(inner), "document_id": 4}, "structured"
    ) == {"structured": inner, "document_id": 4}
    # already a dict -> unchanged
    assert _coerce_stringified_object_arg(
        {"structured": inner}, "structured"
    ) == {"structured": inner}
    # key absent -> unchanged
    assert _coerce_stringified_object_arg(
        {"citation_path": "4.2"}, "structured"
    ) == {"citation_path": "4.2"}
    # non-JSON string -> unchanged (let normal validation reject it)
    assert _coerce_stringified_object_arg(
        {"structured": "not json"}, "structured"
    ) == {"structured": "not json"}
    # JSON that isn't an object -> unchanged
    assert _coerce_stringified_object_arg(
        {"structured": "[1, 2]"}, "structured"
    ) == {"structured": "[1, 2]"}


def test_compact_permitted_use_conditional_carries_instruction():
    """ABS-280 AC2: a conditional permitted_use result must carry an inline
    instruction telling the writer to quote the footnote conditions, so the
    Table 1A carve-out isn't dropped in favour of the use's operating standards.
    """
    from advisor.chat.compact import compact_permitted_use
    from bylaw_retrieval.retrieval.schemas import FootnoteCondition, PermittedUseResult

    conditional = PermittedUseResult(
        use="home occupation use",
        zone="HR-2",
        indeterminate=False,
        permission="conditional",
        footnotes=[
            FootnoteCondition(
                ordinal=15,
                text="⑮ Use is permitted, except within the Halifax Grain Elevator.",
            )
        ],
    )
    out = compact_permitted_use(conditional)
    assert out["permission"] == "conditional"
    assert out["conditions"][0]["text"].startswith("⑮")
    assert "instruction" in out
    assert "conditions" in out["instruction"]
    assert "15" in out["instruction"]

    # A plain permitted result carries NO such instruction.
    permitted = PermittedUseResult(
        use="home occupation use",
        zone="DD",
        indeterminate=False,
        permission="permitted",
    )
    assert "instruction" not in compact_permitted_use(permitted)


def test_compact_permitted_use_unreadable_cell_carries_a_gap_instruction():
    """ABS-484: an unreadable cell is UNKNOWN — the projection must tell the
    writer to say 'not determinable' and must hand it nothing citable, or the
    absent permission gets relayed as a prohibition with the table beside it.
    """
    from advisor.chat.compact import compact_permitted_use
    from bylaw_retrieval.retrieval.schemas import CitationRef, PermittedUseResult

    unreadable = PermittedUseResult(
        use="Multi-unit dwelling use",
        zone="COR",
        indeterminate=True,
        reason_code="unreadable_cell",
        reason="… This is an extraction gap — it does NOT mean the use is prohibited.",
        citation=CitationRef(
            citation_path=None,
            citation_label="Table 1A: Permitted uses by zone",
            page_start=45,
            page_end=45,
            backs=["permitted_use"],
        ),
    )
    out = compact_permitted_use(unreadable)
    assert out["indeterminate"] is True
    assert "permission" not in out
    assert "citation" not in out
    instruction = out["instruction"]
    assert "not determinable" in instruction.lower() or "cannot be determined" in instruction
    assert "prohibited" in instruction


@pytest.mark.asyncio
async def test_lookup_citation_handler_tolerates_stringified_structured(
    seeded_service,
):
    """ABS-280: Opus serialized the nested ``structured`` permitted_use arg as a
    JSON string, which made CitationLookupRequest.model_validate raise and
    stranded the structured permitted-use path (the model thrashed to the
    iteration cap and fell back to ungrounded prose). The handler must parse a
    stringified ``structured`` so the string form behaves like the dict form.
    """
    service, document_id = seeded_service
    _, handlers = build_bylaw_tools(service)

    structured = {
        "kind": "permitted_use",
        "use": "home occupation use",
        "zone": "HR-2",
    }
    dict_form = json.loads(
        await handlers["lookup_citation"](
            {"structured": structured, "document_id": document_id}
        )
    )
    # The stringified form must NOT raise and must produce identical output.
    string_form = json.loads(
        await handlers["lookup_citation"](
            {"structured": json.dumps(structured), "document_id": document_id}
        )
    )
    assert string_form == dict_form


# ---------------------------------------------------------------------------
# ABS-297: WI-7 / WI-3 surface-asymmetry drift guard
#
# ABS-288 (WI-7) flipped the underlying ``RetrievalRequest.include_*`` defaults
# to ``False`` at the MCP / external surface. The advisor production path
# (``src/advisor/chat/tools.py:774-777``) deliberately keeps the LLM-visible
# behaviour True-by-default via an explicit ``payload.get("include_*", True)``
# fallback — this is what keeps WI-3 fan-out leads (``cross_references``,
# ``ancestor_chain``, ``linked_datasets``) flowing to the model whenever it
# omits the include_* flags (which the persona never tells it to set).
#
# The fragility this guards against: if a future refactor drops the explicit
# ``payload.get(..., True)`` fallback and just passes ``payload.get("include_*")``
# (or unpacks the payload directly into ``RetrievalRequest(**payload)``), the
# handler will inherit the post-WI-7 ``False`` defaults and the model silently
# stops seeing fan-out leads. No exception, no warning, just a quiet cost
# regression and a quiet quality regression as the LLM thrashes to find
# cross-references that aren't in the tool_result anymore.
#
# These tests pin the contract at the handler boundary so any such refactor
# fails loudly here rather than silently in production. The corresponding
# Playwright spec ``abs297-advisor-default-include-drift-guard.spec.ts`` pins
# the same contract through the running FastAPI stack.
# ---------------------------------------------------------------------------


class _CapturingRetrievalService:
    """Stub service that records every ``RetrievalRequest`` it receives.

    Returning an empty ``RetrievalResponse`` is enough — the drift guard
    asserts on the request's ``include_*`` flags, not on what came back.
    Bypassing the real retrieval pipeline keeps this test pinned to the
    handler's request-construction behaviour and independent of seed data.
    """

    def __init__(self) -> None:
        self.last_request: RetrievalRequest | None = None

    def search(self, request: RetrievalRequest) -> RetrievalResponse:
        self.last_request = request
        return RetrievalResponse(total_matches=0, matches=[], notes=[])


@pytest.mark.asyncio
async def test_search_bylaw_evidence_handler_defaults_include_flags_true_drift_guard():
    """ABS-297: a default advisor-path payload (no ``include_*`` keys) must
    produce a ``RetrievalRequest`` with all four ``include_*`` flags set to
    ``True`` — the explicit fallback in the handler must override the
    post-WI-7 ``False`` default on ``RetrievalRequest``.

    If this test starts failing, the handler has drifted: WI-3 fan-out
    leads will stop reaching the model in production. Look at
    ``src/advisor/chat/tools.py`` ``search_bylaw_evidence_handler`` —
    each ``payload.get("include_*", True)`` is load-bearing.
    """
    capturing = _CapturingRetrievalService()
    _, handlers = build_bylaw_tools(capturing)

    await handlers["search_bylaw_evidence"]({"query": "any question"})

    req = capturing.last_request
    assert req is not None, "handler did not call service.search"
    assert req.include_context is True, (
        "ABS-297 drift: include_context fell through to RetrievalRequest's "
        "post-WI-7 False default. Restore payload.get('include_context', True)."
    )
    assert req.include_cross_references is True, (
        "ABS-297 drift: include_cross_references fell through to "
        "RetrievalRequest's post-WI-7 False default. WI-3 fan-out leads will "
        "stop reaching the model. Restore payload.get('include_cross_references', True)."
    )
    assert req.include_tables is True, (
        "ABS-297 drift: include_tables fell through to RetrievalRequest's "
        "post-WI-7 False default. Restore payload.get('include_tables', True)."
    )
    assert req.include_datasets is True, (
        "ABS-297 drift: include_datasets fell through to RetrievalRequest's "
        "post-WI-7 False default. WI-3 linked-dataset fan-out will silently "
        "die. Restore payload.get('include_datasets', True)."
    )


@pytest.mark.asyncio
async def test_search_bylaw_evidence_handler_respects_explicit_include_false():
    """ABS-297 paired check: the True-by-default must NOT mask an explicit
    ``False`` from the model. If a future implementation switches to e.g.
    ``payload.get("include_*", True) or True`` to "be safe", a model that
    deliberately opts out (say, for a narrow follow-up to a fan-out parent
    turn) would stop being able to. Pin that contract too.
    """
    capturing = _CapturingRetrievalService()
    _, handlers = build_bylaw_tools(capturing)

    await handlers["search_bylaw_evidence"](
        {
            "query": "any question",
            "include_context": False,
            "include_cross_references": False,
            "include_tables": False,
            "include_datasets": False,
        }
    )

    req = capturing.last_request
    assert req is not None
    assert req.include_context is False
    assert req.include_cross_references is False
    assert req.include_tables is False
    assert req.include_datasets is False


def test_lookup_citation_description_says_what_a_section_returns():
    """ABS-521 AC: ``lookup_citation`` on a section must return its operative
    clauses, or document that it does not and say what to call instead.

    It returns them, so the description has to say so — a payload the model is
    not told to read is a payload it will skip past. The transcript that opened
    ABS-521 shows exactly that failure mode one level up: the model called
    ``lookup_citation {"citation_path": "Part V > 333"}``, got a sentence ending
    "…except:", and answered from it without ever asking what came after the
    colon.
    """
    from advisor.chat.tools import _DESC_LOOKUP_CITATION, _DESC_SEARCH_BYLAW_EVIDENCE

    for description, where in (
        (_DESC_LOOKUP_CITATION, "lookup_citation"),
        (_DESC_SEARCH_BYLAW_EVIDENCE, "search_bylaw_evidence"),
    ):
        assert "operative_clauses" in description, (
            f"{where} no longer tells the model the clauses are there"
        )
        assert "operative_clauses_omitted" in description, (
            f"{where} no longer tells the model what to do when the provision "
            "was truncated"
        )
        assert "citation_path_prefix" in description, (
            f"{where} no longer names the call that reads the rest of a "
            "truncated provision"
        )


def test_operative_clauses_survive_the_compact_projection():
    """The clauses have to reach the model, not just the Pydantic model.

    ``compact_match`` is the only projection the chat tool loop ships, and it
    drops most of what ``RetrievalMatch`` carries. A clause added to the schema
    but not to the projection is a fix that passes its own unit tests and
    changes nothing about the answer the user gets.
    """
    from advisor.chat.compact import compact_match
    from bylaw_retrieval.retrieval.schemas import OperativeClause, RetrievalMatch

    match = RetrievalMatch(
        fragment_id=1,
        document_id=1,
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        fragment_type="section",
        citation_path="Part V > 333",
        page_start=238,
        page_end=238,
        parse_status="parsed",
        text="333 (1) Any new accessory structure shall have no restriction "
        "on the maximum size of its footprint, except:",
        score=1.0,
        operative_clauses=[
            OperativeClause(
                id=2,
                fragment_type="clause",
                citation_label="(a)",
                citation_path="Part V > 333 > (a)",
                page_start=238,
                page_end=238,
                text="(a) … in any DD, DH, CEN-2, CEN1, COR, HR-2, HR-1, "
                "ER-3, ER-2, ER-1, CH-2, or CH-1 zone: 60.0 square metres; or",
            )
        ],
        operative_clauses_omitted=2,
    )

    out = compact_match(match)
    assert out["operative_clauses"] == [
        {
            "fragment_id": 2,
            "citation_path": "Part V > 333 > (a)",
            "citation_label": "(a)",
            "text": match.operative_clauses[0].text,
        }
    ]
    assert "60.0 square metres" in out["operative_clauses"][0]["text"]
    assert out["operative_clauses_omitted"] == 2
    assert "citation_path_prefix" in out["operative_clauses_note"]


def test_a_match_without_clauses_carries_no_empty_keys():
    """Byte stability: the common case must not grow.

    Most fragments state their rule whole, and the module docstring's whole
    argument is that a tool_result is replayed on every subsequent turn. An
    always-present ``"operative_clauses": []`` would bill four bytes per match
    per turn for saying nothing.
    """
    from advisor.chat.compact import compact_match
    from bylaw_retrieval.retrieval.schemas import RetrievalMatch

    match = RetrievalMatch(
        fragment_id=1,
        document_id=1,
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        fragment_type="section",
        citation_path="Part V > 332",
        page_start=238,
        page_end=238,
        parse_status="parsed",
        text="332 One accessory structure per lot …",
        score=1.0,
    )

    out = compact_match(match)
    assert "operative_clauses" not in out
    assert "operative_clauses_omitted" not in out
    assert "operative_clauses_note" not in out
