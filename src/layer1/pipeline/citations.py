from __future__ import annotations

import re
from dataclasses import dataclass

from layer1.models.enums import FragmentType


PART_RE = re.compile(r"^\s*part\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
SCHEDULE_RE = re.compile(r"^\s*schedule\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
APPENDIX_RE = re.compile(r"^\s*appendix\s+([A-Z]|\d+)\b(?:\s*[-:]\s*)?(.*)$", re.IGNORECASE)
NUMERIC_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\b(?:[.)])?\s*(.*)$")
NUMERIC_PAREN_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5}(?:\([A-Za-z0-9]+\))+)\s+(.*)$")
PAREN_NUMERIC_RE = re.compile(r"^\s*\((\d+(?:\.\d+){0,5})\)\s+(.*)$")
CLAUSE_RE = re.compile(r"^\s*\(([a-z])\)\s+(.*)$")
SUBCLAUSE_RE = re.compile(r"^\s*\(([ivxlcdm]+)\)\s+(.*)$", re.IGNORECASE)
FOOTNOTE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\s+)(.+)$")


@dataclass(frozen=True)
class CitationMatch:
    fragment_type: FragmentType
    label: str
    level: int
    title: str
    confidence: float


def parse_citation_label(text: str) -> CitationMatch | None:
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

    match = NUMERIC_PAREN_RE.match(stripped)
    if match:
        return CitationMatch(FragmentType.SECTION, match.group(1), 2, match.group(2).strip(), 0.9)

    match = PAREN_NUMERIC_RE.match(stripped)
    if match:
        return CitationMatch(FragmentType.SUBSECTION, f"({match.group(1)})", 3, match.group(2).strip(), 0.85)

    match = NUMERIC_RE.match(stripped)
    if match:
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

    match = CLAUSE_RE.match(stripped)
    if match:
        return CitationMatch(FragmentType.CLAUSE, f"({match.group(1)})", 5, match.group(2).strip(), 0.85)

    match = SUBCLAUSE_RE.match(stripped)
    if match:
        return CitationMatch(FragmentType.SUBCLAUSE, f"({match.group(1).lower()})", 6, match.group(2).strip(), 0.85)

    return None


def citation_path(parent_path: str | None, label: str | None) -> str | None:
    if not label:
        return parent_path
    return f"{parent_path} > {label}" if parent_path else label
