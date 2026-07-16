"""Schema unit tests for ABS-270 structured query support.

AC-1A.1: New StructuredCitationQuery Pydantic discriminated union in schemas.py;
         imports clean; pytest passes.
AC-1A.6: Submitting both citation_path AND structured raises ValidationError.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from bylaw_retrieval.retrieval.schemas import (
    ATTRIBUTE_VOCABULARY,
    CitationLookupRequest,
    ScheduleRowQuery,
    StructuredCitationQuery,
    ZoneAttributeQuery,
)


# ---------------------------------------------------------------------------
# AC-1A.1: schema imports and basic model construction
# ---------------------------------------------------------------------------


def test_attribute_vocabulary_contains_required_values():
    required = {
        "max_height",
        "max_height_storeys",
        "max_lot_coverage",
        "min_front_setback",
        "min_side_setback",
        "min_rear_setback",
        "max_far",
        "permitted_uses",
        "parking_requirement",
    }
    assert required <= ATTRIBUTE_VOCABULARY


def test_zone_attribute_query_constructs():
    q = ZoneAttributeQuery(zone="HR-2", attribute="max_height")
    assert q.kind == "zone_attribute"
    assert q.zone == "HR-2"
    assert q.attribute == "max_height"


def test_schedule_row_query_constructs():
    q = ScheduleRowQuery(schedule="Table 1A", row="HR-2")
    assert q.kind == "schedule_row"
    assert q.schedule == "Table 1A"
    assert q.row == "HR-2"


def test_structured_citation_query_discriminates_zone_attribute():
    from pydantic import TypeAdapter

    ta = TypeAdapter(StructuredCitationQuery)
    q = ta.validate_python({"kind": "zone_attribute", "zone": "HR-2", "attribute": "max_height"})
    assert isinstance(q, ZoneAttributeQuery)


def test_structured_citation_query_discriminates_schedule_row():
    from pydantic import TypeAdapter

    ta = TypeAdapter(StructuredCitationQuery)
    q = ta.validate_python({"kind": "schedule_row", "schedule": "Table 1A", "row": "HR-2"})
    assert isinstance(q, ScheduleRowQuery)


def test_citation_lookup_request_path_variant():
    req = CitationLookupRequest(citation_path="Part V > Section 95")
    assert req.citation_path == "Part V > Section 95"
    assert req.structured is None


def test_citation_lookup_request_structured_variant_zone_attribute():
    req = CitationLookupRequest(
        structured={"kind": "zone_attribute", "zone": "HR-2", "attribute": "max_height"}
    )
    assert req.citation_path is None
    assert isinstance(req.structured, ZoneAttributeQuery)
    assert req.structured.zone == "HR-2"


def test_citation_lookup_request_structured_variant_schedule_row():
    req = CitationLookupRequest(
        structured={"kind": "schedule_row", "schedule": "Table 1A", "row": "HR-2"}
    )
    assert req.citation_path is None
    assert isinstance(req.structured, ScheduleRowQuery)


# ---------------------------------------------------------------------------
# AC-1A.6: mutual exclusion enforced at the Pydantic layer
# ---------------------------------------------------------------------------


def test_lookup_citation_request_rejects_both_path_and_structured():
    """Submitting both citation_path and structured raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        CitationLookupRequest(
            citation_path="HR-2 > max_height",
            structured=ZoneAttributeQuery(zone="HR-2", attribute="max_height"),
        )
    assert "not both" in str(exc_info.value).lower() or "both" in str(exc_info.value).lower()


def test_lookup_citation_request_rejects_neither_path_nor_structured():
    """Supplying neither raises ValidationError."""
    with pytest.raises(ValidationError):
        CitationLookupRequest(document_id=1)
