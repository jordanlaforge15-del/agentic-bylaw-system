"""Integration tests for MCP tool schemas (AC-1A.7 + AC-2.8).

Covers two tool surfaces:

* ``lookup_citation`` (ABS-270): the ``CitationLookupRequest`` Pydantic
  model produces a valid JSON Schema that exposes both the
  ``citation_path`` and ``structured`` request shapes, with the expected
  discriminator values (zone_attribute, schedule_row).
* ``get_zone_profile`` (ABS-272): the advisor-side ``ToolDefinition``
  (which mirrors the MCP server's tool signature verbatim) exposes a
  well-formed zone/include JSON Schema; the ``ZoneProfile`` model emits a
  clean schema; the registered async handler runs end-to-end against a
  seeded DB; and the FastMCP server registers the tool when the MCP SDK
  is installed.

The Pydantic models are the authoritative source of truth for the
schemas consumed by the advisor's handlers, so validating them here is
equivalent to validating the MCP tool schemas.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import jsonschema
import pytest

from advisor.chat.tools import build_bylaw_tools
from bylaw_retrieval.retrieval import RetrievalService, ZoneProfile
from bylaw_retrieval.retrieval.schemas import CitationLookupRequest
from layer1.db.session import session_scope
from tests.test_get_zone_profile import _seed_regional_centre


# ---------------------------------------------------------------------------
# lookup_citation — structured query schema (ABS-270, AC-1A.7)
# ---------------------------------------------------------------------------


def test_lookup_citation_schema_is_valid_json_schema():
    """The Pydantic model's JSON Schema must pass JSON Schema Draft-7 validation."""
    schema = CitationLookupRequest.model_json_schema()
    # check_schema raises jsonschema.SchemaError if the schema is malformed.
    jsonschema.Draft7Validator.check_schema(schema)


def test_lookup_citation_schema_contains_citation_path_shape():
    """The path-string variant must appear in the generated schema."""
    schema = CitationLookupRequest.model_json_schema()
    schema_str = json.dumps(schema)
    assert "citation_path" in schema_str, (
        "Expected 'citation_path' in the schema but it was absent"
    )


def test_lookup_citation_schema_contains_structured_shape():
    """The structured-query variant must appear in the generated schema."""
    schema = CitationLookupRequest.model_json_schema()
    schema_str = json.dumps(schema)
    assert "structured" in schema_str, (
        "Expected 'structured' in the schema but it was absent"
    )


def test_lookup_citation_schema_contains_discriminator_values():
    """Both discriminator values (zone_attribute, schedule_row) must appear."""
    schema = CitationLookupRequest.model_json_schema()
    schema_str = json.dumps(schema)
    assert "zone_attribute" in schema_str, (
        "Expected 'zone_attribute' discriminator in the schema"
    )
    assert "schedule_row" in schema_str, (
        "Expected 'schedule_row' discriminator in the schema"
    )


def test_lookup_citation_schema_path_variant_validates():
    """A citation_path-only payload validates against the generated schema."""
    schema = CitationLookupRequest.model_json_schema()
    payload = {"citation_path": "Part V > Section 95"}
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    assert not errors, f"Unexpected validation errors for path variant: {errors}"


def test_lookup_citation_schema_structured_variant_validates():
    """A structured-query payload validates against the generated schema."""
    schema = CitationLookupRequest.model_json_schema()
    payload = {
        "structured": {"kind": "zone_attribute", "zone": "HR-2", "attribute": "max_height"}
    }
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    assert not errors, f"Unexpected validation errors for structured variant: {errors}"


# ---------------------------------------------------------------------------
# get_zone_profile — thick-tool schema + callability (ABS-272, AC-2.8)
# ---------------------------------------------------------------------------


def _zone_profile_tool_def():
    """Return the get_zone_profile ToolDefinition from the advisor build."""
    # build_bylaw_tools needs *a* service to bind handlers; the tool
    # definitions themselves are service-independent.
    defs, _handlers = build_bylaw_tools(lambda: None)
    by_name = {d.name: d for d in defs}
    assert "get_zone_profile" in by_name, sorted(by_name)
    return by_name["get_zone_profile"]


def test_get_zone_profile_tool_schema_is_wellformed():
    """The tool's input schema validates as a JSON-Schema object with the
    expected zone/include shape (AC-2.8, schema-validates half).
    """
    tool = _zone_profile_tool_def()
    schema = tool.input_schema

    assert schema["type"] == "object"
    assert schema.get("additionalProperties") is False
    assert schema["required"] == ["zone"]

    props = schema["properties"]
    assert props["zone"]["type"] == "string"

    include = props["include"]
    assert include["type"] == "array"
    assert include["items"]["type"] == "string"
    # include items are constrained to the known section names.
    assert set(include["items"]["enum"]) == {
        "dimensions",
        "uses",
        "parking",
        "citations",
    }

    # The description must steer the model toward the one-call usage and
    # name lookup_citation for drill-down (FR-2.6).
    assert "lookup_citation" in tool.description
    assert "one call" in tool.description.lower()


def test_zone_profile_pydantic_schema_emits_cleanly():
    """ZoneProfile.model_json_schema() must build without error and carry
    the documented top-level fields (AC-2.1 / AC-2.8).
    """
    schema = ZoneProfile.model_json_schema()
    props = schema["properties"]
    for field in (
        "zone",
        "zone_full_name",
        "chapter",
        "dimensions",
        "uses",
        "parking",
        "citations",
        "unknown_zone",
        "confidence",
    ):
        assert field in props, f"ZoneProfile schema missing '{field}'"


def test_get_zone_profile_tool_is_callable_end_to_end(tmp_path: Path):
    """The registered handler runs against a seeded DB and returns a
    structurally valid ZoneProfile projection (AC-2.8, callable half).
    """
    db_url = f"sqlite:///{tmp_path / 'mcp_schema.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        _defs, handlers = build_bylaw_tools(service)
        raw = asyncio.new_event_loop().run_until_complete(
            handlers["get_zone_profile"]({"zone": "HR-2"})
        )

    payload = json.loads(raw)
    assert payload["zone"] == "HR-2"
    assert payload["dimensions"]["max_height_m"] == 25.0
    assert "multi-unit dwelling" in payload["uses"]["permitted"]
    assert payload["citations"], "expected non-empty citations"


def test_fastmcp_server_registers_get_zone_profile():
    """When the MCP SDK is installed, the FastMCP server exposes
    get_zone_profile with a zone/include input schema (AC-2.8).
    """
    pytest.importorskip("mcp.server.fastmcp")
    from bylaw_retrieval.server import create_mcp_server

    server = create_mcp_server()
    tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
    by_name = {t.name: t for t in tools}
    assert "get_zone_profile" in by_name, sorted(by_name)

    schema = by_name["get_zone_profile"].inputSchema
    assert "zone" in schema["properties"]
    assert "include" in schema["properties"]
