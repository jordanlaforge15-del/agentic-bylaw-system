from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PageCoverage(BaseModel):
    page_number: int
    source_char_count: int = 0
    fragment_char_count: int = 0
    overlap_ratio: float = 0.0
    fragment_count: int = 0
    table_count: int = 0
    cross_reference_count: int = 0
    uncertain_fragment_count: int = 0
    unaccounted_block_count: int = 0
    gaps: list[CoverageGap] = Field(default_factory=list)


class CoverageGap(BaseModel):
    gap_type: str
    severity: str
    page: int | None = None
    detail: str
    sample_text: str | None = None


class CoverageSummary(BaseModel):
    page_count: int = 0
    pages_with_fragments: int = 0
    pages_without_fragments: list[int] = Field(default_factory=list)
    total_fragments: int = 0
    parsed_fragments: int = 0
    uncertain_fragments: int = 0
    fragments_without_citation: int = 0
    orphan_fragments: int = 0
    total_tables: int = 0
    tables_without_cells: int = 0
    total_cross_references: int = 0
    resolved_cross_references: int = 0
    unresolved_cross_references: int = 0
    mean_page_overlap: float = 0.0
    low_coverage_pages: list[int] = Field(default_factory=list)
    grade: str = "F"


class DocumentCoverageReport(BaseModel):
    document_id: int
    municipality: str | None = None
    bylaw_name: str | None = None
    summary: CoverageSummary = Field(default_factory=CoverageSummary)
    page_details: list[PageCoverage] = Field(default_factory=list)
    gaps: list[CoverageGap] = Field(default_factory=list)
