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


class DatasetFeatureMatch(BaseModel):
    """A single precinct/feature match against an external geo dataset.

    Populated when ``RetrievalRequest.location`` was supplied and a fragment-
    linked dataset spatially intersects that location.
    """

    feature_id: int
    feature_key: str
    canonical_attributes: dict[str, Any] = Field(default_factory=dict)
    contains_input: bool = False
    overlap_metric: float = 0.0


class LinkedDataset(BaseModel):
    """A dataset linked to the matched fragment via Phase B's bylaw-citation
    binding (e.g. Schedule 15 -> halifax_height_precincts). Always populated
    when present on the fragment so callers can render the dataset summary
    even without a location. Spatial features are filled only when a
    location was supplied and resolved.
    """

    dataset_id: int
    name: str
    publisher: str | None = None
    feature_count: int
    crs: str
    summary_text: str
    source_image_id: int | None = None
    feature_matches: list[DatasetFeatureMatch] = Field(default_factory=list)
    location_resolver: str | None = Field(
        default=None,
        description="Name of the resolver that produced the location used for spatial matching, when applicable.",
    )


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
    retrieval_channels: list[str] = Field(
        default_factory=list,
        description=(
            "Which retrieval channel(s) surfaced this match — e.g. ['text'], "
            "['spatial'], or ['text', 'spatial']. A spatial-only match means "
            "the location intersected a linked dataset even though keyword "
            "scoring didn't pick the fragment up."
        ),
    )
    ancestor_chain: list[AncestorFragment] = Field(default_factory=list)
    cross_references: list[CrossReferenceSummary] = Field(default_factory=list)
    related_tables: list[TableSummary] = Field(default_factory=list)
    linked_datasets: list[LinkedDataset] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class LocationSlot(BaseModel):
    """Structured location parameter on retrieval requests.

    The LLM caller is expected to populate this when the question references
    a specific place — *not* by parsing the question text inside the API.
    Set the fields you have; leave the rest null. ``geometry`` short-circuits
    geocoding entirely and is the right shape for callers that already have
    a point or parcel polygon (a map UI click, an upstream geocoder).
    """

    civic_number: str | None = Field(
        default=None,
        description="Street number, e.g. '1234' or '1234A'. Pair with 'street'.",
    )
    street: str | None = Field(
        default=None,
        description="Street name including suffix, e.g. 'Barrington Street'.",
    )
    unit: str | None = Field(default=None, description="Optional unit/apartment qualifier.")
    parcel_id: str | None = Field(
        default=None,
        description="Parcel identifier (PID) when known. Bypasses address geocoding.",
    )
    named_place: str | None = Field(
        default=None,
        description="Named place, e.g. 'Halifax Citadel'. Coarser than civic_address; produces lower-confidence matches.",
    )
    intersection_streets: list[str] = Field(
        default_factory=list,
        description="Two or more street names for an intersection reference.",
    )
    geometry: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional GeoJSON geometry (Point/Polygon) in EPSG:4326. When set, "
            "skips geocoding and intersects directly against any spatial datasets."
        ),
    )


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
    location: LocationSlot | None = Field(
        default=None,
        description=(
            "Structured location slot. If the user question references a specific address, parcel, "
            "intersection, named place, or coordinate, populate this field rather than embedding the "
            "address in 'query'. The retrieval API will use it to drive spatial filtering of any "
            "linked geo datasets (e.g. height precincts)."
        ),
    )
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
    include_datasets: bool = Field(
        default=True,
        description="Include any external geo datasets linked to matching fragments.",
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

