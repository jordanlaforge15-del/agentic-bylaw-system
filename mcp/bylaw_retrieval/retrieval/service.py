from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable

from sqlalchemy import (
    Select,
    String,
    Text,
    and_,
    bindparam,
    case,
    cast,
    desc,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY, JSONB
from sqlalchemy.orm import Session

from rapidfuzz import fuzz, process
from shapely.geometry import shape as shapely_shape

from bylaw_retrieval.retrieval.schemas import (
    ATTRIBUTE_VOCABULARY,
    BYLAW_INTENTS,
    AddressProfile,
    AdjacentZoningProfile,
    AncestorFragment,
    BylawIntent,
    BylawQueryResponse,
    CitationLookupRequest,
    CitationLookupResponse,
    CitationRef,
    ConformanceAttribute,
    ConformanceCheck,
    CrossReferenceSummary,
    DatasetFeatureMatch,
    DocumentOutlineItem,
    DocumentOutlineResponse,
    DocumentSummary,
    LinkedDataset,
    LocationSlot,
    NeighbourZone,
    OverlayRef,
    PermittedUseQuery,
    PermittedUseResult,
    RetrievalMatch,
    RetrievalRequest,
    RetrievalResponse,
    ScheduleRowQuery,
    TableCellSummary,
    TableSummary,
    ZoneAttributeQuery,
    ZoneDimensions,
    ZoneParking,
    ZoneProfile,
    ConditionalUse,
    ZoneUses,
)
from layer1.db.base import (
    CrossReference,
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    SemanticEntity,
    SourceFragment,
    SourceImage,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
)
from layer1.models.enums import FragmentType
from layer1.semantic.enrichment import (
    enumerate_permission_column,
    resolve_mainland_permitted_use,
    resolve_permission_cell,
    use_row_labels,
)
from layer1.semantic.extractors import normalize_use, normalize_zone
from layer1.semantic.use_matching import match_use
from layer1.semantic.permission_markers import (
    PERMISSION_MATRIX_PROFILE,
    classify_permission_marker,
    ordinal_to_circled,
)
from layer2.retrieval.datasets import _summarize_dataset
from layer2.retrieval.geocode import resolve_location_with_detail
from layer2.retrieval.location import LocationReference, RegexLocationExtractor
from layer2.retrieval.spatial import (
    ResolvedLocation,
    find_abutting_features,
    find_containing_feature,
    query_features,
)

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


# Resolver signature: takes a session, returns document id(s) to scope
# retrieval to (or None if no document exists / no scoping desired).
# Called per-request so a fresh ingest mid-session is picked up without
# a server restart.  Returning a single int pins to one document;
# returning a list pins to the union of those documents.
DocumentIdResolver = Callable[[Session], int | list[int] | None]

# Maps a linked dataset to the AddressProfile facet it populates. Keyed on a
# lowercase substring of the dataset name — the same disambiguation
# convention the rest of the retrieval surface uses ("the dataset name makes
# it clear which 'district' the field describes", see
# layer1.datasets.canonical). Order matters: the first matching keyword
# wins, so the more specific tokens are listed before the generic ones.
#
# Module-level (rather than a RetrievalService class attribute) so the
# corpus-coherence audit (ABS-356) can classify a dataset config's declared
# role from its ``name:`` field the exact same way ``get_address_profile``
# classifies the persisted ``ExternalDataset.name`` — one source of truth for
# "what role does this dataset name imply", not two that could drift apart.
OVERLAY_ROLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("pedestrian", "pedestrian_street"),
    ("heritage", "heritage"),
    ("bonus", "bonus_zoning"),
    ("shadow", "shadow_impact"),
    ("far", "far_precinct"),
    ("floor_area", "far_precinct"),
    ("height", "height_precinct"),
    ("zoning", "zone"),
    ("zone", "zone"),
)


def overlay_role_for_name(name: str | None) -> str:
    """Classify a dataset name into its overlay role via keyword match.

    Falls back to the generic ``"overlay"`` bucket when no keyword matches.
    """
    lowered = (name or "").lower()
    for keyword, role in OVERLAY_ROLE_KEYWORDS:
        if keyword in lowered:
            return role
    return "overlay"


def scoped_linked_datasets(
    session: Session,
    *,
    default_document_id_resolver: DocumentIdResolver | None = None,
    document_id: int | None = None,
    municipality: str | None = None,
    bylaw_name: str | None = None,
) -> list[ExternalDataset]:
    """Return every linked geo dataset visible under the active scope.

    Module-level twin of ``RetrievalService._scoped_linked_datasets`` (which
    delegates here with ``self.session`` / ``self._default_document_id_resolver``)
    so callers without a live ``RetrievalService`` instance — notably the
    corpus-coherence audit (ABS-356), which runs from a CLI/ops context, not
    a request — see exactly the same "is this overlay visible right now" view
    the thick tools use, rather than re-deriving the scoping rule and risking
    divergence.
    """
    dataset_stmt = (
        select(ExternalDataset)
        .join(SourceFragment, SourceFragment.id == ExternalDataset.linked_fragment_id)
        .join(Document, Document.id == SourceFragment.document_id)
        .where(ExternalDataset.linked_fragment_id.is_not(None))
    )
    if default_document_id_resolver is not None:
        result = default_document_id_resolver(session)
        if result is not None:
            default_ids = [result] if isinstance(result, int) else result
            dataset_stmt = dataset_stmt.where(SourceFragment.document_id.in_(default_ids))
    if document_id is not None:
        dataset_stmt = dataset_stmt.where(SourceFragment.document_id == document_id)
    if municipality:
        dataset_stmt = dataset_stmt.where(Document.municipality.ilike(f"%{municipality}%"))
    if bylaw_name:
        dataset_stmt = dataset_stmt.where(Document.bylaw_name.ilike(f"%{bylaw_name}%"))
    return list(session.execute(dataset_stmt).scalars().all())


def latest_document_id_resolver(session: Session) -> int | None:
    """Return the id of the most recently ingested document, or None.

    "Most recent" means largest ``ingestion_timestamp``; ties broken by id
    descending. Used by the MCP server's ``--latest-only`` mode to scope
    every request to the freshest ingest, since dev workflows commonly
    re-ingest the same bylaw multiple times.
    """
    return (
        session.execute(
            select(Document.id).order_by(
                desc(Document.ingestion_timestamp), desc(Document.id)
            ).limit(1)
        )
        .scalars()
        .first()
    )


def latest_per_bylaw_resolver(session: Session) -> list[int] | None:
    """Return the id of the most recently ingested document *per bylaw*.

    Groups documents by ``(municipality, bylaw_name)`` and picks the
    newest ingest of each (largest ``ingestion_timestamp``, ties broken
    by ``id`` descending).  This lets a multi-bylaw deployment search
    across all active bylaws while still de-duplicating re-ingests of
    the same bylaw — the common dev-workflow concern that
    ``latest_document_id_resolver`` was built for, generalized.

    Returns ``None`` when no documents exist (so the caller can
    distinguish "no documents at all" from "an empty scope").
    """
    from sqlalchemy import func  # noqa: PLC0415

    window = func.row_number().over(
        partition_by=[Document.municipality, Document.bylaw_name],
        order_by=[desc(Document.ingestion_timestamp), desc(Document.id)],
    ).label("rn")
    subq = select(Document.id.label("doc_id"), window).subquery()
    ids = (
        session.execute(select(subq.c.doc_id).where(subq.c.rn == 1))
        .scalars()
        .all()
    )
    return list(ids) if ids else None


class RetrievalService:
    def __init__(
        self,
        session: Session,
        *,
        default_document_id_resolver: DocumentIdResolver | None = None,
    ) -> None:
        """Layer 1 retrieval service.

        ``default_document_id_resolver`` lets a deployment scope all queries
        to a chosen document (e.g. "latest only") unless the caller passes
        an explicit ``document_id`` / ``municipality`` / ``bylaw_name``
        filter. The resolver runs per request, so re-ingests are picked up
        without restarting the server.
        """
        self.session = session
        self._default_document_id_resolver = default_document_id_resolver

    def _resolve_default_document_ids(self) -> list[int] | None:
        if self._default_document_id_resolver is None:
            return None
        result = self._default_document_id_resolver(self.session)
        if result is None:
            return None
        if isinstance(result, int):
            return [result]
        return result

    def _dialect_name(self) -> str:
        """Return the bound engine's dialect name (e.g. 'postgresql', 'sqlite').

        Used by the attribute-tag filter clause builder so it can pick
        the indexed JSONB operator on postgres or the LIKE fallback on
        sqlite without crashing the test path.
        """
        bind = self.session.get_bind()
        return bind.dialect.name

    def list_documents(
        self,
        municipality: str | None = None,
        bylaw_name: str | None = None,
        limit: int = 50,
    ) -> list[DocumentSummary]:
        stmt = select(Document).order_by(Document.municipality, Document.bylaw_name, Document.id)
        # Hard scope: when a default document resolver is configured, it
        # ALWAYS pins the result set. Other filters AND with it. A query
        # that asks for a different bylaw/municipality returns empty rather
        # than crossing into a stale or superseded ingest — better empty
        # than wrong.
        default_ids = self._resolve_default_document_ids()
        if default_ids is not None:
            stmt = stmt.where(Document.id.in_(default_ids))
        if municipality:
            stmt = stmt.where(Document.municipality.ilike(f"%{municipality}%"))
        if bylaw_name:
            stmt = stmt.where(Document.bylaw_name.ilike(f"%{bylaw_name}%"))
        docs = self.session.execute(stmt.limit(limit)).scalars().all()
        return [self._document_summary(doc) for doc in docs]

    def get_document_outline(
        self,
        document_id: int,
        max_fragments: int = 250,
        include_text: bool = False,
    ) -> DocumentOutlineResponse:
        document = self._get_document(document_id)
        stmt = (
            select(SourceFragment)
            .where(SourceFragment.document_id == document_id)
            .order_by(SourceFragment.page_start, SourceFragment.reading_order_start, SourceFragment.id)
            .limit(max_fragments)
        )
        fragments = self.session.execute(stmt).scalars().all()
        return DocumentOutlineResponse(
            document=self._document_summary(document),
            fragments=[
                DocumentOutlineItem(
                    fragment_id=fragment.id,
                    fragment_type=fragment.fragment_type.value,
                    citation_label=fragment.citation_label,
                    citation_path=fragment.citation_path,
                    page_start=fragment.page_start,
                    page_end=fragment.page_end,
                    text=fragment.text if include_text else _truncate(fragment.text, 180),
                )
                for fragment in fragments
            ],
        )

    # Number of nearest-citation suggestions returned when an exact
    # lookup misses. Tuned to fit comfortably in one tool_result
    # payload (~10 short strings) — high enough that the right form
    # is almost always in the list, low enough that the agent doesn't
    # spend a turn re-reading dozens of candidates.
    _LOOKUP_SUGGESTION_LIMIT = 8

    # rapidfuzz score cutoff for the suggestion list. WRatio is in
    # the 0..100 range; 30 is intentionally generous so that very
    # short queries like "Table 1A" against longer canonical paths
    # like "Part II > [Table 1A]" survive (typical score ~90). The
    # cap is *only* there to filter pure-noise paths in a large
    # corpus from showing up.
    _LOOKUP_SUGGESTION_CUTOFF = 30.0

    def lookup_citation(self, request: CitationLookupRequest) -> CitationLookupResponse:
        """Resolve a citation against the scoped document.

        Accepts either a ``citation_path`` string (existing behaviour) or a
        ``structured`` query (new — see ``_lookup_via_structured``).

        Returns a :class:`CitationLookupResponse`:

        * **Exact match** → ``match`` populated, ``suggestions`` empty.
        * **No exact match** → ``match`` is ``None`` and ``suggestions``
          carries up to :attr:`_LOOKUP_SUGGESTION_LIMIT` of the nearest
          stored ``citation_path`` values (rapidfuzz WRatio ranking).
          Critically, **this is no longer an exception**: a missed
          path is the most common case during agent exploration
          (the model formats "Table 1A" while the ingest stores
          "Part II > [Table 1A]"), and raising forced the tool-use
          loop into a destructive retry pattern. See ABS-261.
        * **Ambiguous across documents** *still* raises
          ``ValueError`` — the only sensible response there is for
          the caller to add a ``document_id`` and try again, and a
          suggestion list of identical paths in different docs would
          mislead more than help.
        """
        if request.structured is not None:
            return self._lookup_via_structured(request)

        # request.citation_path is guaranteed non-None here (enforced by the
        # model_validator on CitationLookupRequest).
        assert request.citation_path is not None
        stmt = select(SourceFragment).where(SourceFragment.citation_path == request.citation_path)
        # Hard scope: default document ids AND with the request's
        # document_id. See _fragment_scope_statement for rationale.
        default_ids = self._resolve_default_document_ids()
        if default_ids is not None:
            stmt = stmt.where(SourceFragment.document_id.in_(default_ids))
        if request.document_id is not None:
            stmt = stmt.where(SourceFragment.document_id == request.document_id)
        fragments = self.session.execute(stmt.order_by(SourceFragment.id).limit(2)).scalars().all()

        if not fragments:
            # Path didn't match exactly. Surface the nearest stored
            # citation_paths so the agent can correct its formatting
            # in one extra round-trip instead of guessing in a loop.
            suggestions = self._suggest_citation_paths(
                request.citation_path,
                document_id=request.document_id,
                default_ids=default_ids,
            )
            return CitationLookupResponse(match=None, suggestions=suggestions)

        if request.document_id is None and len(fragments) > 1:
            document_ids = ", ".join(str(fragment.document_id) for fragment in fragments)
            raise ValueError(
                f"Citation '{request.citation_path}' is ambiguous across documents; "
                f"provide document_id. Matching document IDs include: {document_ids}"
            )

        fragment = fragments[0]
        match = self._build_match(
            fragment,
            score=1000.0,
            include_context=request.include_context,
            include_cross_references=request.include_cross_references,
            include_tables=request.include_tables,
        )
        return CitationLookupResponse(match=match, suggestions=[])

    def _suggest_citation_paths(
        self,
        requested: str,
        *,
        document_id: int | None,
        default_ids: list[int] | None,
    ) -> list[str]:
        """Return up to ``_LOOKUP_SUGGESTION_LIMIT`` nearest citation_paths.

        Scoped identically to the lookup itself: the request's
        ``document_id`` AND-ed with the service's ``default_ids``, so
        suggestions can never escape the scope the caller asked for.

        Uses rapidfuzz ``WRatio`` (handles partial / token-set / token-sort
        equivalence in one scorer) — well-suited to the asymmetric query
        case we hit in practice, where a short human-style label like
        ``"Table 1A"`` needs to find a longer canonical form like
        ``"Part II > [Table 1A]"``.
        """
        stmt = (
            select(SourceFragment.citation_path)
            .where(SourceFragment.citation_path.is_not(None))
            .distinct()
        )
        if default_ids is not None:
            stmt = stmt.where(SourceFragment.document_id.in_(default_ids))
        if document_id is not None:
            stmt = stmt.where(SourceFragment.document_id == document_id)
        candidates = [row for row in self.session.execute(stmt).scalars().all() if row]
        if not candidates:
            return []
        ranked = process.extract(
            requested,
            candidates,
            scorer=fuzz.WRatio,
            limit=self._LOOKUP_SUGGESTION_LIMIT,
            score_cutoff=self._LOOKUP_SUGGESTION_CUTOFF,
        )
        # process.extract returns (choice, score, index) tuples ordered
        # by descending score. We only need the path strings — the score
        # is internal ranking signal, not something the agent should see
        # (it can't meaningfully act on a fuzz score).
        return [choice for choice, _score, _idx in ranked]

    def _lookup_via_structured(
        self, request: CitationLookupRequest
    ) -> CitationLookupResponse:
        """Resolve a ``StructuredCitationQuery`` to a canonical citation path
        and delegate to the path-string lookup.

        Resolution strategy:
        - ``ZoneAttributeQuery(zone, attribute)`` → canonical path
          ``"{zone} > {attribute}"``.  Unknown attributes raise immediately
          with the accepted vocabulary so the model can self-correct without
          an extra round-trip.
        - ``ScheduleRowQuery(schedule, row)`` → canonical path
          ``"{schedule} > {row}"``.
        - ``PermittedUseQuery(use, zone)`` → a structured permission-matrix
          cell resolution (ABS-279). This one does NOT delegate to the
          path-string lookup: it addresses the cell directly via the Phase-2
          axis bindings and returns its result in ``permitted_use``.

        The constructed path is fed back into the existing path-string lookup,
        so misses surface suggestions via the same rapidfuzz ranking as
        free-text misses (ABS-261 contract preserved).
        """
        structured = request.structured
        if isinstance(structured, PermittedUseQuery):
            result = self.lookup_permitted_use(
                use=structured.use,
                zone=structured.zone,
                document_id=request.document_id,
            )
            return CitationLookupResponse(permitted_use=result)
        if isinstance(structured, ZoneAttributeQuery):
            if structured.attribute not in ATTRIBUTE_VOCABULARY:
                raise ValueError(
                    f"Unknown attribute {structured.attribute!r}. "
                    f"Accepted values: {sorted(ATTRIBUTE_VOCABULARY)}"
                )
            canonical_path = f"{structured.zone} > {structured.attribute}"
        elif isinstance(structured, ScheduleRowQuery):
            canonical_path = f"{structured.schedule} > {structured.row}"
        else:
            raise ValueError(
                f"Unsupported structured query kind: {type(structured).__name__}"
            )

        path_request = CitationLookupRequest(
            citation_path=canonical_path,
            document_id=request.document_id,
            include_context=request.include_context,
            include_cross_references=request.include_cross_references,
            include_tables=request.include_tables,
        )
        return self.lookup_citation(path_request)

    # ------------------------------------------------------------------
    # ABS-279 — structured permitted-use retrieval (use × zone → cell)
    # ------------------------------------------------------------------

    def lookup_permitted_use(
        self,
        *,
        use: str,
        zone: str,
        document_id: int | None = None,
    ) -> PermittedUseResult:
        """Resolve the Table 1A permission-matrix cell for a ``(use, zone)`` pair.

        Phase 3 (ABS-279). Phases 1–2 made a matrix cell addressable as
        *(use entity, zone entity) → marker (+footnote)*; this reads the cell
        directly instead of leaving the model to infer permission from
        flattened table prose (the cause of the hedging on permitted-use
        questions).

        Pipeline:

        1. Find the permission-matrix table(s) within the active-document
           scope (FR4 — the default resolver pins scope; an explicit
           ``document_id`` narrows it further, never crosses bylaws).
        2. For each, resolve the cell via the Phase-2 axis bindings
           (:func:`resolve_permission_cell`): the use selects a row binding,
           the zone a column binding, their indices address the cell.
        3. Classify the cell's marker into ``permitted`` / ``conditional`` /
           ``not_permitted``. A ``conditional`` cell additionally carries the
           footnote ordinal and its joined condition text.
        4. Every partial-match case (unknown use, unknown zone, no matrix in
           scope, or an unbound cell) returns a *typed* indeterminate result
           with a reason — never a silent empty success (FR3).
        """
        tables = self._permission_matrix_tables(document_id=document_id)
        for table in tables:
            resolved = resolve_permission_cell(
                self.session,
                table_id=table.id,
                use_name=use,
                zone=zone,
            )
            if resolved is not None:
                return self._build_permitted_use_result(use, zone, table, resolved)

        # ABS-283: the Mainland LUB encodes permitted uses as prose section
        # lists, not a symbol-dot matrix. When no matrix addressed the cell, fall
        # back to the Mainland section-prose resolver before declaring the query
        # indeterminate.
        mainland = self._resolve_mainland_permitted_use(use, zone, document_id)
        if mainland is not None:
            return mainland

        if not tables:
            return PermittedUseResult(
                use=use,
                zone=zone,
                indeterminate=True,
                reason_code="no_permission_matrix",
                reason=(
                    "No permitted-use matrix (Table 1A-style) is present in the "
                    "active bylaw scope, so this use/zone cannot be resolved."
                ),
            )

        # No matrix addressed the cell — diagnose which axis failed so the
        # caller gets an actionable reason rather than a bare "not found".
        return self._indeterminate_permitted_use(use, zone, tables)

    def _resolve_mainland_permitted_use(
        self, use: str, zone: str, document_id: int | None
    ) -> PermittedUseResult | None:
        """Resolve a ``(use, zone)`` pair via Mainland section-prose use lists.

        Mirrors the matrix scoping contract (FR4): the default-document
        resolver pins the search; an explicit ``document_id`` ANDs with it and
        never crosses bylaws. Returns ``None`` when no Mainland permitted-use
        list covers the zone in scope, so the caller can fall through.
        """
        scope = self._mainland_scope_ids(document_id)
        if scope == []:  # explicit doc outside the pinned scope
            return None
        for scoped_id in scope if scope is not None else [None]:
            resolved = resolve_mainland_permitted_use(
                self.session,
                use_name=use,
                zone=zone,
                document_id=scoped_id,
            )
            if resolved is not None:
                return self._build_mainland_permitted_use_result(use, zone, resolved)
        return None

    def _mainland_scope_ids(self, document_id: int | None) -> list[int] | None:
        """Document ids the Mainland resolver may search, honoring FR4 scoping.

        Returns ``None`` to mean "unscoped — search every document" (no default
        resolver pinned the scope), ``[]`` when an explicit ``document_id`` falls
        outside the pinned scope, or the concrete id list otherwise.
        """
        default_ids = self._resolve_default_document_ids()
        if document_id is not None:
            if default_ids is not None and document_id not in default_ids:
                return []
            return [document_id]
        return default_ids

    def _build_mainland_permitted_use_result(
        self, use: str, zone: str, resolved: dict
    ) -> PermittedUseResult:
        """Project a resolved Mainland use list into a :class:`PermittedUseResult`."""
        document_id = resolved.get("document_id")
        return PermittedUseResult(
            use=use,
            zone=zone,
            indeterminate=False,
            permission=resolved["permission"],
            citation=self._fragment_citation(
                document_id, resolved.get("source_fragment_ids") or []
            ),
            document_id=document_id,
        )

    def _fragment_citation(
        self, document_id: int | None, fragment_ids: list[int]
    ) -> CitationRef | None:
        """Build a grounding citation back to a Mainland source fragment."""
        if not document_id or not fragment_ids:
            return None
        fragment = self.session.get(SourceFragment, fragment_ids[0])
        if fragment is None:
            return None
        document = self.session.get(Document, document_id)
        return CitationRef(
            citation_path=fragment.citation_path,
            citation_label=fragment.citation_label,
            document_id=document_id,
            municipality=document.municipality if document else None,
            bylaw_name=document.bylaw_name if document else None,
            page_start=fragment.page_start,
            page_end=fragment.page_end,
            backs=["permitted_use"],
        )

    def _permission_matrix_tables(
        self, *, document_id: int | None
    ) -> list[SourceTable]:
        """Permission-matrix tables visible under the active-document scope.

        Mirrors the ``lookup_citation`` scoping contract: the default
        document resolver ALWAYS pins the set; an explicit ``document_id``
        ANDs with it. A request for a doc outside the pinned scope therefore
        returns nothing rather than crossing into another bylaw (FR4).
        """
        # ABS-281: permission matrices are identified by their semantic profile
        # (profile_type='permission_matrix'), not the caption — the real corpus
        # carries no captions, so the old caption ILIKE matched nothing.
        stmt = (
            select(SourceTable)
            .join(
                TableSemanticProfile,
                TableSemanticProfile.table_id == SourceTable.id,
            )
            .where(TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE)
            .order_by(SourceTable.page_start, SourceTable.id)
        )
        default_ids = self._resolve_default_document_ids()
        if default_ids is not None:
            stmt = stmt.where(SourceTable.document_id.in_(default_ids))
        if document_id is not None:
            stmt = stmt.where(SourceTable.document_id == document_id)
        return list(self.session.execute(stmt).scalars().all())

    def _build_permitted_use_result(
        self,
        use: str,
        zone: str,
        table: SourceTable,
        resolved: dict,
    ) -> PermittedUseResult:
        """Project a resolved cell dict into a :class:`PermittedUseResult`."""
        marker = resolved.get("permission_marker")
        footnote = resolved.get("footnote")
        # Fall back to on-the-fly classification when the cell wasn't
        # annotated at ingest (Phase-1 metadata absent) — keeps the resolver
        # correct for matrices that only carry the raw glyph in cell.text,
        # and makes a genuinely blank cell read as not_permitted (AC3).
        if marker is None:
            classified = classify_permission_marker(resolved.get("cell_text"))
            marker = classified["permission_marker"]
            footnote = classified.get("footnote")

        footnote_ordinal: int | None = None
        condition_text: str | None = None
        if marker == "conditional":
            footnote_ordinal = footnote
            condition_text = self._footnote_condition_text(
                document_id=table.document_id, ordinal=footnote
            )

        return PermittedUseResult(
            use=use,
            zone=zone,
            indeterminate=False,
            permission=marker,
            footnote_ordinal=footnote_ordinal,
            condition_text=condition_text,
            citation=self._table_citation(table),
            document_id=table.document_id,
            table_id=table.id,
        )

    def _footnote_condition_text(
        self, *, document_id: int, ordinal: int | None
    ) -> str | None:
        """Join the condition text for a conditional cell's footnote ordinal.

        Reconstructs the circled-number glyph (``3 -> ③``) and returns the
        footnote-legend fragment in the same document whose text carries it.
        Document-scoped (no hard-coded page band) so it follows the table
        wherever the matrix lives.

        A footnote legend is the line that *defines* the condition — it begins
        with the circled glyph, e.g. "⑮ Use is permitted, except within the
        Halifax Grain Elevator Special Area...". Ingest usually types these as
        FOOTNOTE, but the Regional Centre LUB legend rows under Table 1A were
        classified as PROSE, which left ``condition_text`` null and blocked the
        advisor from citing the condition (ABS-280). Match the legend by its
        *leading* glyph regardless of ``fragment_type`` (a leading glyph is the
        definition; a mid-text glyph is an inline reference), and still accept a
        true FOOTNOTE that merely contains the glyph. Prefer a FOOTNOTE-typed
        fragment when both exist. The deeper "ingest should type these as
        FOOTNOTE" fix is tracked in ABS-284.
        """
        if ordinal is None:
            return None
        glyph = ordinal_to_circled(ordinal)
        if glyph is None:
            return None
        # FOOTNOTE-typed fragments sort first so a correctly-typed legend wins
        # over a PROSE legend carrying the same glyph.
        footnote_first = case(
            (SourceFragment.fragment_type == FragmentType.FOOTNOTE, 0),
            else_=1,
        )
        stmt = (
            select(SourceFragment)
            .where(SourceFragment.document_id == document_id)
            .where(
                or_(
                    # Legend line of any type: begins with the circled glyph.
                    SourceFragment.text.ilike(f"{glyph}%"),
                    # Correctly-typed footnote that merely contains the glyph.
                    and_(
                        SourceFragment.fragment_type == FragmentType.FOOTNOTE,
                        SourceFragment.text.ilike(f"%{glyph}%"),
                    ),
                )
            )
            .order_by(footnote_first, SourceFragment.page_start, SourceFragment.id)
        )
        fragment = self.session.execute(stmt).scalars().first()
        return fragment.text if fragment is not None else None

    def _table_citation(self, table: SourceTable) -> CitationRef:
        """Build a grounding citation back to a source table."""
        document = self.session.get(Document, table.document_id)
        citation_path: str | None = None
        citation_label: str | None = table.caption
        if table.parent_fragment_id is not None:
            parent = self.session.get(SourceFragment, table.parent_fragment_id)
            if parent is not None:
                citation_path = parent.citation_path
                citation_label = parent.citation_label or table.caption
        return CitationRef(
            citation_path=citation_path,
            citation_label=citation_label,
            document_id=table.document_id,
            municipality=document.municipality if document else None,
            bylaw_name=document.bylaw_name if document else None,
            page_start=table.page_start,
            page_end=table.page_end,
            backs=["permitted_use"],
        )

    def _indeterminate_permitted_use(
        self,
        use: str,
        zone: str,
        tables: list[SourceTable],
    ) -> PermittedUseResult:
        """Classify *why* a ``(use, zone)`` pair didn't resolve to a cell.

        Distinguishes unknown-use, unknown-zone, both-unknown, and
        both-present-but-unbound by probing the axis bindings of the scoped
        matrices. Always returns a typed indeterminate result (FR3).
        """
        table_ids = [table.id for table in tables]
        use_norm = normalize_use(use)
        zone_norm = normalize_zone(zone)
        use_bound = self._axis_entity_exists(
            table_ids, axis="row", entity_type="use", canonical_name=use_norm
        )
        zone_bound = self._axis_entity_exists(
            table_ids, axis="column", entity_type="zone", canonical_name=zone_norm
        )
        if not use_bound and not zone_bound:
            code = "unknown_use_and_zone"
            reason = (
                f"Neither use {use!r} nor zone {zone!r} is bound in the "
                "permitted-use matrix for this bylaw."
            )
        elif not use_bound:
            code = "unknown_use"
            reason = (
                f"Use {use!r} is not bound to any row of the permitted-use "
                "matrix for this bylaw."
            )
        elif not zone_bound:
            code = "unknown_zone"
            reason = (
                f"Zone {zone!r} is not bound to any column of the "
                "permitted-use matrix for this bylaw."
            )
        else:
            code = "unbound_cell"
            reason = (
                f"Use {use!r} and zone {zone!r} are each present, but no single "
                "matrix binds both axes to an addressable cell (low-confidence "
                "or cross-table binding)."
            )
        # ABS-351: when the *use* axis is what failed, surface the closest real
        # matrix rows so one missed lookup is self-correcting instead of a blind
        # retry. Advisory only — the caller re-issues with the intended row; the
        # server never picks one for it.
        suggested_uses: list[str] = []
        if code in ("unknown_use", "unknown_use_and_zone"):
            suggested_uses = match_use(use, use_row_labels(self.session, table_ids)).suggestions
        return PermittedUseResult(
            use=use,
            zone=zone,
            indeterminate=True,
            reason_code=code,
            reason=reason,
            suggested_uses=suggested_uses,
        )

    def _axis_entity_exists(
        self,
        table_ids: list[int],
        *,
        axis: str,
        entity_type: str,
        canonical_name: str,
    ) -> bool:
        """True when some scoped matrix binds ``canonical_name`` on ``axis``."""
        if not table_ids:
            return False
        stmt = (
            select(TableAxisBinding.id)
            .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
            .where(TableAxisBinding.table_id.in_(table_ids))
            .where(TableAxisBinding.axis == axis)
            .where(SemanticEntity.entity_type == entity_type)
            .where(SemanticEntity.canonical_name == canonical_name)
            .limit(1)
        )
        return self.session.execute(stmt).first() is not None

    # ------------------------------------------------------------------
    # ABS-272 — get_zone_profile thick tool
    # ------------------------------------------------------------------
    #
    # The valid section names for the ``include`` filter. ``citations``
    # is always populated regardless of the filter (FR-2.1).
    _ZONE_PROFILE_SECTIONS = ("dimensions", "uses", "parking", "citations")

    # Internal semantic searches run with a higher limit than the
    # default thin-tool call (Phase 1B). A zone's standards are spread
    # across several table-row fragments; 10 comfortably surfaces the
    # right row even when sibling zones share keyword tokens.
    _ZONE_PROFILE_SEARCH_LIMIT = 10

    # Confidence normalisation. ``_score_fragment`` is unbounded but a
    # *zone-anchored* hit — where the zone code appears in the matched
    # fragment's citation_path or label, not just its body text — scores
    # well above this when the dimension keywords also land. Dividing by
    # this constant maps a solid zone-anchored hit to ~1.0 while a
    # body-text-only brush (the zone is mentioned but the dimension
    # keywords are absent) lands below ``_ZONE_FIELD_MIN_CONFIDENCE``.
    _ZONE_FIELD_FULL_SCORE = 40.0

    # Below this normalised confidence a field is treated as "not
    # confidently extracted": the value is dropped to None and NO
    # citation is emitted for it (AC-2.9). Keeps the DTO honest — a
    # value the retrieval couldn't stand behind never reaches the LLM.
    _ZONE_FIELD_MIN_CONFIDENCE = 0.5

    def get_zone_profile(
        self,
        zone: str,
        include: list[str] | None = None,
    ) -> ZoneProfile:
        """Assemble a one-call :class:`ZoneProfile` for ``zone`` (ABS-272).

        This is a *thick* tool: instead of forcing the agent through
        3–5 thin ``search`` + ``lookup_citation`` round-trips to gather
        a zone's height, coverage, setbacks, uses and parking, it
        composes those semantic searches server-side (FR-2.3) and
        extracts the structured values with documented regex heuristics
        (see the ``_extract_zone_*`` module functions).

        ``include`` filters which sections are populated:
        ``"dimensions"``, ``"uses"``, ``"parking"``. ``citations`` is
        always populated and every non-null field traces to at least
        one citation (FR-2.4). ``None`` requests all sections.

        An unrecognised ``zone`` returns
        ``ZoneProfile(zone=zone, unknown_zone=True, citations=[])`` and
        does **not** raise — mirroring the ABS-261 tool-loop-friendly
        pattern (FR-2.5).
        """
        wanted = (
            set(self._ZONE_PROFILE_SECTIONS)
            if include is None
            else {section for section in include if section in self._ZONE_PROFILE_SECTIONS}
        )

        zone_pattern = _zone_pattern(zone)
        citations = _CitationAccumulator()
        confidence: dict[str, float] = {}

        # --- Zone identity (always run; also drives unknown-zone) ------
        identity = self._zone_best_match(f"{zone} zone", zone_pattern)
        dims_match = self._zone_best_match(f"{zone} maximum height lot coverage", zone_pattern)
        setback_match = self._zone_best_match(f"{zone} setback", zone_pattern)
        far_match = self._zone_best_match(f"{zone} floor area ratio", zone_pattern)
        uses_match = self._zone_best_match(f"{zone} use permissions permitted", zone_pattern)

        zone_found = any(
            m is not None
            for m in (identity, dims_match, setback_match, far_match, uses_match)
        )
        if not zone_found:
            # ABS-409: a zone can be known to the corpus only through the
            # permission-matrix column headers (no prose fragment names it —
            # e.g. HCD-SV). Probe the bound matrix columns before declaring
            # the zone unknown, or the uses enumeration below never runs.
            zone_found = self._zone_bound_in_permission_matrix(zone)
        if not zone_found:
            # No fragment anywhere mentions the zone — treat as unknown.
            # No exception, empty citations (FR-2.5 / ABS-261 pattern).
            return ZoneProfile(zone=zone, unknown_zone=True, citations=[])

        # --- Zone full name + chapter (best-effort, ungated) ----------
        zone_full_name: str | None = None
        chapter: str | None = None
        if identity is not None:
            zone_full_name = _extract_zone_full_name(identity.text, zone)
            chapter = _extract_chapter(identity.citation_path)
            if (zone_full_name or chapter) and identity.citation_path:
                citations.add(identity, ["zone_full_name", "chapter"])

        dimensions: ZoneDimensions | None = None
        if "dimensions" in wanted:
            dimensions = self._build_zone_dimensions(
                dims_match, setback_match, far_match, citations, confidence
            )

        uses: ZoneUses | None = None
        if "uses" in wanted:
            uses = self._build_zone_uses(uses_match, zone, citations, confidence)

        parking: ZoneParking | None = None
        if "parking" in wanted:
            parking = self._build_zone_parking(zone, citations, confidence)

        return ZoneProfile(
            zone=zone,
            zone_full_name=zone_full_name,
            chapter=chapter,
            dimensions=dimensions,
            uses=uses,
            parking=parking,
            citations=citations.to_list(),
            unknown_zone=False,
            confidence=confidence,
        )

    def _zone_search(self, query: str) -> list[RetrievalMatch]:
        """Run the internal semantic search used by ``get_zone_profile``.

        Drops the context/cross-ref/table/dataset payloads the profile
        builder doesn't need so the composed searches stay cheap.
        """
        request = RetrievalRequest(
            query=query,
            limit=self._ZONE_PROFILE_SEARCH_LIMIT,
            include_context=False,
            include_cross_references=False,
            include_tables=False,
            include_datasets=False,
        )
        return self.search(request).matches

    def _zone_best_match(self, query: str, zone_pattern) -> RetrievalMatch | None:
        """Highest-scoring search match whose text/citation names the zone.

        Filtering to fragments that actually mention the zone code is
        what keeps a sibling zone's row (which shares dimension keywords)
        from being mistaken for this zone's row.
        """
        for match in self._zone_search(query):
            haystack = " ".join(
                part
                for part in (match.text, match.citation_path, match.citation_label)
                if part
            )
            if zone_pattern.search(haystack):
                return match
        return None

    def _field_confidence(self, match: RetrievalMatch) -> float:
        return min(1.0, match.score / self._ZONE_FIELD_FULL_SCORE)

    def _build_zone_dimensions(
        self,
        dims_match: RetrievalMatch | None,
        setback_match: RetrievalMatch | None,
        far_match: RetrievalMatch | None,
        citations: "_CitationAccumulator",
        confidence: dict[str, float],
    ) -> ZoneDimensions:
        dims = ZoneDimensions()

        # Height + lot coverage live in one row (Table 5), so they share
        # the same match + citation.
        if dims_match is not None:
            conf = self._field_confidence(dims_match)
            if conf >= self._ZONE_FIELD_MIN_CONFIDENCE:
                height = _extract_height_m(dims_match.text)
                coverage = _extract_coverage_pct(dims_match.text)
                if height is not None:
                    dims.max_height_m = height
                    confidence["max_height_m"] = round(conf, 3)
                    citations.add(dims_match, ["max_height_m"])
                if coverage is not None:
                    dims.max_lot_coverage_pct = coverage
                    confidence["max_lot_coverage_pct"] = round(conf, 3)
                    citations.add(dims_match, ["max_lot_coverage_pct"])

        if setback_match is not None:
            conf = self._field_confidence(setback_match)
            if conf >= self._ZONE_FIELD_MIN_CONFIDENCE:
                for kind, attr in (
                    ("front", "front_setback_m"),
                    ("side", "side_setback_m"),
                    ("rear", "rear_setback_m"),
                ):
                    value = _extract_setback_m(setback_match.text, kind)
                    if value is not None:
                        setattr(dims, attr, value)
                        confidence[attr] = round(conf, 3)
                        citations.add(setback_match, [attr])

        if far_match is not None:
            conf = self._field_confidence(far_match)
            if conf >= self._ZONE_FIELD_MIN_CONFIDENCE:
                far = _extract_far(far_match.text)
                if far is not None:
                    dims.max_far = far
                    confidence["max_far"] = round(conf, 3)
                    citations.add(far_match, ["max_far"])

        return dims

    def _build_zone_uses(
        self,
        uses_match: RetrievalMatch | None,
        zone: str,
        citations: "_CitationAccumulator",
        confidence: dict[str, float],
    ) -> ZoneUses:
        # ABS-409: the permission-matrix enumeration is authoritative when the
        # zone binds a matrix column — symbol-dot bylaws (Regional Centre)
        # never carry the P/N prose the regex path below parses, which is why
        # their zone profiles shipped empty use lists. The prose path stays as
        # the fallback for P/N-styled corpora.
        matrix = self._build_zone_uses_from_matrix(zone, citations, confidence)
        if matrix is not None:
            return matrix

        uses = ZoneUses()
        if uses_match is None:
            return uses
        conf = self._field_confidence(uses_match)
        if conf < self._ZONE_FIELD_MIN_CONFIDENCE:
            return uses
        permitted, not_permitted = _extract_uses(uses_match.text, zone)
        if permitted or not_permitted:
            uses.permitted = permitted
            uses.not_permitted = not_permitted
            confidence["uses"] = round(conf, 3)
            citations.add(uses_match, ["uses"])
        return uses

    # Confidence recorded for matrix-enumerated use lists. Matches the table
    # classifier's permission-matrix confidence (_classify_table) — the cells
    # are read directly off bound axes, not regex-extracted from prose.
    _MATRIX_USES_CONFIDENCE = 0.9

    def _zone_bound_in_permission_matrix(self, zone: str) -> bool:
        """True when a scoped permission matrix binds ``zone`` as a column."""
        tables = self._permission_matrix_tables(document_id=None)
        return self._axis_entity_exists(
            [table.id for table in tables],
            axis="column",
            entity_type="zone",
            canonical_name=normalize_zone(zone),
        )

    def _build_zone_uses_from_matrix(
        self,
        zone: str,
        citations: "_CitationAccumulator",
        confidence: dict[str, float],
    ) -> ZoneUses | None:
        """Enumerate the zone's use column from the bound permission matrices.

        Unions across every scoped matrix that binds the zone — a single
        logical table (Table 1A) spans several ``source_table`` rows, each
        carrying a different slice of the use rows. Returns ``None`` when no
        matrix binds the zone so the caller can fall through to the prose
        path. Footnote condition text is joined once per ordinal (deduped) via
        the ABS-280 legend matcher.
        """
        tables = self._permission_matrix_tables(document_id=None)
        if not tables:
            return None

        uses = ZoneUses()
        seen: set[str] = set()
        condition_cache: dict[tuple[int, int], str | None] = {}
        contributing: list[SourceTable] = []
        for table in tables:
            rows = enumerate_permission_column(
                self.session, table_id=table.id, zone=zone
            )
            if rows is None:
                continue
            contributing.append(table)
            for row in rows:
                label = row["use_label"]
                if label in seen:
                    continue
                seen.add(label)
                permission = row["permission"]
                if permission == "permitted":
                    uses.permitted.append(label)
                elif permission == "conditional":
                    ordinal = row.get("footnote_ordinal")
                    condition: str | None = None
                    if ordinal is not None:
                        cache_key = (table.document_id, ordinal)
                        if cache_key not in condition_cache:
                            condition_cache[cache_key] = self._footnote_condition_text(
                                document_id=table.document_id, ordinal=ordinal
                            )
                        condition = condition_cache[cache_key]
                    uses.conditional.append(
                        ConditionalUse(
                            use=label, footnote_ordinal=ordinal, condition=condition
                        )
                    )
                elif permission == "not_permitted":
                    uses.not_permitted.append(label)

        if not contributing or not (
            uses.permitted or uses.conditional or uses.not_permitted
        ):
            # No matrix binds the zone (or only placeholder rows did) — let
            # the caller fall through to the prose-extraction path.
            return None
        confidence["uses"] = self._MATRIX_USES_CONFIDENCE
        for table in contributing:
            ref = self._table_citation(table)
            citations.add_ref(ref, ["uses"])
        return uses

    def _build_zone_parking(
        self,
        zone: str,
        citations: "_CitationAccumulator",
        confidence: dict[str, float],
    ) -> ZoneParking:
        """Parking is general (zone-independent) with per-zone exemptions,
        so this intent does NOT zone-filter the match — it takes the top
        off-street-parking fragment and derives ``applies`` from the
        exemption clause.
        """
        parking = ZoneParking()
        matches = self._zone_search("off-street parking requirements")
        if not matches:
            return parking
        match = matches[0]
        conf = self._field_confidence(match)
        if conf < self._ZONE_FIELD_MIN_CONFIDENCE:
            return parking

        parking.min_spaces_per_dwelling_unit = _extract_parking_min_per_unit(match.text)
        parking.schedule_reference = _extract_parking_schedule_ref(match.text)
        applies, notes = _extract_parking_applicability(match.text, zone)
        parking.applies = applies
        parking.notes = notes
        if any(
            value is not None
            for value in (
                parking.applies,
                parking.min_spaces_per_dwelling_unit,
                parking.schedule_reference,
            )
        ):
            confidence["parking"] = round(conf, 3)
            citations.add(match, ["parking"])
        return parking

    # Spatial-channel scoring constants. The values are deliberately higher
    # than typical text-channel scores so a confident spatial hit (the input
    # point falls inside a precinct polygon) surfaces near the top, even when
    # the linked fragment's text wouldn't otherwise be picked up by the
    # keyword scorer. Partial overlaps (e.g. a line crossing several
    # precincts) score lower so they don't drown out exact containment.
    _SPATIAL_CONTAINS_SCORE = 100.0
    _SPATIAL_PARTIAL_SCORE = 50.0
    _SPATIAL_TEXT_BOTH_BONUS = 10.0

    def search(self, request: RetrievalRequest) -> RetrievalResponse:
        # Two retrievers run in parallel: text-keyword scoring against
        # fragments, and spatial intersection against linked geo datasets
        # when a location is supplied. They produce disjoint or overlapping
        # candidate fragment sets that are merged on fragment_id, with a
        # bonus for fragments surfaced by both channels.
        resolved_location, resolution_detail = self._resolve_location_slot(
            request.location
        )

        text_scored = self._text_channel_scores(request)
        spatial_scored = (
            self._spatial_channel_scores(request, resolved_location)
            if resolved_location is not None
            else {}
        )

        merged = self._merge_channel_scores(text_scored, spatial_scored)
        total_matches = len(merged)

        notes = self._build_response_notes(
            request, resolved_location, resolution_detail
        )

        # Resolve candidate fragments from the union of both channels.
        candidate_fragment_ids = [fid for _, fid, _ in merged[: request.limit]]
        if not candidate_fragment_ids:
            return RetrievalResponse(
                total_matches=0, matches=[], notes=notes
            )

        fragments_by_id = {
            fragment.id: fragment
            for fragment in self.session.execute(
                select(SourceFragment).where(SourceFragment.id.in_(candidate_fragment_ids))
            )
            .scalars()
            .all()
        }

        matches: list[RetrievalMatch] = []
        for score, fragment_id, channels in merged[: request.limit]:
            fragment = fragments_by_id.get(fragment_id)
            if fragment is None:
                continue
            match = self._build_match(
                fragment,
                score=score,
                include_context=request.include_context,
                include_cross_references=request.include_cross_references,
                include_tables=request.include_tables,
                include_datasets=request.include_datasets,
                resolved_location=resolved_location,
            )
            match.retrieval_channels = sorted(channels)
            matches.append(match)

        return RetrievalResponse(
            total_matches=total_matches,
            matches=matches,
            notes=notes,
        )

    def _scoped_linked_datasets(
        self,
        *,
        document_id: int | None = None,
        municipality: str | None = None,
        bylaw_name: str | None = None,
    ) -> list[ExternalDataset]:
        """Return every linked geo dataset visible under the active scope.

        Shared by the spatial retrieval channel (``_spatial_channel_scores``)
        and the thick ``get_address_profile`` tool so both see exactly the
        same dataset set — the default-document resolver AND-ed with any
        explicit document/municipality/bylaw filter. Keeping this in one
        place is what FR-3.3 means by "reuse the spatial-join code": the
        profile composes the established scoping rather than re-deriving them
        and risking divergence from the evaluator's view of the corpus.

        Delegates to the module-level :func:`scoped_linked_datasets` so the
        corpus-coherence audit (ABS-356) can call the identical query outside
        a live service instance.
        """
        return scoped_linked_datasets(
            self.session,
            default_document_id_resolver=self._default_document_id_resolver,
            document_id=document_id,
            municipality=municipality,
            bylaw_name=bylaw_name,
        )

    # ------------------------------------------------------------------
    # Thick tool: get_address_profile (ABS-273 / Phase 3)
    # ------------------------------------------------------------------
    #
    # Overlay roles whose datasets are LINE geometries (street segments) rather
    # than area polygons, so a resolved point must be tested with the *abuts*
    # predicate (nearest designated segment within the buffer) instead of
    # point-in-polygon. Currently just Schedule 7 pedestrian-oriented
    # commercial streets; add future centreline-keyed overlays here.
    _ABUTS_OVERLAY_ROLES: frozenset[str] = frozenset({"pedestrian_street"})

    def _predicate_for_role(self, role: str) -> str:
        """Spatial predicate query_features should use for a given overlay role."""
        return "abuts" if role in self._ABUTS_OVERLAY_ROLES else "intersects"

    def get_address_profile(self, address: str) -> AddressProfile:
        """Resolve an address to its zone + overlay grounding context.

        Free-text ``address`` is parsed with the same deterministic
        extractor the retrieval surface uses, geocoded through the existing
        layered resolver, then intersected against every linked geo dataset
        in scope. The well-known overlays (zoning, height, FAR, heritage,
        bonus zoning) populate the dedicated DTO fields; everything else is
        surfaced under ``overlays``. Each contributing overlay yields a
        ``CitationRef`` so a grounded answer can cite the schedule.

        Never raises for an unresolvable address — FR-3.4 — returning an
        ``AddressProfile`` with ``unresolvable=True`` and empty citations so
        the calling agent can fall back to the thin tools cleanly.
        """
        refs = RegexLocationExtractor().extract(address)
        if not refs:
            return AddressProfile(address=address, unresolvable=True)

        ref = refs[0]
        resolved, _detail = resolve_location_with_detail(self.session, ref)
        canonical_address = ref.raw_text or address
        if resolved is None:
            return AddressProfile(
                address=canonical_address,
                civic_number=ref.civic_number,
                street=ref.street,
                pid=ref.parcel_id,
                unresolvable=True,
            )
        return self._build_address_profile(canonical_address, ref, resolved)

    # -- ABS-375: adjacent-parcel zoning lookup ---------------------------
    #
    # Role marker for the base parcel geography, matching the case-open
    # spatial extractor (``layer2.spatial.extractor.PARCELS_ROLE``) and the
    # ``role: property_parcels`` tag on ``halifax_property_parcels.yaml``.
    _PARCELS_ROLE = "property_parcels"

    def get_adjacent_zoning(self, address: str) -> AdjacentZoningProfile:
        """Resolve the zoning of the parcels abutting an address's parcel.

        The report agent needs this to give a *definitive* rear/side setback
        verdict when the requirement is conditional on the abutting zone
        (ABS-375). It geocodes the address, finds the containing parcel,
        enumerates every parcel touching it, and resolves each neighbour's
        zone by intersecting the neighbour's centroid against the zoning
        overlay — the same intersection ``get_address_profile`` uses for the
        subject parcel.

        Never raises (mirrors ``get_address_profile``). An un-geocodable
        address returns ``unresolvable=True``; a missing parcels dataset or a
        point outside every parcel returns an empty ``neighbours`` list with a
        populated ``note`` so the agent can fall back to text retrieval.
        """
        refs = RegexLocationExtractor().extract(address)
        if not refs:
            return AdjacentZoningProfile(address=address, unresolvable=True)
        ref = refs[0]
        resolved, _detail = resolve_location_with_detail(self.session, ref)
        canonical_address = ref.raw_text or address
        if resolved is None:
            return AdjacentZoningProfile(
                address=canonical_address, unresolvable=True
            )

        parcels_ids = self._parcels_dataset_ids()
        if not parcels_ids:
            return AdjacentZoningProfile(
                address=canonical_address,
                note=(
                    "No property-parcels dataset is ingested, so abutting "
                    "parcels cannot be enumerated. Ingest the parcels dataset "
                    "or resolve the abutting zone from the zoning schedule map."
                ),
            )

        try:
            point = shapely_shape(resolved.geometry)
        except (TypeError, ValueError, KeyError):
            return AdjacentZoningProfile(
                address=canonical_address, unresolvable=True
            )
        if point.geom_type != "Point":
            point = point.representative_point()

        subject = find_containing_feature(
            self.session, dataset_ids=parcels_ids, point=point
        )
        if subject is None:
            return AdjacentZoningProfile(
                address=canonical_address,
                note=(
                    "The geocoded point did not fall inside any parcel "
                    "polygon, so the subject parcel could not be identified."
                ),
            )

        subject_centroid = self._feature_centroid(subject)
        subject_zone, _ = self._resolve_zone_at_point(subject_centroid or point)

        neighbours_features = find_abutting_features(
            self.session, dataset_ids=parcels_ids, subject=subject
        )
        neighbours: list[NeighbourZone] = []
        citation: CitationRef | None = None
        for feature in neighbours_features:
            centroid = self._feature_centroid(feature)
            if centroid is None:
                continue
            zone, zone_dataset = self._resolve_zone_at_point(centroid)
            if citation is None and zone_dataset is not None:
                citation = self._citation_ref_for_dataset(
                    zone_dataset, source="zone"
                )
            neighbours.append(
                NeighbourZone(
                    pid=(feature.canonical_attributes_json or {}).get(
                        "parcel_id"
                    ),
                    zone=zone,
                    direction=(
                        self._bearing(subject_centroid, centroid)
                        if subject_centroid is not None
                        else None
                    ),
                )
            )

        distinct = sorted({n.zone for n in neighbours if n.zone})
        note = None
        if not neighbours:
            note = (
                "No parcels abut the subject parcel in the ingested fabric "
                "(the parcel may front only streets/rights-of-way)."
            )
        return AdjacentZoningProfile(
            address=canonical_address,
            subject_pid=(subject.canonical_attributes_json or {}).get(
                "parcel_id"
            ),
            subject_zone=subject_zone,
            neighbours=neighbours,
            distinct_neighbour_zones=distinct,
            citation=citation,
            note=note,
        )

    def _parcels_dataset_ids(self) -> list[int]:
        """Return dataset ids tagged ``role=property_parcels`` in metadata.

        Parcels are base geography, not a bylaw-linked overlay, so they are
        found by their ``metadata_json.role`` tag rather than through
        ``_scoped_linked_datasets`` (which only sees linked overlays).
        """
        rows = self.session.execute(
            select(ExternalDataset.id, ExternalDataset.metadata_json).order_by(
                ExternalDataset.id
            )
        ).all()
        return [
            int(row.id)
            for row in rows
            if (row.metadata_json or {}).get("role") == self._PARCELS_ROLE
        ]

    def _feature_centroid(self, feature: ExternalDatasetFeature):
        """Return a shapely Point at the feature's centroid, or None."""
        try:
            geom = shapely_shape(feature.geometry_geojson)
        except (TypeError, ValueError, KeyError):
            return None
        if geom.is_empty or not geom.is_valid:
            return None
        centroid = geom.centroid
        return None if centroid.is_empty else centroid

    def _resolve_zone_at_point(
        self, point
    ) -> tuple[str | None, ExternalDataset | None]:
        """Resolve the zone code covering ``point`` (a shapely Point).

        Intersects the point against every in-scope zoning overlay and
        returns the strongest match's ``zone_code`` plus its dataset (for
        citation). Returns ``(None, None)`` when no zone polygon covers the
        point or no zoning dataset is in scope.
        """
        location = ResolvedLocation(
            kind="point", geometry=point.__geo_interface__, source="parcel_centroid"
        )
        for dataset in self._scoped_linked_datasets():
            if self._overlay_role(dataset) != "zone":
                continue
            matches = query_features(
                self.session, dataset_id=dataset.id, location=location
            )
            if not matches:
                continue
            canonical = matches[0].feature.canonical_attributes_json or {}
            zone = canonical.get("zone_code")
            if zone:
                return str(zone), dataset
        return None, None

    @staticmethod
    def _bearing(origin, target) -> str | None:
        """Coarse 8-point compass bearing from ``origin`` to ``target``.

        Both are shapely Points in 4326. Longitude deltas are scaled by
        cos(lat) so the bearing reflects real east/west distance rather than
        raw degrees. Returns None when the points coincide.
        """
        if origin is None or target is None:
            return None
        from math import atan2, cos, degrees, radians  # noqa: PLC0415

        dx = (target.x - origin.x) * cos(radians(origin.y))
        dy = target.y - origin.y
        if dx == 0 and dy == 0:
            return None
        angle = (degrees(atan2(dx, dy)) + 360.0) % 360.0
        dirs = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
        return dirs[int((angle + 22.5) % 360.0 // 45.0)]

    # -- Phase 4 (ABS-274): intent-routed bylaw_query mega-tool -----------
    #
    # Thin tools the model should pivot to when an intent doesn't fit any
    # server-side composition. ``search_bylaw_evidence`` + ``lookup_citation``
    # lead the list (FR-4.2) because they cover the broadest fallback.
    _BYLAW_QUERY_SUGGESTED_TOOLS: tuple[str, ...] = (
        "search_bylaw_evidence",
        "lookup_citation",
        "get_zone_profile",
        "get_address_profile",
        "get_document_outline",
    )

    # Maps a caller-supplied ``proposed`` key to the governing
    # ``ZoneDimensions`` field and the bound type. ``max`` => the proposal
    # must not exceed the limit (height, coverage, FAR); ``min`` => the
    # proposal must not fall below it (setbacks). Aliases are included so the
    # model can use the natural attribute name or the DTO field name.
    _DIMENSIONAL_CHECKS: dict[str, tuple[str, str]] = {
        "height_m": ("max_height_m", "max"),
        "max_height_m": ("max_height_m", "max"),
        "building_height_m": ("max_height_m", "max"),
        "lot_coverage_pct": ("max_lot_coverage_pct", "max"),
        "max_lot_coverage_pct": ("max_lot_coverage_pct", "max"),
        "far": ("max_far", "max"),
        "max_far": ("max_far", "max"),
        "floor_area_ratio": ("max_far", "max"),
        "front_setback_m": ("front_setback_m", "min"),
        "side_setback_m": ("side_setback_m", "min"),
        "rear_setback_m": ("rear_setback_m", "min"),
    }

    def bylaw_query(
        self,
        intent: str,
        address: str | None = None,
        zone: str | None = None,
        proposed: dict | None = None,
    ) -> BylawQueryResponse:
        """Intent-routed composer over the Phase 2/3 thick tools (ABS-274).

        The caller declares its ``intent`` once and the server dispatches to
        the right composition, encoding the "which tool fits?" choice
        server-side instead of leaving it to the model's tool-use loop
        (FR-4.1/4.2):

        * ``zone_feasibility`` -> ``get_zone_profile(zone)`` (full DTO:
          dimensions + uses + parking in ONE call).
        * ``address_lookup``   -> ``get_address_profile(address)``.
        * ``use_check``        -> ``get_zone_profile(zone, include=['uses'])``.
        * ``dimensional_check``-> ``get_zone_profile(zone, include=['dimensions'])``
          plus a :class:`ConformanceCheck` of ``proposed`` against the zone.

        Composition reuses the Phase 2/3 implementations directly — no
        duplicated retrieval logic (FR-4.4) — so a bug there propagates here
        rather than drifting.

        An ``intent`` outside :class:`BylawIntent` returns
        ``unrecognized_intent=True`` with thin-tool suggestions and never
        raises (FR-4.2). A recognised intent missing its required slot
        (``zone``/``address``) likewise degrades to thin-tool suggestions
        rather than crashing.
        """
        if intent not in BYLAW_INTENTS:
            return BylawQueryResponse(
                intent=intent,
                unrecognized_intent=True,
                suggested_tools=list(self._BYLAW_QUERY_SUGGESTED_TOOLS),
            )

        if intent == BylawIntent.ADDRESS_LOOKUP.value:
            if not address:
                return BylawQueryResponse(
                    intent=intent,
                    suggested_tools=list(self._BYLAW_QUERY_SUGGESTED_TOOLS),
                )
            profile = self.get_address_profile(address)
            return BylawQueryResponse(
                intent=intent,
                address_profile=profile,
                citations=list(profile.citations),
            )

        # All remaining intents are zone-scoped.
        if not zone:
            return BylawQueryResponse(
                intent=intent,
                suggested_tools=list(self._BYLAW_QUERY_SUGGESTED_TOOLS),
            )

        if intent == BylawIntent.USE_CHECK.value:
            zone_profile = self.get_zone_profile(zone=zone, include=["uses"])
            return BylawQueryResponse(
                intent=intent,
                zone_profile=zone_profile,
                citations=list(zone_profile.citations),
            )

        if intent == BylawIntent.DIMENSIONAL_CHECK.value:
            zone_profile = self.get_zone_profile(zone=zone, include=["dimensions"])
            conformance = self._build_conformance_check(zone, zone_profile, proposed or {})
            return BylawQueryResponse(
                intent=intent,
                zone_profile=zone_profile,
                conformance_check=conformance,
                citations=list(zone_profile.citations),
            )

        # zone_feasibility — the full profile in a single get_zone_profile
        # call (include=None -> dimensions + uses + parking), so a sibling of
        # AC-4.6 can mock get_zone_profile and assert exactly one call.
        zone_profile = self.get_zone_profile(zone=zone)
        return BylawQueryResponse(
            intent=intent,
            zone_profile=zone_profile,
            citations=list(zone_profile.citations),
        )

    def _build_conformance_check(
        self, zone: str, zone_profile: ZoneProfile, proposed: dict
    ) -> ConformanceCheck:
        """Evaluate each ``proposed`` value against the zone's dimensions.

        Maximums (height/coverage/FAR) fail when the proposal exceeds the
        limit; minimums (setbacks) fail when it falls short. A key with no
        mapped standard, a zone silent on the field, or a non-numeric
        proposal is ``inconclusive`` rather than ``fail`` (FR-4.3).
        """
        dims = zone_profile.dimensions
        results: list[ConformanceAttribute] = []
        for key, value in proposed.items():
            mapping = self._DIMENSIONAL_CHECKS.get(key)
            if mapping is None:
                results.append(
                    ConformanceAttribute(
                        attribute=key,
                        proposed=value,
                        comparison="unknown",
                        status="inconclusive",
                        note=f"No dimensional standard is mapped for '{key}'.",
                    )
                )
                continue
            field_name, bound = mapping
            limit = getattr(dims, field_name, None) if dims is not None else None
            numeric = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            if limit is None or numeric is None:
                note = (
                    f"Zone {zone} is silent on {field_name} (governed by a "
                    "schedule or unspecified)."
                    if numeric is not None
                    else f"Proposed value for '{key}' is not numeric."
                )
                results.append(
                    ConformanceAttribute(
                        attribute=key,
                        proposed=value,
                        limit=limit,
                        comparison=bound,
                        status="inconclusive",
                        note=note,
                    )
                )
                continue
            if bound == "max":
                ok = numeric <= limit
                note = (
                    f"{numeric:g} m/unit is within the {limit:g} maximum."
                    if ok
                    else f"{numeric:g} exceeds the {limit:g} maximum for zone {zone}."
                )
            else:  # min
                ok = numeric >= limit
                note = (
                    f"{numeric:g} meets the {limit:g} minimum."
                    if ok
                    else f"{numeric:g} is below the {limit:g} minimum for zone {zone}."
                )
            results.append(
                ConformanceAttribute(
                    attribute=key,
                    proposed=value,
                    limit=float(limit),
                    comparison=bound,
                    status="pass" if ok else "fail",
                    note=note,
                )
            )

        if any(r.status == "fail" for r in results):
            overall = "fail"
        elif results and all(r.status == "pass" for r in results):
            overall = "pass"
        else:
            overall = "inconclusive"
        return ConformanceCheck(zone=zone, results=results, overall=overall)

    def _overlay_role(self, dataset: ExternalDataset) -> str:
        return overlay_role_for_name(dataset.name)

    def _build_address_profile(
        self,
        address: str,
        ref: LocationReference,
        resolved: ResolvedLocation,
    ) -> AddressProfile:
        profile = AddressProfile(
            address=address,
            civic_number=ref.civic_number,
            street=ref.street,
            pid=ref.parcel_id,
            unresolvable=False,
        )
        overlays: list[OverlayRef] = []
        citations: list[CitationRef] = []
        # "available" = a dataset of this role is in scope, so a non-match is
        # a meaningful False rather than an unknown None.
        heritage_available = False
        bonus_available = False
        pedestrian_available = False

        for dataset in self._scoped_linked_datasets():
            role = self._overlay_role(dataset)
            if role == "heritage":
                heritage_available = True
            elif role == "bonus_zoning":
                bonus_available = True
            elif role == "pedestrian_street":
                pedestrian_available = True

            matches = query_features(
                self.session,
                dataset_id=dataset.id,
                location=resolved,
                predicate=self._predicate_for_role(role),
            )
            if not matches:
                continue
            # Strongest match wins (query_features sorts containment/overlap
            # first), mirroring how the spatial channel keeps the best hit.
            best = matches[0]
            canonical = dict(best.feature.canonical_attributes_json or {})
            label = self._overlay_label(role, canonical, best.feature.feature_key)

            overlays.append(
                OverlayRef(
                    kind=role,
                    dataset_name=dataset.name,
                    label=label,
                    citation=dataset.linked_fragment_citation,
                    attributes=canonical,
                )
            )
            citations.append(self._citation_ref_for_dataset(dataset, source=role))

            if role == "zone":
                profile.zone = canonical.get("zone_code") or label
            elif role == "height_precinct":
                profile.height_precinct = label
            elif role == "far_precinct":
                profile.far_precinct = label
            elif role == "heritage":
                profile.heritage = True
            elif role == "bonus_zoning":
                profile.bonus_zoning_eligible = True
            elif role == "pedestrian_street":
                profile.abuts_pedestrian_street = True

        # A checked-but-unmatched heritage / bonus / pedestrian-street dataset
        # is a definitive "no", distinct from "no such dataset in scope" (left
        # as None). For POCS this is the s.38(2)-vs-s.69(d) branch: a confident
        # False lets the agent apply s.69(d) instead of hedging both scenarios.
        if profile.heritage is None and heritage_available:
            profile.heritage = False
        if profile.bonus_zoning_eligible is None and bonus_available:
            profile.bonus_zoning_eligible = False
        if profile.abuts_pedestrian_street is None and pedestrian_available:
            profile.abuts_pedestrian_street = False

        profile.overlays = overlays
        profile.citations = citations
        return profile

    @staticmethod
    def _overlay_label(
        role: str, canonical: dict[str, object], feature_key: str
    ) -> str | None:
        """Pick the headline label for an overlay from its canonical attrs.

        Prefers an explicit ``display_label`` when the dataset carries one.
        Otherwise synthesises the precinct shorthand the issue's examples
        use ("HP-25", "FA-3.5") from the height/FAR value, so the agent
        sees a stable identifier instead of a bare number.
        """
        display = canonical.get("display_label")
        if isinstance(display, str) and display:
            return display
        if role == "zone":
            zone = canonical.get("zone_code")
            return str(zone) if zone is not None else None
        if role == "height_precinct":
            height_m = canonical.get("max_height_m")
            if height_m is not None:
                return f"HP-{float(height_m):g}"
            storeys = canonical.get("max_height_storeys")
            if storeys is not None:
                return f"HP-{int(storeys)}st"
            return None
        if role == "far_precinct":
            far = canonical.get("max_far")
            return f"FA-{float(far):g}" if far is not None else None
        if role == "heritage":
            return _first_str(canonical, "district_name", "district_label", "district_code")
        if role == "bonus_zoning":
            return _first_str(canonical, "district_code", "district_name")
        if role == "pedestrian_street":
            # The designated street name — cited verbatim ("abuts Quinpool
            # Road, a pedestrian-oriented commercial street per Schedule 7").
            return _first_str(canonical, "street_name", "district_name")
        # Generic overlay: any district-ish name, else the feature key.
        return _first_str(
            canonical, "district_name", "district_label", "district_code"
        ) or feature_key

    def _citation_ref_for_dataset(
        self, dataset: ExternalDataset, *, source: str
    ) -> CitationRef:
        fragment = (
            self.session.get(SourceFragment, dataset.linked_fragment_id)
            if dataset.linked_fragment_id is not None
            else None
        )
        document = None
        if fragment is not None:
            document = self.session.get(Document, fragment.document_id)
        elif dataset.linked_document_id is not None:
            document = self.session.get(Document, dataset.linked_document_id)
        return CitationRef(
            citation_path=fragment.citation_path if fragment else None,
            citation_label=(
                (fragment.citation_label if fragment else None)
                or dataset.linked_fragment_citation
            ),
            document_id=document.id if document else None,
            municipality=document.municipality if document else None,
            bylaw_name=document.bylaw_name if document else None,
            backs=[source],
        )

    def _build_response_notes(
        self,
        request: RetrievalRequest,
        resolved_location: ResolvedLocation | None,
        resolution_detail: str | None,
    ) -> list[str]:
        """Generate server-side advisories aimed at LLM callers.

        Two signals today:
        - "address-shaped text in query but no location field" — a strong
          indicator that the LLM didn't recognise the question needed
          spatial filtering. The note tells the LLM exactly what to
          change in the next call.
        - "location slot supplied but resolution failed" — when the caller
          DID populate ``location`` but the geocoder returned nothing
          (REQUEST_DENIED, ZERO_RESULTS, key unset, ...). Without this
          note the LLM sees empty ``linked_datasets`` with no explanation
          and tends to hallucinate plausible-but-wrong reasons ("may be
          outside the LUB boundary"). Defense-in-depth: the tool
          description tells the LLM upfront to use the slot; this fires
          when the slot WAS used but the resolver lost.
        """
        notes: list[str] = []
        if request.location is None:
            extracted = RegexLocationExtractor().extract(request.query or "")
            if extracted:
                ref = extracted[0]
                if ref.kind == "civic_address" and ref.civic_number and ref.street:
                    notes.append(
                        "The query contains a civic address "
                        f"({ref.raw_text!r}) but no 'location' field was "
                        "supplied. Spatial filtering against zone, height, "
                        "FAR, heritage, and bonus-zoning datasets was NOT "
                        "performed. Re-issue the request with "
                        "location={civic_number: "
                        f"{ref.civic_number!r}, street: {ref.street!r}"
                        "} to receive the spatial answer."
                    )
                elif ref.kind == "parcel_id" and ref.parcel_id:
                    notes.append(
                        f"The query contains a parcel id ({ref.parcel_id!r}) "
                        "but no 'location' field was supplied. Re-issue "
                        "with location={parcel_id: "
                        f"{ref.parcel_id!r}"
                        "} to enable spatial filtering."
                    )
        elif resolved_location is None:
            reason = resolution_detail or "unknown reason"
            notes.append(
                "A 'location' slot was supplied but the geocoder could "
                f"not resolve it ({reason}). Spatial filtering against "
                "zone, height, FAR, heritage, and bonus-zoning datasets "
                "was NOT performed — any spatial fields in this response "
                "are empty as a result. Do not infer a zone or precinct "
                "from text-channel matches alone; tell the user the "
                "address could not be resolved and recommend they verify "
                "via HRM's mapping tools."
            )
        return notes

    # _score_fragment adds +1.0 for any PARSED fragment as a quality signal,
    # independent of whether the query text actually appeared in it. That
    # baseline shouldn't qualify a fragment as a "text-channel match" — it's
    # a metadata bonus, not a content match. Tag a fragment as text-channel
    # only when its score exceeds this baseline.
    _TEXT_CHANNEL_THRESHOLD = 1.0

    def _text_channel_scores(self, request: RetrievalRequest) -> dict[int, float]:
        """Keyword-score every in-scope fragment. Returns {fragment_id: score}
        for fragments whose score exceeds the parse-status baseline (i.e. the
        query text actually matched some content).
        """
        stmt = self._fragment_scope_statement(request)
        fragments = self.session.execute(stmt).scalars().all()
        scored: dict[int, float] = {}
        for fragment in fragments:
            score = self._score_fragment(fragment, request.query)
            if score > self._TEXT_CHANNEL_THRESHOLD:
                scored[fragment.id] = score
        return scored

    def _spatial_channel_scores(
        self,
        request: RetrievalRequest,
        location: ResolvedLocation,
    ) -> dict[int, float]:
        """Spatial intersection against every linked dataset whose linked
        fragment is in the active scope. Returns {fragment_id: score}.

        A linked fragment surfaces at most once per spatial query — if
        multiple datasets share the same linked fragment, the strongest
        match (containment over partial overlap) wins.
        """
        # Mirror the same scope rules used by the text channel so a request
        # under --latest-only (or with explicit document_id / municipality /
        # bylaw_name) constrains the spatial channel identically.
        datasets = self._scoped_linked_datasets(
            document_id=request.document_id,
            municipality=request.municipality,
            bylaw_name=request.bylaw_name,
        )

        scored: dict[int, float] = {}
        # When the caller restricts to a specific attribute, the
        # linked-fragment hit only counts if that fragment is itself
        # tagged with the attribute. Without this, a spatial hit on
        # the zoning schedule would surface for every attribute query
        # ("the zone schedule fragment is linked to the parcel polygon"
        # ≠ "the zone schedule fragment regulates building height").
        allowed_linked_fragment_ids: set[int] | None = None
        if request.attribute_tag_filter:
            linked_ids = [
                d.linked_fragment_id for d in datasets if d.linked_fragment_id is not None
            ]
            if linked_ids:
                allowed_linked_fragment_ids = set(
                    self.session.execute(
                        select(SourceFragment.id)
                        .where(SourceFragment.id.in_(linked_ids))
                        .where(
                            _attribute_tag_filter_clause(
                                request.attribute_tag_filter,
                                dialect_name=self._dialect_name(),
                            )
                        )
                    ).scalars().all()
                )
            else:
                allowed_linked_fragment_ids = set()
        for dataset in datasets:
            assert dataset.linked_fragment_id is not None  # narrowed by query above
            if (
                allowed_linked_fragment_ids is not None
                and dataset.linked_fragment_id not in allowed_linked_fragment_ids
            ):
                continue
            for match in query_features(
                self.session,
                dataset_id=dataset.id,
                location=location,
                predicate=self._predicate_for_role(self._overlay_role(dataset)),
            ):
                score = (
                    self._SPATIAL_CONTAINS_SCORE
                    if match.contains_input
                    else self._SPATIAL_PARTIAL_SCORE
                )
                # Keep the strongest score per linked fragment.
                if score > scored.get(dataset.linked_fragment_id, 0.0):
                    scored[dataset.linked_fragment_id] = score
        return scored

    def _merge_channel_scores(
        self,
        text_scored: dict[int, float],
        spatial_scored: dict[int, float],
    ) -> list[tuple[float, int, list[str]]]:
        """Return [(score, fragment_id, channels)] sorted by score desc.

        Channel set per fragment lets the caller see whether the match came
        from text, spatial, or both. Fragments hit by both channels get a
        small bonus on top of the max channel score so they outrank
        single-channel hits with the same raw score.
        """
        fragment_ids = set(text_scored) | set(spatial_scored)
        merged: list[tuple[float, int, list[str]]] = []
        for fid in fragment_ids:
            text_s = text_scored.get(fid, 0.0)
            spatial_s = spatial_scored.get(fid, 0.0)
            channels: list[str] = []
            if text_s > 0:
                channels.append("text")
            if spatial_s > 0:
                channels.append("spatial")
            score = max(text_s, spatial_s)
            if text_s > 0 and spatial_s > 0:
                score += self._SPATIAL_TEXT_BOTH_BONUS
            merged.append((score, fid, channels))
        merged.sort(key=lambda entry: (-entry[0], entry[1]))
        return merged

    def _resolve_location_slot(
        self, slot: LocationSlot | None
    ) -> tuple[ResolvedLocation | None, str | None]:
        """Translate a structured slot to a ResolvedLocation plus optional failure detail.

        - ``geometry`` short-circuits geocoding (caller already has a point/parcel).
        - Otherwise build a LocationReference from the slot fields and run it
          through the layered ``resolve_location_with_detail`` (in-database
          civic-address dataset, then Google fallback if configured).
        - The MCP path NEVER invokes the regex extractor — that's reserved for
          callers who don't have an LLM in front of them.

        Returns ``(resolved, detail)``. ``detail`` is a short reason string
        when ``resolved`` is None (REQUEST_DENIED, "address malformed", etc.)
        so the caller can surface it back to the LLM as a response note.
        """
        if slot is None:
            return None, None
        if slot.geometry is not None:
            return (
                ResolvedLocation(
                    kind=_kind_from_geometry(slot.geometry),
                    geometry=slot.geometry,
                    confidence=1.0,
                    source="caller_supplied",
                    reference_text=None,
                ),
                None,
            )
        ref = _slot_to_reference(slot)
        if ref is None:
            return None, "location slot did not contain enough information to build a reference"
        return resolve_location_with_detail(self.session, ref)

    def _fragment_scope_statement(self, request: RetrievalRequest) -> Select[tuple[SourceFragment]]:
        stmt = (
            select(SourceFragment)
            .join(Document, Document.id == SourceFragment.document_id)
            .order_by(SourceFragment.page_start, SourceFragment.reading_order_start, SourceFragment.id)
        )
        # Hard scope: when the deployment has configured a default document
        # resolver, those document ids are ALWAYS pinned. Any request
        # filter (document_id, municipality, bylaw_name) ANDs with them.
        # A request asking for a different document or bylaw therefore
        # returns empty rather than leaking into a stale or superseded
        # ingest — better empty than wrong.
        default_ids = self._resolve_default_document_ids()
        if default_ids is not None:
            stmt = stmt.where(SourceFragment.document_id.in_(default_ids))
        if request.document_id is not None:
            stmt = stmt.where(SourceFragment.document_id == request.document_id)
        if request.municipality:
            stmt = stmt.where(Document.municipality.ilike(f"%{request.municipality}%"))
        if request.bylaw_name:
            stmt = stmt.where(Document.bylaw_name.ilike(f"%{request.bylaw_name}%"))
        if request.citation_path_prefix:
            stmt = stmt.where(SourceFragment.citation_path.ilike(f"{request.citation_path_prefix}%"))
        if request.page is not None:
            stmt = stmt.where(SourceFragment.page_start <= request.page, SourceFragment.page_end >= request.page)
        if request.page_start is not None:
            stmt = stmt.where(SourceFragment.page_end >= request.page_start)
        if request.page_end is not None:
            stmt = stmt.where(SourceFragment.page_start <= request.page_end)
        if request.attribute_tag_filter:
            stmt = stmt.where(
                _attribute_tag_filter_clause(
                    request.attribute_tag_filter,
                    dialect_name=self._dialect_name(),
                )
            )
        return stmt

    def _build_match(
        self,
        fragment: SourceFragment,
        *,
        score: float,
        include_context: bool,
        include_cross_references: bool,
        include_tables: bool,
        include_datasets: bool = True,
        resolved_location: ResolvedLocation | None = None,
    ) -> RetrievalMatch:
        document = self._get_document(fragment.document_id)
        return RetrievalMatch(
            fragment_id=fragment.id,
            document_id=document.id,
            municipality=document.municipality,
            bylaw_name=document.bylaw_name,
            fragment_type=fragment.fragment_type.value,
            citation_label=fragment.citation_label,
            citation_path=fragment.citation_path,
            page_start=fragment.page_start,
            page_end=fragment.page_end,
            parse_status=fragment.parse_status.value,
            confidence=fragment.confidence,
            text=fragment.text,
            score=score,
            ancestor_chain=self._ancestor_chain(fragment) if include_context else [],
            cross_references=self._cross_references_for_fragment(fragment) if include_cross_references else [],
            related_tables=self._related_tables_for_fragment(fragment) if include_tables else [],
            linked_datasets=self._linked_datasets_for_fragment(fragment, resolved_location)
            if include_datasets
            else [],
            metadata_json=fragment.metadata_json or {},
        )

    def _linked_datasets_for_fragment(
        self,
        fragment: SourceFragment,
        resolved_location: ResolvedLocation | None,
    ) -> list[LinkedDataset]:
        datasets = (
            self.session.execute(
                select(ExternalDataset).where(ExternalDataset.linked_fragment_id == fragment.id)
            )
            .scalars()
            .all()
        )
        if not datasets:
            return []
        results: list[LinkedDataset] = []
        for dataset in datasets:
            summary = _summarize_dataset(self.session, dataset)
            image_id = (
                self.session.execute(
                    select(SourceImage.id).where(SourceImage.caption_fragment_id == fragment.id)
                )
                .scalars()
                .first()
            )
            feature_matches: list[DatasetFeatureMatch] = []
            if resolved_location is not None:
                for match in query_features(
                    self.session,
                    dataset_id=dataset.id,
                    location=resolved_location,
                    predicate=self._predicate_for_role(self._overlay_role(dataset)),
                ):
                    feature_matches.append(
                        DatasetFeatureMatch(
                            feature_id=match.feature.id,
                            feature_key=match.feature.feature_key,
                            canonical_attributes=dict(
                                match.feature.canonical_attributes_json or {}
                            ),
                            contains_input=match.contains_input,
                            overlap_metric=match.overlap_area,
                        )
                    )
            results.append(
                LinkedDataset(
                    dataset_id=dataset.id,
                    name=dataset.name,
                    publisher=dataset.publisher,
                    feature_count=dataset.feature_count,
                    crs=dataset.crs,
                    summary_text=summary,
                    source_image_id=image_id,
                    feature_matches=feature_matches,
                    location_resolver=(
                        resolved_location.source if resolved_location is not None else None
                    ),
                    location_confidence=(
                        resolved_location.confidence if resolved_location is not None else None
                    ),
                )
            )
        return results

    def _document_summary(self, document: Document) -> DocumentSummary:
        consolidation_date = str(document.consolidation_date) if document.consolidation_date else None
        return DocumentSummary(
            id=document.id,
            municipality=document.municipality,
            bylaw_name=document.bylaw_name,
            source_url=document.source_url,
            version_label=document.version_label,
            consolidation_date=consolidation_date,
            page_count=document.page_count,
            parser_version=document.parser_version,
            ingestion_timestamp=document.ingestion_timestamp,
        )

    def _ancestor_chain(self, fragment: SourceFragment) -> list[AncestorFragment]:
        chain: list[AncestorFragment] = []
        current = fragment.parent
        while current is not None:
            chain.append(
                AncestorFragment(
                    id=current.id,
                    fragment_type=current.fragment_type.value,
                    citation_label=current.citation_label,
                    citation_path=current.citation_path,
                    page_start=current.page_start,
                    page_end=current.page_end,
                    text=current.text,
                )
            )
            current = current.parent
        chain.reverse()
        return chain

    def _cross_references_for_fragment(self, fragment: SourceFragment) -> list[CrossReferenceSummary]:
        refs = self.session.execute(
            select(CrossReference)
            .where(CrossReference.source_fragment_id == fragment.id)
            .order_by(CrossReference.id)
        ).scalars().all()
        target_ids = [ref.target_fragment_id for ref in refs if ref.target_fragment_id]
        target_map = (
            {
                target.id: target
                for target in self.session.execute(
                    select(SourceFragment).where(SourceFragment.id.in_(target_ids))
                ).scalars().all()
            }
            if target_ids
            else {}
        )
        return [
            CrossReferenceSummary(
                id=ref.id,
                raw_reference_text=ref.raw_reference_text,
                target_citation_guess=ref.target_citation_guess,
                resolution_status=ref.resolution_status.value,
                confidence=ref.confidence,
                target_fragment_id=ref.target_fragment_id,
                target_citation_path=target_map[ref.target_fragment_id].citation_path
                if ref.target_fragment_id in target_map
                else None,
            )
            for ref in refs
        ]

    def _related_tables_for_fragment(self, fragment: SourceFragment) -> list[TableSummary]:
        tables = self.session.execute(
            select(SourceTable)
            .where(SourceTable.document_id == fragment.document_id)
            .where(
                (SourceTable.parent_fragment_id == fragment.id)
                | (
                    (SourceTable.page_start <= fragment.page_end)
                    & (SourceTable.page_end >= fragment.page_start)
                )
            )
            .order_by(SourceTable.page_start, SourceTable.id)
        ).scalars().all()
        if not tables:
            return []
        table_ids = [table.id for table in tables]
        cells_by_table: dict[int, list[SourceTableCell]] = defaultdict(list)
        for cell in self.session.execute(
            select(SourceTableCell)
            .where(SourceTableCell.table_id.in_(table_ids))
            .order_by(SourceTableCell.table_id, SourceTableCell.row_index, SourceTableCell.col_index)
        ).scalars().all():
            cells_by_table[cell.table_id].append(cell)
        summaries = []
        for table in tables:
            cells = cells_by_table.get(table.id, [])
            summaries.append(
                TableSummary(
                    id=table.id,
                    caption=table.caption,
                    page_start=table.page_start,
                    page_end=table.page_end,
                    parse_status=table.parse_status.value,
                    parent_fragment_id=table.parent_fragment_id,
                    cells=[
                        TableCellSummary(
                            row_index=cell.row_index,
                            col_index=cell.col_index,
                            text=cell.text,
                            row_header_path=cell.row_header_path,
                            col_header_path=cell.col_header_path,
                        )
                        for cell in cells[:20]
                    ],
                )
            )
        return summaries

    def _get_document(self, document_id: int) -> Document:
        document = self.session.get(Document, document_id)
        if not document:
            raise ValueError(f"Document {document_id} not found")
        return document

    def _score_fragment(self, fragment: SourceFragment, query: str) -> float:
        query_text = query.strip().lower()
        tokens = _tokenize(query_text)
        if not tokens:
            return 0.0
        haystacks = [fragment.text.lower()]
        if fragment.citation_label:
            haystacks.append(fragment.citation_label.lower())
        if fragment.citation_path:
            haystacks.append(fragment.citation_path.lower())

        score = 0.0
        joined = " ".join(haystacks)
        if query_text == (fragment.citation_path or "").lower():
            score += 100.0
        elif fragment.citation_path and query_text in fragment.citation_path.lower():
            score += 35.0
        elif query_text in joined:
            score += 20.0

        unique_tokens = set(tokens)
        for token in unique_tokens:
            if fragment.citation_path and token in fragment.citation_path.lower():
                score += 12.0
            elif fragment.citation_label and token in fragment.citation_label.lower():
                score += 8.0
            elif token in fragment.text.lower():
                score += 4.0

        if fragment.parse_status.value == "parsed":
            score += 1.0
        else:
            score -= 2.0
        return score


def _attribute_tag_filter_clause(attribute_tags: list[str], *, dialect_name: str):
    """Build the dialect-appropriate clause for the attribute_tag filter.

    Postgres path: ``attribute_tags ?| ARRAY[...]`` — the JSONB ``?|``
    operator, which uses the GIN index added by migration 0014. This
    is the indexed hot path the issue calls out.

    Sqlite path (test only): no JSONB operators exist, so we fall back
    to LIKE-matching the JSON text representation of the array. The
    column stores ``["front_setback_m", ...]`` as a literal JSON
    string on sqlite, so ``LIKE '%"front_setback_m"%'`` is a sound
    containment check as long as attribute ids never contain quote
    characters — which the taxonomy enforces.

    Empty input is rejected — empty would degrade silently into an
    always-false (or always-true with OR over no clauses) condition.
    """
    if not attribute_tags:
        raise ValueError("attribute_tag_filter must be non-empty")
    column = SourceFragment.attribute_tags
    if dialect_name == "postgresql":
        # ``?|`` expects ``text[]`` on the RHS. Without an explicit
        # ARRAY(String) bind type, psycopg serialises a Python list
        # as JSONB and Postgres rejects the operator with
        # "operator does not exist: jsonb ?| jsonb" (caught during
        # ABS-46's real-stack e2e run).
        # ``bindparam(None, ...)`` autogenerates a unique parameter
        # name so the helper can be called multiple times in the same
        # statement (it isn't today, but a future caller might) without
        # colliding on bind key.
        return cast(column, JSONB).op("?|")(
            bindparam(
                None,
                list(attribute_tags),
                type_=PG_ARRAY(String),
            )
        )
    # sqlite + anything else: LIKE-match each quoted attribute id.
    return or_(*(column.cast(Text).like(f'%"{tag}"%') for tag in attribute_tags))


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def _first_str(mapping: dict[str, object], *keys: str) -> str | None:
    """Return the first non-empty string value among ``keys`` in ``mapping``."""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
        if value is not None and not isinstance(value, str):
            return str(value)
    return None


# ---------------------------------------------------------------------------
# ABS-272 — zone-profile extraction heuristics
#
# These functions turn the top zone-matching fragment's text into the
# structured values on the ZoneProfile DTO. They are deliberately simple
# regex extractors over the Regional Centre LUB's table-row phrasing
# (e.g. "HR-2 Maximum Height 25.0 m Maximum Lot Coverage 65%"). If a
# future ingest changes how table cells are flattened into fragment
# text, update the patterns here — they are the single place the
# bylaw's surface form is assumed.
# ---------------------------------------------------------------------------


def _zone_pattern(zone: str):
    """Word-bounded, case-insensitive matcher for a zone code.

    Hand-rolled boundaries (``[A-Za-z0-9]`` lookarounds) instead of
    ``\\b`` so that ``HR-2`` does not match inside ``HR-21`` — the
    trailing digit would otherwise satisfy ``\\b`` after the ``2``.
    """
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(zone)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _extract_height_m(text: str) -> float | None:
    """Maximum building height in metres, e.g. 'Maximum Height 25.0 m'."""
    match = re.search(
        r"height\D{0,15}?(\d+(?:\.\d+)?)\s*(?:m\b|metre|meter)", text, re.IGNORECASE
    )
    return float(match.group(1)) if match else None


def _extract_coverage_pct(text: str) -> float | None:
    """Maximum lot coverage percentage, e.g. 'Maximum Lot Coverage 65%'."""
    match = re.search(
        r"lot coverage\D{0,15}?(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE
    )
    return float(match.group(1)) if match else None


def _extract_setback_m(text: str, kind: str) -> float | None:
    """Front/side/rear setback in metres, e.g. 'Front Setback 3.0 m'."""
    match = re.search(
        rf"{kind} setback\D{{0,12}}?(\d+(?:\.\d+)?)\s*m", text, re.IGNORECASE
    )
    return float(match.group(1)) if match else None


def _extract_far(text: str) -> float | None:
    """Numeric maximum floor area ratio, when stated inline.

    Returns None when FAR is delegated to an external schedule (the
    fixture's "governed by Schedule 17" case) — there is no number to
    extract, which is the correct ``None`` outcome.
    """
    match = re.search(
        r"(?:floor area ratio|\bFAR\b|maximum far)\D{0,15}?(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def _extract_uses(text: str, zone: str) -> tuple[list[str], list[str]]:
    """Split a use-permission row into (permitted, not_permitted).

    Operates on flattened table-row text like
    ``"Use Permissions HR-2 single-unit dwelling N secondary suite N
    multi-unit dwelling P home occupation N daycare P"`` where each use
    phrase is followed by a ``P`` (permitted) or ``N`` (not permitted)
    marker. Parsing starts immediately after the first occurrence of the
    zone code so a leading table caption ("Use Permissions") is dropped
    and never absorbed into the first use phrase.
    """
    anchor = _zone_pattern(zone).search(text)
    body = text[anchor.end():] if anchor else text
    permitted: list[str] = []
    not_permitted: list[str] = []
    for name, marker in re.findall(
        r"([A-Za-z][A-Za-z0-9 \-]*?)\s+(P|N)(?![A-Za-z0-9])", body
    ):
        cleaned = name.strip()
        if not cleaned:
            continue
        if marker == "P":
            permitted.append(cleaned)
        else:
            not_permitted.append(cleaned)
    return permitted, not_permitted


def _extract_parking_min_per_unit(text: str) -> float | None:
    """Minimum off-street spaces per dwelling unit, e.g. 'minimum of 1
    parking space per dwelling unit'.
    """
    match = re.search(
        r"minimum of (\d+(?:\.\d+)?)\s+parking space", text, re.IGNORECASE
    )
    return float(match.group(1)) if match else None


def _extract_parking_schedule_ref(text: str) -> str | None:
    """The Table/Schedule governing detailed (non-residential) ratios."""
    match = re.search(r"\b(Table\s+\d+[A-Za-z]?|Schedule\s+\d+[A-Za-z]?)\b", text)
    return match.group(1) if match else None


def _extract_parking_applicability(text: str, zone: str) -> tuple[bool | None, str | None]:
    """Decide whether off-street parking applies to ``zone``.

    Looks for the exemption clause ("no off-street parking is required
    ... in the CEN-1, CEN-2, DH, or DD zone") and returns ``applies =
    zone not in exempt_zones``. When no exemption clause is present but a
    parking fragment was found, the general requirement applies
    (``True``). ``notes`` carries the exemption snippet for context.
    """
    exemption = re.search(
        r"no off-street parking is required[^.]*", text, re.IGNORECASE
    )
    if not exemption:
        # A parking fragment exists but states no exemption — the
        # general requirement applies to every zone.
        return True, None
    snippet = exemption.group(0).strip()
    exempt_zones = {
        token.upper() for token in re.findall(r"\b[A-Z]{2,3}(?:-\d)?\b", snippet)
    }
    applies = zone.upper() not in exempt_zones
    return applies, snippet


def _extract_zone_full_name(text: str, zone: str) -> str | None:
    """Expanded zone name from 'HR-2 Higher Order Residential 2 Zone'."""
    match = re.search(
        rf"{re.escape(zone)}\s+(.+?)\s+Zone\b", text, re.IGNORECASE
    )
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def _extract_chapter(citation_path: str | None) -> str | None:
    """Top-level Part/chapter from a citation path like 'Part II > 30'."""
    if not citation_path:
        return None
    match = re.search(r"\b(Part\s+[IVXLCDM]+)\b", citation_path, re.IGNORECASE)
    return match.group(1) if match else None


class _CitationAccumulator:
    """Collects :class:`CitationRef`s for a profile, de-duplicating by
    ``citation_path`` and unioning the field lists.

    A single fragment (e.g. the Table 5 row) backs more than one field
    (height and coverage); accumulating by path keeps the citations list
    compact while still recording every field each source supports.
    """

    def __init__(self) -> None:
        self._by_path: dict[str, CitationRef] = {}
        self._order: list[str] = []

    def add(self, match: RetrievalMatch, fields: list[str]) -> None:
        path = match.citation_path
        if not path:
            # A field can only be cited if its fragment has a resolvable
            # citation_path (lookup_citation keys on it). Skip otherwise.
            return
        existing = self._by_path.get(path)
        if existing is None:
            self._by_path[path] = CitationRef(
                citation_path=path,
                citation_label=match.citation_label,
                backs=list(fields),
                page_start=match.page_start,
                page_end=match.page_end,
            )
            self._order.append(path)
        else:
            for field in fields:
                if field not in existing.backs:
                    existing.backs.append(field)

    def add_ref(self, ref: CitationRef, fields: list[str]) -> None:
        """Accumulate a pre-built :class:`CitationRef` (ABS-409).

        ``add`` keys strictly on ``citation_path`` and silently drops
        path-less citations — correct for fragment-backed fields, but
        table-backed citations (permission-matrix enumeration) may lack a
        path on corpora whose captions haven't been backfilled yet. Those
        must still surface (FR-2.4: every populated field traces to a
        citation), so path-less refs key on label+pages instead.
        """
        key = ref.citation_path or (
            f"__ref:{ref.citation_label}:{ref.page_start}:{ref.page_end}"
        )
        existing = self._by_path.get(key)
        if existing is None:
            self._by_path[key] = ref.model_copy(update={"backs": list(fields)})
            self._order.append(key)
        else:
            for field in fields:
                if field not in existing.backs:
                    existing.backs.append(field)

    def to_list(self) -> list[CitationRef]:
        return [self._by_path[path] for path in self._order]


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1].rstrip()}..."


def _slot_to_reference(slot: LocationSlot) -> LocationReference | None:
    """Promote the structured slot to the in-memory reference shape used by
    the layered geocoder. Returns None when the slot has nothing usable so
    callers can short-circuit without raising.
    """
    if slot.parcel_id:
        return LocationReference(
            raw_text=f"PID {slot.parcel_id}",
            kind="parcel_id",
            parcel_id=slot.parcel_id,
        )
    if slot.civic_number and slot.street:
        return LocationReference(
            raw_text=f"{slot.civic_number} {slot.street}".strip(),
            kind="civic_address",
            civic_number=slot.civic_number,
            street=slot.street,
            unit=slot.unit,
        )
    if slot.named_place:
        return LocationReference(
            raw_text=slot.named_place,
            kind="named_place",
            name=slot.named_place,
        )
    if len(slot.intersection_streets) >= 2:
        return LocationReference(
            raw_text=" and ".join(slot.intersection_streets),
            kind="intersection",
            streets=list(slot.intersection_streets),
        )
    return None


def _kind_from_geometry(geometry: dict) -> str:
    geom_type = geometry.get("type", "")
    if geom_type in {"Polygon", "MultiPolygon"}:
        return "parcel"
    if geom_type in {"Point", "MultiPoint"}:
        return "point"
    return "shape"
