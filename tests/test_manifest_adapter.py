"""Tests for the manifest → ParsingProfile adapter (ABS-74).

Covers:
- pipeline_ready=False refusal (acceptance #3 of the issue)
- citation hierarchy → ManifestCitationLevel mapping (level-name → FragmentType + depth)
- zone designations → frozenset + compiled regex
- use_class_map normalisation (whitespace + case)
- end-to-end load of the committed Halifax baseline manifest

These are unit tests; the Halifax baseline tolerance test
(``tests/test_manifest_driven_ingest.py``) covers acceptance #2.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from layer1.manifest_adapter import (
    ManifestNotReadyError,
    load_manifest,
    profile_from_manifest,
    profile_for_jurisdiction,
)
from layer1.models.enums import FragmentType
from layer1.pipeline.citations import parse_citation_label


HALIFAX_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1]
    / "abs-learning"
    / "output"
    / "halifax-regional-centre"
    / "manifest.json"
)


def _minimal_payload(
    *,
    pipeline_ready: bool = True,
    parser_config: dict | None = None,
    taxonomy: dict | None = None,
    enrichment: dict | None = None,
) -> dict:
    return {
        "municipality": {
            "name": "Tiny Town",
            "jurisdiction_code": "tinytown",
            "province": "NS",
            "governing_body": "Tiny Town Council",
        },
        "sources": [
            {
                "document_name": "Tiny Town LUB",
                "document_type": "bylaw",
                "document_role": "Primary land-use bylaw",
                "in_scope": True,
            }
        ],
        "parser_config": parser_config,
        "taxonomy": taxonomy,
        "enrichment": enrichment,
        "manifest_version": "0.1.0",
        "status": "draft" if not pipeline_ready else "active",
        "pipeline_ready": pipeline_ready,
    }


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_pipeline_ready_false_refuses_to_emit_profile(tmp_path: Path) -> None:
    """Acceptance #3: pipeline_ready=True must be a meaningful precondition."""
    manifest_path = _write_manifest(tmp_path, _minimal_payload(pipeline_ready=False))
    manifest = load_manifest(manifest_path)
    with pytest.raises(ManifestNotReadyError):
        profile_from_manifest(manifest)


def test_pipeline_ready_false_overridable_for_diagnostics(tmp_path: Path) -> None:
    """The escape hatch exists for tooling, but it has to be asked for."""
    manifest_path = _write_manifest(tmp_path, _minimal_payload(pipeline_ready=False))
    manifest = load_manifest(manifest_path)
    profile = profile_from_manifest(manifest, require_pipeline_ready=False)
    assert profile.jurisdiction_code == "tinytown"


def test_citation_hierarchy_levels_compiled_into_profile(tmp_path: Path) -> None:
    parser_config = {
        "parser_version": "test",
        "citation_scheme": {
            "full_citation_example": "Part 1, Section 12",
            "separator": ", ",
            "hierarchy": [
                {"level": "part", "pattern": r"^Part\s+(\d+)", "label_format": "Part {n}"},
                {"level": "section", "pattern": r"^\d+(?:\.\d+)*", "label_format": "{n}"},
                {"level": "subclause", "pattern": r"^\([ivx]+\)", "label_format": "({n})"},
            ],
        },
        "schedule_patterns": [],
        "table_caption_pattern": None,
        "confidence": 0.9,
        "flags": [],
    }
    manifest = load_manifest(
        _write_manifest(tmp_path, _minimal_payload(parser_config=parser_config))
    )
    profile = profile_from_manifest(manifest)
    levels = profile.manifest_citation_levels
    assert len(levels) == 3
    by_raw = {level.raw_level: level for level in levels}
    assert by_raw["part"].fragment_type is FragmentType.PART
    assert by_raw["part"].level == 1
    assert by_raw["section"].fragment_type is FragmentType.SECTION
    assert by_raw["section"].level == 2
    assert by_raw["subclause"].fragment_type is FragmentType.SUBCLAUSE
    assert by_raw["subclause"].level == 6


def test_unknown_level_name_falls_back_to_section_at_depth_two(tmp_path: Path) -> None:
    parser_config = {
        "parser_version": "test",
        "citation_scheme": {
            "full_citation_example": "Article 1",
            "separator": ", ",
            "hierarchy": [
                # Manifests in the wild sometimes use jurisdiction-specific level names.
                {"level": "stanza", "pattern": r"^Stanza\s+\d+", "label_format": "Stanza {n}"},
                # And sometimes structured "level_3" style.
                {"level": "level_3", "pattern": r"^\(\d+\)", "label_format": "({n})"},
            ],
        },
        "schedule_patterns": [],
        "table_caption_pattern": None,
        "confidence": 0.7,
        "flags": [],
    }
    manifest = load_manifest(
        _write_manifest(tmp_path, _minimal_payload(parser_config=parser_config))
    )
    profile = profile_from_manifest(manifest)
    by_raw = {level.raw_level: level for level in profile.manifest_citation_levels}
    assert by_raw["stanza"].fragment_type is FragmentType.SECTION
    assert by_raw["stanza"].level == 2
    assert by_raw["level_3"].fragment_type is FragmentType.SUBSECTION
    assert by_raw["level_3"].level == 3


def test_invalid_regex_in_manifest_surfaces_clearly(tmp_path: Path) -> None:
    parser_config = {
        "parser_version": "test",
        "citation_scheme": {
            "full_citation_example": "",
            "separator": "",
            "hierarchy": [
                {"level": "part", "pattern": r"[unterminated", "label_format": "Part {n}"},
            ],
        },
        "schedule_patterns": [],
        "table_caption_pattern": None,
        "confidence": 0.5,
        "flags": [],
    }
    manifest = load_manifest(
        _write_manifest(tmp_path, _minimal_payload(parser_config=parser_config))
    )
    with pytest.raises(ValueError, match="not a valid regex"):
        profile_from_manifest(manifest)


# --------------------------------------------------------------------- ABS-284
# Enrichment-classification conventions flow from the manifest's optional
# ``enrichment`` block onto the ParsingProfile, the same way zone/use vocab does.


def test_abs284_enrichment_block_maps_onto_profile(tmp_path: Path) -> None:
    enrichment = {
        "permission_encoding": "section_indexed",
        "permitted_marker_codepoints": ["U+F0B7", "0x25CF"],
        "ignored_marker_codepoints": ["U+F020"],
    }
    manifest = load_manifest(
        _write_manifest(tmp_path, _minimal_payload(enrichment=enrichment))
    )
    profile = profile_from_manifest(manifest)
    assert profile.permission_encoding == "section_indexed"
    assert profile.permitted_marker_codepoints == frozenset({0xF0B7, 0x25CF})
    assert profile.ignored_marker_codepoints == frozenset({0xF020})


def test_abs284_absent_enrichment_block_leaves_profile_defaults_none(tmp_path: Path) -> None:
    """FR3: no enrichment block → profile carries no enrichment overrides, so
    enrichment falls back to the Regional-Centre default."""
    manifest = load_manifest(_write_manifest(tmp_path, _minimal_payload()))
    profile = profile_from_manifest(manifest)
    assert profile.permission_encoding is None
    assert profile.permitted_marker_codepoints is None
    assert profile.ignored_marker_codepoints is None


def test_abs284_invalid_codepoint_surfaces_clearly(tmp_path: Path) -> None:
    enrichment = {"permitted_marker_codepoints": ["not-a-codepoint"]}
    manifest = load_manifest(
        _write_manifest(tmp_path, _minimal_payload(enrichment=enrichment))
    )
    with pytest.raises(ValueError, match="not a valid hex codepoint"):
        profile_from_manifest(manifest)


def test_zone_designations_become_frozenset_and_compiled_pattern(tmp_path: Path) -> None:
    taxonomy = {
        "zone_designations": [
            {"code": "R1", "canonical_type": "residential"},
            {"code": "C-1", "canonical_type": "commercial"},
            {"code": "MIXED-USE", "canonical_type": "mixed_use"},
        ],
        "use_class_map": {},
        "standards_categories": [],
        "companion_bylaws_required": [],
        "confidence": 0.9,
        "flags": [],
    }
    manifest = load_manifest(_write_manifest(tmp_path, _minimal_payload(taxonomy=taxonomy)))
    profile = profile_from_manifest(manifest)
    assert profile.known_zone_codes == frozenset({"R1", "C-1", "MIXED-USE"})
    assert profile.zone_pattern is not None
    text = "The R1 zone abuts the C-1 zone, but MIXED-USE is the new hotness."
    matches = sorted({m.group(0).upper() for m in profile.zone_pattern.finditer(text)})
    assert matches == ["C-1", "MIXED-USE", "R1"]


def test_longer_zone_codes_win_over_prefixes(tmp_path: Path) -> None:
    """Without length-ordering the regex would match 'CDD' before 'CDD-2'."""
    taxonomy = {
        "zone_designations": [
            {"code": "CDD", "canonical_type": "mixed_use"},
            {"code": "CDD-2", "canonical_type": "mixed_use"},
        ],
        "use_class_map": {},
        "standards_categories": [],
        "companion_bylaws_required": [],
        "confidence": 0.9,
        "flags": [],
    }
    manifest = load_manifest(_write_manifest(tmp_path, _minimal_payload(taxonomy=taxonomy)))
    profile = profile_from_manifest(manifest)
    assert profile.zone_pattern is not None
    match = profile.zone_pattern.search("the CDD-2 district")
    assert match is not None and match.group(0).upper() == "CDD-2"


def test_use_class_map_keys_normalized_to_lowercase_collapsed_whitespace(
    tmp_path: Path,
) -> None:
    taxonomy = {
        "zone_designations": [],
        "use_class_map": {
            "Single-Detached Dwelling": "dwelling_single_detached",
            "  Multi  Unit Building ": "building_multi_unit",
        },
        "standards_categories": [],
        "companion_bylaws_required": [],
        "confidence": 0.9,
        "flags": [],
    }
    manifest = load_manifest(_write_manifest(tmp_path, _minimal_payload(taxonomy=taxonomy)))
    profile = profile_from_manifest(manifest)
    assert profile.use_class_map == {
        "single-detached dwelling": "dwelling_single_detached",
        "multi unit building": "building_multi_unit",
    }


def test_no_parser_config_or_taxonomy_yields_empty_overlay(tmp_path: Path) -> None:
    manifest = load_manifest(_write_manifest(tmp_path, _minimal_payload()))
    profile = profile_from_manifest(manifest)
    assert profile.manifest_citation_levels == ()
    assert profile.known_zone_codes is None
    assert profile.zone_pattern is None
    assert profile.use_class_map is None


def test_halifax_baseline_manifest_loads_and_compiles() -> None:
    """The committed Halifax baseline must satisfy the adapter end-to-end.

    This is the manifest the Halifax tolerance integration test loads; if it
    can't be compiled, the rest of the regression won't run.
    """
    assert HALIFAX_MANIFEST_PATH.exists()
    manifest = load_manifest(HALIFAX_MANIFEST_PATH)
    assert manifest.pipeline_ready is True
    profile = profile_from_manifest(manifest)
    assert profile.jurisdiction_code == "halifax-regional-centre"
    assert profile.known_zone_codes is not None
    assert "CEN-2" in profile.known_zone_codes
    assert profile.zone_pattern is not None


def test_profile_for_jurisdiction_resolves_disk_layout(tmp_path: Path) -> None:
    """``profile_for_jurisdiction`` looks under ``{root}/{code}/manifest.json``."""
    payload = _minimal_payload()
    out_dir = tmp_path / "tinytown"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    profile = profile_for_jurisdiction("tinytown", root=tmp_path)
    assert profile.jurisdiction_code == "tinytown"


def test_manifest_part_pattern_drives_citation_parser(tmp_path: Path) -> None:
    """End-to-end: a manifest's PART pattern is what parse_citation_label fires on."""
    parser_config = {
        "parser_version": "test",
        "citation_scheme": {
            "full_citation_example": "Chapter 7",
            "separator": " ",
            "hierarchy": [
                # Deliberately *not* "Part" — proves the manifest pattern wins.
                {"level": "part", "pattern": r"^Chapter\s+(\d+)", "label_format": "Chapter {n}"},
            ],
        },
        "schedule_patterns": [],
        "table_caption_pattern": None,
        "confidence": 0.9,
        "flags": [],
    }
    manifest = load_manifest(
        _write_manifest(tmp_path, _minimal_payload(parser_config=parser_config))
    )
    profile = profile_from_manifest(manifest)
    match = parse_citation_label("Chapter 7 Introduction", profile=profile)
    assert match is not None
    assert match.fragment_type is FragmentType.PART
    assert match.label == "Chapter 7"
    assert match.title == "Introduction"
