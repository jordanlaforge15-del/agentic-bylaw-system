from __future__ import annotations

from pathlib import Path

from layer1.models.schemas import BBox, PageBlockData, TableCellData, TableData
from layer1.models.enums import ParseStatus
from layer1.parsers.base import ParseResult, ParserAdapter
from layer1.parsers.table_fallback import extract_fallback_tables
from layer1.pipeline.block_classifier import classify_text_block, mark_boilerplate, normalize_text
from layer1.profiles import ParsingProfile, get_parsing_profile


class TextParser(ParserAdapter):
    name = "text-fallback"

    def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile: ParsingProfile | None = None) -> ParseResult:
        profile = get_parsing_profile(profile)
        text = path.read_text(encoding="utf-8")
        pages = text.split("\f")
        blocks: list[PageBlockData] = []
        tables: list[TableData] = []
        order = 0
        for page_number, page in enumerate(pages, start=1):
            for line_number, raw_line in enumerate(page.splitlines(), start=1):
                if not raw_line.strip():
                    continue
                block_type = classify_text_block(raw_line, profile=profile)
                block = PageBlockData(
                    page_number=page_number,
                    block_type=block_type,
                    bbox=BBox(x0=0, y0=float(line_number * 12), x1=612, y1=float(line_number * 12 + 10)),
                    reading_order=order,
                    raw_text=raw_line.rstrip(),
                    normalized_text=normalize_text(raw_line),
                    parser_source=self.name,
                    confidence=0.7,
                )
                blocks.append(block)
                order += 1
        mark_boilerplate(blocks, profile=profile)
        tables.extend(_extract_pipe_tables(blocks))
        tables.extend(extract_fallback_tables(blocks))
        return ParseResult(
            page_blocks=blocks,
            tables=tables,
            page_count=len(pages),
            parser_version=self.name,
            raw={"source": "plain_text_lines"} if debug else None,
        )


def _extract_pipe_tables(blocks: list[PageBlockData]) -> list[TableData]:
    tables: list[TableData] = []
    for block_index, block in enumerate(blocks):
        if "|" not in block.raw_text:
            continue
        rows = [row.strip() for row in block.raw_text.split(";") if row.strip()]
        cells: list[TableCellData] = []
        for r_idx, row in enumerate(rows):
            cols = [col.strip() for col in row.strip("|").split("|")]
            if len(cols) < 2:
                continue
            for c_idx, col in enumerate(cols):
                cells.append(TableCellData(row_index=r_idx, col_index=c_idx, text=col))
        if cells:
            tables.append(
                TableData(
                    page_start=block.page_number,
                    page_end=block.page_number,
                    parse_status=ParseStatus.FALLBACK,
                    cells=cells,
                    metadata={"source_block_index": block_index, "parser": "pipe_table_fallback"},
                )
            )
    return tables
