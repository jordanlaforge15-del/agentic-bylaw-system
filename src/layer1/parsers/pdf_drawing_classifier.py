"""PDF drawing-type classifier.

Classifies each page of a multi-page architectural drawing PDF into one of the
standard drawing types used in building permit submissions.

Classification uses a two-pass approach:
1. Title-block heuristic (sheet code regex + keyword matching) as a prior.
2. Claude vision API to refine when image bytes are available.

Results are cached in-memory keyed by (file_hash, page_number) — drawings do not
change classification between re-runs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from anthropic import AsyncAnthropic
except ImportError:
    AsyncAnthropic = None  # type: ignore[assignment,misc]


class DrawingType(str, Enum):
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
    drawing_type: DrawingType
    confidence: float
    method: str
    sheet_code: str | None = None
    multi_drawing_detected: bool = False
    raw_vision_response: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# Sheet-code prefix patterns ordered most-specific first.
# Conventions: A0=general/cover, A1=floor plans, A2=elevations,
# A3=sections, A4-A5=details, A6-A8=schedules, C0/C1/SP=civil+site.
_SHEET_CODE_RULES: list[tuple[re.Pattern[str], DrawingType]] = [
    (re.compile(r"^C[- ]?[01][.\s\-_\d]", re.IGNORECASE), DrawingType.SITE_PLAN),
    (re.compile(r"^SP[.\s\-_\d]", re.IGNORECASE), DrawingType.SITE_PLAN),
    (re.compile(r"^A[- ]?0[.\s\-_\d]", re.IGNORECASE), DrawingType.COVER_SHEET),
    (re.compile(r"^G[.\s\-_\d]", re.IGNORECASE), DrawingType.COVER_SHEET),
    (re.compile(r"^CS[.\s\-_\d]", re.IGNORECASE), DrawingType.COVER_SHEET),
    (re.compile(r"^T[.\s\-_\d]", re.IGNORECASE), DrawingType.COVER_SHEET),
    (re.compile(r"^A[- ]?1[.\s\-_\d]", re.IGNORECASE), DrawingType.FLOOR_PLAN),
    (re.compile(r"^A[- ]?2[.\s\-_\d]", re.IGNORECASE), DrawingType.ELEVATION),
    (re.compile(r"^A[- ]?3[.\s\-_\d]", re.IGNORECASE), DrawingType.SECTION),
    (re.compile(r"^A[- ]?[459][.\s\-_\d]", re.IGNORECASE), DrawingType.DETAIL),
    (re.compile(r"^A[- ]?[678][.\s\-_\d]", re.IGNORECASE), DrawingType.SCHEDULE),
]

_KEYWORD_RULES: list[tuple[re.Pattern[str], DrawingType]] = [
    (re.compile(r"\bsite\s+plan\b", re.IGNORECASE), DrawingType.SITE_PLAN),
    (re.compile(r"\bsite\s+layout\b", re.IGNORECASE), DrawingType.SITE_PLAN),
    (re.compile(r"\bkey\s+plan\b", re.IGNORECASE), DrawingType.SITE_PLAN),
    (re.compile(r"\bfloor\s+plan\b", re.IGNORECASE), DrawingType.FLOOR_PLAN),
    (re.compile(r"\blevel\s+plan\b", re.IGNORECASE), DrawingType.FLOOR_PLAN),
    (re.compile(r"\bground\s+floor\b", re.IGNORECASE), DrawingType.FLOOR_PLAN),
    (re.compile(r"\b(exterior\s+)?elevations?\b", re.IGNORECASE), DrawingType.ELEVATION),
    (re.compile(r"\b(building\s+|wall\s+)?sections?\b", re.IGNORECASE), DrawingType.SECTION),
    (re.compile(r"\bdetails?\b", re.IGNORECASE), DrawingType.DETAIL),
    (re.compile(r"\bschedules?\b", re.IGNORECASE), DrawingType.SCHEDULE),
    (re.compile(r"\bcover\s+sheet\b", re.IGNORECASE), DrawingType.COVER_SHEET),
    (re.compile(r"\btitle\s+(page|sheet)\b", re.IGNORECASE), DrawingType.COVER_SHEET),
    (re.compile(r"\bdrawing\s+index\b", re.IGNORECASE), DrawingType.COVER_SHEET),
]

_VISION_SYSTEM_PROMPT = """\
You are an architectural drawing classifier for building permit submissions.
Classify the drawing page shown into exactly one of:
  site_plan, floor_plan, elevation, section, detail, schedule, cover_sheet, unknown

Guidelines:
- site_plan: parcel boundaries, setbacks, North arrow, site dimensions
- floor_plan: room layout, GFA, parking layout, dimensions at a floor level
- elevation: exterior facade, building heights, fenestration patterns
- section: vertical cut through building, floor-to-floor heights
- detail: enlarged close-up of specific building components
- schedule: tabulated door/window/room/finish data
- cover_sheet: project title block, drawing index, general notes
- unknown: cannot be determined

Respond ONLY with valid JSON (no markdown fences):
{
  "drawing_type": "<type>",
  "confidence": <0.0-1.0>,
  "reasoning": "<one sentence>",
  "multi_drawing_detected": <true|false>
}"""


class DrawingTypeClassifier:
    """Classifies architectural PDF pages into drawing types.

    Parameters
    ----------
    api_key:
        Anthropic API key. Falls back to ``ANTHROPIC_API_KEY`` env var.
    model:
        Claude model for vision calls.
    vision_confidence_threshold:
        Minimum vision confidence required to override a heuristic sheet-code
        classification. Below this the sheet-code heuristic wins.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "claude-haiku-4-5-20251001",
        vision_confidence_threshold: float = 0.6,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._vision_confidence_threshold = vision_confidence_threshold
        self._cache: dict[tuple[str, int], DrawingClassification] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def classify(
        self,
        *,
        file_hash: str,
        page_number: int,
        image_bytes: bytes | None = None,
        title_block_text: str = "",
    ) -> DrawingClassification:
        """Classify a PDF page, caching by (file_hash, page_number)."""
        cache_key = (file_hash, page_number)
        if cache_key in self._cache:
            return self._cache[cache_key]

        heuristic = self._classify_heuristic(title_block_text)

        if image_bytes is None:
            result = heuristic or DrawingClassification(
                drawing_type=DrawingType.UNKNOWN,
                confidence=0.0,
                method="heuristic",
            )
            self._cache[cache_key] = result
            return result

        vision: DrawingClassification | None = None
        try:
            vision = await self._classify_vision(image_bytes, title_block_text=title_block_text)
        except Exception as exc:
            logger.warning("Vision classification failed for page %d: %s", page_number, exc)

        if vision is None:
            result = heuristic or DrawingClassification(
                drawing_type=DrawingType.UNKNOWN,
                confidence=0.0,
                method="heuristic",
            )
        else:
            result = self._combine(heuristic, vision)

        self._cache[cache_key] = result
        return result

    def classify_heuristic(self, title_block_text: str) -> DrawingClassification | None:
        """Public wrapper — useful for callers that only have text."""
        return self._classify_heuristic(title_block_text)

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_size(self) -> int:
        return len(self._cache)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_heuristic(self, text: str) -> DrawingClassification | None:
        text = text.strip()
        if not text:
            return None

        for pattern, drawing_type in _SHEET_CODE_RULES:
            m = pattern.match(text)
            if m:
                return DrawingClassification(
                    drawing_type=drawing_type,
                    confidence=0.80,
                    method="heuristic_sheet_code",
                    sheet_code=m.group(0).strip(),
                )

        for pattern, drawing_type in _KEYWORD_RULES:
            if pattern.search(text):
                return DrawingClassification(
                    drawing_type=drawing_type,
                    confidence=0.65,
                    method="heuristic_keyword",
                )

        return None

    async def _classify_vision(
        self,
        image_bytes: bytes,
        *,
        title_block_text: str = "",
    ) -> DrawingClassification:
        if AsyncAnthropic is None:
            raise RuntimeError("anthropic package is required for vision classification")

        api_key = self._api_key
        if api_key is None:
            import os
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("No API key provided and ANTHROPIC_API_KEY not set")

        client = AsyncAnthropic(api_key=api_key)
        image_b64 = base64.standard_b64encode(image_bytes).decode()

        user_content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64,
                },
            }
        ]
        if title_block_text:
            user_content.append(
                {"type": "text", "text": f"Title block text:\n{title_block_text}"}
            )
        user_content.append({"type": "text", "text": "Classify this drawing page."})

        response = await client.messages.create(
            model=self._model,
            max_tokens=256,
            system=_VISION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = response.content[0].text if response.content else ""
        return self._parse_vision_response(raw)

    def _parse_vision_response(self, raw_text: str) -> DrawingClassification:
        try:
            m = re.search(r"\{.*\}", raw_text, re.DOTALL)
            data = json.loads(m.group() if m else raw_text)
            try:
                drawing_type = DrawingType(data.get("drawing_type", "unknown"))
            except ValueError:
                drawing_type = DrawingType.UNKNOWN
            return DrawingClassification(
                drawing_type=drawing_type,
                confidence=float(data.get("confidence", 0.5)),
                method="vision",
                multi_drawing_detected=bool(data.get("multi_drawing_detected", False)),
                raw_vision_response=raw_text,
                metadata={"reasoning": data.get("reasoning", "")},
            )
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Failed to parse vision response: %s — raw: %r", exc, raw_text[:200])
            return DrawingClassification(
                drawing_type=DrawingType.UNKNOWN,
                confidence=0.0,
                method="vision",
                raw_vision_response=raw_text,
            )

    def _combine(
        self,
        heuristic: DrawingClassification | None,
        vision: DrawingClassification,
    ) -> DrawingClassification:
        if heuristic is None:
            return vision

        if heuristic.drawing_type == vision.drawing_type:
            return DrawingClassification(
                drawing_type=vision.drawing_type,
                confidence=min(1.0, (heuristic.confidence + vision.confidence) / 2 + 0.1),
                method="combined",
                sheet_code=heuristic.sheet_code,
                multi_drawing_detected=vision.multi_drawing_detected,
                raw_vision_response=vision.raw_vision_response,
                metadata={**vision.metadata, "heuristic_type": heuristic.drawing_type.value},
            )

        # Vision wins when sufficiently confident
        if vision.confidence >= self._vision_confidence_threshold:
            return DrawingClassification(
                drawing_type=vision.drawing_type,
                confidence=vision.confidence,
                method="combined_vision_wins",
                sheet_code=heuristic.sheet_code,
                multi_drawing_detected=vision.multi_drawing_detected,
                raw_vision_response=vision.raw_vision_response,
                metadata={
                    **vision.metadata,
                    "heuristic_type": heuristic.drawing_type.value,
                    "heuristic_confidence": heuristic.confidence,
                },
            )

        # Sheet-code heuristic overrides low-confidence vision
        if heuristic.method == "heuristic_sheet_code":
            return DrawingClassification(
                drawing_type=heuristic.drawing_type,
                confidence=heuristic.confidence,
                method="combined_heuristic_wins",
                sheet_code=heuristic.sheet_code,
                multi_drawing_detected=vision.multi_drawing_detected,
                raw_vision_response=vision.raw_vision_response,
                metadata={
                    **vision.metadata,
                    "vision_type": vision.drawing_type.value,
                    "vision_confidence": vision.confidence,
                },
            )

        return DrawingClassification(
            drawing_type=vision.drawing_type,
            confidence=vision.confidence,
            method="combined",
            sheet_code=heuristic.sheet_code,
            multi_drawing_detected=vision.multi_drawing_detected,
            raw_vision_response=vision.raw_vision_response,
            metadata={
                **vision.metadata,
                "heuristic_type": heuristic.drawing_type.value,
                "heuristic_confidence": heuristic.confidence,
            },
        )


def compute_file_hash(path: Path) -> str:
    """SHA-256 hash of a file, truncated to 16 hex chars for cache keys."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]
