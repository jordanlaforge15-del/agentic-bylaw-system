"""Unit tests for the Phase 2 Semantic Mapper agent.

The Anthropic client and PdfBootstrapReader are mocked so no network or
PDF I/O occurs.
"""
from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest

from agents.semantic_mapper import (
    CANONICAL_STANDARDS,
    CANONICAL_USE_CLASSES,
    CANONICAL_ZONE_TYPES,
    SemanticMapperAgent,
    STANDARDS_TOOL_NAME,
    USES_TOOL_NAME,
    ZONES_TOOL_NAME,
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
    mapping, _, _ = agent._extract_use_classes([_make_window()])

    for local, canonical in mapping.items():
        assert canonical in CANONICAL_USE_CLASSES, (
            f"Canonical key {canonical!r} for {local!r} is out of vocabulary"
        )

    assert "single-unit dwelling use" in mapping
    assert mapping["single-unit dwelling use"] == "residential_dwelling_single"


# ----------------------------------------------------------------------- T6-D
def test_t6d_standards_categories_are_canonical_and_cover_known():
    canned = [
        "height",
        "lot_coverage",
        "front_setback",
        "side_setback",
        "rear_setback",
        "parking",
        # Inject an out-of-vocab value to confirm the merge filters it.
        "frontage",
    ]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _standards_response(canned)

    agent = SemanticMapperAgent(fake_client)
    categories, _, _ = agent._extract_standards_categories([_make_window()])

    for cat in categories:
        assert cat in CANONICAL_STANDARDS, f"Out-of-vocab category: {cat!r}"

    # At least 4 of these five must appear.
    benchmark = {"height", "lot_coverage", "front_setback", "side_setback", "parking"}
    overlap = benchmark & set(categories)
    assert len(overlap) >= 4, (
        f"Only {len(overlap)} of the benchmark standards present: {overlap}"
    )


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
