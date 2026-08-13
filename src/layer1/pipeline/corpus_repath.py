"""Apply the ABS-488 citation-path repath to a corpus already in a database.

The parser fix in :mod:`layer1.pipeline.citation_repath` only helps documents
ingested after it. A corpus already loaded — dev's and production's document 4,
where 720 labelled clauses (16.6% of the document) currently have no citation
path at all — is corrected by **migration**, never by re-ingest. The reasoning
is ABS-461's and has not changed (``docs/data-gaps/abs461-production-impact.md``):
re-ingest cannot run where the data is, it reallocates every ``source_fragment``
id so every foreign key -- and every citation the Layer 2 answer tables have
recorded -- stops pointing at the row it described, and it would reshape far
more than the defect.

This module replays the *same* pure walk the builder runs
(:func:`~layer1.pipeline.citation_repath.repath_low_level_fragments`) over the
stored rows, in reading order, and writes back only what moved. Two details
make that replay faithful:

* a collided row's path was blanked at ingest and recorded in
  ``metadata_json.duplicate_citation_path``; the walk is fed that recorded path,
  so it sees the document the builder saw rather than a corpus full of holes;
* Part rows are re-labelled from their own text before the walk, because a Part
  heading's chapter is the discriminator for the heading itself.

Writes are two-phase. ``uq_fragment_citation_path`` is a plain (non-deferrable)
unique constraint, so setting ``Part I > 9 > (a)`` on one row while another
still holds it would abort the statement even though the end state is legal.
Every moving row is blanked and flushed first, then given its new path.

Embeddings are **not** invalidated: ``layer2.pipeline.service`` embeds
``fragment.text`` alone, and no text changes here.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import SourceFragment
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.citation_repath import (
    LOW_LEVEL_FRAGMENT_TYPES,
    part_label_with_chapter,
    repath_low_level_fragments,
)
from layer1.pipeline.citations import APPENDIX_RE, PART_RE, SCHEDULE_RE

DUPLICATE_KEY = "duplicate_citation_path"
REPATH_MARKER = "abs488_repath"

#: Confidence ceiling for a labelled provision no citation path can reach.
#: Mirrors ``hierarchy.UNADDRESSABLE_CONFIDENCE``.
UNADDRESSABLE_CONFIDENCE = 0.6

PART_PATTERNS = {
    FragmentType.PART: PART_RE,
    FragmentType.SCHEDULE: SCHEDULE_RE,
    FragmentType.APPENDIX: APPENDIX_RE,
}


@dataclass
class FragmentChange:
    fragment_id: int
    old_label: str | None
    new_label: str | None
    old_path: str | None
    new_path: str | None
    old_status: ParseStatus
    new_status: ParseStatus
    #: The path this fragment computed but had to give up to a collision, or
    #: ``None`` when it kept the path it computed. Written to
    #: ``metadata_json.duplicate_citation_path``, which is what
    #: ``scripts/audit_citation_path_coverage.py`` counts.
    duplicate_path: str | None = None


@dataclass
class DocumentRepath:
    document_id: int
    fragments: int = 0
    changes: list[FragmentChange] = field(default_factory=list)
    citable_before: int = 0
    citable_after: int = 0
    collided_paths: Counter = field(default_factory=Counter)

    @property
    def recovered(self) -> int:
        return self.citable_before - self.citable_after


@dataclass
class RepathStats:
    documents: list[DocumentRepath] = field(default_factory=list)
    revert_payload: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rows_changed(self) -> int:
        return sum(len(document.changes) for document in self.documents)

    @property
    def recovered(self) -> int:
        return sum(document.recovered for document in self.documents)

    def summary_line(self) -> str:
        return (
            f"citation-path repath: {len(self.documents)} document(s), "
            f"{self.rows_changed} row(s) rewritten, "
            f"{self.recovered} citable provision(s) recovered"
        )


class _Node:
    """A mutable view of a stored fragment, shaped for the pure walk.

    ``citation_path`` deliberately falls back to the recorded collision: the
    walk has to see the path the builder computed, not the ``NULL`` the
    collision rule left behind.
    """

    __slots__ = ("fragment", "fragment_type", "citation_label", "citation_path", "text")

    def __init__(self, fragment: SourceFragment) -> None:
        self.fragment = fragment
        self.fragment_type = fragment.fragment_type
        self.citation_label = fragment.citation_label
        self.citation_path = fragment.citation_path or (fragment.metadata_json or {}).get(DUPLICATE_KEY)
        self.text = fragment.text or ""


def _relabel_part(node: _Node) -> None:
    """Fold a Part/Schedule/Appendix heading's chapter into its own label."""
    pattern = PART_PATTERNS.get(node.fragment_type)
    if pattern is None or not node.citation_label:
        return
    match = pattern.match(" ".join(node.text.split()))
    if not match:
        return
    new_label = part_label_with_chapter(node.citation_label, match.group(2))
    if new_label == node.citation_label:
        return
    if node.citation_path and node.citation_path.endswith(node.citation_label):
        node.citation_path = node.citation_path[: -len(node.citation_label)] + new_label
    node.citation_label = new_label


def _load(session: Session, document_id: int) -> list[SourceFragment]:
    statement = (
        select(SourceFragment)
        .where(SourceFragment.document_id == document_id)
        .order_by(SourceFragment.reading_order_start, SourceFragment.id)
    )
    return list(session.execute(statement).scalars())


def _document_ids(session: Session, document_id: int | None) -> list[int]:
    if document_id is not None:
        return [document_id]
    statement = select(SourceFragment.document_id).distinct().order_by(SourceFragment.document_id)
    return [row[0] for row in session.execute(statement)]


def plan_document_repath(fragments: Sequence[SourceFragment], document_id: int) -> DocumentRepath:
    """Compute the new shape of one document without touching the session."""
    report = DocumentRepath(document_id=document_id, fragments=len(fragments))
    nodes = [_Node(fragment) for fragment in fragments]
    for node in nodes:
        _relabel_part(node)

    paths = repath_low_level_fragments(nodes)
    for index, node in enumerate(nodes):
        if node.fragment_type not in LOW_LEVEL_FRAGMENT_TYPES:
            paths[index] = node.citation_path

    counts = Counter(path for path in paths if path)
    duplicates = {path for path, count in counts.items() if count > 1}
    report.collided_paths = Counter({path: counts[path] for path in duplicates})

    for fragment, node, path in zip(fragments, nodes, paths, strict=True):
        if fragment.citation_label and not fragment.citation_path:
            report.citable_before += 1
        collided = path is not None and path in duplicates
        final_path = None if collided else path
        if node.citation_label and not final_path:
            report.citable_after += 1

        status = fragment.parse_status
        if node.fragment_type in LOW_LEVEL_FRAGMENT_TYPES:
            # Addressability, not the collision, is what makes a clause
            # uncertain — ABS-480 settled that a collision keeps PARSED.
            status = ParseStatus.PARSED if path else ParseStatus.UNCERTAIN
        duplicate_path = path if collided else None
        if (
            node.citation_label == fragment.citation_label
            and final_path == fragment.citation_path
            and status == fragment.parse_status
            and duplicate_path == (fragment.metadata_json or {}).get(DUPLICATE_KEY)
        ):
            continue
        report.changes.append(
            FragmentChange(
                fragment_id=fragment.id,
                old_label=fragment.citation_label,
                new_label=node.citation_label,
                old_path=fragment.citation_path,
                new_path=final_path,
                old_status=fragment.parse_status,
                new_status=status,
                duplicate_path=duplicate_path,
            )
        )
    return report


def repath_corpus(
    session: Session, *, document_id: int | None = None, dry_run: bool = False
) -> RepathStats:
    """Rewrite every clause, subclause and Part-chapter path that moved."""
    stats = RepathStats()
    for target in _document_ids(session, document_id):
        fragments = _load(session, target)
        if not fragments:
            continue
        report = plan_document_repath(fragments, target)
        stats.documents.append(report)
        if dry_run or not report.changes:
            continue
        stats.revert_payload.extend(_apply(session, fragments, report))
    return stats


def _apply(
    session: Session, fragments: Sequence[SourceFragment], report: DocumentRepath
) -> list[dict[str, Any]]:
    by_id = {fragment.id: fragment for fragment in fragments}
    payload: list[dict[str, Any]] = []

    for change in report.changes:
        fragment = by_id[change.fragment_id]
        payload.append(
            {
                "fragment_id": fragment.id,
                "citation_label": fragment.citation_label,
                "citation_path": fragment.citation_path,
                "parse_status": fragment.parse_status.value,
                "confidence": fragment.confidence,
                "metadata_json": dict(fragment.metadata_json or {}),
            }
        )
        # Phase one: vacate every path that is moving, so the unique constraint
        # never sees two rows claiming one address mid-flight.
        fragment.citation_path = None
    session.flush()

    for change in report.changes:
        fragment = by_id[change.fragment_id]
        fragment.citation_label = change.new_label
        fragment.citation_path = change.new_path
        fragment.parse_status = change.new_status
        if fragment.fragment_type in LOW_LEVEL_FRAGMENT_TYPES and fragment.confidence is not None:
            fragment.confidence = (
                min(fragment.confidence, UNADDRESSABLE_CONFIDENCE)
                if change.new_path is None
                else fragment.confidence
            )
        metadata = dict(fragment.metadata_json or {})
        if change.duplicate_path is None:
            metadata.pop(DUPLICATE_KEY, None)
        else:
            metadata[DUPLICATE_KEY] = change.duplicate_path
        metadata[REPATH_MARKER] = True
        fragment.metadata_json = metadata
    session.flush()
    return payload


def revert_corpus_repath(session: Session, payload: Sequence[dict[str, Any]]) -> int:
    """Restore the exact pre-repath state recorded by an applied run."""
    restored = 0
    for record in payload:
        fragment = session.get(SourceFragment, record["fragment_id"])
        if fragment is not None:
            fragment.citation_path = None
    session.flush()

    for record in payload:
        fragment = session.get(SourceFragment, record["fragment_id"])
        if fragment is None:
            continue
        fragment.citation_label = record["citation_label"]
        fragment.citation_path = record["citation_path"]
        fragment.parse_status = ParseStatus(record["parse_status"])
        fragment.confidence = record["confidence"]
        fragment.metadata_json = dict(record["metadata_json"])
        restored += 1
    session.flush()
    return restored
