from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable

from sqlalchemy import Select, desc, select
from sqlalchemy.orm import Session

from bylaw_retrieval.retrieval.schemas import (
    AncestorFragment,
    CitationLookupRequest,
    CrossReferenceSummary,
    DatasetFeatureMatch,
    DocumentOutlineItem,
    DocumentOutlineResponse,
    DocumentSummary,
    LinkedDataset,
    LocationSlot,
    RetrievalMatch,
    RetrievalRequest,
    RetrievalResponse,
    TableCellSummary,
    TableSummary,
)
from layer1.db.base import (
    CrossReference,
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    SourceFragment,
    SourceImage,
    SourceTable,
    SourceTableCell,
)
from layer2.retrieval.datasets import _summarize_dataset
from layer2.retrieval.geocode import resolve_location
from layer2.retrieval.location import LocationReference
from layer2.retrieval.spatial import ResolvedLocation, query_features

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


# Resolver signature: takes a session, returns a document_id (or None if no
# document exists yet). Called per-request so a fresh ingest mid-session is
# picked up without a server restart.
DocumentIdResolver = Callable[[Session], int | None]


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

    def _resolve_default_document_id(self) -> int | None:
        if self._default_document_id_resolver is None:
            return None
        return self._default_document_id_resolver(self.session)

    def list_documents(
        self,
        municipality: str | None = None,
        bylaw_name: str | None = None,
        limit: int = 50,
    ) -> list[DocumentSummary]:
        stmt = select(Document).order_by(Document.municipality, Document.bylaw_name, Document.id)
        # Hard scope: when a default document is configured (--latest-only),
        # it ALWAYS pins the result set. Other filters AND with it. A query
        # that asks for a different bylaw/municipality returns empty rather
        # than crossing into a stale or superseded ingest — better empty
        # than wrong.
        default_id = self._resolve_default_document_id()
        if default_id is not None:
            stmt = stmt.where(Document.id == default_id)
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

    def lookup_citation(self, request: CitationLookupRequest) -> RetrievalMatch:
        stmt = select(SourceFragment).where(SourceFragment.citation_path == request.citation_path)
        # Hard scope: default document id ANDs with the request's
        # document_id. See _fragment_scope_statement for rationale.
        default_id = self._resolve_default_document_id()
        if default_id is not None:
            stmt = stmt.where(SourceFragment.document_id == default_id)
        if request.document_id is not None:
            stmt = stmt.where(SourceFragment.document_id == request.document_id)
        fragments = self.session.execute(stmt.order_by(SourceFragment.id).limit(2)).scalars().all()
        if not fragments:
            scope = f" in document {request.document_id}" if request.document_id is not None else ""
            raise ValueError(f"Citation '{request.citation_path}' not found{scope}")
        if request.document_id is None and len(fragments) > 1:
            document_ids = ", ".join(str(fragment.document_id) for fragment in fragments)
            raise ValueError(
                f"Citation '{request.citation_path}' is ambiguous across documents; "
                f"provide document_id. Matching document IDs include: {document_ids}"
            )
        fragment = fragments[0]
        return self._build_match(
            fragment,
            score=1000.0,
            include_context=request.include_context,
            include_cross_references=request.include_cross_references,
            include_tables=request.include_tables,
        )

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
        resolved_location = self._resolve_location_slot(request.location)

        text_scored = self._text_channel_scores(request)
        spatial_scored = (
            self._spatial_channel_scores(request, resolved_location)
            if resolved_location is not None
            else {}
        )

        merged = self._merge_channel_scores(text_scored, spatial_scored)
        total_matches = len(merged)

        # Resolve candidate fragments from the union of both channels.
        candidate_fragment_ids = [fid for _, fid, _ in merged[: request.limit]]
        if not candidate_fragment_ids:
            return RetrievalResponse(request=request, total_matches=0, matches=[])

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

        return RetrievalResponse(request=request, total_matches=total_matches, matches=matches)

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
        dataset_stmt = (
            select(ExternalDataset)
            .join(SourceFragment, SourceFragment.id == ExternalDataset.linked_fragment_id)
            .join(Document, Document.id == SourceFragment.document_id)
            .where(ExternalDataset.linked_fragment_id.is_not(None))
        )
        default_id = self._resolve_default_document_id()
        if default_id is not None:
            dataset_stmt = dataset_stmt.where(SourceFragment.document_id == default_id)
        if request.document_id is not None:
            dataset_stmt = dataset_stmt.where(SourceFragment.document_id == request.document_id)
        if request.municipality:
            dataset_stmt = dataset_stmt.where(
                Document.municipality.ilike(f"%{request.municipality}%")
            )
        if request.bylaw_name:
            dataset_stmt = dataset_stmt.where(
                Document.bylaw_name.ilike(f"%{request.bylaw_name}%")
            )
        datasets = self.session.execute(dataset_stmt).scalars().all()

        scored: dict[int, float] = {}
        for dataset in datasets:
            assert dataset.linked_fragment_id is not None  # narrowed by query above
            for match in query_features(
                self.session, dataset_id=dataset.id, location=location
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

    def _resolve_location_slot(self, slot: LocationSlot | None) -> ResolvedLocation | None:
        """Translate a structured slot to a ResolvedLocation.

        - ``geometry`` short-circuits geocoding (caller already has a point/parcel).
        - Otherwise build a LocationReference from the slot fields and run it
          through the layered ``resolve_location`` (in-database civic-address
          dataset, then Google fallback if configured).
        - The MCP path NEVER invokes the regex extractor — that's reserved for
          callers who don't have an LLM in front of them.
        """
        if slot is None:
            return None
        if slot.geometry is not None:
            return ResolvedLocation(
                kind=_kind_from_geometry(slot.geometry),
                geometry=slot.geometry,
                confidence=1.0,
                source="caller_supplied",
                reference_text=None,
            )
        ref = _slot_to_reference(slot)
        if ref is None:
            return None
        return resolve_location(self.session, ref)

    def _fragment_scope_statement(self, request: RetrievalRequest) -> Select[tuple[SourceFragment]]:
        stmt = (
            select(SourceFragment)
            .join(Document, Document.id == SourceFragment.document_id)
            .order_by(SourceFragment.page_start, SourceFragment.reading_order_start, SourceFragment.id)
        )
        # Hard scope: when the deployment has configured a default document
        # (--latest-only), that document_id is ALWAYS pinned. Any request
        # filter (document_id, municipality, bylaw_name) ANDs with it. A
        # request asking for a different document or bylaw therefore
        # returns empty rather than leaking into a stale or superseded
        # ingest — better empty than wrong.
        default_id = self._resolve_default_document_id()
        if default_id is not None:
            stmt = stmt.where(SourceFragment.document_id == default_id)
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
                    self.session, dataset_id=dataset.id, location=resolved_location
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


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


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
