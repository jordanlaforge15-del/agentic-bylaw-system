from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from layer1.semantic.taxonomy import standard_terms, use_aliases


@dataclass(frozen=True)
class ProfileOverlay:
    """Manifest-derived overrides for the otherwise-hardcoded zone / use logic.

    Lives in a :class:`contextvars.ContextVar` so the dozens of call sites in
    ``layer1.semantic.enrichment`` don't each have to take a ``profile`` kwarg;
    the enrichment entrypoint sets the overlay once and tears it down on
    completion. Each extractor below falls back to the legacy module-level
    constants when no overlay is active, so the historical code paths keep
    behaving exactly as before.
    """

    zone_pattern: re.Pattern[str] | None = None
    known_zone_codes: Iterable[str] | None = None
    use_class_map: Mapping[str, str] | None = None


_active_overlay: contextvars.ContextVar[ProfileOverlay | None] = contextvars.ContextVar(
    "layer1_semantic_profile_overlay", default=None
)


def use_profile_overlay(overlay: ProfileOverlay | None) -> contextvars.Token:
    """Bind ``overlay`` as the active manifest overlay; returns the token used
    to restore the previous value via :func:`reset_profile_overlay`. Typically
    used as ``token = use_profile_overlay(o); try: ... finally:
    reset_profile_overlay(token)``."""
    return _active_overlay.set(overlay)


def reset_profile_overlay(token: contextvars.Token) -> None:
    _active_overlay.reset(token)


def _current_overlay() -> ProfileOverlay | None:
    return _active_overlay.get()

KNOWN_ZONE_CODES = {
    "CEN-2",
    "CEN-1",
    "CDD-2",
    "CDD-1",
    "HR-2",
    "HR-1",
    "ER-3",
    "ER-2",
    "ER-1",
    "CH-2",
    "CH-1",
    "UC-2",
    "UC-1",
    "DD",
    "DH",
    "COR",
    "CLI",
    "LI",
    "HRI",
    "INS",
    "DND",
    "PCF",
    "RPK",
    "WA",
}
ZONE_RE = re.compile(r"\b[A-Z]{1,4}-?\s?\d[A-Z]?(?:-?\s?[A-Z])?\b|\b(?:DD|DH|COR|CLI|LI|HRI|INS|DND|PCF|RPK|WA)\b", re.I)
# Substrings that match ZONE_RE but are NOT real zone codes. Most common
# sources of false positives on real municipal bylaws:
#   1. Amendment markers: "(RC-May 9/24)", "(E-Jun 13/24)" → MAY-9, JUN-13
#   2. Schedule labels: "Schedule ZM-1", "ZM-2" → ZM-1, ZM-2 (which the
#      parser correctly captures as a schedule but the zone regex also
#      grabs as a "zone code"). See ABS-104.
_MONTH_PREFIX = (
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec"
)
_ZONE_NOISE_RE = re.compile(
    rf"^(?:(?:{_MONTH_PREFIX})\s*-?\s*\d{{1,2}}|ZM-?\s?\d{{1,3}})$",
    re.IGNORECASE,
)
CONDITION_MARKER_RE = re.compile(r"[①-㉟]|\[\d+\]")
TABLE_REF_RE = re.compile(r"\bTable\s+\d+[A-Z]?\b", re.I)
SECTION_REF_RE = re.compile(r"\b(?:Section|Subsection|Schedule)\s+[A-Za-z0-9.\-()]+\b", re.I)
NUMERIC_VALUE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:m2|m²|metres?|meters?|feet|ft|sq\.?\s*ft|square feet|%)\b", re.I)
USE_PHRASE_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9,'()/& -]{1,120}?"
    r"(?:\s+use|\s+dwelling|\s+facility|\s+establishment|\s+suite|\s+unit))\b"
)
DEVELOPMENT_CONTEXT_ALIASES = {
    "rear addition": "rear additions",
    "rear additions": "rear additions",
    "addition": "additions",
    "additions": "additions",
    "internal conversion": "internal conversions",
    "internal conversions": "internal conversions",
    "interior conversion": "internal conversions",
    "interior conversions": "internal conversions",
    "conversion": "conversions",
    "conversions": "conversions",
}
DEVELOPMENT_CONTEXT_RE = re.compile(
    r"\b(rear additions?|internal conversions?|interior conversions?|additions?|conversions?)\b",
    re.I,
)


def normalize_zone(text: str, *, known_zone_codes: Iterable[str] | None = None) -> str:
    upper = re.sub(r"\s+", "", text.upper())
    # Resolve known_zone_codes once — `_correct_leading_ocr_zone` needs it too
    # and we want to honor any manifest overlay before the hyphenation rule
    # kicks in. Without this short-circuit, a manifest declaring "R1" gets
    # reshaped to "R-1" by the fullmatch below and then doesn't round-trip
    # against the manifest's own taxonomy — surprising and wrong.
    if known_zone_codes is None:
        overlay = _current_overlay()
        if overlay is not None and overlay.known_zone_codes is not None:
            known_zone_codes = overlay.known_zone_codes
    codes = list(known_zone_codes) if known_zone_codes is not None else None
    if codes is not None:
        upper_codes = {code.upper() for code in codes}
        if upper.upper() in upper_codes:
            # Preserve the exact spelling the manifest declared (case-insensitive
            # match, then look up the manifest's canonical form).
            for code in codes:
                if code.upper() == upper:
                    return code
            return upper
    corrected = _correct_leading_ocr_zone(upper, known_zone_codes=codes)
    if corrected:
        return corrected
    if "-" in upper:
        return upper
    match = re.fullmatch(r"([A-Z]{1,4})(\d[A-Z]?)([A-Z]?)", upper)
    if match:
        parts = [match.group(1), match.group(2)]
        if match.group(3):
            parts.append(match.group(3))
        return "-".join(parts)
    return upper


def _correct_leading_ocr_zone(
    upper: str, *, known_zone_codes: Iterable[str] | None = None
) -> str | None:
    if known_zone_codes is None:
        overlay = _current_overlay()
        if overlay is not None and overlay.known_zone_codes is not None:
            known_zone_codes = overlay.known_zone_codes
    codes = known_zone_codes if known_zone_codes is not None else KNOWN_ZONE_CODES
    compact = upper.replace("-", "")
    for zone_code in codes:
        zone_compact = zone_code.replace("-", "")
        if compact in {f"L{zone_compact}", f"I{zone_compact}"}:
            return zone_code
    return None


def extract_zones(
    text: str,
    *,
    zone_pattern: re.Pattern[str] | None = None,
    known_zone_codes: Iterable[str] | None = None,
) -> list[str]:
    """Extract zone codes from ``text``.

    Defaults to the Halifax-flavoured module constants ``ZONE_RE`` and
    ``KNOWN_ZONE_CODES``. When a :class:`ProfileOverlay` is active (set by
    ``layer1.semantic.enrichment.enrich_document_semantics`` during manifest-
    driven ingestion) and the caller hasn't passed explicit overrides, the
    overlay supplies the regex and known code set instead.
    """
    if zone_pattern is None or known_zone_codes is None:
        overlay = _current_overlay()
        if overlay is not None:
            if zone_pattern is None and overlay.zone_pattern is not None:
                zone_pattern = overlay.zone_pattern
            if known_zone_codes is None and overlay.known_zone_codes is not None:
                known_zone_codes = overlay.known_zone_codes
    pattern = zone_pattern if zone_pattern is not None else ZONE_RE
    zones = set()
    for match in pattern.finditer(text):
        raw = match.group(0)
        if re.fullmatch(r"m\s*2|m²", raw, flags=re.I):
            continue
        # ABS-104: drop common false-positive substrings (month-abbrev
        # amendment markers, ZM-N schedule labels). These match the zone
        # regex but appear hundreds of times in bylaws that use those
        # conventions, and on a doc that doesn't they cost nothing.
        if _ZONE_NOISE_RE.fullmatch(raw.strip().replace(" ", "")):
            continue
        zones.add(normalize_zone(raw, known_zone_codes=known_zone_codes))
    return sorted(zones)


def normalize_use(
    text: str, *, use_class_map: Mapping[str, str] | None = None
) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().strip(".,;:")).lower()
    normalized = normalized.replace(" - ", "-")
    if use_class_map is None:
        overlay = _current_overlay()
        if overlay is not None and overlay.use_class_map is not None:
            use_class_map = overlay.use_class_map
    # Manifest-supplied mapping wins over the module-level taxonomy aliases —
    # the manifest is the per-jurisdiction source of truth when present.
    if use_class_map is not None and normalized in use_class_map:
        return use_class_map[normalized]
    return use_aliases().get(normalized, normalized)


def extract_uses(
    text: str, *, use_class_map: Mapping[str, str] | None = None
) -> list[str]:
    uses = []
    for match in USE_PHRASE_RE.finditer(text):
        candidate = normalize_use(match.group(1), use_class_map=use_class_map)
        if candidate in {"land use", "permitted use"}:
            continue
        uses.append(candidate)
    return sorted(set(uses))


def extract_standards(text: str) -> list[str]:
    lower = text.lower()
    matches = {term for term in standard_terms() if term in lower}
    return sorted(
        {term for term in matches if not any(term != other and term in other for other in matches)},
        key=lambda value: (-len(value), value),
    )


def extract_condition_refs(text: str) -> list[str]:
    return sorted(set(CONDITION_MARKER_RE.findall(text)))


def extract_table_refs(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", match.group(0)).title() for match in TABLE_REF_RE.finditer(text)})


def extract_section_refs(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", match.group(0)) for match in SECTION_REF_RE.finditer(text)})


def extract_numeric_values(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", match.group(0)) for match in NUMERIC_VALUE_RE.finditer(text)})


def extract_development_contexts(text: str) -> list[str]:
    contexts = set()
    for match in DEVELOPMENT_CONTEXT_RE.finditer(text):
        normalized = re.sub(r"\s+", " ", match.group(1).lower())
        contexts.add(DEVELOPMENT_CONTEXT_ALIASES.get(normalized, normalized))
    return sorted(contexts)


def looks_like_use_label(text: str) -> bool:
    normalized = normalize_use(text)
    return bool(normalized) and (
        normalized.endswith(" use")
        or normalized.endswith(" dwelling")
        or normalized.endswith(" facility")
        or normalized.endswith(" establishment")
        or normalized.endswith(" suite")
        or normalized.endswith(" unit")
    )


# Bare section-number labels like "11(1)", "5A", "28AO(1)", "62EB". Used by
# bylaws that index permission/parking tables by section number rather than
# use-class name (Halifax Mainland LUB is an example). ABS-105.
_SECTION_LABEL_RE = re.compile(
    r"^\s*\d{1,3}[A-Z]{0,4}"
    r"(?:\(\d{1,3}\))?"
    r"(?:\([a-z]{1,4}\))?"
    r"\s*$",
    re.IGNORECASE,
)


def looks_like_section_label(text: str) -> bool:
    """True if ``text`` is a bare bylaw section label like ``11(1)`` or ``28AO``."""
    if not text:
        return False
    return bool(_SECTION_LABEL_RE.match(text))
