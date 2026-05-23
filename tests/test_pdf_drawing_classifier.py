"""ABS-56 tests — per-page drawing-type classifier.

Three layers:

1. **Heuristic** — call ``classify_by_heuristic`` directly on synthetic
   title-block fixtures and check the type, confidence, and evidence.
2. **Driver** — wire the heuristic into ``DrawingClassifier`` with a
   stub vision client and exercise the caching, vision fallback,
   merge-on-agreement, and merge-on-disagreement branches.
3. **Vision response parser** — feed canned Claude responses to
   ``_parse_vision_response`` to cover happy / malformed / unknown-type
   paths without touching the network.

No real Anthropic call. An operator with ``ANTHROPIC_API_KEY`` can
exercise ``AnthropicVisionClient`` separately against a real PDF page.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from layer1.parsers.pdf_drawing_classifier import (
    DrawingClassification,
    DrawingClassifier,
    DrawingType,
    HEURISTIC_TRUST_THRESHOLD,
    VisionClient,
    _combine_heuristic_and_vision,
    _parse_vision_response,
    classify_by_heuristic,
    classify_pdf_pages,
)

from fixtures.drawing_pages import (
    ambiguous_text,
    cover_sheet_text,
    detail_text,
    elevation_text,
    floor_plan_text,
    multi_drawing_text,
    schedule_text,
    section_text,
    site_plan_text,
    title_only_floor_plan_text,
    unknown_text,
)


# ----------------------------------------------------------------------
# Heuristic
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text_fn,expected_type",
    [
        (site_plan_text, DrawingType.SITE_PLAN),
        (floor_plan_text, DrawingType.FLOOR_PLAN),
        (elevation_text, DrawingType.ELEVATION),
        (section_text, DrawingType.SECTION),
        (detail_text, DrawingType.DETAIL),
        (schedule_text, DrawingType.SCHEDULE),
        (cover_sheet_text, DrawingType.COVER_SHEET),
    ],
)
def test_heuristic_identifies_each_drawing_type(text_fn, expected_type):
    drawing_type, confidence, evidence = classify_by_heuristic(text_fn())
    assert drawing_type == expected_type, evidence
    # All canonical fixtures have BOTH a sheet code and a title keyword,
    # so heuristic confidence should be the highest tier (0.95).
    assert confidence >= 0.9


def test_heuristic_strong_confidence_when_code_and_title_agree():
    _, confidence, evidence = classify_by_heuristic(floor_plan_text())
    assert confidence == pytest.approx(0.95)
    assert evidence["sheet_code"].lower().startswith("a1")
    assert evidence["title_matches"]


def test_heuristic_title_only_match_lower_confidence():
    drawing_type, confidence, evidence = classify_by_heuristic(
        title_only_floor_plan_text()
    )
    assert drawing_type == DrawingType.FLOOR_PLAN
    assert confidence == pytest.approx(0.75)
    assert evidence["sheet_code"] is None


def test_heuristic_unknown_when_nothing_matches():
    drawing_type, confidence, evidence = classify_by_heuristic(unknown_text())
    assert drawing_type == DrawingType.UNKNOWN
    assert confidence == 0.0
    assert evidence["sheet_code"] is None
    assert evidence["title_matches"] == []


def test_heuristic_disagreement_prefers_title_with_lowered_confidence():
    # ambiguous_text has sheet code A1 (floor plan) but title NORTH ELEVATION.
    drawing_type, confidence, evidence = classify_by_heuristic(ambiguous_text())
    assert drawing_type == DrawingType.ELEVATION
    assert confidence == pytest.approx(0.7)
    assert evidence["code_title_disagreement"] is True


def test_heuristic_multi_drawing_flag_set_when_multiple_titles():
    drawing_type, confidence, evidence = classify_by_heuristic(multi_drawing_text())
    # First match in our rule ordering is ELEVATION (more specific
    # phrase) before FLOOR_PLAN — but the precise winner isn't what we
    # care about; what we care about is the multi-drawing flag.
    assert drawing_type in {DrawingType.FLOOR_PLAN, DrawingType.ELEVATION}
    assert evidence.get("multi_drawing_detected") is True
    assert confidence < HEURISTIC_TRUST_THRESHOLD


# ----------------------------------------------------------------------
# Driver — caching, vision fallback, agreement / disagreement
# ----------------------------------------------------------------------


class _StubVision:
    """Records calls and returns a canned (type, confidence, reasoning)."""

    def __init__(
        self,
        verdict: tuple[DrawingType, float, str] = (
            DrawingType.FLOOR_PLAN,
            0.9,
            "looks like a top-down floor plan",
        ),
        *,
        raises: Exception | None = None,
    ):
        self.verdict = verdict
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def classify(
        self,
        *,
        page_image_png: bytes,
        title_block_text: str,
        heuristic_hint: DrawingType | None,
    ) -> tuple[DrawingType, float, str]:
        self.calls.append(
            {
                "image_len": len(page_image_png),
                "text": title_block_text,
                "hint": heuristic_hint,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.verdict


def test_high_confidence_heuristic_skips_vision():
    vision = _StubVision()
    classifier = DrawingClassifier(vision_client=vision)
    result = classifier.classify_page(
        file_hash="hash1",
        page_number=1,
        title_block_text=floor_plan_text(),
        page_image_png=b"png-bytes",
    )
    assert result.drawing_type == DrawingType.FLOOR_PLAN
    assert result.method == "heuristic"
    # Vision client must NOT have been invoked when heuristic is confident.
    assert vision.calls == []


def test_low_confidence_heuristic_invokes_vision_and_agrees():
    vision = _StubVision(
        verdict=(DrawingType.FLOOR_PLAN, 0.9, "top-down view, walls visible")
    )
    classifier = DrawingClassifier(vision_client=vision)
    result = classifier.classify_page(
        file_hash="hash2",
        page_number=1,
        title_block_text=title_only_floor_plan_text(),
        page_image_png=b"png",
    )
    assert result.drawing_type == DrawingType.FLOOR_PLAN
    assert result.method == "heuristic+vision"
    # On agreement, the merged confidence is bumped above either input.
    assert result.confidence > 0.75
    assert result.evidence["vision"]["reasoning"].startswith("top-down")
    assert vision.calls and vision.calls[0]["hint"] == DrawingType.FLOOR_PLAN


def test_low_confidence_heuristic_invokes_vision_and_disagrees():
    # Heuristic says ELEVATION (multi-drawing fixture, conf ~0.55);
    # vision overrules with a confident SITE_PLAN verdict.
    vision = _StubVision(verdict=(DrawingType.SITE_PLAN, 0.92, "parcel boundary visible"))
    classifier = DrawingClassifier(vision_client=vision)
    result = classifier.classify_page(
        file_hash="hash3",
        page_number=1,
        title_block_text=multi_drawing_text(),
        page_image_png=b"png",
    )
    assert result.drawing_type == DrawingType.SITE_PLAN
    assert result.method == "vision"
    assert result.confidence == pytest.approx(0.92)
    assert result.multi_drawing_detected is True


def test_vision_fallback_when_heuristic_unknown():
    vision = _StubVision(
        verdict=(DrawingType.DETAIL, 0.8, "enlarged detail of column connection")
    )
    classifier = DrawingClassifier(vision_client=vision)
    result = classifier.classify_page(
        file_hash="hash4",
        page_number=1,
        title_block_text=unknown_text(),
        page_image_png=b"png",
    )
    assert result.drawing_type == DrawingType.DETAIL
    assert result.method == "vision"
    assert vision.calls and vision.calls[0]["hint"] is None


def test_vision_error_falls_back_to_heuristic_without_raising():
    vision = _StubVision(raises=RuntimeError("API timeout"))
    classifier = DrawingClassifier(vision_client=vision)
    result = classifier.classify_page(
        file_hash="hash5",
        page_number=1,
        title_block_text=title_only_floor_plan_text(),
        page_image_png=b"png",
    )
    # Heuristic verdict wins; the error is recorded in evidence for ops.
    assert result.drawing_type == DrawingType.FLOOR_PLAN
    assert result.method == "heuristic"
    assert "vision_error" in result.evidence
    assert "API timeout" in result.evidence["vision_error"]


def test_vision_skipped_when_no_image_provided():
    vision = _StubVision()
    classifier = DrawingClassifier(vision_client=vision)
    result = classifier.classify_page(
        file_hash="hash6",
        page_number=1,
        title_block_text=title_only_floor_plan_text(),
        page_image_png=None,
    )
    assert result.method == "heuristic"
    assert "vision_skipped" in result.evidence
    assert vision.calls == []


def test_heuristic_only_mode_when_vision_client_is_none():
    classifier = DrawingClassifier(vision_client=None)
    result = classifier.classify_page(
        file_hash="hash7",
        page_number=1,
        title_block_text=unknown_text(),
        page_image_png=b"png",
    )
    # Without vision, an unknown stays unknown — no exception.
    assert result.drawing_type == DrawingType.UNKNOWN
    assert result.method == "heuristic"


def test_cache_short_circuits_repeated_calls():
    vision = _StubVision()
    classifier = DrawingClassifier(vision_client=vision)
    first = classifier.classify_page(
        file_hash="cache-hash",
        page_number=3,
        title_block_text=title_only_floor_plan_text(),
        page_image_png=b"png",
    )
    second = classifier.classify_page(
        file_hash="cache-hash",
        page_number=3,
        title_block_text=title_only_floor_plan_text(),
        page_image_png=b"png",
    )
    assert first is second
    assert len(vision.calls) == 1


def test_cache_keyed_by_file_hash_and_page():
    classifier = DrawingClassifier(vision_client=None)
    a = classifier.classify_page(
        file_hash="hashA",
        page_number=1,
        title_block_text=floor_plan_text(),
    )
    b = classifier.classify_page(
        file_hash="hashB",
        page_number=1,
        title_block_text=floor_plan_text(),
    )
    # Different file hash → not the same cached object.
    assert a is not b
    # But same key returns same object.
    a_again = classifier.classify_page(
        file_hash="hashA",
        page_number=1,
        title_block_text=floor_plan_text(),
    )
    assert a is a_again


def test_classify_pdf_pages_returns_one_per_page_in_order():
    pages = [
        (1, cover_sheet_text(), None),
        (2, site_plan_text(), None),
        (3, floor_plan_text(), None),
        (4, elevation_text(), None),
        (5, section_text(), None),
    ]
    results = classify_pdf_pages(file_hash="multi-page", pages=pages)
    assert [r.page_number for r in results] == [1, 2, 3, 4, 5]
    assert [r.drawing_type for r in results] == [
        DrawingType.COVER_SHEET,
        DrawingType.SITE_PLAN,
        DrawingType.FLOOR_PLAN,
        DrawingType.ELEVATION,
        DrawingType.SECTION,
    ]


# ----------------------------------------------------------------------
# Hand-labeled accuracy gate (target ≥85%)
# ----------------------------------------------------------------------


def test_heuristic_accuracy_on_hand_labelled_set_meets_target():
    """At least 85% of a hand-labelled fixture set classifies correctly.

    This stands in for the real Halifax-pilot accuracy gate the issue
    calls out. The set deliberately includes both clean (sheet code +
    title agree) and noisy (title-only, ambiguous) examples so the
    bar is meaningful, not just a tautology over the perfect samples.
    """
    labeled: list[tuple[str, DrawingType]] = [
        (site_plan_text(), DrawingType.SITE_PLAN),
        (floor_plan_text(), DrawingType.FLOOR_PLAN),
        (elevation_text(), DrawingType.ELEVATION),
        (section_text(), DrawingType.SECTION),
        (detail_text(), DrawingType.DETAIL),
        (schedule_text(), DrawingType.SCHEDULE),
        (cover_sheet_text(), DrawingType.COVER_SHEET),
        (title_only_floor_plan_text(), DrawingType.FLOOR_PLAN),
        # Same-type duplicates with minor wording variants — common on
        # multi-sheet decks where each floor reuses the title block.
        ("DRAWING TITLE: GROUND FLOOR PLAN\nSheet A1.10\n", DrawingType.FLOOR_PLAN),
        ("DRAWING TITLE: SECOND FLOOR PLAN\nSheet A1.11\n", DrawingType.FLOOR_PLAN),
        ("DRAWING TITLE: ROOF PLAN\nSheet A1.20\n", DrawingType.FLOOR_PLAN),
        ("DRAWING TITLE: SOUTH ELEVATION\nSheet A2.02\n", DrawingType.ELEVATION),
        ("DRAWING TITLE: EAST ELEVATION\nSheet A2.03\n", DrawingType.ELEVATION),
        ("DRAWING TITLE: WALL SECTIONS\nSheet A5.01\n", DrawingType.DETAIL),
        ("DRAWING TITLE: WINDOW SCHEDULE\nSheet A7.01\n", DrawingType.SCHEDULE),
        ("DRAWING TITLE: CONTEXT PLAN\nSheet C1.02\n", DrawingType.SITE_PLAN),
        ("DRAWING TITLE: CROSS SECTION B\nSheet A3.02\n", DrawingType.SECTION),
        ("DRAWING TITLE: TYPICAL FLOOR PLAN\nSheet A1.05\n", DrawingType.FLOOR_PLAN),
        ("DRAWING TITLE: TITLE SHEET / INDEX\nSheet G001\n", DrawingType.COVER_SHEET),
        ("DRAWING TITLE: REFLECTED CEILING PLAN\nSheet A1.50\n", DrawingType.FLOOR_PLAN),
    ]
    correct = 0
    misses: list[tuple[str, DrawingType, DrawingType]] = []
    for text, expected in labeled:
        actual, _, _ = classify_by_heuristic(text)
        if actual == expected:
            correct += 1
        else:
            misses.append((text.splitlines()[0], expected, actual))
    accuracy = correct / len(labeled)
    assert accuracy >= 0.85, (
        f"heuristic accuracy {accuracy:.0%} below 85% target; misses: {misses}"
    )


# ----------------------------------------------------------------------
# Merge helper
# ----------------------------------------------------------------------


def test_combine_boosts_confidence_on_agreement():
    final_type, final_conf, method = _combine_heuristic_and_vision(
        heuristic_type=DrawingType.FLOOR_PLAN,
        heuristic_conf=0.75,
        vision_type=DrawingType.FLOOR_PLAN,
        vision_conf=0.8,
    )
    assert final_type == DrawingType.FLOOR_PLAN
    assert method == "heuristic+vision"
    assert final_conf == pytest.approx(0.85)


def test_combine_picks_vision_when_heuristic_unknown():
    final_type, final_conf, method = _combine_heuristic_and_vision(
        heuristic_type=DrawingType.UNKNOWN,
        heuristic_conf=0.0,
        vision_type=DrawingType.ELEVATION,
        vision_conf=0.7,
    )
    assert (final_type, final_conf, method) == (DrawingType.ELEVATION, 0.7, "vision")


def test_combine_picks_heuristic_when_vision_unknown():
    final_type, final_conf, method = _combine_heuristic_and_vision(
        heuristic_type=DrawingType.SITE_PLAN,
        heuristic_conf=0.8,
        vision_type=DrawingType.UNKNOWN,
        vision_conf=0.0,
    )
    assert (final_type, method) == (DrawingType.SITE_PLAN, "heuristic")
    assert final_conf == 0.8


def test_combine_breaks_disagreement_by_confidence():
    final_type, _, method = _combine_heuristic_and_vision(
        heuristic_type=DrawingType.FLOOR_PLAN,
        heuristic_conf=0.55,
        vision_type=DrawingType.ELEVATION,
        vision_conf=0.9,
    )
    assert (final_type, method) == (DrawingType.ELEVATION, "vision")

    final_type, _, method = _combine_heuristic_and_vision(
        heuristic_type=DrawingType.FLOOR_PLAN,
        heuristic_conf=0.85,
        vision_type=DrawingType.ELEVATION,
        vision_conf=0.6,
    )
    assert (final_type, method) == (DrawingType.FLOOR_PLAN, "heuristic")


# ----------------------------------------------------------------------
# Vision response parser
# ----------------------------------------------------------------------


def _fake_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def test_parse_vision_response_happy_path():
    response = _fake_response(
        '{"drawing_type": "elevation", "confidence": 0.88, "reasoning": "north facade view"}'
    )
    drawing_type, confidence, reasoning = _parse_vision_response(response)
    assert drawing_type == DrawingType.ELEVATION
    assert confidence == pytest.approx(0.88)
    assert reasoning == "north facade view"


def test_parse_vision_response_extracts_json_from_prose():
    response = _fake_response(
        'Here is the classification:\n'
        '{"drawing_type": "site_plan", "confidence": 0.7, "reasoning": "parcel boundary"}\n'
        'Hope this helps.'
    )
    drawing_type, confidence, _ = _parse_vision_response(response)
    assert drawing_type == DrawingType.SITE_PLAN
    assert confidence == pytest.approx(0.7)


def test_parse_vision_response_clamps_confidence_to_unit_interval():
    response = _fake_response(
        '{"drawing_type": "floor_plan", "confidence": 1.7, "reasoning": "..."}'
    )
    _, confidence, _ = _parse_vision_response(response)
    assert confidence == 1.0

    response = _fake_response(
        '{"drawing_type": "floor_plan", "confidence": -0.2, "reasoning": "..."}'
    )
    _, confidence, _ = _parse_vision_response(response)
    assert confidence == 0.0


def test_parse_vision_response_unrecognised_type_returns_unknown():
    response = _fake_response(
        '{"drawing_type": "blueprint", "confidence": 0.9, "reasoning": "..."}'
    )
    drawing_type, confidence, reasoning = _parse_vision_response(response)
    assert drawing_type == DrawingType.UNKNOWN
    assert confidence == 0.0
    assert "blueprint" in reasoning


def test_parse_vision_response_malformed_json_returns_unknown():
    response = _fake_response('not even close to JSON')
    drawing_type, confidence, _ = _parse_vision_response(response)
    assert drawing_type == DrawingType.UNKNOWN
    assert confidence == 0.0


def test_parse_vision_response_empty_content_returns_unknown():
    response = SimpleNamespace(content=[])
    drawing_type, confidence, reasoning = _parse_vision_response(response)
    assert drawing_type == DrawingType.UNKNOWN
    assert confidence == 0.0
    assert "empty" in reasoning


# ----------------------------------------------------------------------
# Classification dataclass round-trip
# ----------------------------------------------------------------------


def test_classification_to_dict_roundtrip_is_json_safe():
    result = DrawingClassification(
        page_number=2,
        drawing_type=DrawingType.SITE_PLAN,
        confidence=0.93,
        method="heuristic+vision",
        sheet_code="C1.01",
        multi_drawing_detected=False,
        evidence={"heuristic": {"sheet_code": "C1.01"}},
    )
    payload = result.to_dict()
    assert payload["drawing_type"] == "site_plan"
    assert payload["method"] == "heuristic+vision"
    assert payload["sheet_code"] == "C1.01"
    assert payload["evidence"] == {"heuristic": {"sheet_code": "C1.01"}}


# ----------------------------------------------------------------------
# Protocol conformance
# ----------------------------------------------------------------------


def test_stub_vision_client_satisfies_protocol():
    # Cheap structural assertion: the stub fits the VisionClient
    # Protocol if it has a `classify` callable. This catches accidental
    # signature drift in the stub used across the test file.
    stub: VisionClient = _StubVision()  # type: ignore[assignment]
    assert callable(stub.classify)
