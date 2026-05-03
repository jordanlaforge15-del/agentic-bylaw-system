from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import Document, ExternalDataset, SourceFragment


@dataclass
class LinkResult:
    """Outcome of an attempt to link a dataset to its bylaw fragment."""

    dataset_id: int
    document_id: int | None
    fragment_id: int | None
    status: str  # "linked" | "no_document" | "no_fragment" | "ambiguous_fragment"
    detail: str


def link_dataset_to_bylaw(session: Session, dataset_id: int) -> LinkResult:
    """Resolve a dataset's declared bylaw link and persist the FK pointers.

    Looks up the document by the dataset's stored ``links_to.document_match``
    (municipality + bylaw_name) and the fragment by its citation_label. Both
    misses are *soft* failures — the dataset persists as an orphan and the
    audit surface (``find_orphan_datasets``) reports it for human review.
    Hard failures (multiple matching documents/fragments) are surfaced via
    the ``status`` field rather than raised, so a bulk relink doesn't abort.
    """
    dataset = session.get(ExternalDataset, dataset_id)
    if dataset is None:
        raise ValueError(f"ExternalDataset {dataset_id} not found")

    links_to = dataset.metadata_json.get("links_to") or {}
    document_match = links_to.get("document_match") or {}
    municipality = document_match.get("municipality")
    bylaw_name = document_match.get("bylaw_name")
    citation = dataset.linked_fragment_citation

    if not municipality or not bylaw_name or not citation:
        return _record_link(
            session,
            dataset,
            None,
            None,
            "no_document",
            "dataset is missing links_to.document_match or fragment_citation",
        )

    documents = (
        session.execute(
            select(Document).where(
                Document.municipality == municipality,
                Document.bylaw_name == bylaw_name,
            )
        )
        .scalars()
        .all()
    )
    if not documents:
        return _record_link(
            session,
            dataset,
            None,
            None,
            "no_document",
            f"no document matching municipality={municipality!r} bylaw_name={bylaw_name!r}",
        )
    if len(documents) > 1:
        # Multiple matching documents (different file_hash, same name) - prefer
        # the most recently ingested. This keeps the dataset live but flags it
        # for review via the link metadata.
        documents.sort(key=lambda d: d.ingestion_timestamp, reverse=True)
        ambiguous_detail = (
            f"{len(documents)} documents matched; linked to most recent "
            f"(id={documents[0].id})"
        )
    else:
        ambiguous_detail = ""

    document = documents[0]
    fragments = (
        session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document.id,
                SourceFragment.citation_label == citation,
            )
        )
        .scalars()
        .all()
    )
    if not fragments:
        return _record_link(
            session,
            dataset,
            document.id,
            None,
            "no_fragment",
            f"document {document.id} has no fragment with citation_label={citation!r}",
        )
    if len(fragments) > 1:
        return _record_link(
            session,
            dataset,
            document.id,
            None,
            "ambiguous_fragment",
            f"document {document.id} has {len(fragments)} fragments matching {citation!r}",
        )

    detail = f"linked to fragment {fragments[0].id}"
    if ambiguous_detail:
        detail = f"{detail} ({ambiguous_detail})"
    return _record_link(session, dataset, document.id, fragments[0].id, "linked", detail)


def find_orphan_datasets(session: Session) -> list[ExternalDataset]:
    """Return every persisted dataset that is not yet linked to a fragment.

    Excludes role-bearing datasets (e.g. ``civic_address``) that
    intentionally don't bind to a bylaw fragment — those aren't orphans,
    they just play a different role in the system.
    """
    rows = list(
        session.execute(
            select(ExternalDataset).where(ExternalDataset.linked_fragment_id.is_(None))
        )
        .scalars()
        .all()
    )
    return [row for row in rows if (row.metadata_json or {}).get("role") is None]


def relink_orphan_datasets(session: Session) -> list[LinkResult]:
    """Try linking every dataset that the orphan audit considers an orphan.

    Role-bearing datasets are skipped — they have no link to relink.
    """
    return [link_dataset_to_bylaw(session, dataset.id) for dataset in find_orphan_datasets(session)]


def _record_link(
    session: Session,
    dataset: ExternalDataset,
    document_id: int | None,
    fragment_id: int | None,
    status: str,
    detail: str,
) -> LinkResult:
    dataset.linked_document_id = document_id
    dataset.linked_fragment_id = fragment_id
    history = list(dataset.metadata_json.get("link_history") or [])
    history.append(
        {
            "status": status,
            "detail": detail,
            "document_id": document_id,
            "fragment_id": fragment_id,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    dataset.metadata_json["link_history"] = history
    dataset.metadata_json["link_status"] = status
    session.flush()
    return LinkResult(
        dataset_id=dataset.id,
        document_id=document_id,
        fragment_id=fragment_id,
        status=status,
        detail=detail,
    )
