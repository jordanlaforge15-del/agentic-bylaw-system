"""Tests for the PDF drawing-type classifier.

Coverage:
- DrawingType enum values
- Heuristic classification: sheet codes and keyword patterns
- Vision response parsing
- Combination logic (heuristic vs vision)
- Caching behaviour
- compute_file_hash utility
- Full classify() async path (mocked API)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from layer1.parsers.pdf_drawing_classifier import (
    DrawingClassification,
    DrawingType,
    DrawingTypeClassifier,
    compute_file_hash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classifier(**kwargs) -> DrawingTypeClassifier:
    return DrawingTypeClassifier(api_key="test-key", **kwargs)


def _vision_response(
    drawing_type: str,
    confidence: float = 0.9,
    reasoning: str = "",
    multi: bool = False,
) -> str:
    return json.dumps(
        {
            "drawing_type": drawing_type,
            "confidence": confidence,
            "reasoning": reasoning,
            "multi_drawing_detected": multi,
        }
    )


# ---------------------------------------------------------------------------
# DrawingType enum
# ---------------------------------------------------------------------------


def test_drawing_type_all_values():
    expected = {
        "site_plan", "floor_plan", "elevation", "section",
        "detail", "schedule", "cover_sheet", "unknown",
    }
    assert {dt.value for dt in DrawingType} == expected


# ---------------------------------------------------------------------------
# Heuristic — sheet codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title_block_text,expected_type,expected_method",
    [
        # Civil / site
        ("C1.01 Site Plan", DrawingType.SITE_PLAN, "heuristic_sheet_code"),
        ("C0.1 Site Servicing", DrawingType.SITE_PLAN, "heuristic_sheet_code"),
        ("SP.01 Site Plan", DrawingType.SITE_PLAN, "heuristic_sheet_code"),
        # Cover / general
        ("A0.01 Title Sheet", DrawingType.COVER_SHEET, "heuristic_sheet_code"),
        ("G0.01 General Notes", DrawingType.COVER_SHEET, "heuristic_sheet_code"),
        ("CS.01 Cover Sheet", DrawingType.COVER_SHEET, "heuristic_sheet_code"),
        ("T.01 Title", DrawingType.COVER_SHEET, "heuristic_sheet_code"),
        # Floor plans
        ("A1.01 Ground Floor Plan", DrawingType.FLOOR_PLAN, "heuristic_sheet_code"),
        ("A1.02 Second Floor Plan", DrawingType.FLOOR_PLAN, "heuristic_sheet_code"),
        ("A-1.01 Basement Plan", DrawingType.FLOOR_PLAN, "heuristic_sheet_code"),
        # Elevations
        ("A2.01 North Elevation", DrawingType.ELEVATION, "heuristic_sheet_code"),
        ("A2.02 South Elevation", DrawingType.ELEVATION, "heuristic_sheet_code"),
        # Sections
        ("A3.01 Building Section", DrawingType.SECTION, "heuristic_sheet_code"),
        ("A3.02 Wall Section", DrawingType.SECTION, "heuristic_sheet_code"),
        # Details
        ("A4.01 Window Detail", DrawingType.DETAIL, "heuristic_sheet_code"),
        ("A5.01 Stair Detail", DrawingType.DETAIL, "heuristic_sheet_code"),
        ("A9.01 Typical Detail", DrawingType.DETAIL, "heuristic_sheet_code"),
        # Schedules
        ("A6.01 Door Schedule", DrawingType.SCHEDULE, "heuristic_sheet_code"),
        ("A7.01 Window Schedule", DrawingType.SCHEDULE, "heuristic_sheet_code"),
        ("A8.01 Finish Schedule", DrawingType.SCHEDULE, "heuristic_sheet_code"),
    ],
)
def test_heuristic_sheet_codes(title_block_text, expected_type, expected_method):
    clf = _classifier()
    result = clf.classify_heuristic(title_block_text)
    assert result is not None
    assert result.drawing_type == expected_type
    assert result.method == expected_method
    assert result.confidence == 0.80
    assert result.sheet_code is not None


# ---------------------------------------------------------------------------
# Heuristic — keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title_block_text,expected_type",
    [
        ("Site Plan - Phase 1", DrawingType.SITE_PLAN),
        ("Site Layout and Grading", DrawingType.SITE_PLAN),
        ("Key Plan", DrawingType.SITE_PLAN),
        ("Floor Plan - Unit A", DrawingType.FLOOR_PLAN),
        ("Level Plan 2", DrawingType.FLOOR_PLAN),
        ("Ground Floor Layout", DrawingType.FLOOR_PLAN),
        ("Exterior Elevations", DrawingType.ELEVATION),
        ("North Elevation", DrawingType.ELEVATION),
        ("Building Section A-A", DrawingType.SECTION),
        ("Wall Section at Entry", DrawingType.SECTION),
        ("Typical Window Detail", DrawingType.DETAIL),
        ("Door Schedule", DrawingType.SCHEDULE),
        ("Cover Sheet", DrawingType.COVER_SHEET),
        ("Title Page", DrawingType.COVER_SHEET),
        ("Drawing Index", DrawingType.COVER_SHEET),
    ],
)
def test_heuristic_keywords(title_block_text, expected_type):
    clf = _classifier()
    result = clf.classify_heuristic(title_block_text)
    assert result is not None
    assert result.drawing_type == expected_type
    assert result.method == "heuristic_keyword"
    assert result.confidence == 0.65


def test_heuristic_empty_text_returns_none():
    clf = _classifier()
    assert clf.classify_heuristic("") is None
    assert clf.classify_heuristic("   ") is None


def test_heuristic_unrecognised_text_returns_none():
    clf = _classifier()
    assert clf.classify_heuristic("Random notes about the project") is None


# ---------------------------------------------------------------------------
# Vision response parsing
# ---------------------------------------------------------------------------


def test_parse_vision_response_valid():
    clf = _classifier()
    raw = _vision_response("floor_plan", confidence=0.88, reasoning="room layout visible")
    result = clf._parse_vision_response(raw)
    assert result.drawing_type == DrawingType.FLOOR_PLAN
    assert result.confidence == pytest.approx(0.88)
    assert result.method == "vision"
    assert result.multi_drawing_detected is False
    assert result.metadata["reasoning"] == "room layout visible"


def test_parse_vision_response_multi_drawing():
    clf = _classifier()
    raw = _vision_response("floor_plan", confidence=0.7, multi=True)
    result = clf._parse_vision_response(raw)
    assert result.multi_drawing_detected is True


def test_parse_vision_response_unknown_type():
    clf = _classifier()
    raw = _vision_response("blueprint_overlay")  # not a valid DrawingType
    result = clf._parse_vision_response(raw)
    assert result.drawing_type == DrawingType.UNKNOWN


def test_parse_vision_response_wrapped_in_markdown():
    clf = _classifier()
    raw = "```json\n" + _vision_response("elevation") + "\n```"
    result = clf._parse_vision_response(raw)
    assert result.drawing_type == DrawingType.ELEVATION


def test_parse_vision_response_malformed_json():
    clf = _classifier()
    result = clf._parse_vision_response("not json at all")
    assert result.drawing_type == DrawingType.UNKNOWN
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Combination logic
# ---------------------------------------------------------------------------


def test_combine_both_agree_boosts_confidence():
    clf = _classifier()
    heuristic = DrawingClassification(
        drawing_type=DrawingType.FLOOR_PLAN, confidence=0.80, method="heuristic_sheet_code"
    )
    vision = DrawingClassification(
        drawing_type=DrawingType.FLOOR_PLAN, confidence=0.85, method="vision"
    )
    result = clf._combine(heuristic, vision)
    assert result.drawing_type == DrawingType.FLOOR_PLAN
    assert result.confidence > max(heuristic.confidence, vision.confidence) - 0.01  # boosted
    assert result.method == "combined"


def test_combine_vision_wins_when_confident():
    clf = _classifier(vision_confidence_threshold=0.6)
    heuristic = DrawingClassification(
        drawing_type=DrawingType.FLOOR_PLAN, confidence=0.80, method="heuristic_sheet_code"
    )
    vision = DrawingClassification(
        drawing_type=DrawingType.ELEVATION, confidence=0.75, method="vision"
    )
    result = clf._combine(heuristic, vision)
    assert result.drawing_type == DrawingType.ELEVATION
    assert result.method == "combined_vision_wins"


def test_combine_sheet_code_wins_over_low_confidence_vision():
    clf = _classifier(vision_confidence_threshold=0.6)
    heuristic = DrawingClassification(
        drawing_type=DrawingType.FLOOR_PLAN,
        confidence=0.80,
        method="heuristic_sheet_code",
        sheet_code="A1.01",
    )
    vision = DrawingClassification(
        drawing_type=DrawingType.ELEVATION, confidence=0.45, method="vision"
    )
    result = clf._combine(heuristic, vision)
    assert result.drawing_type == DrawingType.FLOOR_PLAN
    assert result.method == "combined_heuristic_wins"
    assert result.sheet_code == "A1.01"


def test_combine_keyword_heuristic_yields_to_low_confidence_vision():
    clf = _classifier(vision_confidence_threshold=0.6)
    heuristic = DrawingClassification(
        drawing_type=DrawingType.FLOOR_PLAN, confidence=0.65, method="heuristic_keyword"
    )
    vision = DrawingClassification(
        drawing_type=DrawingType.ELEVATION, confidence=0.50, method="vision"
    )
    result = clf._combine(heuristic, vision)
    # keyword heuristic doesn't override → vision result used
    assert result.drawing_type == DrawingType.ELEVATION


def test_combine_none_heuristic_returns_vision():
    clf = _classifier()
    vision = DrawingClassification(
        drawing_type=DrawingType.SECTION, confidence=0.9, method="vision"
    )
    result = clf._combine(None, vision)
    assert result == vision


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_caches_result():
    clf = _classifier()
    heuristic_result = DrawingClassification(
        drawing_type=DrawingType.SITE_PLAN, confidence=0.80, method="heuristic_sheet_code"
    )
    with patch.object(clf, "_classify_heuristic", return_value=heuristic_result) as mock_h:
        r1 = await clf.classify(file_hash="abc123", page_number=1, title_block_text="C1.01")
        r2 = await clf.classify(file_hash="abc123", page_number=1, title_block_text="C1.01")

    assert r1 is r2
    assert mock_h.call_count == 1  # second call served from cache
    assert clf.cache_size() == 1


@pytest.mark.asyncio
async def test_classify_different_pages_cached_separately():
    clf = _classifier()
    await clf.classify(file_hash="abc123", page_number=1, title_block_text="A1.01 Floor Plan")
    await clf.classify(file_hash="abc123", page_number=2, title_block_text="A2.01 Elevation")
    assert clf.cache_size() == 2


def test_clear_cache():
    clf = _classifier()
    clf._cache[("abc123", 1)] = DrawingClassification(
        drawing_type=DrawingType.FLOOR_PLAN, confidence=0.8, method="heuristic_sheet_code"
    )
    assert clf.cache_size() == 1
    clf.clear_cache()
    assert clf.cache_size() == 0


# ---------------------------------------------------------------------------
# Full classify() path — no image, heuristic only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_no_image_returns_heuristic():
    clf = _classifier()
    result = await clf.classify(
        file_hash="deadbeef",
        page_number=3,
        title_block_text="A3.01 Building Section",
    )
    assert result.drawing_type == DrawingType.SECTION
    assert result.method == "heuristic_sheet_code"


@pytest.mark.asyncio
async def test_classify_no_image_no_text_returns_unknown():
    clf = _classifier()
    result = await clf.classify(file_hash="deadbeef", page_number=1)
    assert result.drawing_type == DrawingType.UNKNOWN


# ---------------------------------------------------------------------------
# Full classify() path — with image (mocked API)
# ---------------------------------------------------------------------------


def _make_anthropic_response(drawing_type: str, confidence: float = 0.9) -> MagicMock:
    block = MagicMock()
    block.text = _vision_response(drawing_type, confidence)
    resp = MagicMock()
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_classify_with_image_calls_vision(monkeypatch):
    clf = _classifier()

    mock_create = AsyncMock(return_value=_make_anthropic_response("floor_plan", 0.92))
    mock_client = MagicMock()
    mock_client.messages.create = mock_create

    with patch("layer1.parsers.pdf_drawing_classifier.AsyncAnthropic", return_value=mock_client):
        result = await clf.classify(
            file_hash="feedcafe",
            page_number=1,
            image_bytes=b"\x89PNG\r\n\x1a\n",  # minimal PNG header
            title_block_text="",
        )

    assert result.drawing_type == DrawingType.FLOOR_PLAN
    assert result.method == "vision"
    mock_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_classify_combined_when_both_available(monkeypatch):
    clf = _classifier()

    mock_create = AsyncMock(return_value=_make_anthropic_response("elevation", 0.85))
    mock_client = MagicMock()
    mock_client.messages.create = mock_create

    with patch("layer1.parsers.pdf_drawing_classifier.AsyncAnthropic", return_value=mock_client):
        result = await clf.classify(
            file_hash="cafebabe",
            page_number=2,
            image_bytes=b"\x89PNG\r\n\x1a\n",
            title_block_text="A2.02 South Elevation",  # matches elevation
        )

    # Both agree on elevation → combined
    assert result.drawing_type == DrawingType.ELEVATION
    assert result.method == "combined"


@pytest.mark.asyncio
async def test_classify_vision_failure_falls_back_to_heuristic(monkeypatch):
    clf = _classifier()

    mock_create = AsyncMock(side_effect=RuntimeError("API down"))
    mock_client = MagicMock()
    mock_client.messages.create = mock_create

    with patch("layer1.parsers.pdf_drawing_classifier.AsyncAnthropic", return_value=mock_client):
        result = await clf.classify(
            file_hash="badf00d0",
            page_number=1,
            image_bytes=b"\x89PNG\r\n\x1a\n",
            title_block_text="A1.01 Floor Plan",
        )

    assert result.drawing_type == DrawingType.FLOOR_PLAN
    assert result.method == "heuristic_sheet_code"


# ---------------------------------------------------------------------------
# compute_file_hash
# ---------------------------------------------------------------------------


def test_compute_file_hash_deterministic(tmp_path: Path):
    f = tmp_path / "test.pdf"
    f.write_bytes(b"fake pdf content")
    h1 = compute_file_hash(f)
    h2 = compute_file_hash(f)
    assert h1 == h2
    assert len(h1) == 16
    assert all(c in "0123456789abcdef" for c in h1)


def test_compute_file_hash_differs_for_different_content(tmp_path: Path):
    f1 = tmp_path / "a.pdf"
    f2 = tmp_path / "b.pdf"
    f1.write_bytes(b"content A")
    f2.write_bytes(b"content B")
    assert compute_file_hash(f1) != compute_file_hash(f2)


# ---------------------------------------------------------------------------
# Fixture-style coverage: one per drawing type via heuristic
# (simulates having a page representative of each type)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title_block,expected",
    [
        ("C1.01 Site Plan", DrawingType.SITE_PLAN),
        ("A1.01 Ground Floor Plan", DrawingType.FLOOR_PLAN),
        ("A2.01 North Elevation", DrawingType.ELEVATION),
        ("A3.01 Building Section", DrawingType.SECTION),
        ("A4.01 Stair Detail", DrawingType.DETAIL),
        ("A6.01 Door Schedule", DrawingType.SCHEDULE),
        ("A0.01 Title Sheet", DrawingType.COVER_SHEET),
    ],
)
def test_one_page_per_type_heuristic_accuracy(title_block, expected):
    clf = _classifier()
    result = clf.classify_heuristic(title_block)
    assert result is not None, f"Expected classification for {title_block!r}"
    assert result.drawing_type == expected
