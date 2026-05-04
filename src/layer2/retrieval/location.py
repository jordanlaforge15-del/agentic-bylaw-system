from __future__ import annotations

import re
from typing import Literal, Protocol

from pydantic import BaseModel, Field


LocationKind = Literal["civic_address", "parcel_id", "named_place", "intersection"]


class LocationReference(BaseModel):
    """A free-floating location parameter extracted from a question.

    Carried through retrieval as a *pending parameter* — only consumed when
    traversal reaches a spatial node (a DATASET candidate). Distinct from a
    ``ResolvedLocation``, which is the output of geocoding (Phase E).
    """

    raw_text: str
    kind: LocationKind
    civic_number: str | None = None
    street: str | None = None
    unit: str | None = None
    parcel_id: str | None = None
    name: str | None = None
    streets: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class LocationExtractor(Protocol):
    """Strategy interface — swap in an LLM-backed implementation in
    production while tests use the deterministic default.
    """

    def extract(self, question_text: str) -> list[LocationReference]: ...


# Deterministic patterns. Intentionally narrow: covers civic addresses and
# PIDs cleanly; named places and intersections are LLM territory and the
# regex extractor returns nothing for them rather than guessing.
_CIVIC_PATTERN = re.compile(
    r"\b(?P<num>\d{1,5})\s+"
    r"(?P<street>[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,3})"
    r"\s+(?P<suffix>St(?:reet)?|Ave(?:nue)?|Rd|Road|Blvd|Boulevard|Dr(?:ive)?|Ln|Lane|Way|Cres(?:cent)?|Pl(?:ace)?|Ct|Court|Hwy|Highway|Pkwy|Parkway|Terr(?:ace)?)\b\.?",
    re.IGNORECASE,
)
_PID_PATTERN = re.compile(
    r"\bPID[:\s]+(?P<value>\d{6,12})\b",
    re.IGNORECASE,
)


class RegexLocationExtractor:
    """Default extractor — recognises clearly-formatted civic addresses
    ("1234 Barrington Street") and PIDs ("PID 00012345"). Anything fuzzier
    (named places, intersections, "the corner of X and Y") returns no
    references; the LLM extractor handles those when configured.
    """

    def extract(self, question_text: str) -> list[LocationReference]:
        refs: list[LocationReference] = []
        for match in _PID_PATTERN.finditer(question_text):
            refs.append(
                LocationReference(
                    raw_text=match.group(0),
                    kind="parcel_id",
                    parcel_id=match.group("value"),
                )
            )
        for match in _CIVIC_PATTERN.finditer(question_text):
            street = f"{match.group('street')} {match.group('suffix')}".strip()
            refs.append(
                LocationReference(
                    raw_text=match.group(0).strip(),
                    kind="civic_address",
                    civic_number=match.group("num"),
                    street=street,
                    confidence=0.9,
                )
            )
        return refs


def extract_location_references(
    question_text: str,
    *,
    extractor: LocationExtractor | None = None,
) -> list[LocationReference]:
    """Front door. Defaults to the deterministic regex extractor; pass an
    LLM-backed extractor to recover named places and intersections."""
    return (extractor or RegexLocationExtractor()).extract(question_text)
