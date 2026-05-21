"""Tests for manifest persistence (round-trip + path convention)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from manifest.models import (
    CitationLevel,
    CitationScheme,
    CityIntakeManifest,
    Municipality,
    ParserConfig,
    QAReport,
    SourceDocument,
    TaxonomyMap,
    ZoneDesignation,
)
from manifest.persistence import (
    DEFAULT_OUTPUT_ROOT,
    MANIFEST_FILENAME,
    load_manifest,
    load_manifest_for_jurisdiction,
    manifest_path,
    save_manifest,
)


def _toy_manifest(jurisdiction_code: str = "TINYTOWN") -> CityIntakeManifest:
    return CityIntakeManifest(
        municipality=Municipality(
            name="Tiny Town",
            jurisdiction_code=jurisdiction_code,
            province="NS",
            governing_body="Tiny Town Council",
        ),
        sources=[
            SourceDocument(
                document_name="Tiny Town LUB",
                document_type="bylaw",
                document_role="Primary land-use bylaw",
                parent_document=None,
                source_url="file:///tinytown-lub.pdf",
                format="pdf",
                access_method="direct_download",
                page_count_estimate=42,
                last_amended=None,
                amendment_series=None,
                in_scope=True,
            )
        ],
        parser_config=ParserConfig(
            parser_version="docling:test",
            citation_scheme=CitationScheme(
                full_citation_example="Part 1, Section 3",
                separator=", ",
                hierarchy=[
                    CitationLevel(level="part", pattern=r"^Part\s+\d+", label_format="Part {n}"),
                    CitationLevel(level="section", pattern=r"^\d+(?:\.\d+)*", label_format="{n}"),
                ],
            ),
            schedule_patterns=[r"^Schedule\s+[A-Z]"],
            table_caption_pattern=None,
            confidence=0.9,
            flags=[],
        ),
        taxonomy=TaxonomyMap(
            zone_designations=[
                ZoneDesignation(code="R1", canonical_type="residential"),
                ZoneDesignation(code="C1", canonical_type="commercial"),
            ],
            use_class_map={"single-detached dwelling": "dwelling_single_detached"},
            standards_categories=["height", "setback"],
            companion_bylaws_required=[],
            confidence=0.85,
            flags=[],
        ),
        qa_report=QAReport(
            status="PASS",
            citation_resolution_rate=0.95,
            zone_completeness=0.9,
            pattern_coverage=0.92,
            flags=[],
            recommended_action="approve",
        ),
        manifest_version="0.1.0",
        status="active",
        pipeline_ready=True,
        flags=[],
    )


def test_default_path_uses_abs_learning_output_root() -> None:
    """``manifest_path`` follows the convention from ABS-74."""
    assert manifest_path("HRM") == DEFAULT_OUTPUT_ROOT / "HRM" / MANIFEST_FILENAME


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    original = _toy_manifest()
    written = save_manifest(original, root=tmp_path)
    assert written.exists()
    assert written == tmp_path / "TINYTOWN" / MANIFEST_FILENAME

    reloaded = load_manifest(written)
    assert reloaded == original

    by_code = load_manifest_for_jurisdiction("TINYTOWN", root=tmp_path)
    assert by_code == original


def test_load_rejects_invalid_payload(tmp_path: Path) -> None:
    bad = tmp_path / "BADTOWN" / MANIFEST_FILENAME
    bad.parent.mkdir(parents=True)
    bad.write_text('{"not": "a manifest"}', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_manifest(bad)
