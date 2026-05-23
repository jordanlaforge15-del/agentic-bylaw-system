"""PDF → Phase-1 attribute extraction (ABS-55).

Stand up the candidate-with-confidence pipeline for architectural-drawing
PDFs. Where the IFC path (ABS-49) reads structured properties and emits
high-confidence facts, the PDF path interprets a *drawing* — title
block, dimension strings, callouts — and so every output carries a
confidence ∈ [0, 1] plus the evidence (page, bbox, OCR string, model
rationale) a human reviewer needs to confirm or override.

Stack
-----
* **Docling** (already used for bylaw ingest in
  ``layer1.parsers.factory``) for layout-aware text extraction with
  bounding boxes and table detection. Handles both raster + vector PDFs.
* **PyMuPDF (fitz)** to render each page to a high-res raster the
  vision LLM can read, and to inspect vector content (CAD-like
  polylines, font metadata, scale-bar text) on vector-native PDFs.
* **Vision LLM** (Claude Sonnet 4.6 with image input) to interpret the
  rasterised page + Docling's text blocks and emit a structured
  candidate set: title-block fields, dimension strings paired to
  taxonomy attributes, callouts.

The vision LLM is reached through a small ``VisionLLMClient`` Protocol
defined in this module rather than through ``advisor.llm.LLMGateway``,
because the unified gateway does not yet carry an ``ImageBlock`` type
(documented TODO in ``advisor.llm.anthropic_backend``). Wrapping the
Anthropic Messages API directly keeps this issue from blocking on a
gateway refactor — once ImageBlock lands, swap the default client for
a thin LLMGateway adapter.

Confidence policy
-----------------
| level     | meaning                                                          |
| --------- | ---------------------------------------------------------------- |
| 0.85-0.95 | Vision LLM read a labelled field off the title block directly.   |
| 0.65-0.85 | Vision LLM paired a dimension string to a boundary segment.      |
| 0.40-0.65 | Heuristic / weak signal (e.g. scale-bar inferred unit).          |

Concrete numbers always come from the vision LLM's own per-field
confidence — we don't override them. The bands above are advisory.
Setbacks specifically come in lower-confidence than categorical fields
because pairing a dimension string to a *specific* boundary segment is
the documented weak point of the approach (per the issue feasibility
research).

Scope (deliberately bounded; ABS-56 / ABS-57 / ABS-58 land the rest)
* In: title-block field extraction, dimension → setback / GFA /
  footprint-area / height pairing, drawing-type-hint dispatch, vector
  PDF analysis for CAD layer info, raster rendering for vision.
* Out: human-confirmation UI (separate issue), accuracy validation
  against ground truth (separate issue), heavy OCR pipelines for
  scan-only PDFs lacking any text layer (Docling fallback only).

Plugs into the submission factory: importing this module registers the
PDF extractor for ``SubmissionSourceType.PDF``. The mock vision client
defined here is the production default — wire a real
``AnthropicVisionClient`` via ``set_default_vision_client`` from the
advisor bootstrap when an API key is configured.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionExtractionResult,
    SubmissionIngestConfig,
)
from layer1.parsers.submission_factory import register_extractor
from layer2.compliance.db.models import (
    SubmissionAttributeSource,
    SubmissionSourceType,
)


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Vision LLM client — Protocol + Anthropic implementation + null default
# ----------------------------------------------------------------------


@dataclass
class VisionPageContext:
    """Per-page payload handed to the vision LLM.

    The vision LLM sees a rasterised page image plus the Docling text
    spans (so it can ground its reading on the OCR text rather than
    hallucinating). ``drawing_type_hint`` comes from the drawing
    classifier (separate issue); ``None`` means "unknown" and the LLM
    falls back to a general drawing-interpretation prompt.
    """

    page_number: int
    page_image_png: bytes
    text_blocks: list[dict[str, Any]]
    drawing_type_hint: str | None = None
    page_width_pts: float = 0.0
    page_height_pts: float = 0.0


@dataclass
class VisionFieldExtraction:
    """One field the vision LLM lifted off a drawing page.

    The vision LLM is asked to emit a candidate set per page; this
    record is one element of that set. ``attribute_key`` is the
    Phase-1 taxonomy id (or None for context-only fields like
    ``project_name`` that don't map to taxonomy yet). ``bbox`` is
    optional — the LLM doesn't always localise a field on the page.
    """

    attribute_key: str | None
    value: Any
    confidence: float
    unit: str | None = None
    bbox: list[float] | None = None
    ocr_string: str | None = None
    rationale: str | None = None
    raw_label: str | None = None


@dataclass
class VisionPageResult:
    """Full vision-LLM output for one page."""

    page_number: int
    fields: list[VisionFieldExtraction]
    drawing_type: str | None = None
    raw_response: str | None = None
    warnings: list[str] = field(default_factory=list)


class VisionLLMClient(Protocol):
    """Provider-agnostic vision-LLM surface used by the PDF extractor.

    Production wires an Anthropic-backed implementation; tests inject a
    deterministic stub. Kept as a Protocol so we can swap to OpenAI / a
    local VLM without touching the extractor.
    """

    def analyze_page(self, context: VisionPageContext) -> VisionPageResult: ...


class _NullVisionClient:
    """Default client used when no real vision LLM is configured.

    Emits an empty candidate set + a warning, so the rest of the
    pipeline still runs (Docling text blocks + footprint polygon
    extraction still produce useful diagnostics) and the operator sees
    a clear "vision not configured" signal in the result warnings.
    """

    def analyze_page(self, context: VisionPageContext) -> VisionPageResult:
        return VisionPageResult(
            page_number=context.page_number,
            fields=[],
            drawing_type=context.drawing_type_hint,
            raw_response=None,
            warnings=[
                "vision LLM client not configured — page interpreted "
                "from Docling text only; configure an AnthropicVisionClient "
                "or pass vision_client=... to extract_pdf"
            ],
        )


_DEFAULT_VISION_CLIENT: VisionLLMClient = _NullVisionClient()


def set_default_vision_client(client: VisionLLMClient) -> None:
    """Replace the process-global default vision client.

    Called from the advisor bootstrap when an Anthropic API key is
    configured. Tests don't use this — they pass ``vision_client=`` to
    ``extract_pdf`` directly to avoid leaking state across tests.
    """
    global _DEFAULT_VISION_CLIENT
    _DEFAULT_VISION_CLIENT = client


def get_default_vision_client() -> VisionLLMClient:
    return _DEFAULT_VISION_CLIENT


# ----------------------------------------------------------------------
# Public entry point — registered with the submission factory
# ----------------------------------------------------------------------


def extract_pdf(
    source_path: Path,
    config: SubmissionIngestConfig,
    *,
    vision_client: VisionLLMClient | None = None,
    raster_dpi: int = 200,
    max_pages: int | None = None,
) -> SubmissionExtractionResult:
    """Extract Phase-1 candidate attributes from an architectural-drawing PDF.

    The pipeline runs per-page:

    1. Docling produces text spans with bounding boxes.
    2. PyMuPDF renders the page to a PNG at ``raster_dpi``.
    3. The vision LLM interprets the page (title block, dimensions,
       callouts) and emits ``VisionFieldExtraction`` candidates.
    4. We aggregate candidates across pages, choose the best one per
       taxonomy key, attach evidence, and return.

    Heavy dependencies (``docling``, ``fitz``) are imported lazily so
    the layer-1 import graph stays light for non-PDF callers.

    Returns a ``SubmissionExtractionResult`` — soft-fails on missing
    fields (warning + attribute omitted), hard-fails (raises) only when
    the file cannot be opened at all.
    """
    if vision_client is None:
        vision_client = _DEFAULT_VISION_CLIENT

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover — install-time error
        raise RuntimeError(
            "PyMuPDF is required to ingest PDF submissions. "
            "Install with `.venv/bin/pip install -e '.[ingest]'`."
        ) from exc

    try:
        doc = fitz.open(str(source_path))
    except Exception as exc:
        raise RuntimeError(f"failed to open PDF {source_path}: {exc}") from exc

    state = _ExtractionState()

    docling_pages = _safe_docling_pages(source_path, state)

    try:
        page_count = doc.page_count
        for page_index in range(page_count):
            if max_pages is not None and page_index >= max_pages:
                state.warnings.append(
                    f"PDF has {page_count} pages; only the first {max_pages} "
                    "were processed (max_pages clamp)."
                )
                break
            page = doc.load_page(page_index)
            page_number = page_index + 1
            page_text_blocks = docling_pages.get(page_number, [])
            is_vector = _page_has_vector_text(page)
            page_image_png = _render_page_png(page, dpi=raster_dpi)

            ctx = VisionPageContext(
                page_number=page_number,
                page_image_png=page_image_png,
                text_blocks=page_text_blocks,
                drawing_type_hint=None,
                page_width_pts=float(page.rect.width),
                page_height_pts=float(page.rect.height),
            )
            try:
                page_result = vision_client.analyze_page(ctx)
            except Exception as exc:  # noqa: BLE001 — vision LLM is best-effort
                logger.warning(
                    "vision LLM failed on page %s of %s: %s",
                    page_number,
                    source_path,
                    exc,
                )
                state.warnings.append(
                    f"vision LLM failed on page {page_number}: {exc}"
                )
                continue

            state.warnings.extend(page_result.warnings)
            state.page_summaries.append(
                {
                    "page_number": page_number,
                    "is_vector_text": is_vector,
                    "drawing_type": page_result.drawing_type,
                    "n_text_blocks": len(page_text_blocks),
                    "n_vision_fields": len(page_result.fields),
                }
            )
            for f in page_result.fields:
                state.candidates.append(
                    _Candidate(
                        page_number=page_number,
                        drawing_type=page_result.drawing_type,
                        is_vector_page=is_vector,
                        field=f,
                    )
                )

        site_plan_geo = _extract_site_plan_geometry(doc, state)
    finally:
        doc.close()

    attributes = _aggregate_candidates_to_attributes(state)

    raw_metadata = {
        "extractor": {
            "name": "pdf-submission",
            "raster_dpi": raster_dpi,
            "page_count": page_count,
            "pages_processed": len(state.page_summaries),
            "per_page": state.page_summaries,
            "vision_client": type(vision_client).__name__,
        }
    }

    return SubmissionExtractionResult(
        source_type=SubmissionSourceType.PDF,
        source_artifact_path=str(source_path),
        attributes=attributes,
        footprint_geojson=site_plan_geo,
        raw_metadata=raw_metadata,
        warnings=state.warnings,
    )


# ----------------------------------------------------------------------
# Internal state
# ----------------------------------------------------------------------


@dataclass
class _Candidate:
    """A vision-LLM-emitted field paired with the page it came from.

    Aggregation picks the highest-confidence candidate per taxonomy
    key; the rest are surfaced on ``evidence.other_candidates`` so the
    confirmation UI can show alternatives.
    """

    page_number: int
    drawing_type: str | None
    is_vector_page: bool
    field: VisionFieldExtraction


@dataclass
class _ExtractionState:
    candidates: list[_Candidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page_summaries: list[dict[str, Any]] = field(default_factory=list)


# ----------------------------------------------------------------------
# Docling integration (text + bboxes per page)
# ----------------------------------------------------------------------


def _safe_docling_pages(
    source_path: Path, state: _ExtractionState
) -> dict[int, list[dict[str, Any]]]:
    """Run Docling on the PDF and return text blocks keyed by page number.

    Docling failures are non-fatal: we warn and fall back to PyMuPDF's
    raw text extraction so the vision LLM at least sees *some* OCR text
    context. Same fallback philosophy as the bylaw-side parser.
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter
    except ImportError:
        state.warnings.append(
            "docling not installed — falling back to PyMuPDF text extraction "
            "for vision-LLM grounding."
        )
        return _pymupdf_text_pages(source_path, state)

    try:
        converter = DocumentConverter()
        result = converter.convert(str(source_path))
    except Exception as exc:  # noqa: BLE001
        state.warnings.append(
            f"docling conversion failed ({exc}); falling back to PyMuPDF text."
        )
        return _pymupdf_text_pages(source_path, state)

    document = result.document
    pages: dict[int, list[dict[str, Any]]] = {}
    try:
        page_numbers = sorted(document.pages.keys())
    except Exception:
        page_numbers = []
    for page_number in page_numbers:
        page = document.pages[page_number]
        page_height = float(getattr(page.size, "height", 0.0) or 0.0)
        blocks: list[dict[str, Any]] = []
        try:
            iterator = document.iterate_items(page_no=page_number, with_groups=False)
        except TypeError:
            # Older docling-core signatures lacked the page_no kwarg.
            iterator = document.iterate_items()
        for item_pack in iterator:
            item = item_pack[0] if isinstance(item_pack, tuple) else item_pack
            text = getattr(item, "text", None) or ""
            if not text or not text.strip():
                continue
            bbox = _docling_bbox(item, page_height)
            blocks.append(
                {
                    "text": text.strip(),
                    "bbox": bbox,
                    "label": getattr(
                        getattr(item, "label", None), "value", None
                    ),
                }
            )
        pages[page_number] = blocks
    if not pages:
        # Docling produced nothing usable; fall back.
        return _pymupdf_text_pages(source_path, state)
    return pages


def _docling_bbox(item: Any, page_height: float) -> list[float] | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    try:
        first = prov[0]
        bbox = first.bbox
        x0 = float(bbox.l)
        x1 = float(bbox.r)
        # Docling reports coords either top-left or bottom-left; normalise
        # to a top-left-origin tuple so vision-LLM rationales reference
        # the same coordinate system as PyMuPDF's page render.
        try:
            from docling_core.types.doc.base import CoordOrigin

            origin = bbox.coord_origin
            if origin == CoordOrigin.BOTTOMLEFT:
                y0 = page_height - float(bbox.t)
                y1 = page_height - float(bbox.b)
            else:
                y0 = float(bbox.t)
                y1 = float(bbox.b)
        except Exception:
            y0 = float(getattr(bbox, "t", 0.0))
            y1 = float(getattr(bbox, "b", 0.0))
        return [x0, min(y0, y1), x1, max(y0, y1)]
    except Exception:
        return None


def _pymupdf_text_pages(
    source_path: Path, state: _ExtractionState
) -> dict[int, list[dict[str, Any]]]:
    """Minimal text-block extraction via PyMuPDF.

    Used as a fallback when Docling can't be imported / fails. Loses
    layout-aware features (table detection, label tagging) but at least
    feeds the vision LLM with the OCR strings that *are* present.
    """
    import fitz

    pages: dict[int, list[dict[str, Any]]] = {}
    with fitz.open(str(source_path)) as doc:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            blocks: list[dict[str, Any]] = []
            for raw in page.get_text("blocks") or []:
                x0, y0, x1, y1, text, *_ = raw
                if not text or not text.strip():
                    continue
                blocks.append(
                    {
                        "text": text.strip(),
                        "bbox": [float(x0), float(y0), float(x1), float(y1)],
                        "label": None,
                    }
                )
            pages[page_index + 1] = blocks
    return pages


# ----------------------------------------------------------------------
# PyMuPDF helpers — raster rendering + vector detection
# ----------------------------------------------------------------------


def _render_page_png(page: Any, *, dpi: int) -> bytes:
    """Rasterise a PDF page to PNG bytes at ``dpi``.

    Renders into a PyMuPDF pixmap and serialises straight to PNG. The
    DPI default (200) is the documented sweet spot for vision LLMs:
    high enough that dimension strings read cleanly, low enough that
    the image payload stays under provider size limits.
    """
    import fitz

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    return pixmap.tobytes("png")


def _page_has_vector_text(page: Any) -> bool:
    """Detect whether a page has any selectable (vector) text.

    Used to label per-page provenance ("this was a vector page vs. a
    raster scan") for the audit trail. Doesn't affect extraction logic
    — the vision LLM sees both kinds — but downstream accuracy review
    needs the distinction.
    """
    try:
        text = page.get_text("text") or ""
    except Exception:
        return False
    return bool(text.strip())


# ----------------------------------------------------------------------
# Site-plan geometry — best-effort vector extraction for ABS-51 hand-off
# ----------------------------------------------------------------------


def _extract_site_plan_geometry(
    doc: Any, state: _ExtractionState
) -> dict[str, Any] | None:
    """Best-effort site-plan footprint polygon for ABS-51 setbacks.

    Strategy: scan vector pages for closed polylines on layers
    suggesting "building" / "footprint" / "outline". This is a
    deliberately weak heuristic — PDFs rarely tag CAD layers in a
    machine-readable way, and the vision LLM is the primary path for
    setback extraction. We surface a warning when no polygon is found
    so the confirmation UI knows to prompt for a manual outline.

    Returns GeoJSON or None. The CRS is "page-coordinates" — ABS-52 /
    ABS-51 don't need this geometry to be georeferenced, only to be a
    closed ring the user can drag/confirm in the UI.
    """
    # The scaffold ships the warning + None path; concrete polyline
    # extraction is an opt-in follow-up (the vision LLM's dimension
    # candidates supersede polyline guesses for most projects).
    state.warnings.append(
        "site-plan polygon vector extraction not yet implemented for PDF "
        "submissions — confirmation UI should prompt for a manual outline "
        "when setbacks are present in dimension candidates."
    )
    return None


# ----------------------------------------------------------------------
# Candidate aggregation → ExtractedAttribute
# ----------------------------------------------------------------------


# Taxonomy keys the PDF extractor currently emits. Vision-LLM-supplied
# ``attribute_key`` values outside this set are dropped with a warning
# (the pipeline's taxonomy normalisation does this anyway, but a local
# allow-list here keeps the per-attribute shaping logic honest).
_SUPPORTED_TAXONOMY_KEYS: frozenset[str] = frozenset({
    "building_height_m",
    "building_height_storeys",
    "gross_floor_area_m2",
    "building_footprint_area_m2",
    "front_setback_m",
    "rear_setback_m",
    "side_setback_left_m",
    "side_setback_right_m",
    "primary_use_class",
    "residential_unit_count",
    "parking_stalls_count",
    "bicycle_stalls_count",
    "loading_bays_count",
    "occupancy_type",
    "construction_type",
})


# Attribute keys this issue surfaces from title-block fields (not
# dimension strings). Keeping the buckets separate lets us tag the
# evidence with the right ``source_field`` so reviewers know which
# part of the drawing each candidate came from.
_TITLE_BLOCK_KEYS: frozenset[str] = frozenset({
    "primary_use_class",
    "occupancy_type",
    "construction_type",
})


def _aggregate_candidates_to_attributes(
    state: _ExtractionState,
) -> list[ExtractedAttribute]:
    """Collapse per-page candidates into one ExtractedAttribute per key.

    Tie-break: highest vision-LLM confidence wins. Lower-ranked
    candidates are kept on ``evidence.other_candidates`` so the
    confirmation UI can show "the LLM also considered 7.2m from page 2"
    and let the human flip the choice.
    """
    by_key: dict[str, list[_Candidate]] = {}
    unsupported: dict[str, int] = {}
    for cand in state.candidates:
        key = cand.field.attribute_key
        if key is None:
            continue
        if key not in _SUPPORTED_TAXONOMY_KEYS:
            unsupported[key] = unsupported.get(key, 0) + 1
            continue
        by_key.setdefault(key, []).append(cand)

    if unsupported:
        state.warnings.append(
            "vision LLM emitted unsupported attribute_key values "
            f"(dropped): {sorted(unsupported.items())}"
        )

    out: list[ExtractedAttribute] = []
    for key, cands in by_key.items():
        cands.sort(key=lambda c: c.field.confidence, reverse=True)
        primary = cands[0]
        alternatives = [_candidate_dict(c) for c in cands[1:]]

        bucket = "title_block" if key in _TITLE_BLOCK_KEYS else "drawing_annotation"
        evidence: dict[str, Any] = {
            "page_number": primary.page_number,
            "drawing_type": primary.drawing_type,
            "source_bucket": bucket,
            "is_vector_page": primary.is_vector_page,
            "bbox": primary.field.bbox,
            "ocr_string": primary.field.ocr_string,
            "rationale": primary.field.rationale,
            "raw_label": primary.field.raw_label,
        }
        if alternatives:
            evidence["other_candidates"] = alternatives

        out.append(
            ExtractedAttribute(
                attribute_key=key,
                value=primary.field.value,
                unit=primary.field.unit,
                confidence=primary.field.confidence,
                source=SubmissionAttributeSource.EXTRACTED,
                evidence=evidence,
            )
        )

    out.sort(key=lambda a: a.attribute_key)
    return out


def _candidate_dict(c: _Candidate) -> dict[str, Any]:
    return {
        "page_number": c.page_number,
        "drawing_type": c.drawing_type,
        "value": c.field.value,
        "unit": c.field.unit,
        "confidence": c.field.confidence,
        "bbox": c.field.bbox,
        "ocr_string": c.field.ocr_string,
        "rationale": c.field.rationale,
    }


# ----------------------------------------------------------------------
# Anthropic-backed default vision client (lazy import)
# ----------------------------------------------------------------------


_VISION_SYSTEM_PROMPT = (
    "You are an architectural drawing reader. You will receive one "
    "rasterised page of an architectural submission PDF plus the OCR "
    "text Docling lifted from the same page. Your job is to identify "
    "candidate Phase-1 zoning attributes — title-block fields and "
    "dimension strings — and emit them as a structured JSON object.\n\n"
    "Per the system design you are emitting *candidates with confidence* "
    "that a human will confirm before any evaluator runs. Be cautious: "
    "low confidence is better than a wrong confident answer. If you are "
    "not sure which boundary a dimension refers to, set confidence ≤ 0.6 "
    "and explain in `rationale`.\n\n"
    "Allowed attribute keys: building_height_m, building_height_storeys, "
    "gross_floor_area_m2, building_footprint_area_m2, front_setback_m, "
    "rear_setback_m, side_setback_left_m, side_setback_right_m, "
    "primary_use_class, residential_unit_count, parking_stalls_count, "
    "bicycle_stalls_count, loading_bays_count, occupancy_type, "
    "construction_type. Use exactly these keys — anything else is "
    "dropped by the downstream taxonomy filter."
)


_VISION_RESPONSE_SCHEMA_DESCRIPTION = (
    "Respond ONLY with a JSON object on a single line, no markdown "
    "fences. Shape:\n"
    "{\n"
    '  "drawing_type": "site_plan" | "floor_plan" | "elevation" | '
    '"section" | "title_sheet" | null,\n'
    '  "fields": [\n'
    '    {"attribute_key": "front_setback_m", "value": 7.5, '
    '"unit": "m", "confidence": 0.7, "bbox": [x0, y0, x1, y1] | null, '
    '"ocr_string": "7500", "rationale": "...", "raw_label": "..."}\n'
    "  ]\n"
    "}\n"
    "Units must be SI (m, m2). Convert mm/inches before emitting."
)


class AnthropicVisionClient:
    """Default Anthropic-backed vision client.

    Wraps ``anthropic.Anthropic`` directly (not the unified
    ``LLMGateway``) because the gateway does not yet ship ImageBlock
    support. Importing ``anthropic`` is deferred to ``__init__`` so the
    layer-1 import graph doesn't pull the SDK unless a real PDF ingest
    actually runs.

    The client is *synchronous* (``layer1.parsers.ingest_submission``
    runs synchronously and per-page calls are short). When we move
    extraction into an async worker we'll add an ``async_complete``
    method that uses ``anthropic.AsyncAnthropic``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 2048,
        client: Any | None = None,
    ) -> None:
        if client is None:
            from anthropic import Anthropic

            client = Anthropic(api_key=api_key)
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def analyze_page(self, context: VisionPageContext) -> VisionPageResult:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_VISION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(
                                    context.page_image_png
                                ).decode("ascii"),
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Page {context.page_number} of an "
                                "architectural PDF submission.\n\n"
                                "Docling OCR text blocks (top-left origin, "
                                "PDF points):\n"
                                f"{json.dumps(context.text_blocks)[:6000]}\n\n"
                                f"{_VISION_RESPONSE_SCHEMA_DESCRIPTION}"
                            ),
                        },
                    ],
                }
            ],
        )
        raw = "".join(
            block.text
            for block in getattr(message, "content", [])
            if getattr(block, "type", None) == "text"
        )
        return _parse_vision_response(
            page_number=context.page_number,
            raw_response=raw,
            drawing_type_hint=context.drawing_type_hint,
        )


def _parse_vision_response(
    *, page_number: int, raw_response: str, drawing_type_hint: str | None
) -> VisionPageResult:
    """Decode the vision LLM's JSON response into a VisionPageResult.

    The LLM is instructed to emit a one-line JSON object, but real
    models occasionally wrap responses in ```json fences or leak a
    short prose preamble. Strip the obvious wrappers; if parsing still
    fails, return an empty result with a warning rather than raising,
    so a single bad page doesn't abort the whole ingest.
    """
    warnings: list[str] = []
    text = (raw_response or "").strip()
    if text.startswith("```"):
        # Strip a markdown fence (```json ... ``` or ``` ... ```).
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        if text.endswith("```"):
            text = text[:-3]
    text = text.strip()
    if not text:
        return VisionPageResult(
            page_number=page_number,
            fields=[],
            drawing_type=drawing_type_hint,
            raw_response=raw_response,
            warnings=[f"vision LLM returned empty response on page {page_number}"],
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return VisionPageResult(
            page_number=page_number,
            fields=[],
            drawing_type=drawing_type_hint,
            raw_response=raw_response,
            warnings=[
                f"vision LLM response on page {page_number} was not JSON: {exc}"
            ],
        )

    if not isinstance(payload, dict):
        return VisionPageResult(
            page_number=page_number,
            fields=[],
            drawing_type=drawing_type_hint,
            raw_response=raw_response,
            warnings=[
                f"vision LLM response on page {page_number} was not a JSON object"
            ],
        )

    drawing_type = payload.get("drawing_type") or drawing_type_hint
    fields_raw = payload.get("fields") or []
    fields: list[VisionFieldExtraction] = []
    for entry in fields_raw:
        if not isinstance(entry, dict):
            continue
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        fields.append(
            VisionFieldExtraction(
                attribute_key=entry.get("attribute_key"),
                value=entry.get("value"),
                confidence=confidence,
                unit=entry.get("unit"),
                bbox=entry.get("bbox"),
                ocr_string=entry.get("ocr_string"),
                rationale=entry.get("rationale"),
                raw_label=entry.get("raw_label"),
            )
        )

    return VisionPageResult(
        page_number=page_number,
        fields=fields,
        drawing_type=drawing_type,
        raw_response=raw_response,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Register with the submission factory
# ----------------------------------------------------------------------


def _registered_extract_pdf(
    source_path: Path, config: SubmissionIngestConfig
) -> SubmissionExtractionResult:
    """Adapter the factory uses — pins the default vision client.

    Kept thin so tests calling ``extract_pdf`` directly can still pass
    ``vision_client=...`` without going through the registry.
    """
    return extract_pdf(source_path, config)


register_extractor(SubmissionSourceType.PDF, _registered_extract_pdf)


__all__ = [
    "AnthropicVisionClient",
    "VisionFieldExtraction",
    "VisionLLMClient",
    "VisionPageContext",
    "VisionPageResult",
    "extract_pdf",
    "get_default_vision_client",
    "set_default_vision_client",
]
