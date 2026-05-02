from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from bylaw_retrieval.retrieval.schemas import (
    AncestorFragment,
    CitationLookupRequest,
    CrossReferenceSummary,
    DocumentOutlineItem,
    DocumentOutlineResponse,
    DocumentSummary,
    RetrievalMatch,
    RetrievalRequest,
    RetrievalResponse,
    TableCellSummary,
    TableSummary,
)
from layer1.db.base import CrossReference, Document, SourceFragment, SourceTable, SourceTableCell

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class RetrievalService:
    def __init__(self, session: Session):
        self.session = session

    def list_documents(
        self,
        municipality: str | None = None,
        bylaw_name: str | None = None,
        limit: int = 50,
    ) -> list[DocumentSummary]:
        stmt = select(Document).order_by(Document.municipality, Document.bylaw_name, Document.id)
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
        if request.document_id is not None:
            stmt = stmt.where(SourceFragment.document_id == request.document_id)
        fragment = self.session.execute(stmt.order_by(SourceFragment.id)).scalars().first()
        if not fragment:
            scope = f" in document {request.document_id}" if request.document_id is not None else ""
            raise ValueError(f"Citation '{request.citation_path}' not found{scope}")
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
        matches = [
            self._build_match(
                fragment,
                score=score,
                include_context=request.include_context,
                include_cross_references=request.include_cross_references,
                include_tables=request.include_tables,
            )
            for score, fragment in scored[: request.limit]
        ]
        return RetrievalResponse(request=request, total_matches=len(scored), matches=matches)

    def _fragment_scope_statement(self, request: RetrievalRequest) -> Select[tuple[SourceFragment]]:
        stmt = (
            select(SourceFragment)
            .join(Document, Document.id == SourceFragment.document_id)
            .order_by(SourceFragment.page_start, SourceFragment.reading_order_start, SourceFragment.id)
        )
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
        return stmt.limit(500)

    def _build_match(
        self,
        fragment: SourceFragment,
        *,
        score: float,
        include_context: bool,
        include_cross_references: bool,
        include_tables: bool,
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
            metadata_json=fragment.metadata_json or {},
        )

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

