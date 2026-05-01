from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from layer1.models.enums import BlockType
from layer1.parsers.base import ParseResult
from layer1.pipeline.block_classifier import normalize_text


class BylawProfile(Protocol):
    name: str

    def applies_to(self, path: Path) -> bool: ...

    def postprocess_parse_result(self, result: ParseResult) -> ParseResult: ...


class DefaultBylawProfile:
    name = "default"
    use_full_docling = True
    use_docling_table_structure = True

    def applies_to(self, path: Path) -> bool:
        return True

    def postprocess_parse_result(self, result: ParseResult) -> ParseResult:
        return result


class RegionalCentreLandUseBylawProfile(DefaultBylawProfile):
    name = "halifax-regional-centre-lub"
    use_full_docling = True
    use_docling_table_structure = True

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

    def applies_to(self, path: Path) -> bool:
        normalized_name = path.name.lower()
        return "regionalcentrelub" in normalized_name or "regional centre" in normalized_name

    def postprocess_parse_result(self, result: ParseResult) -> ParseResult:
        for block in result.page_blocks:
            text = block.normalized_text or normalize_text(block.raw_text)
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


PROFILES: tuple[BylawProfile, ...] = (
    RegionalCentreLandUseBylawProfile(),
)


def profile_for_path(path: Path) -> BylawProfile:
    for profile in PROFILES:
        if profile.applies_to(path):
            return profile
    return DefaultBylawProfile()


def _append_profile(parser_version: str | None, profile_name: str) -> str:
    base = parser_version or "unknown-parser"
    return f"{base}+profile:{profile_name}"
