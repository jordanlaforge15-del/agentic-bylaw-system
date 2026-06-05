from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    location_confidence: float | None = Field(
        default=None,
        description=(
            "Confidence (0..1) of the geocoded location used for spatial "
            "matching. Values below ~0.85 indicate the geocoder fell back "
            "to RANGE_INTERPOLATED or GEOMETRIC_CENTER quality — the "
            "resulting feature_matches may be approximate. Callers should "
            "qualify any answer based on a low-confidence match."
        ),
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
    attribute_tag_filter: list[str] | None = Field(
        default=None,
        description=(
            "Hard pre-filter on the candidate fragment set. When set, only "
            "fragments whose ``source_fragment.attribute_tags`` array contains "
            "at least one of the supplied attribute IDs are considered for "
            "scoring (the multiple-tag case is a union). Default None "
            "preserves prior behaviour for non-evaluator callers. Populated by "
            "the compliance evaluator (one search per submission attribute) "
            "so per-attribute retrieval is O(clauses-tagged-with-this-attribute) "
            "instead of O(all-clauses)."
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
    limit: int = Field(
        default=5,
        ge=1,
        le=50,
        description=(
            "Maximum number of fragments to return. Default 5; bump to 15 when the "
            "question covers multiple dimensions (e.g. zone feasibility + height + setbacks); "
            "cap is 50. Higher values reduce iteration count at the cost of larger tool-result payloads."
        ),
    )


class RetrievalResponse(BaseModel):
    request: RetrievalRequest
    total_matches: int
    matches: list[RetrievalMatch] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "Server-side advisories about how the request was handled. "
            "Populated when the server detects that the caller could have "
            "produced better results by structuring the request differently "
            "— e.g. an address embedded in the 'query' string but no "
            "'location' field supplied. LLM clients should read these notes "
            "and adjust subsequent calls accordingly."
        ),
    )


# High-frequency attribute vocabulary visible in the TC-005 tool-sequence
# baseline.  Unknown attributes are rejected before the DB is touched so
# the model gets a tight error with the accepted list rather than an empty
# suggestions envelope.
ATTRIBUTE_VOCABULARY: frozenset[str] = frozenset(
    [
        "max_height",
        "max_height_storeys",
        "max_lot_coverage",
        "min_front_setback",
        "min_side_setback",
        "min_rear_setback",
        "max_far",
        "permitted_uses",
        "parking_requirement",
    ]
)


class ZoneAttributeQuery(BaseModel):
    """Look up a zone's attribute rule, e.g. max height for HR-2."""

    kind: Literal["zone_attribute"] = "zone_attribute"
    zone: str = Field(..., description="Zone code, e.g. 'HR-2'.")
    attribute: str = Field(
        ...,
        description=(
            f"Attribute to retrieve. Accepted values: {sorted(ATTRIBUTE_VOCABULARY)}"
        ),
    )


class ScheduleRowQuery(BaseModel):
    """Look up a specific row in a named schedule."""

    kind: Literal["schedule_row"] = "schedule_row"
    schedule: str = Field(..., description="Schedule name, e.g. 'Table 1A'.")
    row: str = Field(..., description="Row identifier within the schedule, e.g. 'HR-2'.")


# Discriminated union — the 'kind' field lets Pydantic (and JSON Schema
# validators) route the payload to the right variant without ambiguity.
StructuredCitationQuery = Annotated[
    ZoneAttributeQuery | ScheduleRowQuery,
    Field(discriminator="kind"),
]


class CitationLookupRequest(BaseModel):
    """Request envelope for ``lookup_citation``.

    Exactly one of ``citation_path`` or ``structured`` must be supplied.
    Supplying both raises a Pydantic ``ValidationError``; supplying neither
    also raises.  This is enforced by the ``@model_validator`` below rather
    than at the JSON-Schema level so the error message can name both fields.
    """

    citation_path: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Exact citation path to retrieve, e.g. '4.2' or 'Schedule B > 3'. "
            "Mutually exclusive with 'structured'."
        ),
    )
    structured: StructuredCitationQuery | None = Field(
        default=None,
        description=(
            "Structured query variant.  Use instead of 'citation_path' when "
            "the zone code and attribute (or schedule + row) are known. "
            "Mutually exclusive with 'citation_path'."
        ),
    )
    document_id: int | None = Field(default=None, description="Optional document scope.")
    include_context: bool = Field(default=True)
    include_cross_references: bool = Field(default=True)
    include_tables: bool = Field(default=True)

    @model_validator(mode="after")
    def _require_exactly_one(self) -> "CitationLookupRequest":
        has_path = self.citation_path is not None
        has_structured = self.structured is not None
        if has_path and has_structured:
            raise ValueError(
                "Provide either 'citation_path' or 'structured', not both."
            )
        if not has_path and not has_structured:
            raise ValueError(
                "One of 'citation_path' or 'structured' is required."
            )
        return self


class CitationLookupResponse(BaseModel):
    """Result envelope for ``lookup_citation``.

    Before ABS-261 the service returned ``RetrievalMatch`` directly and
    raised ``ValueError`` when nothing matched. That forced the agent's
    tool-use loop to treat a missed path as a tool error, retry with
    random variations, and burn through ``max_iterations`` worth of
    ``messages.create`` calls before falling back to synthesis (~2×
    the necessary token spend on ~38% of multi-turn advisor turns).

    The envelope now distinguishes three cases — and only one of them
    is an exception (ambiguous-across-documents, which the caller
    must resolve before any sensible match exists):

    * ``match`` populated, ``suggestions`` empty: exact hit.
    * ``match`` is ``None``, ``suggestions`` non-empty: no exact path
      matched, but here are the closest stored ``citation_path``
      values ranked by fuzzy score. The caller should pick one and
      re-issue ``lookup_citation``.
    * ``match`` is ``None``, ``suggestions`` empty: nothing remotely
      similar exists in this document (e.g. ``Schedule 99``). The
      caller should switch tools to ``search_bylaw_evidence`` or
      ``get_document_outline`` instead of more lookups.
    """

    match: RetrievalMatch | None = Field(
        default=None,
        description=(
            "The fragment for an exact citation_path hit. Null when the "
            "requested path did not resolve in the scoped document."
        ),
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description=(
            "Ranked candidate citation_path values when ``match`` is null. "
            "Pick the closest one and re-issue lookup_citation with it; do "
            "NOT guess further variants."
        ),
    )


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


class OverlayRef(BaseModel):
    """A single schedule-keyed overlay that intersects a resolved address.

    Produced by ``get_address_profile`` for every linked geo dataset whose
    feature contains (or overlaps) the resolved point — zoning, height,
    FAR, heritage, bonus-zoning, shadow-impact, etc. The dedicated
    ``AddressProfile`` fields (``zone``, ``height_precinct``, …) carry the
    headline value for the well-known overlays; ``overlays`` is the
    exhaustive list so a caller can read any additional schedule-keyed
    layer the corpus links without the DTO having to grow a field per
    schedule.
    """

    kind: str = Field(
        ...,
        description=(
            "Coarse overlay class derived from the linked dataset — one of "
            "'zone', 'height_precinct', 'far_precinct', 'heritage', "
            "'bonus_zoning', 'shadow_impact', or 'overlay' (catch-all)."
        ),
    )
    dataset_name: str = Field(..., description="The linked ExternalDataset.name.")
    label: str | None = Field(
        default=None,
        description=(
            "Human-readable value for this overlay — the zone code, the "
            "synthesised precinct label, the heritage district name, etc."
        ),
    )
    citation: str | None = Field(
        default=None,
        description=(
            "The bylaw citation the dataset is bound to (e.g. 'Schedule 15'), "
            "copied from the dataset's linked fragment."
        ),
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The matched feature's canonical attributes (zone_code, "
            "max_height_m, max_far, district_name, …) verbatim."
        ),
    )


class CitationRef(BaseModel):
    """A traceable pointer from a populated thick-DTO field (ZoneProfile,
    AddressProfile, …) back to the authoritative bylaw source fragment.

    Unified across get_zone_profile and get_address_profile. ``backs``
    names which DTO facet(s)/field(s) this citation supports — it
    subsumes the former ``source`` (single facet, e.g. 'zone',
    'height_precinct') and ``fields`` (zone-profile field names, e.g.
    ['max_height_m', 'max_lot_coverage_pct']). ``citation_path`` is
    optional: address overlays may cite a dataset that has no resolvable
    path, while zone-field citations always set it (and can pass it to
    lookup_citation to retrieve the original fragment).
    """

    citation_path: str | None = Field(
        default=None,
        description="Stored citation_path of the linked fragment, if any; resolvable via lookup_citation.",
    )
    citation_label: str | None = Field(
        default=None,
        description="Human-readable citation label (falls back to the dataset's linked_fragment_citation).",
    )
    document_id: int | None = Field(default=None, description="Owning document id, if resolved.")
    municipality: str | None = Field(default=None, description="Owning document municipality.")
    bylaw_name: str | None = Field(default=None, description="Owning document bylaw name.")
    page_start: int | None = Field(default=None, description="Source page range start.")
    page_end: int | None = Field(default=None, description="Source page range end.")
    backs: list[str] = Field(
        default_factory=list,
        description=(
            "Which DTO facet(s)/field(s) this citation supports. Single-element "
            "for address overlays (e.g. ['zone']); multi-element for zone fields "
            "(e.g. ['max_height_m', 'max_lot_coverage_pct'])."
        ),
    )


class AddressProfile(BaseModel):
    """One-call grounding context for a case-bound, address-anchored query.

    Collapses the address → zone spatial resolution plus every linked
    overlay lookup (height precinct, FAR precinct, heritage, bonus zoning,
    …) into a single response, so the agent gets the case's context up
    front instead of spending 4+ tool calls assembling it. Built by
    ``RetrievalService.get_address_profile``; see FR-3.2.

    When the address cannot be geocoded/matched, ``unresolvable`` is True
    and the spatial fields stay null — the method never raises (FR-3.4).
    """

    address: str = Field(
        ..., description="The address echoed back (canonical form when resolved)."
    )
    civic_number: str | None = Field(default=None, description="Parsed civic number, if any.")
    street: str | None = Field(default=None, description="Parsed street name, if any.")
    pid: str | None = Field(default=None, description="Parcel identifier, when supplied or resolved.")
    zone: str | None = Field(default=None, description="Zone code, e.g. 'HR-2'.")
    zone_chapter: str | None = Field(
        default=None,
        description=(
            "Bylaw chapter/part that governs the zone, e.g. 'Part V'. Left "
            "null until the Phase-2 zone-profile composition lands; the thin "
            "tools surface the zone detail in the meantime."
        ),
    )
    height_precinct: str | None = Field(
        default=None, description="Height-precinct label, e.g. 'HP-25' (from Schedule 15)."
    )
    far_precinct: str | None = Field(
        default=None, description="Floor-area-ratio precinct label, e.g. 'FA-3.5' (from Schedule 17)."
    )
    heritage: bool | None = Field(
        default=None,
        description=(
            "True when the address falls in a heritage conservation district; "
            "False when a heritage dataset was checked and did not match; null "
            "when no heritage dataset is in scope."
        ),
    )
    bonus_zoning_eligible: bool | None = Field(
        default=None,
        description=(
            "True when the address falls in a bonus/incentive zoning rate "
            "district; False when checked and unmatched; null when no bonus "
            "dataset is in scope."
        ),
    )
    overlays: list[OverlayRef] = Field(
        default_factory=list,
        description="Every schedule-keyed overlay that intersected the resolved address.",
    )
    citations: list[CitationRef] = Field(
        default_factory=list,
        description="One citation per overlay that contributed a value.",
    )
    unresolvable: bool = Field(
        default=False,
        description="True when the address could not be geocoded/matched; spatial fields stay null.",
    )


# ---------------------------------------------------------------------------
# ABS-272 — get_zone_profile thick tool
#
# A "thick" tool collapses the 3–5 thin retrieval round-trips an agent
# previously made to assemble a single zone's standards (height,
# coverage, setbacks, uses, parking) into ONE call. The DTO below is
# the return shape. Every nested field is optional: ``None`` means the
# bylaw does not specify the value OR semantic retrieval could not
# extract it with enough confidence. The intelligence is preserved
# because the server-side implementation composes the same semantic
# ``search_bylaw_evidence`` the agent would have called — it just does
# so in one place and extracts the structured values for the caller.
# ---------------------------------------------------------------------------


class ZoneDimensions(BaseModel):
    """Dimensional standards for a zone. All fields optional — ``None``
    when the bylaw doesn't specify the value or it couldn't be extracted
    with sufficient confidence.
    """

    max_height_m: float | None = Field(
        default=None,
        description="Maximum building height in metres. None when governed by an external precinct schedule or unspecified.",
    )
    max_lot_coverage_pct: float | None = Field(
        default=None,
        description="Maximum lot coverage as a percentage (e.g. 65 for 65%).",
    )
    front_setback_m: float | None = Field(default=None, description="Minimum front setback in metres.")
    side_setback_m: float | None = Field(default=None, description="Minimum side setback in metres.")
    rear_setback_m: float | None = Field(default=None, description="Minimum rear setback in metres.")
    max_far: float | None = Field(
        default=None,
        description="Maximum floor area ratio. None when governed by an external schedule or unspecified.",
    )


class ZoneUses(BaseModel):
    """Use permissions for a zone. ``permitted`` lists explicitly
    permitted uses; ``not_permitted`` lists uses explicitly marked as
    not permitted. Either may be empty when retrieval found no use table
    for the zone.
    """

    permitted: list[str] = Field(
        default_factory=list,
        description="Uses explicitly permitted in this zone (table cell 'P').",
    )
    not_permitted: list[str] = Field(
        default_factory=list,
        description="Uses explicitly NOT permitted in this zone (table cell 'N').",
    )


class ZoneParking(BaseModel):
    """Parking applicability and references for a zone. Parking rules in
    the Regional Centre LUB are general (not per-zone) with per-zone
    exemptions, so ``applies`` records whether the general requirement is
    waived for this zone.
    """

    applies: bool | None = Field(
        default=None,
        description="True when off-street parking is required in this zone; False when the zone is explicitly exempt.",
    )
    min_spaces_per_dwelling_unit: float | None = Field(
        default=None,
        description="Minimum off-street parking spaces required per dwelling unit, when specified.",
    )
    schedule_reference: str | None = Field(
        default=None,
        description="Reference to the schedule/table governing non-residential or detailed parking ratios (e.g. 'Table 8').",
    )
    notes: str | None = Field(
        default=None,
        description="Short free-text note (e.g. the exemption clause text) for context.",
    )


class ZoneProfile(BaseModel):
    """One-call profile of a zone's standards (ABS-272).

    Returned by ``get_zone_profile``. Sections are populated according
    to the caller's ``include`` filter; ``citations`` is always
    populated and every non-null field traces to at least one entry in
    it. ``unknown_zone`` distinguishes "the zone wasn't found" from "the
    zone exists but the bylaw is silent on these fields" — the former
    returns an otherwise-empty profile WITHOUT raising (FR-2.5).
    """

    zone: str = Field(..., description="The zone code, echoed from the request (e.g. 'HR-2').")
    zone_full_name: str | None = Field(
        default=None, description="Expanded zone name (e.g. 'Higher Order Residential 2'), when extractable."
    )
    chapter: str | None = Field(
        default=None, description="Top-level chapter/part the zone is established under (e.g. 'Part II')."
    )
    dimensions: ZoneDimensions | None = Field(
        default=None, description="Dimensional standards; populated when 'dimensions' is in the include set."
    )
    uses: ZoneUses | None = Field(
        default=None, description="Use permissions; populated when 'uses' is in the include set."
    )
    parking: ZoneParking | None = Field(
        default=None, description="Parking applicability; populated when 'parking' is in the include set."
    )
    citations: list[CitationRef] = Field(
        default_factory=list,
        description="Source citations for every populated field. Always present (empty only for an unknown zone).",
    )
    unknown_zone: bool = Field(
        default=False,
        description="True when no fragment mentioning the zone could be found. citations is empty and all sections null.",
    )
    confidence: dict[str, float] = Field(
        default_factory=dict,
        description="Per-field semantic match confidence (0..1), keyed by DTO field name, for populated fields.",
    )

