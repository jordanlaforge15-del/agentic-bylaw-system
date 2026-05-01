from __future__ import annotations

import re

from layer1.models.enums import BlockType, ParseStatus
from layer1.models.schemas import PageBlockData, TableCellData, TableData
from layer1.pipeline.block_classifier import detect_table_like_text, normalize_text


MULTISPACE_SPLIT_RE = re.compile(r"\s{2,}")


def extract_fallback_tables(blocks: list[PageBlockData]) -> list[TableData]:
    tables: list[TableData] = []
    current_group: list[tuple[int, PageBlockData]] = []

    def flush_group() -> None:
        nonlocal current_group
        if not current_group:
            return
        source_indices = [idx for idx, _ in current_group]
        text = "\n".join(block.raw_text.strip() for _, block in current_group if block.raw_text.strip())
        if not detect_table_like_text(text):
            current_group = []
            return
        cells = _extract_cells(text)
        if cells:
            tables.append(
                TableData(
                    page_start=current_group[0][1].page_number,
                    page_end=current_group[-1][1].page_number,
                    parse_status=ParseStatus.FALLBACK,
                    cells=cells,
                    metadata={
                        "source_block_index": source_indices[0],
                        "source_block_indices": source_indices,
                        "parser": "multiline_table_fallback",
                    },
                )
            )
        current_group = []

    for block_index, block in enumerate(blocks):
        if block.block_type == BlockType.TABLE_REGION and "|" not in block.raw_text:
            if (
                current_group
                and (
                    block.page_number != current_group[-1][1].page_number
                    or block.reading_order != current_group[-1][1].reading_order + 1
                )
            ):
                flush_group()
            current_group.append((block_index, block))
            continue
        flush_group()

    flush_group()
    return tables


def _extract_cells(text: str) -> list[TableCellData]:
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    cells: list[TableCellData] = []
    for row_index, row in enumerate(rows):
        columns = [normalize_text(part) for part in MULTISPACE_SPLIT_RE.split(row) if normalize_text(part)]
        if len(columns) == 1:
            columns = [normalize_text(row)]
        for col_index, value in enumerate(columns):
            cells.append(TableCellData(row_index=row_index, col_index=col_index, text=value))
    return cells
