from __future__ import annotations

import re

from layer1.models.enums import ResolutionStatus
from layer1.models.schemas import CrossReferenceData, FragmentData

REFERENCE_PATTERNS = [
    re.compile(
        r"\b(?:section|sections|subsection|subsections)\s+(\d+(?:\.\d+){0,5})\s+to\s+(\d+(?:\.\d+){0,5})\s+inclusive\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:section|sections|subsection|subsections)\s+(\d+(?:\.\d+){0,5})\b(?!\s+to\s+\d)", re.IGNORECASE),
    re.compile(r"\bSchedule\s+([A-Z]|\d+)\b", re.IGNORECASE),
    re.compile(r"\b(?:provided\s+in|provided\s+by|subject\s+to|except\s+as\s+provided\s+in)\s+(\d+(?:\.\d+){0,5})\b", re.IGNORECASE),
]


def detect_cross_references(fragments: list[FragmentData]) -> list[CrossReferenceData]:
    by_label = {fragment.citation_label: idx for idx, fragment in enumerate(fragments) if fragment.citation_label}
    refs: list[CrossReferenceData] = []
    seen: set[tuple[int, str, str | None]] = set()
    for idx, fragment in enumerate(fragments):
        for regex in REFERENCE_PATTERNS:
            for match in regex.finditer(fragment.text):
                raw = match.group(0)
                if regex.groups == 2:
                    start_token = match.group(1)
                    end_token = match.group(2)
                    target = f"{start_token} to {end_token}"
                    target_idx = by_label.get(start_token)
                else:
                    token = match.group(1)
                    if raw.lower().startswith("schedule"):
                        target = f"Schedule {token.upper()}"
                    else:
                        target = token
                    target_idx = by_label.get(target)
                if raw.lower().startswith("schedule"):
                    target = f"Schedule {token.upper()}"
                key = (idx, raw, target)
                if key in seen:
                    continue
                seen.add(key)
                refs.append(
                    CrossReferenceData(
                        source_fragment_index=idx,
                        raw_reference_text=raw,
                        target_citation_guess=target,
                        target_fragment_index=target_idx,
                        resolution_status=ResolutionStatus.RESOLVED if target_idx is not None else ResolutionStatus.UNRESOLVED,
                        confidence=0.85 if target_idx is not None else 0.65,
                    )
                )
    return refs
