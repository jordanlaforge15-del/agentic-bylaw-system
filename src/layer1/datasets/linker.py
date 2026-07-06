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


def _as_aware(dt: datetime) -> datetime:
    """Coerce a timestamp to UTC-aware for safe cross-document comparison.

    Documents are stored in a ``DateTime(timezone=True)`` column, so Postgres
    returns aware values and a freshly-created ``datetime.now(timezone.utc)``
    is aware too. SQLite, however, drops the tzinfo on round-trip — so relinking
    within the same session (a DB-read sibling vs. an in-memory aware document,
    the ABS-355 re-ingest path) can mix naive and aware datetimes and raise on
    ``<``. Treat a naive value as UTC so the sort never blows up regardless of
    dialect.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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
        # the most recently ingested one that actually CONTAINS the cited
        # fragment. Picking blindly by recency and then reporting no_fragment
        # spuriously orphans the dataset whenever the newest same-named
        # document lacks the schedule but an older sibling carries it (e.g. two
        # synthetic bylaws colliding on name in the shared e2e DB). Fall back to
        # most-recent overall when none contain it, preserving no_fragment.
        documents.sort(key=lambda d: _as_aware(d.ingestion_timestamp), reverse=True)
        with_fragment = [
            d for d in documents if _fragments_matching_citation(session, d.id, citation)
        ]
        document = with_fragment[0] if with_fragment else documents[0]
        ambiguous_detail = (
            f"{len(documents)} documents matched; linked to most recent "
            f"(id={document.id})"
        )
    else:
        document = documents[0]
        ambiguous_detail = ""

    return _bind_dataset_to_document(
        session, dataset, document, citation, ambiguous_detail=ambiguous_detail
    )


def _fragments_matching_citation(
    session: Session, document_id: int, citation: str
) -> list[SourceFragment]:
    return list(
        session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_label == citation,
            )
        )
        .scalars()
        .all()
    )


def _bind_dataset_to_document(
    session: Session,
    dataset: ExternalDataset,
    document: Document,
    citation: str,
    *,
    ambiguous_detail: str = "",
) -> LinkResult:
    """Resolve ``citation`` within a *specific* document and persist the link.

    Unlike ``link_dataset_to_bylaw``'s multi-document preference logic, this
    binds strictly to ``document``: if that document lacks the cited fragment
    the dataset is recorded as a flagged ``no_fragment`` orphan rather than
    falling back to an older same-named sibling. That distinction is what the
    ABS-355 re-link pass needs — a superseded layer must follow the amendment
    onto the new version or be surfaced as an orphan, never left pinned to a
    now-out-of-retrieval-scope prior version.
    """
    fragments = _fragments_matching_citation(session, document.id, citation)
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


def relink_superseded_datasets(session: Session, document: Document) -> list[LinkResult]:
    """Re-point geo datasets pinned to an older version of ``document``'s bylaw.

    Geo layers pin to a specific document version at ingest time
    (``ExternalDataset.linked_fragment_id`` → ``SourceFragment`` → ``Document``).
    Retrieval scoping only surfaces layers whose pinned document is the newest
    per ``(municipality, bylaw_name)`` (``latest_per_bylaw_resolver`` +
    ``_scoped_linked_datasets``). So re-ingesting an amended bylaw PDF under the
    same name silently evicts every existing layer — zone, height, FAR,
    heritage, POCS — and ``get_address_profile`` starts returning ``zone=null``.

    This runs at the tail of document ingestion: when ``document`` has older
    same-named siblings, every dataset still pinned to one of those older
    siblings is re-resolved **strictly against ``document``** (the freshly
    re-ingested version). On a hit the FK pointers move onto the new version;
    on a miss (schedule dropped or renamed in the new version) the dataset is
    recorded as an orphan (``linked_fragment_id`` cleared) with a detail naming
    the missing citation, surfaced by ``find_orphan_datasets`` — never silently
    stranded on a now-out-of-scope prior version.

    Note it deliberately does *not* route through ``link_dataset_to_bylaw``:
    that function's ABS-349 multi-document preference would happily re-bind a
    dropped-schedule layer to the older sibling that still carries the citation,
    leaving it pinned to a document that ``latest_per_bylaw_resolver`` excludes
    — the exact silent strand this pass exists to prevent.

    Only fires when ``document`` is the newest version of its bylaw (matching
    the resolver's ``(ingestion_timestamp desc, id desc)`` ordering): a
    backfill re-ingest of an *older* version must not evict datasets that are
    correctly pinned to the current latest. Idempotent — a second pass finds
    every dataset already pinned to ``document`` (no longer on an older
    sibling), so there is nothing left to move — and a no-op when ``document``
    has no siblings.
    """
    siblings = (
        session.execute(
            select(Document).where(
                Document.municipality == document.municipality,
                Document.bylaw_name == document.bylaw_name,
            )
        )
        .scalars()
        .all()
    )
    if len(siblings) <= 1:
        return []

    # ``document`` only supersedes the others when it is the resolver-latest.
    latest = max(siblings, key=lambda d: (_as_aware(d.ingestion_timestamp), d.id))
    if latest.id != document.id:
        return []

    older_sibling_ids = [d.id for d in siblings if d.id != document.id]
    datasets = (
        session.execute(
            select(ExternalDataset).where(
                ExternalDataset.linked_document_id.in_(older_sibling_ids)
            )
        )
        .scalars()
        .all()
    )
    results: list[LinkResult] = []
    for dataset in datasets:
        citation = dataset.linked_fragment_citation
        if not citation:
            # No declared citation to re-resolve — leave the existing link as-is
            # rather than blindly orphaning a role/manually-pinned dataset.
            continue
        results.append(_bind_dataset_to_document(session, dataset, document, citation))
    return results


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
