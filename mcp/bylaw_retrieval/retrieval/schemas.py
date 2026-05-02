from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DocumentSummary(BaseModel):
    id: int
    municipality: str
    bylaw_name: str
    source_url: str | None = None
    version_label: str | None = None
    consolidation_date: str | None = None
    page_count: int | None = None
    parser_version: str | None = None
    ingestion_timestamp: datetime


class AncestorFragment(BaseModel):
    id: int
    fragment_type: str
    citation_label: str | None = None
    citation_path: str | None = None
    page_start: int
    page_end: int
    text: str


class CrossReferenceSummary(BaseModel):
    id: int
    raw_reference_text: str
    target_citation_guess: str | None = None
    resolution_status: str
    confidence: float | None = None
    target_fragment_id: int | None = None
    target_citation_path: str | None = None


class TableCellSummary(BaseModel):
    row_index: int
    col_index: int
    text: str
    row_header_path: str | None = None
    col_header_path: str | None = None


class TableSummary(BaseModel):
    id: int
    caption: str | None = None
    page_start: int
    page_end: int
    parse_status: str
    parent_fragment_id: int | None = None
    cells: list[TableCellSummary] = Field(default_factory=list)


class RetrievalMatch(BaseModel):
    fragment_id: int
    document_id: int
    municipality: str
    bylaw_name: str
    fragment_type: str
    citation_label: str | None = None
    citation_path: str | None = None
    page_start: int
    page_end: int
    parse_status: str
    confidence: float | None = None
    text: str
    score: float
    ancestor_chain: list[AncestorFragment] = Field(default_factory=list)
    cross_references: list[CrossReferenceSummary] = Field(default_factory=list)
    related_tables: list[TableSummary] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class RetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language retrieval query.")
    document_id: int | None = Field(default=None, description="Optional document scope.")
    municipality: str | None = Field(default=None, description="Optional municipality filter.")
    bylaw_name: str | None = Field(default=None, description="Optional bylaw-name filter.")
    citation_path_prefix: str | None = Field(
        default=None,
        description="Optional citation prefix such as '4.2' or 'Schedule B'.",
    )
    page: int | None = Field(default=None, ge=1, description="Optional exact page filter.")
    page_start: int | None = Field(default=None, ge=1, description="Optional page range start.")
    page_end: int | None = Field(default=None, ge=1, description="Optional page range end.")
    include_context: bool = Field(
        default=True,
        description="Include ancestor chain and related context for each match.",
    )
    include_cross_references: bool = Field(
        default=True,
        description="Include cross-references attached to matching fragments.",
    )
    include_tables: bool = Field(
        default=True,
        description="Include nearby or attached tables when present.",
    )
    limit: int = Field(default=8, ge=1, le=25)


class RetrievalResponse(BaseModel):
    request: RetrievalRequest
    total_matches: int
    matches: list[RetrievalMatch] = Field(default_factory=list)


class CitationLookupRequest(BaseModel):
    citation_path: str = Field(..., min_length=1, description="Exact citation path to retrieve.")
    document_id: int | None = Field(default=None, description="Optional document scope.")
    include_context: bool = Field(default=True)
    include_cross_references: bool = Field(default=True)
    include_tables: bool = Field(default=True)


class DocumentOutlineItem(BaseModel):
    fragment_id: int
    fragment_type: str
    citation_label: str | None = None
    citation_path: str | None = None
    page_start: int
    page_end: int
    text: str


class DocumentOutlineResponse(BaseModel):
    document: DocumentSummary
    fragments: list[DocumentOutlineItem] = Field(default_factory=list)

