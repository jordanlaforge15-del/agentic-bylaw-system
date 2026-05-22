"""Task 2 tests: City Intake Manifest Pydantic v2 models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from manifest.models import (
    CitationLevel,
    CitationScheme,
    CityIntakeManifest,
    Municipality,
    ParserConfig,
    SourceDocument,
)


def _halifax_manifest() -> CityIntakeManifest:
    """Construct the Halifax ground-truth manifest used by T2-A."""
    return CityIntakeManifest(
        municipality=Municipality(
            name="Halifax Regional Municipality",
            jurisdiction_code="HRM",
            province="NS",
            governing_body="Halifax Regional Council",
        ),
        sources=[
            SourceDocument(
                document_name="Regional Centre Land Use By-Law",
                document_type="bylaw",
                document_role="Primary land-use bylaw for the Regional Centre planning area",
                parent_document=None,
                source_url="file:///regionalcentrelub.pdf",
                format="pdf",
                access_method="direct_download",
                page_count_estimate=457,
                last_amended=None,
                amendment_series=None,
                in_scope=True,
            ),
        ],
        parser_config=None,
        qa_report=None,
        manifest_version="0.1.0",
        status="draft",
        pipeline_ready=False,
    )


def _valid_citation_scheme() -> CitationScheme:
    return CitationScheme(
        full_citation_example="Part 3, Section 12, Subsection (2)",
        separator=", ",
        hierarchy=[
            CitationLevel(level="part", pattern=r"^Part\s+\d+", label_format="Part {n}"),
            CitationLevel(level="section", pattern=r"^Section\s+\d+", label_format="Section {n}"),
        ],
    )


def test_t2a_halifax_manifest_round_trips() -> None:
    """T2-A: Halifax manifest serializes and deserializes losslessly."""
    original = _halifax_manifest()
    payload = original.model_dump_json()
    rehydrated = CityIntakeManifest.model_validate_json(payload)
    assert rehydrated == original


def test_t2b_validation_catches_bad_confidence() -> None:
    """T2-B: confidence outside [0, 1] surfaces as ValidationError."""
    scheme = _valid_citation_scheme()
    with pytest.raises(ValidationError):
        ParserConfig(
            parser_version="docling:test",
            citation_scheme=scheme,
            schedule_patterns=[],
            table_caption_pattern=None,
            confidence=1.5,
            flags=[],
        )


def test_t2c_validation_catches_all_out_of_scope_sources() -> None:
    """T2-C: a manifest with zero in-scope sources is rejected."""
    out_of_scope = SourceDocument(
        document_name="Historical Bylaw (repealed)",
        document_type="bylaw",
        document_role="Superseded",
        parent_document=None,
        source_url="file:///old.pdf",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=None,
        last_amended=None,
        amendment_series=None,
        in_scope=False,
    )
    with pytest.raises(ValidationError):
        CityIntakeManifest(
            municipality=Municipality(
                name="Halifax Regional Municipality",
                jurisdiction_code="HRM",
                province="NS",
                governing_body="Halifax Regional Council",
            ),
            sources=[out_of_scope],
            parser_config=None,
            qa_report=None,
            manifest_version="0.1.0",
            status="draft",
            pipeline_ready=False,
        )


def test_t2d_schedule_with_null_parent_document_is_allowed() -> None:
    """T2-D: deliberate non-validation — schedules may omit parent_document.

    The schema does not couple ``document_type='schedule'`` to a non-null
    ``parent_document``. Cross-field linkage is enforced downstream by the
    QA agent; the model layer must not reject it.
    """
    SourceDocument(
        document_name="Schedule A — Zoning Map",
        document_type="schedule",
        document_role="Zoning map referenced by the primary bylaw",
        parent_document=None,
        source_url="file:///schedule-a.pdf",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=12,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )
