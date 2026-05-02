from __future__ import annotations

import re

from layer1.semantic.taxonomy import standard_terms, use_aliases

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


def normalize_zone(text: str) -> str:
    upper = re.sub(r"\s+", "", text.upper())
    corrected = _correct_leading_ocr_zone(upper)
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


def _correct_leading_ocr_zone(upper: str) -> str | None:
    compact = upper.replace("-", "")
    for zone_code in KNOWN_ZONE_CODES:
        zone_compact = zone_code.replace("-", "")
        if compact in {f"L{zone_compact}", f"I{zone_compact}"}:
            return zone_code
    return None


def extract_zones(text: str) -> list[str]:
    zones = set()
    for match in ZONE_RE.finditer(text):
        raw = match.group(0)
        if re.fullmatch(r"m\s*2|m²", raw, flags=re.I):
            continue
        zones.add(normalize_zone(raw))
    return sorted(zones)


def normalize_use(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().strip(".,;:")).lower()
    normalized = normalized.replace(" - ", "-")
    return use_aliases().get(normalized, normalized)


def extract_uses(text: str) -> list[str]:
    uses = []
    for match in USE_PHRASE_RE.finditer(text):
        candidate = normalize_use(match.group(1))
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
