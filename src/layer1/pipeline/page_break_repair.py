"""Repair fragments a mid-token page break split in an already-ingested corpus.

Companion to the parser guard in :mod:`layer1.pipeline.hierarchy` (ABS-461).
The parser fix only helps documents ingested *after* it lands; every corpus
already in a database still carries the damage, and re-ingesting a 457-page
bylaw to correct four fragments would churn every fragment id in the document
(and every foreign key pointing at one). This module does the surgical version.

The defect signature is a pair of adjacent fragments where the first ends on a
hyphen mid-token and the second resumes it:

    7121  CLAUSE (a)   "...any portion of which, is zoned ER-3, ER-"
    7122  SECTION 2    "2, ER-1, CH-2, CH-1, PCF, or RPK zone: ..."

Because the tail opens with a bare number, the parser read it as a new section,
so ``7122`` became a phantom ``Part V > 2`` and the clauses that followed hung
off it. The repair, per pair:

1. splices the tail's text back onto the head, widening the head's page range
   and source-block provenance;
2. rewrites every citation_path under the phantom onto the prefix the phantom
   displaced -- the nearest preceding real SECTION/SUBSECTION path;
3. reparents the phantom's children onto the head (what the fixed parser does);
4. deletes the phantom fragment.

Every path rewrite is checked against the document's existing paths first: the
``uq_fragment_citation_path`` constraint means a collision would abort the whole
transaction, so colliding rewrites are skipped and reported instead.

Head fragments whose text changed have their embeddings invalidated -- a stale
vector is worse than a missing one, and re-embedding is a separate documented
pass (``layer2 embed-fragments``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import bindparam, inspect, select, text
from sqlalchemy.orm import Session

from layer1.db.base import SourceFragment
from layer1.models.enums import BlockType, FragmentType, ParseStatus

# Mirrors hierarchy.HYPHEN_BREAK_TAIL_RE / HYPHEN_BREAK_HEAD_RE: the hyphen has
# to be flanked by alphanumerics across the break, which is what separates a
# wrapped token from a paragraph that merely ends in a dash.
HYPHEN_BREAK_TAIL_RE = re.compile(r"[A-Za-z0-9]-$")
HYPHEN_BREAK_HEAD_RE = re.compile(r"^[A-Za-z0-9]")

# Mirrors hierarchy.CONTINUATION_JOINABLE_BLOCK_TYPES. Headings are excluded on
# purpose: the Mainland LUB's amendment log has table cells that end on a hyphen
# and are followed by an unrelated cell in the next column ("Added R-4B
# (Dunbrack Multi-" / "Case 22332"). Those are adjacent, not continuous.
JOINABLE_BLOCK_TYPES = {BlockType.PARAGRAPH.value, BlockType.LIST_ITEM.value}

SECTION_LIKE = {FragmentType.SECTION, FragmentType.SUBSECTION}

REPAIR_MARKER = "abs461_joined_page_break"


@dataclass
class PathRewrite:
    fragment_id: int
    old_path: str
    new_path: str


@dataclass
class PageBreakSplit:
    """One detected head/tail pair and everything the repair did about it."""

    document_id: int
    head_id: int
    tail_id: int
    head_text_before: str
    head_text_after: str
    phantom_path: str | None = None
    replacement_prefix: str | None = None
    rewrites: list[PathRewrite] = field(default_factory=list)
    reparented_ids: list[int] = field(default_factory=list)
    skipped_collisions: list[PathRewrite] = field(default_factory=list)

    @property
    def unresolved(self) -> bool:
        """True when the phantom's paths could not be rehomed.

        Splicing the text is always safe; rehoming needs a real ancestor path to
        graft onto. Without one the phantom's descendants would keep a citation
        path naming a section that does not exist, so the caller needs to know.
        """
        return self.phantom_path is not None and self.replacement_prefix is None


@dataclass
class RepairStats:
    splits: list[PageBreakSplit] = field(default_factory=list)
    embeddings_invalidated: int = 0
    revert_payload: list[dict] = field(default_factory=list)

    @property
    def phantom_sections_removed(self) -> int:
        return sum(1 for split in self.splits if split.phantom_path is not None)

    @property
    def paths_rewritten(self) -> int:
        return sum(len(split.rewrites) for split in self.splits)

    @property
    def unresolved(self) -> list[PageBreakSplit]:
        return [split for split in self.splits if split.unresolved]

    def summary_line(self) -> str:
        return (
            f"page-break splits: {len(self.splits)} joined, "
            f"{self.phantom_sections_removed} phantom section(s) removed, "
            f"{self.paths_rewritten} citation_path(s) rewritten, "
            f"{len(self.unresolved)} unresolved, "
            f"{self.embeddings_invalidated} embedding(s) invalidated"
        )


# Columns carried into the revert sidecar so a deleted phantom can be recreated
# byte-for-byte, original id included.
_TAIL_COLUMNS = (
    "id",
    "document_id",
    "fragment_type",
    "citation_label",
    "citation_path",
    "parent_fragment_id",
    "page_start",
    "page_end",
    "reading_order_start",
    "reading_order_end",
    "text",
    "parse_status",
    "confidence",
    "source_block_ids_json",
    "metadata_json",
    "attribute_tags",
)


def _block_type(fragment: SourceFragment) -> str | None:
    return (fragment.metadata_json or {}).get("block_type")


def _is_joinable(fragment: SourceFragment) -> bool:
    return _block_type(fragment) in JOINABLE_BLOCK_TYPES and bool((fragment.text or "").strip())


def _is_hyphen_break_pair(head: SourceFragment, tail: SourceFragment) -> bool:
    if not (_is_joinable(head) and _is_joinable(tail)):
        return False
    if not HYPHEN_BREAK_TAIL_RE.search(head.text.rstrip()):
        return False
    return HYPHEN_BREAK_HEAD_RE.match(tail.text.lstrip()) is not None


def _document_fragments(session: Session, document_id: int) -> list[SourceFragment]:
    stmt = (
        select(SourceFragment)
        .where(SourceFragment.document_id == document_id)
        .order_by(SourceFragment.reading_order_start, SourceFragment.id)
    )
    return list(session.execute(stmt).scalars())


def _target_document_ids(session: Session, document_id: int | None) -> list[int]:
    if document_id is not None:
        return [document_id]
    stmt = select(SourceFragment.document_id).distinct().order_by(SourceFragment.document_id)
    return list(session.execute(stmt).scalars())


def _replacement_prefix(ordered: list[SourceFragment], tail_index: int) -> str | None:
    """The citation path the phantom displaced.

    Scans backward from the phantom for the nearest real SECTION/SUBSECTION
    with a path. For the 198(1) break that finds section 198 (``Part V > 198``);
    for the 94.5 break it finds subsection 94.5 (``Part V > 94 > 94.5``). In
    both cases splicing that prefix in place of the phantom's own reproduces
    exactly the path the clause's unbroken siblings already carry.
    """
    for candidate in reversed(ordered[:tail_index]):
        if candidate.fragment_type in SECTION_LIKE and candidate.citation_path:
            return candidate.citation_path
    return None


def find_page_break_splits(session: Session, *, document_id: int | None = None) -> list[PageBreakSplit]:
    """Detect hyphen-broken head/tail fragment pairs without writing anything."""
    splits: list[PageBreakSplit] = []
    for doc_id in _target_document_ids(session, document_id):
        ordered = _document_fragments(session, doc_id)
        for index in range(len(ordered) - 1):
            head, tail = ordered[index], ordered[index + 1]
            if not _is_hyphen_break_pair(head, tail):
                continue
            split = PageBreakSplit(
                document_id=doc_id,
                head_id=head.id,
                tail_id=tail.id,
                head_text_before=head.text,
                head_text_after=f"{head.text.rstrip()}{tail.text.lstrip()}",
                phantom_path=tail.citation_path,
            )
            if tail.citation_path:
                split.replacement_prefix = _replacement_prefix(ordered, index + 1)
                _plan_path_rewrites(ordered, split, tail.citation_path)
            splits.append(split)
    return splits


def _plan_path_rewrites(
    ordered: list[SourceFragment], split: PageBreakSplit, phantom_path: str
) -> None:
    if not split.replacement_prefix:
        return
    existing = {f.citation_path for f in ordered if f.citation_path}
    for fragment in ordered:
        path = fragment.citation_path
        if fragment.id == split.tail_id or not path:
            continue
        if path != phantom_path and not path.startswith(f"{phantom_path} > "):
            continue
        new_path = split.replacement_prefix + path[len(phantom_path) :]
        rewrite = PathRewrite(fragment_id=fragment.id, old_path=path, new_path=new_path)
        if new_path in existing:
            split.skipped_collisions.append(rewrite)
            continue
        existing.add(new_path)
        split.rewrites.append(rewrite)


def repair_page_break_splits(
    session: Session, *, document_id: int | None = None, dry_run: bool = False
) -> RepairStats:
    """Splice hyphen-broken fragments back together and unwind their phantoms."""
    splits = find_page_break_splits(session, document_id=document_id)
    stats = RepairStats(splits=splits)
    if dry_run or not splits:
        return stats

    by_id = {
        fragment.id: fragment
        for fragment in session.execute(
            select(SourceFragment).where(
                SourceFragment.id.in_(
                    [split.head_id for split in splits] + [split.tail_id for split in splits]
                )
            )
        ).scalars()
    }

    for split in splits:
        head, tail = by_id[split.head_id], by_id[split.tail_id]
        record = {
            "head": {
                "id": head.id,
                "text": head.text,
                "page_end": head.page_end,
                "reading_order_end": head.reading_order_end,
                "source_block_ids_json": list(head.source_block_ids_json or []),
                "metadata_json": dict(head.metadata_json or {}),
            },
            "tail": _serialize_tail(tail),
            "rewrites": [
                {"fragment_id": r.fragment_id, "old_path": r.old_path, "new_path": r.new_path}
                for r in split.rewrites
            ],
        }

        head.text = split.head_text_after
        head.page_end = max(head.page_end, tail.page_end)
        if tail.reading_order_end is not None:
            head.reading_order_end = max(head.reading_order_end or 0, tail.reading_order_end)
        head.source_block_ids_json = list(head.source_block_ids_json or []) + list(
            tail.source_block_ids_json or []
        )
        head.metadata_json = {**(head.metadata_json or {}), REPAIR_MARKER: tail.id}

        for rewrite in split.rewrites:
            session.get(SourceFragment, rewrite.fragment_id).citation_path = rewrite.new_path

        children = session.execute(
            select(SourceFragment).where(SourceFragment.parent_fragment_id == tail.id)
        ).scalars()
        for child in children:
            child.parent_fragment_id = head.id
            split.reparented_ids.append(child.id)

        record["reparented_ids"] = list(split.reparented_ids)
        stats.revert_payload.append(record)

        # Flush before the delete so the rewrites clear the unique constraint
        # ahead of the phantom row disappearing.
        session.flush()
        session.delete(tail)

    stats.embeddings_invalidated = _invalidate_embeddings(
        session, [split.head_id for split in splits]
    )
    session.flush()
    return stats


def _serialize_tail(tail: SourceFragment) -> dict:
    """Snapshot the phantom row as JSON so ``--revert`` can recreate it."""
    payload: dict = {}
    for column in _TAIL_COLUMNS:
        value = getattr(tail, column)
        if isinstance(value, list):
            value = list(value)
        elif isinstance(value, dict):
            value = dict(value)
        payload[column] = value
    payload["fragment_type"] = FragmentType(tail.fragment_type).value
    payload["parse_status"] = ParseStatus(tail.parse_status).value
    return payload


def _deserialize_tail(payload: dict) -> SourceFragment:
    state = dict(payload)
    state["fragment_type"] = FragmentType(state["fragment_type"])
    state["parse_status"] = ParseStatus(state["parse_status"])
    return SourceFragment(**state)


def revert_page_break_splits(session: Session, payload: list[dict]) -> int:
    """Undo an applied repair from its sidecar. Returns rows restored.

    Order matters: descendants go back onto the phantom prefix *before* the
    phantom row is re-inserted, so no intermediate state violates
    ``uq_fragment_citation_path``.
    """
    restored = 0
    for record in payload:
        for rewrite in record["rewrites"]:
            fragment = session.get(SourceFragment, rewrite["fragment_id"])
            if fragment is not None:
                fragment.citation_path = rewrite["old_path"]
                restored += 1

        head_state = record["head"]
        head = session.get(SourceFragment, head_state["id"])
        if head is not None:
            head.text = head_state["text"]
            head.page_end = head_state["page_end"]
            head.reading_order_end = head_state["reading_order_end"]
            head.source_block_ids_json = list(head_state["source_block_ids_json"])
            head.metadata_json = dict(head_state["metadata_json"])
            restored += 1
        session.flush()

        tail_state = record["tail"]
        if session.get(SourceFragment, tail_state["id"]) is None:
            session.add(_deserialize_tail(tail_state))
            restored += 1
        session.flush()

        for child_id in record["reparented_ids"]:
            child = session.get(SourceFragment, child_id)
            if child is not None:
                child.parent_fragment_id = tail_state["id"]
                restored += 1
    session.flush()
    return restored


def _invalidate_embeddings(session: Session, fragment_ids: list[int]) -> int:
    """Drop embeddings for fragments whose text changed.

    A vector computed from the truncated text would keep scoring the fragment
    as if it still ended at "ER-". Deleting is the honest option: the fragment
    stays keyword-retrievable and a re-embed pass rebuilds the vector.

    ``fragment_embedding`` is a Layer 2 table, so this is a guarded raw delete
    rather than an ORM one — a Layer 1 pipeline module importing a Layer 2 model
    would invert the layering, and a Layer-1-only database (the sqlite fixtures)
    has no such table at all.
    """
    if not fragment_ids:
        return 0
    if not inspect(session.get_bind()).has_table("fragment_embedding"):
        return 0
    statement = text(
        "DELETE FROM fragment_embedding WHERE source_fragment_id IN :fragment_ids"
    ).bindparams(bindparam("fragment_ids", expanding=True))
    return session.execute(statement, {"fragment_ids": fragment_ids}).rowcount or 0
