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
        # Apply default scope only when no explicit filter was given.
        if not municipality and not bylaw_name:
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
        # Honour explicit document_id; otherwise apply the default scope so
        # ambiguous citation paths across multiple ingests resolve to the
        # active document instead of erroring on ambiguity.
        effective_document_id = request.document_id
        if effective_document_id is None:
            effective_document_id = self._resolve_default_document_id()
        if effective_document_id is not None:
            stmt = stmt.where(SourceFragment.document_id == effective_document_id)
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

    def search(self, request: RetrievalRequest) -> RetrievalResponse:
        stmt = self._fragment_scope_statement(request)
        fragments = self.session.execute(stmt).scalars().all()
        scored = []
        for fragment in fragments:
            score = self._score_fragment(fragment, request.query)
            if score <= 0:
                continue
            scored.append((score, fragment))
        scored.sort(key=lambda item: (-item[0], item[1].page_start, item[1].id))
        resolved_location = self._resolve_location_slot(request.location)
        matches = [
            self._build_match(
                fragment,
                score=score,
                include_context=request.include_context,
                include_cross_references=request.include_cross_references,
                include_tables=request.include_tables,
                include_datasets=request.include_datasets,
                resolved_location=resolved_location,
            )
            for score, fragment in scored[: request.limit]
        ]
        return RetrievalResponse(request=request, total_matches=len(scored), matches=matches)

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
        # Default scope only applies when the caller gave no scoping filter
        # at all. Any of document_id / municipality / bylaw_name disables it,
        # so comparative queries across bylaws still work (e.g. a request
        # filtering by municipality reaches every doc for that municipality).
        effective_document_id = request.document_id
        if (
            effective_document_id is None
            and not request.municipality
            and not request.bylaw_name
        ):
            effective_document_id = self._resolve_default_document_id()
        if effective_document_id is not None:
            stmt = stmt.where(SourceFragment.document_id == effective_document_id)
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
