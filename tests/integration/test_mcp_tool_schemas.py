"""Integration tests for MCP tool schemas.  AC-1A.7.

Validates that:
- The CitationLookupRequest Pydantic model produces a valid JSON Schema.
- Both request shapes (citation_path and structured) appear in that schema.
- The structured query's sub-types (zone_attribute, schedule_row) are
  represented with the expected discriminator values.

The Pydantic model is the authoritative source of truth for the schema
consumed by the advisor's lookup_citation handler, so validating it here
is equivalent to validating the MCP tool schema.
"""
from __future__ import annotations

import json

import jsonschema
import pytest

from bylaw_retrieval.retrieval.schemas import CitationLookupRequest


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
