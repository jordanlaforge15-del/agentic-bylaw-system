from __future__ import annotations

import re
from dataclasses import dataclass

from layer1.models.enums import FragmentType
from layer1.profiles import ParsingProfile, get_parsing_profile


PART_RE = re.compile(r"^\s*part\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
SCHEDULE_RE = re.compile(r"^\s*schedule\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
APPENDIX_RE = re.compile(r"^\s*appendix\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
COMPOUND_SECTION_RE = re.compile(
    r"^\s*((?:\d+[A-Z]*)(?:\([0-9A-Za-z]+\))*[A-Z]?)(?=\s|$)\s*(.*)$"
)
SPLIT_COMPOUND_SECTION_RE = re.compile(r"^\s*(\d+)\s+([A-Z]{1,3})\b\s+(.*)$")
NUMERIC_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\b(?:[.)])?\s*(.*)$")
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
                parsed = _parse_compound_section_label(joined_label, title)
                if parsed:
                    return parsed
        compound = COMPOUND_SECTION_RE.match(stripped)
        if compound:
            label = compound.group(1)
            title = compound.group(2).strip()
            parsed = _parse_compound_section_label(label, title)
            if parsed:
                return parsed

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
