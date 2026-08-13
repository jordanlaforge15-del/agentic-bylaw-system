from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_serializer, model_validator


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
    retrieval_enabled: bool = False


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

    @model_serializer(mode="wrap")
    def _omit_empty(self, handler: Any, info: Any) -> dict[str, Any]:
        d = handler(self)
        return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}


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
            "instead of O(all-clauses). Valid IDs come from the Phase-1 attribute "
            "taxonomy (``src/layer2/compliance/attributes/taxonomy.yaml``). Omit "
            "the field to search unfiltered; an EMPTY list is a validation error, "
            "not a no-op."
        ),
    )

    include_context: bool = Field(
        default=False,
        description="Include ancestor chain and related context for each match.",
    )
    include_cross_references: bool = Field(
        default=False,
        description="Include cross-references attached to matching fragments.",
    )
    include_tables: bool = Field(
        default=False,
        description="Include nearby or attached tables when present.",
    )
    include_datasets: bool = Field(
        default=False,
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

    @model_validator(mode="after")
    def _reject_empty_attribute_tag_filter(self) -> "RetrievalRequest":
        """An empty ``attribute_tag_filter`` is a caller error, not "no filter".

        ABS-479 exposed this field to the chat + OpenAI tool surfaces, so an
        LLM can now send it, and ``[]`` is exactly the shape a model emits when
        it means "no filter". The service's ``if request.attribute_tag_filter:``
        gate would accept that silently as unfiltered — the opposite of what
        ``_attribute_tag_filter_clause`` decided (it raises on empty precisely
        because an empty clause set degrades into always-true/always-false).
        Rejecting here applies the same rule to every caller as a clean
        validation error, which the chat tool loop renders as a tool error the
        model can correct instead of an unnoticed widening of the search.
        """
        if self.attribute_tag_filter is not None and not self.attribute_tag_filter:
            raise ValueError(
                "attribute_tag_filter must be non-empty; omit the field "
                "entirely to search without an attribute pre-filter"
            )
        return self


class RetrievalResponse(BaseModel):
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


class PermittedUseQuery(BaseModel):
    """Resolve a single permitted-use matrix cell: is ``use`` allowed in ``zone``?

    ABS-279 (Phase 3). Unlike ``zone_attribute`` — which returns a zone's
    whole permitted-uses prose — this addresses the exact ``(use, zone)``
    cell of the Table 1A permission matrix via the Phase-2 axis bindings and
    returns a typed permission (permitted / conditional / not_permitted) plus
    any footnote condition. Carried inside the ``lookup_citation`` structured
    envelope rather than a new top-level tool to keep the agent's tool count
    flat (Token Optimization direction).
    """

    kind: Literal["permitted_use"] = "permitted_use"
    use: str = Field(..., description="Use name, e.g. 'Restaurant use'.")
    zone: str = Field(..., description="Zone code, e.g. 'HR-2'.")


# Discriminated union — the 'kind' field lets Pydantic (and JSON Schema
# validators) route the payload to the right variant without ambiguity.
StructuredCitationQuery = Annotated[
    ZoneAttributeQuery | ScheduleRowQuery | PermittedUseQuery,
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
    include_context: bool = Field(default=False)
    include_cross_references: bool = Field(default=False)
    include_tables: bool = Field(default=False)

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
    permitted_use: "PermittedUseResult | None" = Field(
        default=None,
        description=(
            "Populated only for a structured 'permitted_use' query (ABS-279): "
            "the resolved (use, zone) permission-matrix cell. ``match`` and "
            "``suggestions`` stay empty in that case."
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
            "'bonus_zoning', 'shadow_impact', 'pedestrian_street', or "
            "'overlay' (catch-all)."
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
    # -- ABS-472: which by-law governs THIS feature ------------------------
    governing_bylaw: str | None = Field(
        default=None,
        description=(
            "The by-law that governs this particular feature, when the layer "
            "publishes it per feature (municipality-wide layers do). May "
            "differ from the by-law named in 'citation' — that one is the "
            "document the layer as a whole is linked to. Null when the layer "
            "carries no per-feature by-law attribution."
        ),
    )
    governing_bylaw_held: bool | None = Field(
        default=None,
        description=(
            "True when 'governing_bylaw' is a document in the active "
            "retrieval corpus; False when it is not held, in which case "
            "'citation' is null because there is no honest citation to give "
            "— the overlay's value is the publisher's mapping, not something "
            "this corpus can source. Null when unknown."
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


class NeighbourZone(BaseModel):
    """The zone of a single parcel abutting the subject parcel.

    Produced by ``get_adjacent_zoning`` for every parcel that touches (or
    lies within the abut buffer of) the subject parcel. ``zone`` is the
    neighbour parcel's zone code resolved by intersecting its centroid
    against the zoning overlay; ``pid`` echoes the neighbour's parcel id
    when the parcels dataset carries one. ``zone`` is null when a parcel
    abuts but its centroid did not fall inside any zoning polygon (an edge
    case for slivers/rights-of-way) — a typed absence, never a silent drop.
    """

    pid: str | None = Field(
        default=None, description="Neighbour parcel identifier, when available."
    )
    zone: str | None = Field(
        default=None,
        description=(
            "Neighbour parcel's zone code (e.g. 'DH-1'), resolved by "
            "intersecting the neighbour's centroid against the zoning "
            "overlay. Null when the neighbour centroid matched no zone "
            "polygon."
        ),
    )
    direction: str | None = Field(
        default=None,
        description=(
            "Coarse compass bearing of the neighbour relative to the subject "
            "parcel centroid — one of 'N', 'NE', 'E', 'SE', 'S', 'SW', 'W', "
            "'NW'. Lets a report say 'the parcel to the east is zoned DH-1' "
            "so the setback that depends on the abutting zone can be pinned "
            "to the correct lot line."
        ),
    )


class AdjacentZoningProfile(BaseModel):
    """Zoning of the parcels abutting an address's parcel (ABS-375).

    Development-standards and variance reports repeatedly punt rear/side
    setback verdicts to the customer because the requirement is conditional
    on the *abutting* parcel's zone (e.g. a Downtown DH zone permits a 0.0 m
    side setback where it abuts another DH lot but requires a metre-plus
    setback where it abuts a residential lot). The chat/report agent had no
    way to resolve the neighbour's zone mid-run — ``get_address_profile``
    only resolves the *subject* parcel. This DTO is the one-call answer:
    the subject zone plus every abutting parcel's zone, so the agent can
    give a definitive PASS/FAIL instead of "UNCERTAIN — depends on adjacent
    zoning", and a variance writer can resolve the governing setback row
    rather than adopting the applicant's asserted figure.

    Never raises for an unresolvable address or an un-ingested parcels
    dataset — the method returns ``unresolvable=True`` (bad address) or an
    empty ``neighbours`` list with a populated ``note`` so the calling
    agent can fall back cleanly.
    """

    address: str = Field(
        ..., description="The address echoed back (canonical form when resolved)."
    )
    subject_pid: str | None = Field(
        default=None, description="The subject parcel's identifier, when resolved."
    )
    subject_zone: str | None = Field(
        default=None,
        description="The subject parcel's own zone code (for cross-checking).",
    )
    neighbours: list[NeighbourZone] = Field(
        default_factory=list,
        description="One entry per parcel abutting the subject parcel.",
    )
    distinct_neighbour_zones: list[str] = Field(
        default_factory=list,
        description=(
            "The distinct zone codes among the neighbours, sorted. A "
            "single-element list means every abutting lot shares one zone — "
            "the setback that depends on the abutting zone is then "
            "unambiguous."
        ),
    )
    citation: "CitationRef | None" = Field(
        default=None,
        description=(
            "Citation back to the zoning schedule the neighbour zones were "
            "read from, so a grounded answer can cite the source."
        ),
    )
    unresolvable: bool = Field(
        default=False,
        description="True when the address could not be geocoded/matched.",
    )
    note: str | None = Field(
        default=None,
        description=(
            "Human-readable advisory when the lookup could not fully resolve "
            "— e.g. no parcels dataset ingested, or the geocoded point fell "
            "outside every parcel polygon. Null on a clean resolution."
        ),
    )


class PermittedUseResult(BaseModel):
    """Resolved permitted-use matrix cell for a single ``(use, zone)`` pair.

    ABS-279 (Phase 3). Returned inside ``CitationLookupResponse.permitted_use``
    for a structured ``permitted_use`` query. Two mutually-exclusive shapes the
    caller dispatches on:

    * **Resolved** — ``indeterminate=False``, ``permission`` is one of
      ``permitted`` / ``conditional`` / ``not_permitted``. ``footnote_ordinal``
      and ``condition_text`` are populated only when ``conditional``.
      ``citation`` traces the answer to the source table.
    * **Indeterminate** — ``indeterminate=True``, ``permission`` is null, and
      ``reason`` / ``reason_code`` explain why the cell could not be addressed
      (unknown use, unknown zone, no permission matrix in scope, an unbound
      cell, or — ABS-483 — an ``unreadable_cell``: the pair addressed a cell
      whose marker could not be extracted, which is a gap in our data and not
      a prohibition). This is a *typed* not-found, never a silent empty
      success.
    """

    use: str = Field(..., description="The use as queried (echoed).")
    zone: str = Field(..., description="The zone as queried (echoed).")
    indeterminate: bool = Field(
        default=False,
        description="True when the cell could not be resolved; see 'reason'.",
    )
    reason_code: str | None = Field(
        default=None,
        description=(
            "Machine-readable indeterminate cause: 'unknown_use', "
            "'unknown_zone', 'unknown_use_and_zone', 'unbound_cell', "
            "'unreadable_cell' (the cell was addressed but its marker could "
            "not be extracted — NOT a prohibition), or 'no_permission_matrix'. "
            "Null when resolved."
        ),
    )
    reason: str | None = Field(
        default=None,
        description="Human-readable explanation when 'indeterminate' is true.",
    )
    suggested_uses: list[str] = Field(
        default_factory=list,
        description=(
            "Ranked near-miss use-row labels when 'reason_code' is 'unknown_use' "
            "(ABS-351). The queried term didn't match a matrix row, but these are "
            "the closest real rows — pick the intended one verbatim and re-issue "
            "the permitted_use query with it. Do NOT guess further variants. "
            "Empty when the use resolved or nothing was close enough."
        ),
    )
    permission: Literal["permitted", "conditional", "not_permitted"] | None = Field(
        default=None,
        description="The resolved permission; null when indeterminate.",
    )
    footnote_ordinal: int | None = Field(
        default=None,
        description="Footnote ordinal (e.g. 3 for ③) when permission is 'conditional'.",
    )
    condition_text: str | None = Field(
        default=None,
        description=(
            "The footnote's condition text, joined from the table's footnote "
            "fragments, when permission is 'conditional'."
        ),
    )
    citation: CitationRef | None = Field(
        default=None,
        description="Citation back to the source permission-matrix table.",
    )
    document_id: int | None = Field(
        default=None, description="Owning document id of the resolved table."
    )
    table_id: int | None = Field(
        default=None, description="Source permission-matrix table id."
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

    Three outcomes are distinct and must stay distinct (ABS-466):

      * ``unresolvable=True`` — no point at all; nothing was looked up.
      * ``outside_mapped_area=True`` — a point was found, but it falls
        outside every mapped boundary, so ``zone`` is null because the
        corpus does not cover the location, not because the property is
        unzoned.
      * neither — the point was matched against the corpus, and
        ``resolution_quality`` says how much the resulting zone is worth.
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
    abuts_pedestrian_street: bool | None = Field(
        default=None,
        description=(
            "True when the parcel/point abuts a pedestrian-oriented commercial "
            "street designated on Schedule 7 of the Regional Centre LUB; False "
            "when a Schedule 7 dataset was checked and no designated segment "
            "lies within the abut buffer; null when no Schedule 7 dataset is in "
            "scope. Decides the ground-floor-use branch: s.38(2) prohibits "
            "ground-floor office on a POCS street, s.69(d) permits it otherwise. "
            "Unlike the precinct overlays this is an *abuts* test (nearest "
            "designated street segment within the buffer), not point-in-polygon."
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
    # -- ABS-466: resolution quality -------------------------------------
    #
    # The zone above is only as good as the point that selected it. These
    # fields say how that point was arrived at, so an answer built on an
    # estimate can be qualified instead of stated flat.
    resolution_quality: (
        Literal["rooftop", "interpolated", "centroid", "approximate", "unknown"] | None
    ) = Field(
        default=None,
        description=(
            "How precisely the address resolved to a point: 'rooftop' (matched "
            "the building), 'interpolated' (estimated along the street from "
            "surrounding civic numbers — the civic number was NOT found), "
            "'centroid' (centre of a block/route), 'approximate' (locality "
            "only), 'unknown'. Anything other than 'rooftop' means the point "
            "may sit on a neighbouring parcel, so the zone and every setback, "
            "height and floor-area figure derived from it may belong to the "
            "wrong property. QUALIFY any answer built on a non-rooftop match; "
            "do not state the zone as fact. Null when unresolvable."
        ),
    )
    location_confidence: float | None = Field(
        default=None,
        description=(
            "Confidence (0..1) of the geocoded point: 0.95 rooftop, 0.85 "
            "interpolated, 0.6 block centroid. Same scale as "
            "LinkedDataset.location_confidence."
        ),
    )
    location_type: str | None = Field(
        default=None,
        description=(
            "The external geocoder's raw quality enum (ROOFTOP, "
            "RANGE_INTERPOLATED, GEOMETRIC_CENTER, APPROXIMATE) when the point "
            "came from it; null for in-database civic-address resolutions."
        ),
    )
    location_resolver: str | None = Field(
        default=None,
        description=(
            "Which resolver produced the point — a civic-address dataset name, "
            "'google_maps', or a cache row's resolver."
        ),
    )
    outside_mapped_area: bool = Field(
        default=False,
        description=(
            "True when the address DID resolve to a point but that point falls "
            "outside every mapped zoning/overlay boundary in scope, so no zone "
            "could be assigned. Distinct from 'unresolvable' (address not "
            "found at all) and from a genuine no-overlay answer: it means the "
            "corpus does not cover this location. Do NOT state a zone — say "
            "the address is outside the mapped plan area and needs "
            "confirmation."
        ),
    )
    caveats: list[str] = Field(
        default_factory=list,
        description=(
            "Ready-to-quote qualifications the answer MUST carry, one per "
            "quality problem with this resolution (non-rooftop match, point "
            "outside the mapped area). Empty means the resolution is precise "
            "and fully covered. When non-empty, surface the substance of these "
            "to the user rather than answering as if the zone were certain."
        ),
    )
    # -- ABS-469: does the address exist? --------------------------------
    #
    # A geocoder answers "100 Robie Street" by interpolating a position from
    # the surrounding civic numbering — it never found the number, because
    # there is no 100 Robie Street. The fields below check the number against
    # the municipality's own data, so a fabricated address is refused and
    # corrected rather than answered from somebody else's parcel.
    civic_address_status: (
        Literal["confirmed", "not_found", "unverifiable"] | None
    ) = Field(
        default=None,
        description=(
            "Whether the civic number exists in the municipality's own data: "
            "'confirmed' (a published civic-address point, or a street segment "
            "whose address range covers it), 'not_found' (the street is known "
            "and NO published address or range covers this number — the "
            "address does not exist; do NOT state a zone, tell the user and "
            "offer 'suggested_civic_numbers'), 'unverifiable' (no municipal "
            "address data in scope, or an unrecognised street — says nothing "
            "about the address). Null for parcel-id and named-place lookups."
        ),
    )
    civic_address_evidence: str | None = Field(
        default=None,
        description=(
            "What settled 'civic_address_status' — the method and dataset, "
            "e.g. 'street_centerline_ranges (halifax_street_centerlines)'. A "
            "'civic_address_points' verdict is a municipal fact; a "
            "'street_centerline_ranges' verdict is inferred from published "
            "per-segment address ranges and is right ~99.85% of the time."
        ),
    )
    valid_civic_number_ranges: list[str] = Field(
        default_factory=list,
        description=(
            "Civic-number ranges that DO exist on this street, same parity as "
            "the number asked about, nearest first (e.g. ['820-2180']). "
            "Populated when 'civic_address_status' is 'not_found' — quote "
            "these back so the user can correct the address."
        ),
    )
    suggested_civic_numbers: list[str] = Field(
        default_factory=list,
        description=(
            "The nearest valid civic numbers on this street. Populated with "
            "'valid_civic_number_ranges'; offer them as 'did you mean …?'."
        ),
    )
    # -- ABS-469: is the zone safe to rely on? ---------------------------
    #
    # Orthogonal to geocoder quality: even an exact rooftop point is unsafe
    # when the parcel abuts or straddles a zone line, which is exactly the
    # mechanism that produces a confidently wrong setback.
    zone_boundary_distance_m: float | None = Field(
        default=None,
        description=(
            "Distance in metres from the resolved point to the nearest polygon "
            "carrying a DIFFERENT zone code, when that is within ~25 m. Null "
            "when no other zone is nearby (or no zoning dataset is in scope). "
            "A small value means the zone above may belong to the neighbouring "
            "parcel — say so and name 'nearest_other_zone'."
        ),
    )
    nearest_other_zone: str | None = Field(
        default=None,
        description=(
            "Zone code of the nearest polygon with a different zone, paired "
            "with 'zone_boundary_distance_m'."
        ),
    )
    parcel_zones: list[str] = Field(
        default_factory=list,
        description=(
            "Every zone the resolved parcel intersects, largest share first, "
            "when it intersects more than one. A multi-zone parcel means the "
            "standards differ across the lot and the governing zone depends on "
            "where on the lot the work is proposed — never answer with one "
            "zone as if it governed the whole parcel. Empty when the parcel "
            "sits in a single zone or no parcel fabric is in scope."
        ),
    )
    # -- ABS-472: which by-law actually governs this parcel? --------------
    #
    # A municipality-wide zoning layer spans many by-law areas, so a zone code
    # says nothing about which by-law defines it. Answering a Downtown Halifax
    # DH-1 parcel out of the Regional Centre LUB is not a hedge-worthy
    # imprecision — it is the wrong by-law, and every standard that follows is
    # someone else's.
    governing_bylaw: str | None = Field(
        default=None,
        description=(
            "The by-law that governs the resolved parcel's zone, read from "
            "the zoning layer's own per-feature by-law attribution (e.g. "
            "'Downtown Halifax Land Use By-law'). Null when the zoning layer "
            "in scope does not publish per-feature attribution."
        ),
    )
    governing_bylaw_code: str | None = Field(
        default=None,
        description=(
            "Publisher-namespaced short code for 'governing_bylaw' (e.g. "
            "'hrm:DHFX'), when the layer carries one."
        ),
    )
    governing_bylaw_status: Literal["held", "not_held", "unknown"] | None = Field(
        default=None,
        description=(
            "Whether the by-law that governs this parcel is in the retrieval "
            "corpus: 'held' (it is — the zone citation points at it), "
            "'not_held' (it is NOT — the zone code is the municipality's "
            "published mapping, but NO standard from the governing by-law is "
            "available here and the zone carries no citation. Do NOT answer "
            "with permitted uses, height, setbacks, floor-area or any other "
            "standard, and do NOT substitute another by-law's: name the "
            "governing by-law and tell the user it must be consulted "
            "directly), 'unknown' (the layer publishes no per-feature "
            "attribution). Null when no zone resolved."
        ),
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


class ConditionalUse(BaseModel):
    """A use permitted in a zone subject to a footnote condition (ABS-409).

    Produced by the permission-matrix enumeration path: a circled-number
    marker (e.g. ③) in the use × zone cell, with the footnote legend's text
    joined as ``condition`` when the legend fragment resolves.
    """

    use: str = Field(description="The use name as the bylaw prints it.")
    footnote_ordinal: int | None = Field(
        default=None, description="The circled-number footnote ordinal, e.g. 3 for ③."
    )
    condition: str | None = Field(
        default=None,
        description="The footnote legend's condition text, when resolvable.",
    )


class ZoneUses(BaseModel):
    """Use permissions for a zone. ``permitted`` lists explicitly
    permitted uses; ``not_permitted`` lists uses explicitly marked as
    not permitted; ``conditional`` lists uses permitted subject to a
    footnote condition (symbol-matrix bylaws). Any may be empty when
    retrieval found no use table for the zone.
    """

    permitted: list[str] = Field(
        default_factory=list,
        description=(
            "Uses explicitly permitted in this zone (table cell 'P', or a "
            "● marker in symbol-matrix bylaws)."
        ),
    )
    not_permitted: list[str] = Field(
        default_factory=list,
        description=(
            "Uses explicitly NOT permitted in this zone (table cell 'N', or "
            "a blank matrix cell)."
        ),
    )
    conditional: list[ConditionalUse] = Field(
        default_factory=list,
        description="Uses permitted subject to a footnote condition.",
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



# ---------------------------------------------------------------------------
# Phase 4 (ABS-274) — intent-routed bylaw_query mega-tool
# ---------------------------------------------------------------------------
#
# bylaw_query is a *composer* over the Phase 2/3 thick tools
# (get_zone_profile, get_address_profile). The LLM declares its intent once
# and the server dispatches to the right composition, encoding the
# "which tool fits this question?" decision server-side. See FR-4.1–4.5.
#
# The intent vocabulary lives here (a shared schema module) precisely so the
# tool schema AND any system-prompt copy can reference the same canonical
# values — implementation note in the ticket. ``BylawIntent`` is a
# ``str, Enum`` so ``intent in {e.value for e in BylawIntent}`` is the
# single source of truth for "is this a recognised intent?".


class BylawIntent(str, Enum):
    """Canonical intent vocabulary for ``bylaw_query`` (FR-4.2).

    Each value names a server-side composition over the thin/thick tools.
    An ``intent`` outside this set is *not* an error — ``bylaw_query``
    returns ``unrecognized_intent=True`` with thin-tool suggestions so the
    model pivots without burning an exception-driven retry (FR-4.2).
    """

    ZONE_FEASIBILITY = "zone_feasibility"
    ADDRESS_LOOKUP = "address_lookup"
    USE_CHECK = "use_check"
    DIMENSIONAL_CHECK = "dimensional_check"


# Ordered list of the canonical intent string values — handy for the tool
# schema description and the system prompt so the model sees the same
# vocabulary the dispatcher recognises.
BYLAW_INTENTS: tuple[str, ...] = tuple(intent.value for intent in BylawIntent)


class ConformanceAttribute(BaseModel):
    """A single proposed value evaluated against the governing zone standard.

    ``status`` is ``pass``/``fail``/``inconclusive`` (FR-4.3): ``fail`` when
    the proposed value violates the standard (exceeds a maximum or falls
    short of a minimum), ``pass`` when it conforms, and ``inconclusive``
    when the zone is silent on the attribute, the attribute isn't a known
    dimensional standard, or the proposed value isn't comparable.
    """

    attribute: str = Field(..., description="Proposed attribute key, echoed from the request.")
    proposed: float | int | str | bool | None = Field(
        default=None, description="The proposed value supplied by the caller."
    )
    limit: float | None = Field(
        default=None,
        description="The governing zone standard the proposal was compared against, when known.",
    )
    comparison: Literal["max", "min", "unknown"] = Field(
        default="unknown",
        description=(
            "Bound type: 'max' (proposal must not exceed the limit, e.g. "
            "height), 'min' (proposal must not fall below it, e.g. setback), "
            "or 'unknown' (no dimensional standard mapped)."
        ),
    )
    status: Literal["pass", "fail", "inconclusive"] = Field(
        ..., description="Per-attribute verdict against the zone standard."
    )
    note: str | None = Field(
        default=None, description="Short human-readable explanation of the verdict."
    )


class ConformanceCheck(BaseModel):
    """Per-attribute pass/fail/inconclusive of ``proposed`` against a zone's
    dimensional standards (FR-4.3). Populated only for the
    ``dimensional_check`` intent. ``overall`` is ``fail`` if any attribute
    fails, ``pass`` if every attribute passes, otherwise ``inconclusive``.
    """

    zone: str = Field(..., description="Zone the proposal was evaluated against.")
    results: list[ConformanceAttribute] = Field(
        default_factory=list, description="One entry per proposed attribute."
    )
    overall: Literal["pass", "fail", "inconclusive"] = Field(
        ..., description="Roll-up verdict across all evaluated attributes."
    )


class BylawQueryResponse(BaseModel):
    """Envelope returned by the intent-routed ``bylaw_query`` tool (FR-4.3).

    Exactly which of ``address_profile`` / ``zone_profile`` /
    ``conformance_check`` is populated depends on ``intent``:

    * ``zone_feasibility`` / ``use_check`` -> ``zone_profile``
    * ``address_lookup`` -> ``address_profile``
    * ``dimensional_check`` -> ``zone_profile`` + ``conformance_check``

    An unrecognised ``intent`` sets ``unrecognized_intent=True`` and lists
    the thin tools to fall back to in ``suggested_tools`` — never an
    exception (FR-4.2).
    """

    intent: str = Field(..., description="The intent echoed back from the request.")
    address_profile: AddressProfile | None = Field(
        default=None, description="Populated for the 'address_lookup' intent."
    )
    zone_profile: ZoneProfile | None = Field(
        default=None,
        description="Populated for zone-scoped intents (zone_feasibility, use_check, dimensional_check).",
    )
    conformance_check: ConformanceCheck | None = Field(
        default=None, description="Populated for the 'dimensional_check' intent."
    )
    citations: list[CitationRef] = Field(
        default_factory=list,
        description="Aggregated citations backing the composed answer (mirrors the source profile's).",
    )
    unrecognized_intent: bool = Field(
        default=False,
        description="True when 'intent' is outside the BylawIntent vocabulary; fall back to suggested_tools.",
    )
    suggested_tools: list[str] = Field(
        default_factory=list,
        description="Thin tools to fall back to when the intent is unrecognised (FR-4.2).",
    )


class OverlayDeclaration(BaseModel):
    """A single "this bylaw's dataset config declares this overlay" fact.

    Normalized projection of a ``layer1.datasets.config.DatasetConfig``'s
    ``links_to`` block, decoupled from reading YAML off disk so the
    corpus-coherence audit (ABS-356) can be driven either by the real
    ``src/layer1/datasets/`` directory (CLI / ops surface) or by an explicit
    list supplied in-process (tests, the e2e test-only endpoint).
    """

    dataset_name: str = Field(..., description="The dataset config's declared name.")
    municipality: str = Field(..., description="links_to.document_match.municipality")
    bylaw_name: str = Field(..., description="links_to.document_match.bylaw_name")
    fragment_citation: str = Field(..., description="links_to.fragment_citation")


class MissingOverlayRole(BaseModel):
    """One overlay role a dataset config declares that isn't visible in scope."""

    role: str = Field(..., description="Overlay role classified from dataset_name (see overlay_role_for_name).")
    dataset_name: str
    municipality: str
    bylaw_name: str
    fragment_citation: str
    reason: Literal["unlinked", "orphaned", "evicted"] = Field(
        ...,
        description=(
            "unlinked: no dataset with this name was ever ingested. "
            "orphaned: the dataset exists but the linker never resolved it to a fragment. "
            "evicted: the dataset is linked, but its document fell outside the active "
            "retrieval scope (e.g. superseded by a newer ingest of the same bylaw)."
        ),
    )
    detail: str = Field(..., description="Human-readable explanation for a log line or CLI error.")


class CorpusCoherenceReport(BaseModel):
    """Result of auditing every declared overlay role against retrieval scope (ABS-356).

    ``coherent`` is the loud/quiet signal: True means every overlay role any
    checked dataset config declares is actually visible through
    ``scoped_linked_datasets`` for its bylaw; False means at least one
    silently degraded (see ``missing`` for which role, and why).
    """

    coherent: bool
    checked_roles: int = Field(..., description="Number of overlay-role dataset configs evaluated.")
    bylaws_checked: int = Field(..., description="Number of distinct (municipality, bylaw_name) pairs evaluated.")
    missing: list[MissingOverlayRole] = Field(default_factory=list)


class UnheldGoverningBylaw(BaseModel):
    """One by-law a geo layer attributes features to but the corpus doesn't hold (ABS-472).

    Municipality-wide layers span many by-law areas. Where the governing
    by-law is missing from the retrieval corpus, every feature in that area is
    ground we can map but cannot answer standards for — and before ABS-472 we
    answered it anyway, citing whichever document the layer was linked to.
    """

    dataset_name: str = Field(..., description="The layer carrying the features.")
    governing_bylaw: str = Field(
        ..., description="The by-law the features name as governing, verbatim."
    )
    governing_bylaw_code: str | None = Field(
        default=None, description="Publisher-namespaced short code, when the layer carries one."
    )
    feature_count: int = Field(
        ..., description="How many of the layer's features this by-law governs."
    )
    detail: str = Field(..., description="Human-readable line for a CLI table or ops log.")


class GoverningBylawCoverageReport(BaseModel):
    """How much of each municipality-wide geo layer we can actually cite (ABS-472).

    Informational, not a health tripwire: a municipality publishes far more
    by-law areas than any corpus is likely to ingest, so ``complete=False`` is
    the expected steady state and does NOT mean anything is broken. What it
    quantifies is exposure — how much ground a spatial query can land on and
    come back with a zone whose by-law we do not hold. Answers for that ground
    are refused/caveated at request time (``AddressProfile.
    governing_bylaw_status``); this report is what tells an operator how much
    of it there is and which by-law to ingest next.
    """

    complete: bool = Field(
        ...,
        description="True when every attributed feature's governing by-law is in scope.",
    )
    datasets_checked: int = Field(
        ..., description="Layers declaring per-feature governing-by-law attribution."
    )
    features_checked: int = Field(..., description="Attributed features evaluated.")
    covered_features: int = Field(
        ..., description="Features whose governing by-law IS in the retrieval corpus."
    )
    unheld_features: int = Field(
        ..., description="Features whose governing by-law is NOT in the corpus."
    )
    unheld: list[UnheldGoverningBylaw] = Field(
        default_factory=list,
        description="One entry per (layer, governing by-law) gap, largest first.",
    )


class E2eContaminationMarker(BaseModel):
    """One row in a non-test database that carries an e2e-suite fingerprint (ABS-432).

    The three marker kinds are the exact fingerprints every
    ``scripts/seed_e2e_*.py`` fixture stamps on the rows it creates:

    * ``document_parser_version`` — ``document.parser_version = 'e2e-seed'``
    * ``document_file_hash``      — ``document.file_hash LIKE 'e2e-%'``
    * ``external_dataset_name``   — ``external_dataset.name LIKE 'e2e_%'``
      (literal underscore, escaped in SQL)

    A single document row matching both document markers is reported once,
    with both kinds listed in ``marker_kinds``.
    """

    table: Literal["document", "external_dataset"] = Field(
        ..., description="Which table the offending row lives in."
    )
    row_id: int = Field(..., description="Primary key of the offending row.")
    marker_kinds: list[str] = Field(
        ...,
        description=(
            "Every e2e fingerprint this row matches: 'document_parser_version', "
            "'document_file_hash', and/or 'external_dataset_name'."
        ),
    )
    detail: str = Field(
        ...,
        description=(
            "Human-readable identification of the row (bylaw name + file hash "
            "for documents, dataset name for external datasets) for a red "
            "banner or ops log line."
        ),
    )


class E2eContaminationReport(BaseModel):
    """Result of sweeping a database for e2e-suite fixture markers (ABS-432).

    Defense-in-depth behind the dev/e2e Postgres split (ABS-428) and the
    dev-DB purge (ABS-429): if test artifacts ever reach a non-test database
    again, this report surfaces them loudly — in the ``dev-up.sh`` preflight,
    the ``scripts/corpus_coherence_audit.py`` CLI, and the
    ``/v1/monitoring/corpus-coherence`` ops endpoint — instead of silently
    polluting manual testing and real answers.

    ``contaminated`` is green-only-when-zero: True the moment any marker row
    exists. Whether that's an incident depends on the deployment — an e2e
    stack's own database legitimately holds these rows (the caller decides;
    see the monitoring router's markers-expected handling).
    """

    contaminated: bool = Field(..., description="True when any marker row exists.")
    marker_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Row count per marker kind, always carrying all three keys: "
            "'document_parser_version', 'document_file_hash', "
            "'external_dataset_name'."
        ),
    )
    markers: list[E2eContaminationMarker] = Field(
        default_factory=list,
        description="Every offending row, one entry per row (deduplicated across marker kinds).",
    )


class EnabledDocumentRef(BaseModel):
    """One retrieval-enabled document inside a name-collision group (ABS-434)."""

    id: int = Field(..., description="Document primary key.")
    municipality: str = Field(..., description="The municipality exactly as stored.")
    bylaw_name: str = Field(..., description="The bylaw name exactly as stored.")


class EnabledNameCollision(BaseModel):
    """Multiple ENABLED documents sharing one normalized bylaw identity (ABS-434).

    The doc-15/38 shape: two enabled documents whose ``(municipality,
    bylaw_name)`` differ only by case/hyphenation/whitespace ("By-law" vs
    "By-Law"). Every exact-match pass (migration-0024 backfill,
    ``enable-retrieval`` sibling detection/``--replace``, the ABS-355
    relink) sees them as unrelated bylaws, so the enabled corpus silently
    fragments. Exact duplicates (two enabled versions under the *same*
    literal name) are also reported — >1 enabled per normalized identity is
    the violation either way.
    """

    normalized_municipality: str = Field(
        ..., description="The group key's municipality half (see layer1.naming.normalize_bylaw_name)."
    )
    normalized_bylaw_name: str = Field(
        ..., description="The group key's bylaw-name half."
    )
    document_ids: list[int] = Field(
        ..., description="Every enabled document id in the group, ascending."
    )
    documents: list[EnabledDocumentRef] = Field(
        ..., description="The colliding documents with their names exactly as stored."
    )
    detail: str = Field(
        ..., description="Human-readable description naming the ids and stored spellings."
    )


class EnabledNameCollisionReport(BaseModel):
    """Result of auditing enabled documents for normalized-name collisions (ABS-434).

    ``collision_free`` is the loud/quiet signal: True means every normalized
    ``(municipality, bylaw_name)`` identity has at most one retrieval-enabled
    document; False means at least one identity has several (see
    ``collisions`` for the ids). Unlike the e2e-contamination sweep there is
    no deployment where a collision is legitimate — red is red everywhere,
    including the e2e stack's own database.
    """

    collision_free: bool = Field(
        ..., description="True when no normalized identity has more than one enabled document."
    )
    enabled_documents: int = Field(
        ..., description="Number of retrieval-enabled documents examined."
    )
    identities_checked: int = Field(
        ..., description="Number of distinct normalized (municipality, bylaw_name) identities among them."
    )
    collisions: list[EnabledNameCollision] = Field(default_factory=list)
