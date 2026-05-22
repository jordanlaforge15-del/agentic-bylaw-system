"""Unit tests for the Phase 2 Semantic Mapper agent.

The Anthropic client and PdfBootstrapReader are mocked so no network or
PDF I/O occurs.
"""
from __future__ import annotations

import json
from typing import List
from unittest.mock import MagicMock

import pytest

from agents.semantic_mapper import (
    CANONICAL_STANDARDS,
    CANONICAL_USE_CLASSES,
    CANONICAL_ZONE_TYPES,
    DEFAULT_VOCABULARY_PATH,
    DEFAULT_VOCABULARY_VERSION,
    SemanticMapperAgent,
    STANDARDS_TOOL_NAME,
    USES_TOOL_NAME,
    ZONES_TOOL_NAME,
    load_vocabulary,
)
from manifest.models import SourceDocument


def _make_response(payload: dict, tool_name: str):
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = payload
    response = MagicMock()
    response.content = [block]
    return response


def _zones_response(zones: List[dict], confidence: float = 0.9):
    return _make_response(
        {"zones": zones, "confidence": confidence}, ZONES_TOOL_NAME
    )


def _uses_response(mappings: List[dict], confidence: float = 0.9):
    return _make_response(
        {"mappings": mappings, "confidence": confidence}, USES_TOOL_NAME
    )


def _standards_response(categories: List[str], confidence: float = 0.9):
    return _make_response(
        {"categories": categories, "confidence": confidence}, STANDARDS_TOOL_NAME
    )


def _make_window(idx: int = 0) -> dict:
    return {
        "window_index": idx,
        "start_page": 100 + idx * 15,
        "end_page": 100 + idx * 15 + 14,
        "text": "stub window text",
    }


# ----------------------------------------------------------------------- T6-A
def test_t6a_extract_zones_finds_minimum_required_codes():
    canned_zones = [
        {"code": "ER-1", "canonical_type": "residential", "full_name": "Established Residential 1"},
        {"code": "UC-1", "canonical_type": "mixed_use", "full_name": "Urban Centre 1"},
        {"code": "CEN-1", "canonical_type": "mixed_use", "full_name": "Central 1"},
        {"code": "DD", "canonical_type": "mixed_use", "full_name": "Downtown Dartmouth"},
        {"code": "INS", "canonical_type": "institutional", "full_name": "Institutional"},
        {"code": "CLI", "canonical_type": "commercial", "full_name": "Corridor Light Industrial"},
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _zones_response(canned_zones)

    agent = SemanticMapperAgent(fake_client)
    designations, flags, confidence = agent._extract_zones([_make_window()])

    codes = {z.code for z in designations}
    required = {"ER-1", "UC-1", "CEN-1", "DD", "INS", "CLI"}
    missing = required - codes
    assert not missing, f"Missing required zone codes: {missing}"

    # Confirm we routed through the forced tool-use path
    fake_client.messages.create.assert_called_once()
    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": ZONES_TOOL_NAME}


# ----------------------------------------------------------------------- T6-B
def test_t6b_canonical_type_mapping_is_valid():
    canned_zones = [
        {"code": "ER-1", "canonical_type": "residential"},
        {"code": "UC-1", "canonical_type": "mixed_use"},
        {"code": "CEN-1", "canonical_type": "mixed_use"},
        {"code": "INS", "canonical_type": "institutional"},
        {"code": "CH-1", "canonical_type": "commercial"},
        {"code": "RPK", "canonical_type": "open_space"},
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _zones_response(canned_zones)

    agent = SemanticMapperAgent(fake_client)
    designations, _, _ = agent._extract_zones([_make_window()])

    assert designations, "Expected at least one ZoneDesignation"
    for d in designations:
        assert d.canonical_type in CANONICAL_ZONE_TYPES, (
            f"Invalid canonical_type {d.canonical_type!r} on {d.code}"
        )
        assert d.canonical_type, f"Missing canonical_type on {d.code}"


# ----------------------------------------------------------------------- T6-C
def test_t6c_use_class_map_keys_are_canonical():
    canned = [
        {"local_term": "single-unit dwelling use", "canonical_key": "residential_dwelling_single"},
        {"local_term": "multi-unit dwelling use", "canonical_key": "residential_dwelling_multi"},
        {"local_term": "secondary suite use", "canonical_key": "residential_dwelling_accessory"},
        {"local_term": "home occupation use", "canonical_key": "home_occupation"},
        {"local_term": "daycare use", "canonical_key": "institutional_education"},
        {"local_term": "retail store use", "canonical_key": "retail_general"},
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _uses_response(canned)

    agent = SemanticMapperAgent(fake_client)
    mapping, candidates, _, _ = agent._extract_use_classes([_make_window()])

    for local, canonical in mapping.items():
        assert canonical in CANONICAL_USE_CLASSES, (
            f"Canonical key {canonical!r} for {local!r} is out of vocabulary"
        )

    assert "single-unit dwelling use" in mapping
    assert mapping["single-unit dwelling use"] == "residential_dwelling_single"
    # Everything in this canned payload is in-vocab so no candidates expected.
    assert candidates == []


# ----------------------------------------------------------------------- T6-D
def test_t6d_standards_categories_are_canonical_and_cover_known():
    canned = [
        "height",
        "lot_coverage",
        "front_setback",
        "side_setback",
        "rear_setback",
        "parking",
        # Out-of-vocab value: should land in proposed_categories, NOT be silently dropped.
        "frontage",
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _standards_response(canned)

    agent = SemanticMapperAgent(fake_client)
    categories, proposed, _, _ = agent._extract_standards_categories([_make_window()])

    for cat in categories:
        assert cat in CANONICAL_STANDARDS, f"Out-of-vocab category: {cat!r}"

    # At least 4 of these five must appear.
    benchmark = {"height", "lot_coverage", "front_setback", "side_setback", "parking"}
    overlap = benchmark & set(categories)
    assert len(overlap) >= 4, (
        f"Only {len(overlap)} of the benchmark standards present: {overlap}"
    )

    # ABS-76: out-of-vocab proposals must be preserved, not silently dropped.
    assert "frontage" in proposed, (
        f"'frontage' should have been preserved as a proposed_category, got {proposed!r}"
    )
    assert "frontage" not in categories


# ----------------------------------------------------------------------- T6-E
def test_t6e_confidence_reduces_on_zone_type_conflict(monkeypatch, tmp_path):
    """``map()`` must surface canonical_type conflicts and reduce confidence."""

    class _FakeReader:
        def __init__(self, _path):
            pass

        def extract_pages(self):
            return {p: "stub" for p in range(1, 40)}

        def detect_content_zone(self, _pages):
            return (1, 30)

    monkeypatch.setattr(
        "agents.semantic_mapper.PdfBootstrapReader", _FakeReader
    )

    dummy = tmp_path / "stub.pdf"
    dummy.write_bytes(b"%PDF-stub")

    source_doc = SourceDocument(
        document_name="Stub Bylaw",
        document_type="bylaw",
        document_role="primary",
        parent_document=None,
        source_url=f"file://{dummy}",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=30,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )

    fake_client = MagicMock()
    # The window finders will be patched to return two zone windows and zero
    # standards windows, so the LLM is invoked 2x for zones + 2x for uses.
    fake_client.messages.create.side_effect = [
        _zones_response(
            [{"code": "CEN-1", "canonical_type": "commercial"}], confidence=1.0
        ),
        _zones_response(
            [{"code": "CEN-1", "canonical_type": "mixed_use"}], confidence=1.0
        ),
        _uses_response([], confidence=1.0),
        _uses_response([], confidence=1.0),
    ]

    agent = SemanticMapperAgent(fake_client)
    monkeypatch.setattr(
        agent,
        "_find_zone_section_windows",
        lambda pages, zone: [_make_window(0), _make_window(1)],
    )
    monkeypatch.setattr(
        agent, "_find_standards_windows", lambda pages, zone: []
    )

    taxonomy = agent.map(source_doc)

    assert taxonomy.confidence < 1.0, (
        f"Confidence not reduced after conflict: {taxonomy.confidence}"
    )
    conflict_flags = [
        f for f in taxonomy.flags if "CEN-1" in f and "conflict" in f.lower()
    ]
    assert conflict_flags, (
        f"Expected a CEN-1 conflict flag, got flags: {taxonomy.flags!r}"
    )
    # The merged map should still expose CEN-1 once.
    cen1 = [z for z in taxonomy.zone_designations if z.code == "CEN-1"]
    assert len(cen1) == 1, f"Expected single CEN-1 entry, got {len(cen1)}"
    # Vocabulary version always populated on the produced taxonomy.
    assert taxonomy.vocabulary_version == DEFAULT_VOCABULARY_VERSION


# --------------------------------------------------- non-T6 implementation checks
def test_zone_window_finder_returns_centred_windows():
    """The page with the most zone-code mentions should anchor a window."""
    pages = {p: "" for p in range(1, 41)}
    # Pages 18-22 are zone-dense
    for p in range(18, 23):
        pages[p] = "ER-1 ER-2 ER-3 UC-1 UC-2 CEN-1 CEN-2"
    pages[20] = "ER-1 ER-2 ER-3 UC-1 UC-2 CEN-1 CEN-2 CDD-1 CDD-2 CH-1 CH-2 DD-1"

    fake_client = MagicMock()
    agent = SemanticMapperAgent(fake_client)
    windows = agent._find_zone_section_windows(pages, (1, 40))

    assert windows, "No zone windows returned"
    first = windows[0]
    # Window of 15 pages should contain the densest page 20
    assert first["start_page"] <= 20 <= first["end_page"]
    assert first["end_page"] - first["start_page"] + 1 == 15


def test_standards_window_finder_picks_numeric_dense_pages():
    pages = {p: "" for p in range(1, 41)}
    for p in (10, 11, 12):
        pages[p] = "Maximum height 14m. Front setback 3m. Lot coverage 60%. Parking 1 space."

    fake_client = MagicMock()
    agent = SemanticMapperAgent(fake_client)
    windows = agent._find_standards_windows(pages, (1, 40))

    assert windows
    assert any(
        w["start_page"] <= 11 <= w["end_page"] for w in windows
    ), f"Standards window did not cover page 11: {windows}"


# =====================================================================
# ABS-76: open vocabulary — diagnostic + vocabulary-as-data tests
# =====================================================================


def test_abs76_use_class_out_of_vocab_preserved_as_candidate():
    """Out-of-vocab canonical_keys must surface as vocabulary_extension_candidates,
    not be silently dropped (the prior behaviour at semantic_mapper.py:499)."""
    canned = [
        # In-vocab: lands in mapping
        {"local_term": "single-unit dwelling use", "canonical_key": "residential_dwelling_single"},
        # Out-of-vocab: prior code dropped these on the floor; now preserved.
        {"local_term": "cannabis retail use", "canonical_key": "cannabis_retail"},
        {"local_term": "rooming house use", "canonical_key": "rooming_house"},
        {"local_term": "data centre use", "canonical_key": "data_centre"},
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _uses_response(canned)

    agent = SemanticMapperAgent(fake_client)
    mapping, candidates, _, _ = agent._extract_use_classes([_make_window(3)])

    # Mapping still only contains in-vocab entries.
    assert mapping == {"single-unit dwelling use": "residential_dwelling_single"}
    # All three out-of-vocab proposals preserved as candidates.
    proposed_keys = {c.proposed_canonical_key for c in candidates}
    local_terms = {c.local_term for c in candidates}
    assert proposed_keys == {"cannabis_retail", "rooming_house", "data_centre"}
    assert local_terms == {"cannabis retail use", "rooming house use", "data centre use"}
    # was_in_vocabulary is False on every candidate (we only emit candidates for out-of-vocab).
    assert all(c.was_in_vocabulary is False for c in candidates)
    # source_window propagates the window index for operator triage.
    assert all(c.source_window == 3 for c in candidates)


def test_abs76_taxonomymap_flags_surface_candidate_counts(monkeypatch, tmp_path):
    """``map()`` must add a single human-readable flag summarising candidates,
    not 50 raw flags (per plan step 5)."""

    class _FakeReader:
        def __init__(self, _path):
            pass

        def extract_pages(self):
            return {p: "stub" for p in range(1, 40)}

        def detect_content_zone(self, _pages):
            return (1, 30)

    monkeypatch.setattr("agents.semantic_mapper.PdfBootstrapReader", _FakeReader)

    dummy = tmp_path / "stub.pdf"
    dummy.write_bytes(b"%PDF-stub")
    source_doc = SourceDocument(
        document_name="Stub Bylaw",
        document_type="bylaw",
        document_role="primary",
        parent_document=None,
        source_url=f"file://{dummy}",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=30,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        # 1 zone window
        _zones_response(
            [{"code": "ZX-1", "canonical_type": "commercial"}], confidence=1.0
        ),
        # 1 uses window: mix of in-vocab + out-of-vocab proposals
        _uses_response(
            [
                {"local_term": "retail store use", "canonical_key": "retail_general"},
                {"local_term": "cannabis retail use", "canonical_key": "cannabis_retail"},
                {"local_term": "data centre use", "canonical_key": "data_centre"},
            ],
            confidence=1.0,
        ),
        # 1 standards window: in-vocab + proposed
        _standards_response(
            ["height", "parking", "green_roof_coverage", "tree_canopy"], confidence=1.0
        ),
    ]

    agent = SemanticMapperAgent(fake_client)
    monkeypatch.setattr(
        agent, "_find_zone_section_windows", lambda pages, zone: [_make_window(0)]
    )
    monkeypatch.setattr(
        agent, "_find_standards_windows", lambda pages, zone: [_make_window(1)]
    )

    taxonomy = agent.map(source_doc)

    # Use_class map only contains in-vocab.
    assert taxonomy.use_class_map == {"retail store use": "retail_general"}
    # Candidates preserved for operator review.
    candidate_keys = {c.proposed_canonical_key for c in taxonomy.vocabulary_extension_candidates}
    assert candidate_keys == {"cannabis_retail", "data_centre"}
    # Standards: in-vocab in the canonical list, out-of-vocab in proposed_categories.
    assert taxonomy.standards_categories == ["height", "parking"]
    assert set(taxonomy.proposed_categories) == {"green_roof_coverage", "tree_canopy"}
    # Aggregated flag summaries — one for use-classes, one for standards.
    use_flags = [f for f in taxonomy.flags if "vocabulary_extension_candidates" in f]
    std_flags = [f for f in taxonomy.flags if "proposed_standards_categories" in f]
    assert len(use_flags) == 1, f"Expected exactly one summary flag, got {taxonomy.flags!r}"
    assert "2" in use_flags[0]  # 2 candidates
    assert len(std_flags) == 1, f"Expected exactly one std summary flag, got {taxonomy.flags!r}"
    assert "2" in std_flags[0]


def test_abs76_vocabulary_loaded_from_fixture():
    """The bundled fixture is the source of truth for the module-level constants."""
    data = load_vocabulary()
    assert data["version"] == DEFAULT_VOCABULARY_VERSION
    assert tuple(data["zone_types"]) == CANONICAL_ZONE_TYPES
    assert tuple(data["use_classes"]) == CANONICAL_USE_CLASSES
    assert tuple(data["standards"]) == CANONICAL_STANDARDS
    # And the fixture file actually lives where the loader expects it.
    assert DEFAULT_VOCABULARY_PATH.is_file()


def test_abs76_vocabulary_path_override(tmp_path):
    """A SemanticMapperAgent constructed with vocabulary_path uses that file."""
    custom = {
        "version": "v99-test",
        "zone_types": ["residential", "unknown"],
        "use_classes": ["custom_use_a", "custom_use_b", "other"],
        "standards": ["height", "custom_standard"],
    }
    vocab_file = tmp_path / "custom_vocab.json"
    vocab_file.write_text(json.dumps(custom))

    fake_client = MagicMock()
    agent = SemanticMapperAgent(fake_client, vocabulary_path=vocab_file)
    assert agent.vocabulary_version == "v99-test"
    assert agent.use_classes == ("custom_use_a", "custom_use_b", "other")
    assert agent.standards == ("height", "custom_standard")

    # Run the use-class merge through the custom vocab.
    canned = [
        {"local_term": "thing", "canonical_key": "custom_use_a"},  # in-vocab
        {"local_term": "other thing", "canonical_key": "retail_general"},  # NOT in-vocab here
    ]
    fake_client.messages.create.return_value = _uses_response(canned)
    mapping, candidates, _, _ = agent._extract_use_classes([_make_window()])

    assert mapping == {"thing": "custom_use_a"}
    # retail_general is in the default vocab but NOT in this agent's custom vocab,
    # so it must surface as a candidate.
    assert len(candidates) == 1
    assert candidates[0].proposed_canonical_key == "retail_general"


def test_abs76_vocabulary_version_persisted_on_taxonomymap(monkeypatch, tmp_path):
    """Re-ingest determinism: the version used at ingest is on the manifest."""

    class _FakeReader:
        def __init__(self, _path):
            pass

        def extract_pages(self):
            return {p: "stub" for p in range(1, 20)}

        def detect_content_zone(self, _pages):
            return (1, 15)

    monkeypatch.setattr("agents.semantic_mapper.PdfBootstrapReader", _FakeReader)

    dummy = tmp_path / "stub.pdf"
    dummy.write_bytes(b"%PDF-stub")
    source_doc = SourceDocument(
        document_name="Stub Bylaw",
        document_type="bylaw",
        document_role="primary",
        parent_document=None,
        source_url=f"file://{dummy}",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=15,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )

    custom = {
        "version": "v42",
        "zone_types": ["residential", "unknown"],
        "use_classes": ["other"],
        "standards": ["height"],
    }
    vocab_file = tmp_path / "v42_vocab.json"
    vocab_file.write_text(json.dumps(custom))

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _zones_response([{"code": "AA-1", "canonical_type": "residential"}], confidence=1.0),
        _uses_response([], confidence=1.0),
    ]
    agent = SemanticMapperAgent(fake_client, vocabulary_path=vocab_file)
    monkeypatch.setattr(
        agent, "_find_zone_section_windows", lambda pages, zone: [_make_window(0)]
    )
    monkeypatch.setattr(agent, "_find_standards_windows", lambda pages, zone: [])

    taxonomy = agent.map(source_doc)
    assert taxonomy.vocabulary_version == "v42"


def test_abs76_vocabulary_missing_key_raises(tmp_path):
    bad = tmp_path / "bad_vocab.json"
    bad.write_text(json.dumps({"version": "x", "use_classes": [], "standards": []}))
    with pytest.raises(ValueError, match="zone_types"):
        load_vocabulary(bad)


def test_abs76_use_class_other_is_not_a_candidate():
    """If the LLM legitimately uses 'other' (which IS in vocab), it must NOT
    show up as a candidate — only proposals outside the vocab are candidates."""
    canned = [
        {"local_term": "miscellaneous use", "canonical_key": "other"},
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _uses_response(canned)

    agent = SemanticMapperAgent(fake_client)
    mapping, candidates, _, _ = agent._extract_use_classes([_make_window()])
    assert mapping == {"miscellaneous use": "other"}
    assert candidates == []
