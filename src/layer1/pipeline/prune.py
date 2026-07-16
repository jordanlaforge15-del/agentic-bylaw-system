from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from layer1.db.base import (
    CrossReference,
    Document,
    IngestionRun,
    PageBlock,
    SourceFragment,
    SourceImage,
    SourceTable,
)


@dataclass
class PruneEntry:
    id: int
    ingestion_timestamp: datetime
    file_hash: str
    page_count: int | None
    municipality: str
    bylaw_name: str
    fragment_count: int = 0
    block_count: int = 0
    table_count: int = 0
    cross_reference_count: int = 0
    image_count: int = 0
    run_count: int = 0


@dataclass
class PruneResult:
    dry_run: bool
    entries: list[PruneEntry] = field(default_factory=list)
    deleted_count: int = 0


def prune_superseded_documents(
    session: Session,
    *,
    municipality: str | None = None,
    bylaw_name: str | None = None,
    keep_latest: int = 1,
    dry_run: bool = True,
) -> PruneResult:
    """Find (and optionally delete) stale Document rows after re-ingest.

    Groups documents by (municipality, bylaw_name), keeps the newest
    ``keep_latest`` rows per group, and targets the rest for deletion.
    Cascade is handled by the ORM relationships (``cascade="all,
    delete-orphan"``) and by the database-level ``ondelete="CASCADE"``
    on SourceImage's FK.
    """
    query = session.query(Document)
    if municipality:
        query = query.filter(Document.municipality == municipality)
    if bylaw_name:
        query = query.filter(Document.bylaw_name == bylaw_name)

    # Fetch all candidate documents, newest-first within each group.
    docs = (
        query
        .order_by(
            Document.municipality,
            Document.bylaw_name,
            Document.ingestion_timestamp.desc(),
        )
        .all()
    )

    # Group by (municipality, bylaw_name), preserving DESC order.
    groups: dict[tuple[str, str], list[Document]] = {}
    for doc in docs:
        key = (doc.municipality, doc.bylaw_name)
        groups.setdefault(key, []).append(doc)

    # Collect superseded docs (beyond the keep_latest window).
    to_delete: list[Document] = []
    for group_docs in groups.values():
        to_delete.extend(group_docs[keep_latest:])

    entries: list[PruneEntry] = []
    for doc in to_delete:
        fragment_count = (
            session.query(func.count(SourceFragment.id))
            .filter(SourceFragment.document_id == doc.id)
            .scalar() or 0
        )
        block_count = (
            session.query(func.count(PageBlock.id))
            .filter(PageBlock.document_id == doc.id)
            .scalar() or 0
        )
        table_count = (
            session.query(func.count(SourceTable.id))
            .filter(SourceTable.document_id == doc.id)
            .scalar() or 0
        )
        xref_count = (
            session.query(func.count(CrossReference.id))
            .filter(CrossReference.document_id == doc.id)
            .scalar() or 0
        )
        image_count = (
            session.query(func.count(SourceImage.id))
            .filter(SourceImage.document_id == doc.id)
            .scalar() or 0
        )
        run_count = (
            session.query(func.count(IngestionRun.id))
            .filter(IngestionRun.document_id == doc.id)
            .scalar() or 0
        )
        entries.append(
            PruneEntry(
                id=doc.id,
                ingestion_timestamp=doc.ingestion_timestamp,
                file_hash=doc.file_hash,
                page_count=doc.page_count,
                municipality=doc.municipality,
                bylaw_name=doc.bylaw_name,
                fragment_count=fragment_count,
                block_count=block_count,
                table_count=table_count,
                cross_reference_count=xref_count,
                image_count=image_count,
                run_count=run_count,
            )
        )

    if dry_run:
        return PruneResult(dry_run=True, entries=entries, deleted_count=0)

    for doc in to_delete:
        session.delete(doc)

    return PruneResult(dry_run=False, entries=entries, deleted_count=len(to_delete))
