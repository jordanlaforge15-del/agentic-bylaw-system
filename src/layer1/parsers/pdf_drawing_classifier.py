"""ABS-56: Per-page drawing-type classification for architectural PDFs.

Identifies whether each page of a multi-page drawing set is a site plan,
floor plan, elevation, section, detail, schedule, cover sheet, or unknown
so the PDF extractor can apply page-type-specific extraction strategies.

Two-stage pipeline:

1. **Title-block heuristic** — drawing sheet codes follow industry
   conventions (``A1.x`` = floor plan, ``A2.x`` = elevations, ``A3.x`` =
   sections, ``A5.x`` = details, ``C1.x`` = civil/site, ``G/A0.x`` = cover).
   The heuristic also scans the text for explicit titles
   ("SITE PLAN", "FLOOR PLAN", "ELEVATION", ...). This is the cheap prior.
2. **Vision refinement** — if the heuristic returns low-confidence or
   ``unknown``, fall back to a Claude vision call with the raster of the
   page + the extracted title-block text. The vision client is injected
   so tests don't need network.

Results are cached by ``(file_hash, page_number)`` because a drawing
sheet doesn't change classification on re-runs. The cache is in-memory
per ``DrawingClassifier`` instance; callers that want disk persistence
can pass a ``cache`` dict from elsewhere.

Out of scope for this issue:

* Attribute extraction — the consumer (PDF extractor) acts on the
  classification.
* Splitting sheet bundles where a single PDF page contains multiple
  drawings (overlay layouts). The classifier surfaces
  ``multi_drawing_detected=True`` when the heuristic finds several
  conflicting cues and lets the extractor decide.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


logger = logging.getLogger(__name__)


DEFAULT_VISION_MODEL = "claude-sonnet-4-6"

# Confidence at which we trust the heuristic alone and skip vision.
# Set high enough that a strong title-block match (sheet code + title
# keyword) doesn't pay for an LLM call, but low enough that any
# ambiguity routes to vision.
HEURISTIC_TRUST_THRESHOLD = 0.85

# Confidence we assign vision when the heuristic was unknown. Claude is
# usually right on these but we don't have a labelled set yet, so don't
# pretend to be more certain than the eval data supports.
VISION_DEFAULT_CONFIDENCE = 0.75


class DrawingType(str, Enum):
    """Coarse architectural drawing categories.

    String-valued so the classification round-trips through JSON without
    a custom encoder. The set is closed — anything we can't place lands
    in ``UNKNOWN`` and the extractor falls back to its generic strategy.
    """

    SITE_PLAN = "site_plan"
    FLOOR_PLAN = "floor_plan"
    ELEVATION = "elevation"
    SECTION = "section"
    DETAIL = "detail"
    SCHEDULE = "schedule"
    COVER_SHEET = "cover_sheet"
    UNKNOWN = "unknown"


@dataclass
class DrawingClassification:
    """Outcome of classifying a single PDF page.

    ``method`` records which stage made the call so downstream consumers
    (and the eval harness) can split accuracy by path. ``evidence``
    captures the matching text / sheet code / vision rationale; it's a
    free-form dict because the heuristic and vision shapes don't overlap.
    """

    page_number: int
    drawing_type: DrawingType
    confidence: float
    method: str  # "heuristic" | "vision" | "heuristic+vision"
    sheet_code: str | None = None
    multi_drawing_detected: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "drawing_type": self.drawing_type.value,
            "confidence": self.confidence,
            "method": self.method,
            "sheet_code": self.sheet_code,
            "multi_drawing_detected": self.multi_drawing_detected,
            "evidence": self.evidence,
        }


# ----------------------------------------------------------------------
# Title-block heuristic
# ----------------------------------------------------------------------


# Drawing sheet code prefix → drawing type. AIA / industry-standard
# A-series subdivisions map to specific drawing types; civil (C),
# landscape (L), structural (S), and MEP (M/E/P) series exist too but
# are out of scope for the bylaw pipeline today — they all land as
# UNKNOWN with a sheet-code note for downstream.
#
# Patterns are matched against the *first* sheet code we find on the
# page (regex below). Longest-prefix wins so ``A0.1`` matches the
# cover-sheet branch before ``A1.x``.
_SHEET_CODE_RULES: list[tuple[re.Pattern[str], DrawingType, str]] = [
    # Cover / general sheets: ``A0.x``, ``A000``, ``G-101``, ``T1.0``.
    (
        re.compile(r"^(?:A0[\.\-]?\d+|A000\d*|G[\.\-]?\d+|T[\.\-]?\d+)$", re.IGNORECASE),
        DrawingType.COVER_SHEET,
        "sheet code matches cover / general series (A0, G, T)",
    ),
    # Civil / site: ``C1.x``, ``C-101``.
    (
        re.compile(r"^C[\.\-]?\d+(?:[\.\-]?\d+)?$", re.IGNORECASE),
        DrawingType.SITE_PLAN,
        "sheet code matches civil / site series (C)",
    ),
    # Floor plans: ``A1.x``, ``A100`` series.
    (
        re.compile(r"^A1[\.\-]?\d+$", re.IGNORECASE),
        DrawingType.FLOOR_PLAN,
        "sheet code matches floor-plan series (A1.x)",
    ),
    # Elevations: ``A2.x``, ``A200`` series.
    (
        re.compile(r"^A2[\.\-]?\d+$", re.IGNORECASE),
        DrawingType.ELEVATION,
        "sheet code matches elevation series (A2.x)",
    ),
    # Sections: ``A3.x``, ``A300`` series.
    (
        re.compile(r"^A3[\.\-]?\d+$", re.IGNORECASE),
        DrawingType.SECTION,
        "sheet code matches section series (A3.x)",
    ),
    # Details: ``A4.x`` (exterior) and ``A5.x`` (interior).
    (
        re.compile(r"^A[45][\.\-]?\d+$", re.IGNORECASE),
        DrawingType.DETAIL,
        "sheet code matches detail series (A4.x / A5.x)",
    ),
    # Schedules: ``A6.x``, ``A7.x``.
    (
        re.compile(r"^A[67][\.\-]?\d+$", re.IGNORECASE),
        DrawingType.SCHEDULE,
        "sheet code matches schedule series (A6.x / A7.x)",
    ),
]


# Sheet codes typically look like ``A1.01``, ``A-101``, ``C1.1``,
# ``G001``. The regex is intentionally loose so we catch the variants
# different firms use; the post-match _SHEET_CODE_RULES then disambiguate.
_SHEET_CODE_RE = re.compile(
    r"\b([A-Za-z]{1,2}[\.\-]?\d{1,4}(?:[\.\-]?\d{1,3})?)\b"
)


# Title keywords → drawing type. These match the explicit sheet titles
# stamped in the title block ("SITE PLAN", "FIRST FLOOR PLAN", ...).
# Matched as case-insensitive whole phrases. Order matters: more
# specific phrases come first so a "WALL SECTION" sheet (an enlarged
# construction detail, not a building section) lands in DETAIL before
# the generic section rule fires. The section rule itself requires a
# modifier ("building", "cross", ...) or an "A"/"A-A" suffix; bare
# "section" appears in too many non-section sheet titles to use as a
# standalone signal.
_TITLE_KEYWORD_RULES: list[tuple[re.Pattern[str], DrawingType, str]] = [
    (
        re.compile(r"\bcover\s+sheet\b|\btitle\s+sheet\b|\bdrawing\s+(?:index|list)\b", re.IGNORECASE),
        DrawingType.COVER_SHEET,
        "title text mentions cover / title sheet",
    ),
    (
        re.compile(r"\bsite\s+plan\b|\bcontext\s+plan\b|\bsurvey\s+plan\b", re.IGNORECASE),
        DrawingType.SITE_PLAN,
        "title text mentions site plan",
    ),
    (
        re.compile(
            r"\bschedules?\b|\bdoor\s+schedule\b|\bwindow\s+schedule\b|\broom\s+schedule\b|\bfinish\s+schedule\b",
            re.IGNORECASE,
        ),
        DrawingType.SCHEDULE,
        "title text mentions schedule",
    ),
    (
        re.compile(
            r"\bwall\s+sections?\b|\benlarged\s+plan\b|\bdetails\b|"
            r"\b(?:construction|architectural|structural|typical|exterior|interior)\s+detail\b",
            re.IGNORECASE,
        ),
        DrawingType.DETAIL,
        "title text mentions detail / wall section / enlarged plan",
    ),
    (
        re.compile(r"\b(?:north|south|east|west|front|rear|side|building)\s+elevation\b|\belevations?\b", re.IGNORECASE),
        DrawingType.ELEVATION,
        "title text mentions elevation",
    ),
    (
        re.compile(
            r"\b(?:building|cross|longitudinal|transverse)\s+sections?\b|\bsections?\s+[A-Z](?:[\-\.][A-Z])?\b",
            re.IGNORECASE,
        ),
        DrawingType.SECTION,
        "title text mentions building / cross / longitudinal section",
    ),
    (
        re.compile(
            r"\b(?:floor|roof|ceiling|foundation|basement|ground|first|second|third|mezzanine|typical|reflected\s+ceiling)\s+plan\b|\bfloor\s+plan\b",
            re.IGNORECASE,
        ),
        DrawingType.FLOOR_PLAN,
        "title text mentions floor / roof / foundation plan",
    ),
]


def classify_by_heuristic(
    title_block_text: str,
) -> tuple[DrawingType, float, dict[str, Any]]:
    """Classify a page from its title-block / page text alone.

    Returns ``(drawing_type, confidence, evidence)``. Confidence is:

    * ``0.95`` — both sheet code and title keyword agree.
    * ``0.85`` — sheet code matched; no title keyword to corroborate.
    * ``0.75`` — title keyword matched; no recognisable sheet code.
    * ``0.55`` — two different title keywords matched (multi-drawing
      indicator); the higher-priority one wins but confidence drops.
    * ``0.0``  — nothing matched; return ``UNKNOWN``.

    The evidence dict captures everything the caller might want to log
    (matched text, code, rule notes). It is intentionally JSON-safe.
    """
    text = title_block_text or ""

    code_type, sheet_code, code_note = _match_sheet_code(text)
    title_matches = _match_title_keywords(text)

    evidence: dict[str, Any] = {
        "sheet_code": sheet_code,
        "sheet_code_note": code_note,
        "title_matches": [
            {"text": match_text, "type": dtype.value, "note": note}
            for match_text, dtype, note in title_matches
        ],
    }

    primary_title_type = title_matches[0][1] if title_matches else None
    distinct_title_types = {dtype for _, dtype, _ in title_matches}

    if code_type is not None and primary_title_type is not None:
        if code_type == primary_title_type:
            return code_type, 0.95, evidence
        # Disagreement: trust the explicit title text over the code prefix.
        # Firms occasionally publish floor plans on ``A0`` cover-style
        # sheets when the project is single-page; the title wins.
        evidence["code_title_disagreement"] = True
        return primary_title_type, 0.7, evidence

    if code_type is not None:
        return code_type, 0.85, evidence

    if primary_title_type is not None:
        if len(distinct_title_types) >= 2:
            # Multiple distinct drawing types named in the same page text.
            # Could be a legend, an index, or a true multi-drawing sheet.
            # Don't trust the heuristic — surface to vision.
            evidence["multi_drawing_detected"] = True
            return primary_title_type, 0.55, evidence
        return primary_title_type, 0.75, evidence

    return DrawingType.UNKNOWN, 0.0, evidence


def _match_sheet_code(text: str) -> tuple[DrawingType | None, str | None, str | None]:
    """Pull the first plausible sheet code and map it to a drawing type.

    Returns ``(drawing_type, sheet_code_text, rule_note)`` — any of which
    may be None when no code matches.
    """
    for candidate in _SHEET_CODE_RE.finditer(text):
        code = candidate.group(1).strip()
        # Filter out very-likely-not-sheet-codes (years, page numbers,
        # measurements). A sheet code always has a letter prefix and a
        # short numeric tail; the regex already enforces that, but we
        # also reject standalone 4-digit years (1900–2099) just in case
        # the page text leaks one through.
        if re.fullmatch(r"\d{4}", code):
            continue
        for pattern, dtype, note in _SHEET_CODE_RULES:
            if pattern.match(code):
                return dtype, code, note
    return None, None, None


def _match_title_keywords(text: str) -> list[tuple[str, DrawingType, str]]:
    """Run every title-keyword rule against the text.

    Returns matches in rule order (which is by specificity, most
    specific first). Empty list if nothing matches.
    """
    matches: list[tuple[str, DrawingType, str]] = []
    seen_types: set[DrawingType] = set()
    for pattern, dtype, note in _TITLE_KEYWORD_RULES:
        m = pattern.search(text)
        if m and dtype not in seen_types:
            matches.append((m.group(0), dtype, note))
            seen_types.add(dtype)
    return matches


# ----------------------------------------------------------------------
# Vision refinement
# ----------------------------------------------------------------------


class VisionClient(Protocol):
    """Shape the classifier depends on; both real and stub satisfy it.

    A vision client takes the page raster bytes + the title-block text
    and returns a ``DrawingType`` plus a confidence and a free-form
    rationale string. Implementations are responsible for their own
    transport (Anthropic SDK, httpx, etc.) and for any retry / cost
    accounting; the classifier just calls and trusts the result.
    """

    def classify(
        self,
        *,
        page_image_png: bytes,
        title_block_text: str,
        heuristic_hint: DrawingType | None,
    ) -> tuple[DrawingType, float, str]: ...


# Prompt for the vision call. The classifier sends the page raster plus
# the title-block text and asks for a single label from the closed set.
# Constrained-output JSON keeps the parser simple; we don't need streaming.
_VISION_SYSTEM_PROMPT = """\
You classify a single page of an architectural drawing PDF.

Return strict JSON in this exact shape (no prose, no markdown fence):
{"drawing_type": "<one of: site_plan, floor_plan, elevation, section, \
detail, schedule, cover_sheet, unknown>", \
"confidence": <number between 0 and 1>, \
"reasoning": "<one short sentence>"}

Definitions:
- site_plan: shows parcel boundary, setbacks, north arrow, site dims.
- floor_plan: top-down view of a single floor with rooms and walls.
- elevation: orthographic exterior view (north/south/east/west).
- section: vertical cut through the building showing floor-to-floor.
- detail: enlarged construction detail or wall section.
- schedule: tabular door/window/room/finish schedule.
- cover_sheet: title sheet, drawing index, abbreviations, project info.
- unknown: cannot determine, or none of the above fits.

If the page clearly shows multiple drawing types side by side, pick the
dominant one and lower confidence to reflect the ambiguity.
"""


class AnthropicVisionClient:
    """Production VisionClient backed by the Anthropic SDK.

    Synchronous because the PDF extractor itself is sync, and per-page
    classification doesn't benefit from async. Lazily imports
    ``anthropic`` so the layer1 ingest path doesn't require the SDK
    when the heuristic alone is enough (e.g. low-stakes runs that
    disable vision entirely).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_VISION_MODEL,
        max_tokens: int = 256,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - exercised via unit test stub
            raise RuntimeError(
                "anthropic SDK is required for AnthropicVisionClient; "
                "install with `pip install anthropic`."
            ) from exc
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "AnthropicVisionClient: api_key not provided and "
                "ANTHROPIC_API_KEY env var is unset."
            )
        self._client = Anthropic(api_key=resolved_key)
        self._model = model
        self._max_tokens = max_tokens

    def classify(
        self,
        *,
        page_image_png: bytes,
        title_block_text: str,
        heuristic_hint: DrawingType | None,
    ) -> tuple[DrawingType, float, str]:
        b64 = base64.standard_b64encode(page_image_png).decode("ascii")
        hint_line = (
            f"Heuristic hint (may be wrong): {heuristic_hint.value}\n"
            if heuristic_hint and heuristic_hint != DrawingType.UNKNOWN
            else ""
        )
        user_text = (
            f"{hint_line}"
            f"Title-block / page text extracted by Docling:\n{title_block_text or '(empty)'}\n\n"
            "Classify this page."
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": _VISION_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        )
        return _parse_vision_response(response)


def _parse_vision_response(response: Any) -> tuple[DrawingType, float, str]:
    """Pull ``drawing_type``, ``confidence``, ``reasoning`` from a Claude response.

    Permissive: occasionally a model wraps JSON in prose. We extract the
    first ``{ ... }`` substring and json.loads it. On parse failure or
    unknown drawing-type strings, returns ``(UNKNOWN, 0.0, "<reason>")``
    so the caller can fall back to the heuristic without raising.
    """
    text_chunks: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            text_chunks.append(text)
    joined = "".join(text_chunks).strip()
    if not joined:
        return DrawingType.UNKNOWN, 0.0, "vision returned empty content"

    start = joined.find("{")
    end = joined.rfind("}")
    if start == -1 or end == -1 or end < start:
        return DrawingType.UNKNOWN, 0.0, f"vision returned non-JSON content: {joined[:120]!r}"
    try:
        parsed = json.loads(joined[start : end + 1])
    except json.JSONDecodeError as exc:
        return DrawingType.UNKNOWN, 0.0, f"vision JSON parse failed: {exc}"

    raw_type = str(parsed.get("drawing_type", "")).strip().lower()
    try:
        drawing_type = DrawingType(raw_type)
    except ValueError:
        return DrawingType.UNKNOWN, 0.0, f"vision returned unrecognised drawing_type: {raw_type!r}"

    confidence_raw = parsed.get("confidence", VISION_DEFAULT_CONFIDENCE)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = VISION_DEFAULT_CONFIDENCE
    confidence = max(0.0, min(1.0, confidence))

    reasoning = str(parsed.get("reasoning", "")).strip() or "(no rationale)"
    return drawing_type, confidence, reasoning


# ----------------------------------------------------------------------
# Classifier driver
# ----------------------------------------------------------------------


CacheKey = tuple[str, int]


@dataclass
class DrawingClassifier:
    """Orchestrates heuristic + vision per page with a result cache.

    Wire-up:

    * Pass ``vision_client=None`` to disable the vision stage entirely
      (heuristic-only mode — useful in tests and in batch runs where
      the cost / latency of vision isn't worth it).
    * Pass ``vision_client=AnthropicVisionClient(...)`` for production.
    * Tests pass a stub that returns canned ``(DrawingType, float, str)``
      tuples so the classifier can be exercised without network.

    ``cache`` is an injectable mapping so callers can share one cache
    across many classifier instances, or persist it to disk between
    runs. The keys are ``(file_hash, page_number)``; the values are
    full ``DrawingClassification`` records.
    """

    vision_client: VisionClient | None = None
    cache: dict[CacheKey, DrawingClassification] = field(default_factory=dict)
    heuristic_trust_threshold: float = HEURISTIC_TRUST_THRESHOLD

    def classify_page(
        self,
        *,
        file_hash: str,
        page_number: int,
        title_block_text: str,
        page_image_png: bytes | None = None,
    ) -> DrawingClassification:
        """Classify a single page; return a cached result on re-runs.

        ``page_image_png`` may be omitted when the caller knows the
        heuristic will fire (or when running in heuristic-only mode).
        If vision is configured but no image is provided, we record a
        warning in evidence and return the heuristic verdict — better
        than failing the ingest for a missing raster.
        """
        cache_key: CacheKey = (file_hash, page_number)
        if cache_key in self.cache:
            return self.cache[cache_key]

        heuristic_type, heuristic_conf, evidence = classify_by_heuristic(title_block_text)
        sheet_code = evidence.get("sheet_code")
        multi = bool(evidence.get("multi_drawing_detected"))

        if (
            self.vision_client is None
            or heuristic_conf >= self.heuristic_trust_threshold
        ):
            result = DrawingClassification(
                page_number=page_number,
                drawing_type=heuristic_type,
                confidence=heuristic_conf,
                method="heuristic",
                sheet_code=sheet_code,
                multi_drawing_detected=multi,
                evidence={"heuristic": evidence},
            )
            self.cache[cache_key] = result
            return result

        if page_image_png is None:
            evidence_without_image = {
                "heuristic": evidence,
                "vision_skipped": "no page image provided; heuristic-only verdict",
            }
            result = DrawingClassification(
                page_number=page_number,
                drawing_type=heuristic_type,
                confidence=heuristic_conf,
                method="heuristic",
                sheet_code=sheet_code,
                multi_drawing_detected=multi,
                evidence=evidence_without_image,
            )
            self.cache[cache_key] = result
            return result

        # Pass None for the hint when the heuristic was UNKNOWN — the
        # vision model shouldn't be biased by a non-answer.
        hint = heuristic_type if heuristic_type != DrawingType.UNKNOWN else None
        try:
            vision_type, vision_conf, reasoning = self.vision_client.classify(
                page_image_png=page_image_png,
                title_block_text=title_block_text,
                heuristic_hint=hint,
            )
        except Exception as exc:
            # Vision is a refinement; never let it sink the ingest. Log
            # and fall through to the heuristic answer with a note.
            logger.warning(
                "drawing-type vision call failed on page %s: %s", page_number, exc
            )
            result = DrawingClassification(
                page_number=page_number,
                drawing_type=heuristic_type,
                confidence=heuristic_conf,
                method="heuristic",
                sheet_code=sheet_code,
                multi_drawing_detected=multi,
                evidence={
                    "heuristic": evidence,
                    "vision_error": str(exc),
                },
            )
            self.cache[cache_key] = result
            return result

        final_type, final_conf, method = _combine_heuristic_and_vision(
            heuristic_type=heuristic_type,
            heuristic_conf=heuristic_conf,
            vision_type=vision_type,
            vision_conf=vision_conf,
        )
        result = DrawingClassification(
            page_number=page_number,
            drawing_type=final_type,
            confidence=final_conf,
            method=method,
            sheet_code=sheet_code,
            multi_drawing_detected=multi,
            evidence={
                "heuristic": evidence,
                "vision": {
                    "type": vision_type.value,
                    "confidence": vision_conf,
                    "reasoning": reasoning,
                },
            },
        )
        self.cache[cache_key] = result
        return result


def _combine_heuristic_and_vision(
    *,
    heuristic_type: DrawingType,
    heuristic_conf: float,
    vision_type: DrawingType,
    vision_conf: float,
) -> tuple[DrawingType, float, str]:
    """Merge the heuristic prior and the vision verdict.

    Rules:

    * Agreement → take the type, confidence boosted toward 1 but capped
      at ``min(0.99, max(heuristic_conf, vision_conf) + 0.05)``.
    * Disagreement, heuristic was UNKNOWN → take vision.
    * Disagreement, vision was UNKNOWN → take heuristic.
    * Disagreement otherwise → take whichever is more confident; in a
      true tie, prefer vision (it actually looked at the page).
    """
    if heuristic_type == vision_type:
        boosted = min(0.99, max(heuristic_conf, vision_conf) + 0.05)
        return heuristic_type, boosted, "heuristic+vision"

    if heuristic_type == DrawingType.UNKNOWN:
        return vision_type, vision_conf, "vision"
    if vision_type == DrawingType.UNKNOWN:
        return heuristic_type, heuristic_conf, "heuristic"

    if vision_conf >= heuristic_conf:
        return vision_type, vision_conf, "vision"
    return heuristic_type, heuristic_conf, "heuristic"


# ----------------------------------------------------------------------
# Convenience entrypoint
# ----------------------------------------------------------------------


def classify_pdf_pages(
    *,
    file_hash: str,
    pages: list[tuple[int, str, bytes | None]],
    vision_client: VisionClient | None = None,
    cache: dict[CacheKey, DrawingClassification] | None = None,
) -> list[DrawingClassification]:
    """Classify every page in a drawing PDF in one shot.

    ``pages`` is a list of ``(page_number, title_block_text, page_image_png)``
    tuples. Returns a list of ``DrawingClassification`` in the same order
    as the input. Cache hits short-circuit per page, so re-running on
    the same PDF is effectively free.
    """
    classifier = DrawingClassifier(
        vision_client=vision_client, cache=cache if cache is not None else {}
    )
    return [
        classifier.classify_page(
            file_hash=file_hash,
            page_number=page_no,
            title_block_text=text,
            page_image_png=image,
        )
        for page_no, text, image in pages
    ]


__all__ = [
    "DEFAULT_VISION_MODEL",
    "AnthropicVisionClient",
    "DrawingClassification",
    "DrawingClassifier",
    "DrawingType",
    "VisionClient",
    "classify_by_heuristic",
    "classify_pdf_pages",
    "HEURISTIC_TRUST_THRESHOLD",
]
