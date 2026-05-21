"""Tests for the evaluate_submission_against_bylaws MCP tool wiring.

Exercises two surfaces:

* The MCP server's tool — confirms the FastMCP registration, the
  schema validation, and the round-trip from raw input dicts to the
  evaluator's JSON response.
* The advisor's in-process tool dispatcher — confirms the new
  handler is registered, the tool definition lists it, and the JSON
  payload returned matches what the agent will see.

Tests run against a sqlite database populated with a small bylaw
corpus seeded by ``_seed_corpus`` (front yard minimum 4.5 m). They
never call out to the LLM — the evaluator's default
``RegexConstraintExtractor`` handles the synthetic clauses.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bylaw_retrieval.retrieval import RetrievalService
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


def _make_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'mcp_tool.db'}"
    create_all(db_url)
    return db_url


def _seed_corpus(session) -> None:
    document = Document(
        municipality="HRM",
        bylaw_name="Test Bylaw",
        source_path="t.pdf",
        file_hash="hh-mcp",
        mime_type="application/pdf",
        page_count=1,
        parser_version="test",
    )
    session.add(document)
    session.flush()
    session.add(
        SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.2.1",
            page_start=1,
            page_end=1,
            text="The minimum front yard shall not be less than 4.5 metres.",
            parse_status=ParseStatus.PARSED,
            attribute_tags=["front_setback_m"],
            confidence=1.0,
        )
    )
    session.add(
        SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.3.1",
            page_start=2,
            page_end=2,
            text="The maximum building height shall not exceed 11 metres.",
            parse_status=ParseStatus.PARSED,
            attribute_tags=["building_height_m"],
            confidence=1.0,
        )
    )


def test_advisor_dispatcher_registers_evaluator_tool(tmp_path: Path) -> None:
    """The advisor's build_bylaw_tools() exposes the new tool + handler."""
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    from advisor.chat.tools import build_bylaw_tools

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        tool_defs, handlers = build_bylaw_tools(service)

    names = {t.name for t in tool_defs}
    assert "evaluate_submission_against_bylaws" in names
    assert "evaluate_submission_against_bylaws" in handlers

    # The schema must require `attributes`; everything else is optional
    # so an agent can omit unrelated fields without tripping
    # additionalProperties.
    spec = next(t for t in tool_defs if t.name == "evaluate_submission_against_bylaws")
    assert spec.input_schema["required"] == ["attributes"]
    assert spec.input_schema["additionalProperties"] is False


def test_advisor_handler_round_trips_to_compliance_matrix(tmp_path: Path) -> None:
    """Calling the registered handler returns a JSON compliance matrix."""
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    from advisor.chat.tools import build_bylaw_tools

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        _tool_defs, handlers = build_bylaw_tools(service)
        handler = handlers["evaluate_submission_against_bylaws"]

        result_str = asyncio.run(
            handler(
                {
                    "attributes": [
                        {
                            "attribute_key": "front_setback_m",
                            "value": 6.0,
                            "unit": "m",
                        },
                        {
                            "attribute_key": "building_height_m",
                            "value": 9.0,
                            "unit": "m",
                        },
                    ],
                }
            )
        )

    payload = json.loads(result_str)
    assert payload["overall_status"] == "compliant"
    keys = {r["attribute_key"] for r in payload["attribute_results"]}
    assert keys == {"front_setback_m", "building_height_m"}
    # Every clause must carry its citation_path so the UI can render
    # a clickable link.
    for result in payload["attribute_results"]:
        for clause in result["applicable_clauses"]:
            assert clause["citation_path"]


def test_advisor_handler_drops_invalid_attribute_entries(tmp_path: Path) -> None:
    """Entries without a valid ``attribute_key`` are dropped silently.

    The schema's ``required: ["attribute_key"]`` already catches bad
    items at validation time; the handler's defensive ``if not isinstance``
    is a belt-and-braces guard against malformed payloads that slip
    through. The point of this test is to confirm we don't crash on
    them.
    """
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    from advisor.chat.tools import build_bylaw_tools

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        _tool_defs, handlers = build_bylaw_tools(service)
        handler = handlers["evaluate_submission_against_bylaws"]
        result_str = asyncio.run(
            handler(
                {
                    "attributes": [
                        {"attribute_key": "front_setback_m", "value": 6.0, "unit": "m"},
                        {"value": 99},  # no attribute_key
                        {"attribute_key": "", "value": 99},
                    ],
                }
            )
        )
    payload = json.loads(result_str)
    keys = {r["attribute_key"] for r in payload["attribute_results"]}
    assert keys == {"front_setback_m"}


def test_advisor_handler_surfaces_failing_verdict(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    from advisor.chat.tools import build_bylaw_tools

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        _tool_defs, handlers = build_bylaw_tools(service)
        handler = handlers["evaluate_submission_against_bylaws"]
        result_str = asyncio.run(
            handler(
                {
                    "attributes": [
                        {
                            "attribute_key": "front_setback_m",
                            "value": 3.0,
                            "unit": "m",
                        },
                    ],
                }
            )
        )
    payload = json.loads(result_str)
    assert payload["overall_status"] == "non_compliant"
    front_result = payload["attribute_results"][0]
    assert front_result["verdict"] == "fail"
    # shortfall must be surfaced so the UI can render "you need X more".
    assert "shortfall" in (front_result["delta"] or {})


def test_advisor_handler_propagates_document_filters(tmp_path: Path) -> None:
    """document_filters narrow the corpus before retrieval."""
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    from advisor.chat.tools import build_bylaw_tools

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        _tool_defs, handlers = build_bylaw_tools(service)
        handler = handlers["evaluate_submission_against_bylaws"]
        # Filter to a municipality that doesn't exist — should produce
        # an "uncertain" verdict (no clauses in scope).
        result_str = asyncio.run(
            handler(
                {
                    "attributes": [
                        {
                            "attribute_key": "front_setback_m",
                            "value": 6.0,
                            "unit": "m",
                        },
                    ],
                    "document_filters": {"municipality": "Nowhereville"},
                }
            )
        )
    payload = json.loads(result_str)
    assert payload["overall_status"] == "uncertain"
    notes = " ".join(payload["attribute_results"][0]["notes"])
    assert "no bylaw clauses tagged" in notes
