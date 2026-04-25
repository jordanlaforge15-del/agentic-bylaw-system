from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ParsingProfile:
    name: str
    allow_compound_section_labels: bool = False
    allow_docling_section_labels_from_list_blocks: bool = False
    protect_docling_headings_from_boilerplate: bool = False
    promote_roman_subclauses_after_heading: bool = False
    definition_start_re: re.Pattern[str] = re.compile(r'(?m)^\s*["“][^"\n]{2,80}?["”]\s+means\b')
    term_definition_re: re.Pattern[str] = re.compile(
        r"^\s*['\"“]?[A-Z][A-Za-z0-9 /&,'()-]{2,100}['\"”]?\s+(?:means|includes)\b",
        re.IGNORECASE,
    )
    term_reference_re: re.Pattern[str] = re.compile(
        r"^\s*['\"“]?[A-Z][A-Za-z0-9 /&,'()-]{2,100}['\"”]?\s*[-:]\s*see\b",
        re.IGNORECASE,
    )
    docling_definition_like_re: re.Pattern[str] = re.compile(
        r"^\s*['\"“]?[A-Z][A-Za-z0-9 /&,'()-]{2,100}['\"”]?\s+(?:means|includes)\b",
        re.IGNORECASE,
    )


DEFAULT_PROFILE = ParsingProfile(name="default")


HALIFAX_PROFILE = ParsingProfile(
    name="halifax",
    allow_compound_section_labels=True,
    allow_docling_section_labels_from_list_blocks=True,
    protect_docling_headings_from_boilerplate=True,
    promote_roman_subclauses_after_heading=True,
    term_definition_re=re.compile(
        r"^\s*['\"“]?[A-Z][A-Za-z0-9 /&,'()-]{2,100}['\"”]?\s+(?:means|includes|shall\s+mean|shall\s+include|\(deleted\b)",
        re.IGNORECASE,
    ),
    term_reference_re=re.compile(
        r"^\s*['\"“]?[A-Z][A-Za-z0-9 /&,'()-]{2,100}['\"”]?\s*[-:]\s*see\b",
        re.IGNORECASE,
    ),
    docling_definition_like_re=re.compile(
        r"^\s*['\"“]?[A-Z][A-Za-z0-9 /&,'()-]{2,100}['\"”]?\s+(?:means|includes|shall\s+mean|shall\s+include|\(Deleted\b)",
        re.IGNORECASE,
    ),
)


PROFILES: dict[str, ParsingProfile] = {
    DEFAULT_PROFILE.name: DEFAULT_PROFILE,
    HALIFAX_PROFILE.name: HALIFAX_PROFILE,
}


def get_parsing_profile(name: str | ParsingProfile | None) -> ParsingProfile:
    if isinstance(name, ParsingProfile):
        return name
    if not name:
        return HALIFAX_PROFILE
    key = name.strip().lower()
    try:
        return PROFILES[key]
    except KeyError as exc:
        available = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown parsing profile '{name}'. Available profiles: {available}") from exc


def available_profile_names() -> list[str]:
    return sorted(PROFILES)
