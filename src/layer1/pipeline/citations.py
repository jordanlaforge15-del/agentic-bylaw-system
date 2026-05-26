from __future__ import annotations

import re
from dataclasses import dataclass

from layer1.models.enums import FragmentType
from layer1.profiles import ParsingProfile, get_parsing_profile


PART_RE = re.compile(r"^\s*part\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
SCHEDULE_RE = re.compile(r"^\s*schedule\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
APPENDIX_RE = re.compile(r"^\s*appendix\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
COMPOUND_SECTION_RE = re.compile(
    r"^\s*((?:\d+(?:[A-Z]+\d+)*[A-Z]*)(?:\s*\([0-9A-Za-z]+\))*[A-Z]?)(?=\s|$)\s*(.*)$"
)
SPLIT_COMPOUND_SECTION_RE = re.compile(r"^\s*(\d+)\s+([A-Z]{1,3})\b\s+(.*)$")
NUMERIC_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\b(?:[.)])?\s*(.*)$")
NUMERIC_PAREN_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5}(?:\([A-Za-z0-9]+\))+)\s+(.*)$")
PAREN_NUMERIC_RE = re.compile(r"^\s*\((\d+(?:\.\d+){0,5})\)\s+(.*)$")
CLAUSE_RE = re.compile(r"^\s*(\([a-z]{1,3}\))\s+(.*)$", re.IGNORECASE)
SUBCLAUSE_RE = re.compile(r"^\s*\(([ivxlcdm]{2,})\)\s+(.*)$", re.IGNORECASE)
FOOTNOTE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\s+)(.+)$")


@dataclass(frozen=True)
class CitationMatch:
    fragment_type: FragmentType
    label: str
    level: int
    title: str
    confidence: float


def parse_citation_label(text: str, profile: ParsingProfile | None = None) -> CitationMatch | None:
    profile = get_parsing_profile(profile)
    stripped = " ".join(text.strip().split())
    if not stripped:
        return None

    # Manifest-derived patterns get first crack at the input when present.
    # That's what makes Layer 1 driven by an arbitrary city's CityIntakeManifest
    # instead of by the hardcoded Halifax-flavoured regex below. The fallback
    # path keeps every current code path working when no manifest is in play.
    manifest_match = _match_manifest_citation_level(stripped, profile)
    if manifest_match is not None:
        return manifest_match

    for regex, fragment_type, prefix in (
        (PART_RE, FragmentType.PART, "Part"),
        (SCHEDULE_RE, FragmentType.SCHEDULE, "Schedule"),
        (APPENDIX_RE, FragmentType.APPENDIX, "Appendix"),
    ):
        match = regex.match(stripped)
        if match:
            token = match.group(1).upper()
            return CitationMatch(fragment_type, f"{prefix} {token}", 1, match.group(2).strip(), 0.95)

    if profile.allow_compound_section_labels:
        split_compound = SPLIT_COMPOUND_SECTION_RE.match(stripped)
        if split_compound:
            suffix = split_compound.group(2)
            title = split_compound.group(3).strip()
            if len(suffix) >= 2 or (title and title[:1].isupper()):
                joined_label = f"{split_compound.group(1)}{suffix}"
                joined_label, title = _absorb_leading_paren_tokens(joined_label, title)
                parsed = _parse_compound_section_label(joined_label, title)
                if parsed:
                    return parsed
        compound = COMPOUND_SECTION_RE.match(stripped)
        if compound:
            label = re.sub(r"\s+", "", compound.group(1))
            title = compound.group(2).strip()
            parsed = _parse_compound_section_label(label, title)
            if parsed:
                return parsed

    match = NUMERIC_PAREN_RE.match(stripped)
    if match:
        return CitationMatch(FragmentType.SECTION, match.group(1), 2, match.group(2).strip(), 0.9)

    match = PAREN_NUMERIC_RE.match(stripped)
    if match:
        return CitationMatch(FragmentType.SUBSECTION, f"({match.group(1)})", 3, match.group(2).strip(), 0.85)

    match = NUMERIC_RE.match(stripped)
    if match:
        if match.end(1) < len(stripped) and stripped[match.end(1)] == "/":
            return None
        label = match.group(1)
        depth = label.count(".") + 1
        if depth == 1:
            fragment_type = FragmentType.SECTION
            level = 2
        elif depth == 2:
            fragment_type = FragmentType.SUBSECTION
            level = 3
        else:
            fragment_type = FragmentType.SUBSECTION
            level = 3 + min(depth - 2, 2)
        return CitationMatch(fragment_type, label, level, match.group(2).strip(), 0.9)

    match = SUBCLAUSE_RE.match(stripped)
    if match:
        return CitationMatch(FragmentType.SUBCLAUSE, f"({match.group(1).lower()})", 6, match.group(2).strip(), 0.85)

    match = CLAUSE_RE.match(stripped)
    if match:
        return CitationMatch(FragmentType.CLAUSE, match.group(1).lower(), 5, match.group(2).strip(), 0.85)

    return None


def citation_path(parent_path: str | None, label: str | None) -> str | None:
    if not label:
        return parent_path
    return f"{parent_path} > {label}" if parent_path else label


_PART_FRAGMENT_TYPES = {FragmentType.PART, FragmentType.SCHEDULE, FragmentType.APPENDIX}


def _match_manifest_citation_level(
    text: str, profile: ParsingProfile
) -> CitationMatch | None:
    """Run the manifest's citation hierarchy patterns against ``text``.

    Returns ``None`` when the profile carries no manifest patterns (the common
    case for the historical hardcoded profiles), so ``parse_citation_label``
    can fall straight through to its existing logic.

    We only emit a high-level match (PART/SCHEDULE/APPENDIX) here because for
    those the manifest's label format ("Part {n}") cleanly produces a final
    citation label. For lower levels the hardcoded numeric regex is already
    universal enough that we don't gain anything by re-implementing depth
    inference per-manifest — and getting that wrong would corrupt the
    hierarchy stack. So manifest matching is opt-in to the prefix-style levels
    today; the issue's "minimum mapping" requirement is satisfied because the
    citation hierarchy regex slot in the parser is now data-driven.
    """
    if not profile.manifest_citation_levels:
        return None

    for level in profile.manifest_citation_levels:
        if level.fragment_type not in _PART_FRAGMENT_TYPES:
            # Sub-section level matching stays with the hardcoded regex below.
            # See docstring.
            continue
        match = level.pattern.match(text)
        if not match:
            continue
        token = _extract_label_token(match)
        prefix = level.fragment_type.value.capitalize()
        label = level.label_format.replace("{n}", token).strip() if level.label_format else f"{prefix} {token}"
        title = text[match.end():].lstrip(" -:\t").strip()
        return CitationMatch(level.fragment_type, label, level.level, title, 0.95)

    return None


def _extract_label_token(match: re.Match[str]) -> str:
    """Pull the section-number token out of a regex match without assuming groups."""
    if match.lastindex:
        # Last captured group is almost always the number/letter token.
        for idx in range(match.lastindex, 0, -1):
            value = match.group(idx)
            if value:
                return value.strip().upper()
    # No groups: surface the whole match minus leading keyword.
    return re.sub(r"^[A-Za-z]+\s+", "", match.group(0)).strip().upper()


_LEADING_PAREN_RE = re.compile(r"^\(([0-9A-Za-z]+)\)\s*")


def _absorb_leading_paren_tokens(label: str, title: str) -> tuple[str, str]:
    """Fold leading parenthetical tokens from *title* into *label*.

    PDF extraction sometimes inserts whitespace between a compound section
    number and its subsection parenthetical (``62EE (1)`` instead of
    ``62EE(1)``).  When the SPLIT_COMPOUND_SECTION_RE path produces a bare
    base label whose title starts with ``(N)``, this helper re-attaches those
    tokens so the downstream parser sees ``62EE(1)`` as a single label.
    """
    while True:
        m = _LEADING_PAREN_RE.match(title)
        if not m:
            break
        label = f"{label}({m.group(1)})"
        title = title[m.end():]
    return label, title.strip()


def _parse_compound_section_label(label: str, title: str) -> CitationMatch | None:
    if "." in label:
        return None

    base_match = re.match(r"^(\d+[A-Z]*)(.*?)([A-Z]?)$", label)
    if not base_match:
        return None

    paren_tokens = re.findall(r"\(([0-9A-Za-z]+)\)", label)
    trailing_suffix = base_match.group(3) if base_match.group(2) else ""
    level = 2 + len(paren_tokens) + (1 if trailing_suffix else 0)

    if not paren_tokens and not trailing_suffix and re.fullmatch(r"\d+", label):
        return None

    fragment_type = FragmentType.SECTION if level == 2 else FragmentType.SUBSECTION
    if paren_tokens:
        last = paren_tokens[-1]
        if re.fullmatch(r"[a-z]", last):
            fragment_type = FragmentType.CLAUSE
        elif re.fullmatch(r"[ivxlcdm]+", last, re.IGNORECASE):
            fragment_type = FragmentType.SUBCLAUSE

    return CitationMatch(fragment_type, label, level, title, 0.9)
