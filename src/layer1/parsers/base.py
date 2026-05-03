from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from layer1.models.schemas import ImageData, PageBlockData, TableData
from layer1.profiles import ParsingProfile


@dataclass
class ParseResult:
    page_blocks: list[PageBlockData]
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageData] = field(default_factory=list)
    page_count: int | None = None
    parser_version: str | None = None
    warnings: list[str] = field(default_factory=list)
    raw: dict | None = None


class ParserAdapter:
    name = "base"

    def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile: ParsingProfile | None = None) -> ParseResult:
        raise NotImplementedError
