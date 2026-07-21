"""Operator publish/unpublish of documents to the retrieval API (ABS-413).

The retrieval corpus is exactly the set of documents with
``Document.retrieval_enabled`` set — nothing is derived from ingestion
recency, and a fresh ingest defaults to disabled. This module is the one
implementation behind every toggle surface (the layer1 CLI's
``enable-retrieval``/``disable-retrieval``/``list-documents`` and the e2e
test endpoint), mirroring the ``prune.py`` pattern of pure session functions
returning dataclass results.

Enabling a document also runs the geo-dataset relink pass
(``relink_superseded_datasets``) so layers pinned to a sibling version follow
the publish — the ABS-355 amendment concern, moved from ingest-time to
enable-time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.datasets.linker import LinkResult, relink_superseded_datasets
from layer1.db.base import Document


@dataclass
class DocumentStatusEntry:
    id: int
    municipality: str
    bylaw_name: str
    version_label: str | None
    ingestion_timestamp: datetime
    retrieval_enabled: bool


@dataclass
class PublishChange:
    document_id: int
    municipality: str
    bylaw_name: str
    enabled: bool
    # "requested" — an id the caller named; "replaced-sibling" — an enabled
    # same-bylaw sibling disabled by replace=True.
    reason: str


@dataclass
class PublishResult:
    changes: list[PublishChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    relink_results: list[LinkResult] = field(default_factory=list)


def list_retrieval_status(
    session: Session,
    *,
    municipality: str | None = None,
    bylaw_name: str | None = None,
    enabled_only: bool = False,
) -> list[DocumentStatusEntry]:
    stmt = select(Document).order_by(
        Document.municipality, Document.bylaw_name, Document.ingestion_timestamp, Document.id
    )
    if municipality:
        stmt = stmt.where(Document.municipality.ilike(f"%{municipality}%"))
    if bylaw_name:
        stmt = stmt.where(Document.bylaw_name.ilike(f"%{bylaw_name}%"))
    if enabled_only:
        stmt = stmt.where(Document.retrieval_enabled.is_(True))
    return [
        DocumentStatusEntry(
            id=doc.id,
            municipality=doc.municipality,
            bylaw_name=doc.bylaw_name,
            version_label=doc.version_label,
            ingestion_timestamp=doc.ingestion_timestamp,
            retrieval_enabled=doc.retrieval_enabled,
        )
        for doc in session.execute(stmt).scalars().all()
    ]


def set_retrieval_enabled(
    session: Session,
    document_ids: Sequence[int],
    enabled: bool,
    *,
    replace: bool = False,
    dry_run: bool = False,
) -> PublishResult:
    """Enable or disable documents for the retrieval API.

    All-or-nothing on identity: any unknown id raises ``ValueError`` before
    anything is changed. Enabling runs the geo-dataset relink pass for each
    newly enabled document; with ``replace=True``, enabled same-bylaw
    siblings are disabled in the same transaction (the amendment-publish
    workflow). Without ``replace``, an enabled sibling produces a warning —
    two enabled versions of one bylaw is representable but almost always an
    operator mistake.

    ``dry_run=True`` reports the would-be changes and warnings without
    mutating anything (and without relinking), so callers can confirm with
    the operator before committing — the ``prune_superseded_documents``
    pattern.
    """
    ids = list(dict.fromkeys(document_ids))
    docs = (
        session.execute(select(Document).where(Document.id.in_(ids)))
        .scalars()
        .all()
    )
    found = {doc.id: doc for doc in docs}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValueError(f"Unknown document id(s): {', '.join(str(i) for i in missing)}")

    result = PublishResult()

    if not enabled:
        for doc_id in ids:
            doc = found[doc_id]
            if not doc.retrieval_enabled:
                result.warnings.append(
                    f"Document {doc.id} ({doc.bylaw_name}) is already disabled."
                )
                continue
            if not dry_run:
                doc.retrieval_enabled = False
            result.changes.append(
                PublishChange(
                    document_id=doc.id,
                    municipality=doc.municipality,
                    bylaw_name=doc.bylaw_name,
                    enabled=False,
                    reason="requested",
                )
            )
        if not dry_run:
            session.flush()
        # On a dry run the flags are untouched, so exclude the would-be
        # disabled ids from the "anything left?" probe explicitly.
        remaining_stmt = select(Document.id).where(Document.retrieval_enabled.is_(True))
        disabled_ids = {change.document_id for change in result.changes}
        if disabled_ids:
            remaining_stmt = remaining_stmt.where(Document.id.notin_(disabled_ids))
        remaining = session.execute(remaining_stmt.limit(1)).first()
        if remaining is None:
            result.warnings.append(
                "No documents remain enabled — the retrieval API will return "
                "empty results until a document is enabled."
            )
        return result

    for doc_id in ids:
        doc = found[doc_id]
        siblings = (
            session.execute(
                select(Document).where(
                    Document.municipality == doc.municipality,
                    Document.bylaw_name == doc.bylaw_name,
                    Document.retrieval_enabled.is_(True),
                    Document.id != doc.id,
                    Document.id.notin_(ids),
                )
            )
            .scalars()
            .all()
        )
        if siblings:
            if replace:
                for sibling in siblings:
                    if not dry_run:
                        sibling.retrieval_enabled = False
                    result.changes.append(
                        PublishChange(
                            document_id=sibling.id,
                            municipality=sibling.municipality,
                            bylaw_name=sibling.bylaw_name,
                            enabled=False,
                            reason="replaced-sibling",
                        )
                    )
            else:
                sibling_ids = ", ".join(str(s.id) for s in siblings)
                result.warnings.append(
                    f"Document {doc.id} ({doc.bylaw_name}) has other enabled "
                    f"version(s) of the same bylaw: {sibling_ids}. Retrieval "
                    "will search all of them; use replace to disable the "
                    "older version(s)."
                )
        if doc.retrieval_enabled:
            result.warnings.append(
                f"Document {doc.id} ({doc.bylaw_name}) is already enabled."
            )
        else:
            if not dry_run:
                doc.retrieval_enabled = True
            result.changes.append(
                PublishChange(
                    document_id=doc.id,
                    municipality=doc.municipality,
                    bylaw_name=doc.bylaw_name,
                    enabled=True,
                    reason="requested",
                )
            )
        if dry_run:
            continue
        session.flush()
        # Publish-driven amendment support: pull geo layers pinned to sibling
        # versions onto the newly enabled document (no-op without siblings).
        result.relink_results.extend(relink_superseded_datasets(session, doc))

    if not dry_run:
        session.flush()
    return result
