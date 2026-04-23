from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from layer1.models.enums import BlockType, FragmentType, ParseStatus, ResolutionStatus


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class PageBlockData(BaseModel):
    page_number: int
    block_type: BlockType
    bbox: BBox | None = None
    reading_order: int
    raw_text: str
    normalized_text: str | None = None
    is_boilerplate: bool = False
    parser_source: str
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FragmentData(BaseModel):
    fragment_type: FragmentType
    citation_label: str | None = None
    citation_path: str | None = None
    parent_index: int | None = None
    page_start: int
    page_end: int
    reading_order_start: int | None = None
    reading_order_end: int | None = None
    text: str
    parse_status: ParseStatus = ParseStatus.PARSED
    confidence: float | None = None
    source_block_indices: list[int] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TableCellData(BaseModel):
    row_index: int
    col_index: int
    text: str
    row_header_path: str | None = None
    col_header_path: str | None = None
    bbox: BBox | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TableData(BaseModel):
    parent_fragment_index: int | None = None
    caption: str | None = None
    page_start: int
    page_end: int
    parse_status: ParseStatus = ParseStatus.PARSED
    cells: list[TableCellData] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CrossReferenceData(BaseModel):
    source_fragment_index: int
    raw_reference_text: str
    target_citation_guess: str | None = None
    target_fragment_index: int | None = None
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentMetadata(BaseModel):
    municipality: str
    bylaw_name: str
    source_path: str
    source_url: str | None = None
    file_hash: str
    version_label: str | None = None
    consolidation_date: date | None = None
    mime_type: str
    page_count: int | None = None
    ingestion_timestamp: datetime
    parser_version: str | None = None


class ValidationReport(BaseModel):
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors
