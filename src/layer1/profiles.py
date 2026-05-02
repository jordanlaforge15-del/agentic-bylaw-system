from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING

from layer1.models.enums import BlockType

if TYPE_CHECKING:
    from layer1.parsers.base import ParseResult


@dataclass(frozen=True)
class ParsingProfile:
    name: str
    allow_compound_section_labels: bool = False
    allow_docling_section_labels_from_list_blocks: bool = False
    protect_docling_headings_from_boilerplate: bool = False
    promote_roman_subclauses_after_heading: bool = False
    use_full_docling: bool = True
    use_docling_table_structure: bool = True
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

    def applies_to(self, path: Path) -> bool:
        return False

    def postprocess_parse_result(self, result: ParseResult) -> ParseResult:
        return result


class RegionalCentreLandUseBylawProfile(ParsingProfile):
    _zone_hyphen_re = re.compile(r"\b([A-Z]{1,4})-\s+(\d[A-Z]?)\b")
    _split_rule_re = re.compile(
        r"^("
        r"\(\d+(?:\.\d+)?\)|"
        r"district,\s+any main building|"
        r"setback as specified on Schedule 18\.?|"
        r"not exceed the maximum required building height specified on Schedule 15\.?"
        r")",
        re.I,
    )

    def __init__(self) -> None:
        super().__init__(
            name="halifax-regional-centre-lub",
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

    def applies_to(self, path: Path) -> bool:
        normalized_name = path.name.lower()
        return "regionalcentrelub" in normalized_name or "regional centre" in normalized_name

    def postprocess_parse_result(self, result: ParseResult) -> ParseResult:
        for block in result.page_blocks:
            text = block.normalized_text or _normalize_text(block.raw_text)
            text = self._zone_hyphen_re.sub(r"\1-\2", text)
            block.normalized_text = text
            if block.page_number <= 6:
                block.is_boilerplate = True
                block.block_type = BlockType.FOOTER
            if text.startswith("Regional Centre Land Use By-law |"):
                block.is_boilerplate = True
                block.block_type = BlockType.FOOTER
            elif self._split_rule_re.search(text):
                block.is_boilerplate = False
                block.block_type = BlockType.LIST_ITEM if text.startswith("(") else BlockType.PARAGRAPH
        result.parser_version = _append_profile(result.parser_version, self.name)
        return result


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

REGIONAL_CENTRE_PROFILE = RegionalCentreLandUseBylawProfile()

PROFILES: dict[str, ParsingProfile] = {
    DEFAULT_PROFILE.name: DEFAULT_PROFILE,
    HALIFAX_PROFILE.name: HALIFAX_PROFILE,
    REGIONAL_CENTRE_PROFILE.name: REGIONAL_CENTRE_PROFILE,
}

PATH_PROFILES: tuple[ParsingProfile, ...] = (REGIONAL_CENTRE_PROFILE,)


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


def profile_for_path(path: Path, profile: str | ParsingProfile | None = None) -> ParsingProfile:
    if profile is not None:
        return get_parsing_profile(profile)
    for candidate in PATH_PROFILES:
        if candidate.applies_to(path):
            return candidate
    return HALIFAX_PROFILE


def available_profile_names() -> list[str]:
    return sorted(PROFILES)


def _append_profile(parser_version: str | None, profile_name: str) -> str:
    base = parser_version or "unknown-parser"
    return f"{base}+profile:{profile_name}"


def _normalize_text(text: str) -> str:
    return " ".join(text.split())
