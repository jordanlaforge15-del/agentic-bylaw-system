"""ABS-55: PDF submission extractor tests.

Exercises ``extract_pdf`` against synthetic PDFs built per-test, with
a deterministic stub vision client. The real Anthropic call path is
covered separately by ``test_anthropic_vision_client_*`` (mock SDK
client). Importing ``layer1.parsers.pdf_submission`` registers the
extractor with the submission factory; the registry side-effect is
asserted in ``test_factory_dispatches_pdf_via_registration``.

Design contract this test file enforces:

* Every emitted attribute carries a confidence ∈ [0, 1] AND evidence
  dict including page_number + source_bucket.
* Vision-LLM-emitted keys outside the supported set are dropped with
  a warning, not persisted.
* The vector-vs-raster page provenance is captured in raw_metadata.
* The extractor never raises on a missing field — gaps become
  warnings the confirmation UI surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# Import for the registration side-effect; symbols are used directly.
from layer1.parsers.pdf_submission import (
    AnthropicVisionClient,
    VisionFieldExtraction,
    VisionLLMClient,
    VisionPageContext,
    VisionPageResult,
    _parse_vision_response,
    extract_pdf,
)
from layer1.parsers.submission_factory import extract_submission, get_extractor
from layer1.models.submission_schemas import SubmissionIngestConfig
from layer2.compliance.db.models import (
    SubmissionAttributeSource,
    SubmissionSourceType,
)

from fixtures.submissions.synthetic_pdf import (
    write_mixed_pdf,
    write_raster_pdf,
    write_vector_pdf,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _attr_by_key(result, key: str):
    matches = [a for a in result.attributes if a.attribute_key == key]
    assert matches, (
        f"{key!r} not in extraction result; got "
        f"{[a.attribute_key for a in result.attributes]}"
    )
    assert len(matches) == 1, (
        f"{key!r} appeared {len(matches)} times — aggregator is double-emitting"
    )
    return matches[0]


def _has_attr(result, key: str) -> bool:
    return any(a.attribute_key == key for a in result.attributes)


@pytest.fixture()
def cfg() -> SubmissionIngestConfig:
    return SubmissionIngestConfig(run_evaluator=False)


# ----------------------------------------------------------------------
# Deterministic stub vision client
# ----------------------------------------------------------------------


@dataclass
class _ScriptedVisionClient:
    """Returns a fixed VisionPageResult per page number.

    Tests that need per-page variation define a small dict keyed by
    page_number and the stub returns the matching result. Pages with
    no scripted answer get an empty result (mimics a real LLM that
    found nothing on a page).
    """

    by_page: dict[int, VisionPageResult] = field(default_factory=dict)
    calls: list[VisionPageContext] = field(default_factory=list)

    def analyze_page(self, context: VisionPageContext) -> VisionPageResult:
        self.calls.append(context)
        if context.page_number in self.by_page:
            return self.by_page[context.page_number]
        return VisionPageResult(
            page_number=context.page_number,
            fields=[],
            drawing_type=None,
            warnings=[],
        )


def _field(
    key: str | None,
    value: Any,
    *,
    confidence: float,
    unit: str | None = None,
    bbox: list[float] | None = None,
    ocr_string: str | None = None,
    rationale: str | None = None,
) -> VisionFieldExtraction:
    return VisionFieldExtraction(
        attribute_key=key,
        value=value,
        confidence=confidence,
        unit=unit,
        bbox=bbox,
        ocr_string=ocr_string,
        rationale=rationale,
    )


# ----------------------------------------------------------------------
# Factory registration
# ----------------------------------------------------------------------


def test_factory_dispatches_pdf_via_registration(tmp_path: Path, cfg):
    """Importing the module registers the extractor against PDF source_type.

    The factory dispatch is what makes the scaffold ingest pipeline
    pick up PDFs without an explicit import — same plumbing as the
    IFC and APS paths.
    """
    extractor = get_extractor(SubmissionSourceType.PDF)
    # Registered adapter calls extract_pdf internally; symbol identity
    # check is loose because the adapter is the registered callable.
    assert extractor is not None

    pdf = write_vector_pdf(tmp_path / "ok.pdf")
    result = extract_submission(pdf, SubmissionSourceType.PDF, config=cfg)
    assert result.source_type == SubmissionSourceType.PDF
    assert result.source_artifact_path == str(pdf)


# ----------------------------------------------------------------------
# Default (no vision client configured) path
# ----------------------------------------------------------------------


def test_default_extract_warns_when_no_vision_client(tmp_path: Path, cfg):
    """The default vision client is null — extract_pdf must still run.

    Without a configured vision LLM, we still want the pipeline to
    return a result (so callers can persist the submission row, run
    Docling, etc.). The result should carry a clear warning that
    the LLM was skipped, and no attributes should be emitted.
    """
    pdf = write_vector_pdf(tmp_path / "no-vision.pdf")
    result = extract_pdf(pdf, cfg)

    assert result.attributes == []
    assert any("vision LLM client not configured" in w for w in result.warnings)
    assert result.raw_metadata["extractor"]["vision_client"] == "_NullVisionClient"


# ----------------------------------------------------------------------
# Vector PDF — title-block + dimension fields
# ----------------------------------------------------------------------


def test_vector_pdf_extracts_title_block_and_setback(tmp_path: Path, cfg):
    pdf = write_vector_pdf(
        tmp_path / "vec.pdf",
        title_text="ARCH-001 Site Plan",
        dimension_text="FRONT SETBACK 7.5 m",
    )

    stub = _ScriptedVisionClient(
        by_page={
            1: VisionPageResult(
                page_number=1,
                drawing_type="site_plan",
                fields=[
                    _field(
                        "primary_use_class",
                        "residential",
                        confidence=0.9,
                        ocr_string="RESIDENTIAL",
                        rationale="title-block 'USE' row",
                    ),
                    _field(
                        "front_setback_m",
                        7.5,
                        confidence=0.7,
                        unit="m",
                        bbox=[50.0, 60.0, 250.0, 95.0],
                        ocr_string="7.5 m",
                        rationale="dimension string adjacent to front lot line",
                    ),
                ],
                warnings=[],
            )
        }
    )

    result = extract_pdf(pdf, cfg, vision_client=stub)

    use = _attr_by_key(result, "primary_use_class")
    assert use.value == "residential"
    assert use.confidence == pytest.approx(0.9)
    assert use.evidence["source_bucket"] == "title_block"
    assert use.evidence["page_number"] == 1
    assert use.evidence["drawing_type"] == "site_plan"
    assert use.source == SubmissionAttributeSource.EXTRACTED

    setback = _attr_by_key(result, "front_setback_m")
    assert setback.value == pytest.approx(7.5)
    assert setback.unit == "m"
    assert setback.confidence == pytest.approx(0.7)
    assert setback.evidence["source_bucket"] == "drawing_annotation"
    assert setback.evidence["ocr_string"] == "7.5 m"
    assert setback.evidence["bbox"] == [50.0, 60.0, 250.0, 95.0]

    # Per-page provenance
    summaries = result.raw_metadata["extractor"]["per_page"]
    assert summaries[0]["is_vector_text"] is True
    assert summaries[0]["drawing_type"] == "site_plan"
    assert summaries[0]["n_vision_fields"] == 2


# ----------------------------------------------------------------------
# Confidence-based aggregation across pages
# ----------------------------------------------------------------------


def test_multiple_pages_choose_highest_confidence_candidate(tmp_path: Path, cfg):
    """When the LLM emits the same attribute on two pages, keep the
    highest-confidence one and surface the loser on
    ``evidence.other_candidates``. This is the seam the confirmation
    UI uses to let a reviewer flip the choice without re-running
    extraction.
    """
    pdf = write_mixed_pdf(tmp_path / "mixed.pdf")

    stub = _ScriptedVisionClient(
        by_page={
            1: VisionPageResult(
                page_number=1,
                drawing_type="site_plan",
                fields=[
                    _field(
                        "front_setback_m",
                        7.5,
                        confidence=0.55,
                        unit="m",
                        ocr_string="7.5 m",
                        rationale="approximate read from page 1",
                    ),
                ],
            ),
            2: VisionPageResult(
                page_number=2,
                drawing_type="site_plan",
                fields=[
                    _field(
                        "front_setback_m",
                        7.2,
                        confidence=0.85,
                        unit="m",
                        ocr_string="7200 mm",
                        rationale="clear dimension line on page 2",
                    ),
                ],
            ),
        }
    )

    result = extract_pdf(pdf, cfg, vision_client=stub)

    setback = _attr_by_key(result, "front_setback_m")
    assert setback.value == pytest.approx(7.2)
    assert setback.confidence == pytest.approx(0.85)
    assert setback.evidence["page_number"] == 2
    others = setback.evidence["other_candidates"]
    assert len(others) == 1
    assert others[0]["page_number"] == 1
    assert others[0]["value"] == pytest.approx(7.5)


# ----------------------------------------------------------------------
# Raster PDF — provenance should mark page as non-vector
# ----------------------------------------------------------------------


def test_raster_pdf_marks_page_as_non_vector(tmp_path: Path, cfg):
    """The vector-detection branch is what tells the confirmation UI
    whether to trust the extracted candidates or to bias toward more
    aggressive manual review. A scan-only page must come back with
    ``is_vector_text=False`` regardless of what the LLM emits.
    """
    pdf = write_raster_pdf(tmp_path / "scan.pdf")

    stub = _ScriptedVisionClient(
        by_page={
            1: VisionPageResult(
                page_number=1,
                drawing_type="floor_plan",
                fields=[
                    _field(
                        "building_height_m",
                        9.0,
                        confidence=0.55,
                        unit="m",
                        rationale="approximate read off blurry scan",
                    ),
                ],
            )
        }
    )

    result = extract_pdf(pdf, cfg, vision_client=stub)

    summaries = result.raw_metadata["extractor"]["per_page"]
    assert summaries[0]["is_vector_text"] is False
    height = _attr_by_key(result, "building_height_m")
    assert height.evidence["is_vector_page"] is False
    # Scan-extracted fields should bear lower confidence per the issue's
    # accuracy expectations; the extractor doesn't override what the LLM
    # says but we assert the band the LLM was meant to produce.
    assert height.confidence < 0.7


# ----------------------------------------------------------------------
# Mixed PDF — per-page provenance survives aggregation
# ----------------------------------------------------------------------


def test_mixed_pdf_records_per_page_provenance(tmp_path: Path, cfg):
    pdf = write_mixed_pdf(tmp_path / "mix.pdf")

    stub = _ScriptedVisionClient(
        by_page={
            1: VisionPageResult(
                page_number=1,
                drawing_type="site_plan",
                fields=[_field("rear_setback_m", 3.0, confidence=0.8, unit="m")],
            ),
            2: VisionPageResult(
                page_number=2,
                drawing_type="floor_plan",
                fields=[_field(
                    "residential_unit_count", 12, confidence=0.7
                )],
            ),
        }
    )

    result = extract_pdf(pdf, cfg, vision_client=stub)

    summaries = result.raw_metadata["extractor"]["per_page"]
    assert len(summaries) == 2
    assert summaries[0]["is_vector_text"] is True
    assert summaries[1]["is_vector_text"] is False

    rs = _attr_by_key(result, "rear_setback_m")
    assert rs.evidence["page_number"] == 1
    assert rs.evidence["is_vector_page"] is True

    units = _attr_by_key(result, "residential_unit_count")
    assert units.evidence["page_number"] == 2
    assert units.evidence["is_vector_page"] is False


# ----------------------------------------------------------------------
# Confidence + evidence schema contract
# ----------------------------------------------------------------------


def test_every_attribute_has_confidence_in_unit_interval(tmp_path: Path, cfg):
    """The whole design centres on candidates-with-confidence. This is
    the contract test that confirms it for every attribute the
    aggregator emits — no -1, no >1, no NaN.
    """
    pdf = write_vector_pdf(tmp_path / "conf.pdf")

    stub = _ScriptedVisionClient(
        by_page={
            1: VisionPageResult(
                page_number=1,
                drawing_type="site_plan",
                fields=[
                    _field("primary_use_class", "residential", confidence=0.9),
                    _field("residential_unit_count", 24, confidence=0.85),
                    _field("front_setback_m", 6.0, confidence=0.6, unit="m"),
                    _field("parking_stalls_count", 30, confidence=0.75),
                ],
            )
        }
    )
    result = extract_pdf(pdf, cfg, vision_client=stub)

    assert len(result.attributes) == 4
    for attr in result.attributes:
        assert 0.0 <= attr.confidence <= 1.0
        assert "page_number" in attr.evidence
        assert "source_bucket" in attr.evidence


# ----------------------------------------------------------------------
# Unsupported taxonomy keys are dropped with a warning
# ----------------------------------------------------------------------


def test_unsupported_attribute_keys_are_dropped_with_warning(tmp_path: Path, cfg):
    """The LLM is instructed to use a fixed key vocabulary, but real
    models occasionally invent keys. Anything outside the local
    allow-list must be dropped with a clear warning rather than
    silently persisted — otherwise the downstream taxonomy filter
    would re-drop it without provenance.
    """
    pdf = write_vector_pdf(tmp_path / "drop.pdf")
    stub = _ScriptedVisionClient(
        by_page={
            1: VisionPageResult(
                page_number=1,
                drawing_type="site_plan",
                fields=[
                    _field("front_setback_m", 5.0, confidence=0.7, unit="m"),
                    _field("totally_made_up_attr", "nope", confidence=0.9),
                    _field(None, "context-only", confidence=0.9),
                ],
            )
        }
    )

    result = extract_pdf(pdf, cfg, vision_client=stub)
    assert _has_attr(result, "front_setback_m")
    assert not _has_attr(result, "totally_made_up_attr")
    assert any(
        "unsupported attribute_key" in w and "totally_made_up_attr" in w
        for w in result.warnings
    )


# ----------------------------------------------------------------------
# Vision-LLM failure is non-fatal
# ----------------------------------------------------------------------


class _RaisingVisionClient:
    """Always raises — exercises the per-page exception guard."""

    def analyze_page(self, context: VisionPageContext) -> VisionPageResult:
        raise RuntimeError("simulated provider outage")


def test_vision_llm_exception_is_caught_per_page(tmp_path: Path, cfg):
    pdf = write_mixed_pdf(tmp_path / "boom.pdf")
    result = extract_pdf(pdf, cfg, vision_client=_RaisingVisionClient())
    assert result.attributes == []
    assert any(
        "vision LLM failed on page 1" in w for w in result.warnings
    )
    assert any(
        "vision LLM failed on page 2" in w for w in result.warnings
    )


# ----------------------------------------------------------------------
# Site-plan geometry — best-effort placeholder
# ----------------------------------------------------------------------


def test_site_plan_geometry_warns_when_not_yet_implemented(tmp_path: Path, cfg):
    """ABS-55 ships the scaffold; the vector-polygon extraction is an
    opt-in follow-up. The result must surface a clear warning so the
    confirmation UI knows to prompt for a manual outline rather than
    quietly emit setbacks based on a polygon that was never extracted.
    """
    pdf = write_vector_pdf(tmp_path / "site.pdf")
    stub = _ScriptedVisionClient(
        by_page={1: VisionPageResult(page_number=1, fields=[])}
    )
    result = extract_pdf(pdf, cfg, vision_client=stub)
    assert result.footprint_geojson is None
    assert any(
        "site-plan polygon vector extraction not yet implemented" in w
        for w in result.warnings
    )


# ----------------------------------------------------------------------
# Vision response parsing — round-trips + fence stripping + bad JSON
# ----------------------------------------------------------------------


def test_parse_vision_response_strips_markdown_fence():
    raw = (
        "```json\n"
        '{"drawing_type": "site_plan", "fields": ['
        '{"attribute_key": "front_setback_m", "value": 7.5, '
        '"unit": "m", "confidence": 0.7}]}\n'
        "```"
    )
    result = _parse_vision_response(
        page_number=1, raw_response=raw, drawing_type_hint=None
    )
    assert result.drawing_type == "site_plan"
    assert len(result.fields) == 1
    assert result.fields[0].attribute_key == "front_setback_m"
    assert result.fields[0].value == 7.5
    assert result.fields[0].confidence == pytest.approx(0.7)


def test_parse_vision_response_clamps_confidence_into_unit_interval():
    raw = (
        '{"drawing_type": null, "fields": ['
        '{"attribute_key": "primary_use_class", "value": "residential", '
        '"confidence": 1.7},'
        '{"attribute_key": "rear_setback_m", "value": 3, '
        '"confidence": -0.5}]}'
    )
    result = _parse_vision_response(
        page_number=1, raw_response=raw, drawing_type_hint="floor_plan"
    )
    assert result.fields[0].confidence == 1.0
    assert result.fields[1].confidence == 0.0
    # When the LLM emits drawing_type: null, fall back to the caller's hint.
    assert result.drawing_type == "floor_plan"


def test_parse_vision_response_handles_garbage():
    result = _parse_vision_response(
        page_number=2, raw_response="not json at all", drawing_type_hint=None
    )
    assert result.fields == []
    assert any("was not JSON" in w for w in result.warnings)


def test_parse_vision_response_handles_empty():
    result = _parse_vision_response(
        page_number=3, raw_response="", drawing_type_hint=None
    )
    assert result.fields == []
    assert any("empty response" in w for w in result.warnings)


# ----------------------------------------------------------------------
# AnthropicVisionClient — mock SDK, verify it wires through _parse_vision_response
# ----------------------------------------------------------------------


class _FakeAnthropicMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeAnthropicMessagesAPI:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeAnthropicMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text: str) -> None:
        self.messages = _FakeAnthropicMessagesAPI(response_text)


def test_anthropic_vision_client_sends_image_and_parses_response():
    """Mock the Anthropic SDK and verify the client (a) ships the
    base64-encoded image, (b) parses the JSON the model returned, and
    (c) returns a VisionPageResult shaped the same as the stub does
    for the rest of the tests.
    """
    fake_response = (
        '{"drawing_type": "elevation", "fields": ['
        '{"attribute_key": "building_height_m", "value": 12, '
        '"unit": "m", "confidence": 0.8}]}'
    )
    fake_client = _FakeAnthropicClient(fake_response)
    client = AnthropicVisionClient(
        api_key="unused-in-test",
        model="claude-sonnet-4-6",
        client=fake_client,
    )
    ctx = VisionPageContext(
        page_number=1,
        page_image_png=b"\x89PNG\r\n\x1a\n-fake-",
        text_blocks=[{"text": "Title", "bbox": [0, 0, 10, 10]}],
        drawing_type_hint=None,
        page_width_pts=842.0,
        page_height_pts=595.0,
    )
    result = client.analyze_page(ctx)

    assert result.drawing_type == "elevation"
    assert len(result.fields) == 1
    assert result.fields[0].attribute_key == "building_height_m"
    assert result.fields[0].value == 12

    sent = fake_client.messages.last_kwargs
    assert sent["model"] == "claude-sonnet-4-6"
    user_msg = sent["messages"][0]
    assert user_msg["role"] == "user"
    image_block = user_msg["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    # Bytes are base64-encoded — non-empty and ASCII-safe.
    assert image_block["source"]["data"]
    assert image_block["source"]["data"].isascii()


# ----------------------------------------------------------------------
# Max-pages clamp
# ----------------------------------------------------------------------


def test_max_pages_clamp_warns_and_stops(tmp_path: Path, cfg):
    """Long PDFs can blow the LLM token budget; ``max_pages`` lets the
    caller bound the work. The extractor must stop after N pages AND
    leave a warning so the operator knows the result is partial.
    """
    pdf = write_mixed_pdf(tmp_path / "trunc.pdf")  # 2 pages
    stub = _ScriptedVisionClient(
        by_page={
            1: VisionPageResult(
                page_number=1,
                drawing_type="site_plan",
                fields=[_field("rear_setback_m", 3.0, confidence=0.8, unit="m")],
            )
        }
    )
    result = extract_pdf(pdf, cfg, vision_client=stub, max_pages=1)
    assert len(stub.calls) == 1
    assert any("max_pages clamp" in w for w in result.warnings)
    assert _has_attr(result, "rear_setback_m")
