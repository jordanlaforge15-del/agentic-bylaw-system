"""Post-persist table-caption linking (ABS-409).

The HRM land-use bylaws caption their tables in a text block ("Table 1A:
Permitted uses by zone (…)") that parses as neither a citation label nor a
heading, so ingest leaves it as an unaddressed PROSE fragment
(``citation_path=NULL``) and the table rows themselves carry ``caption=NULL``
and ``parent_fragment_id=NULL``. The consequences compound downstream:
``lookup_citation("Table 1A")`` can never resolve, and the table classifier
(``layer1.semantic.enrichment._classify_table``) — whose strongest signals are
caption phrases like "permitted uses by zone" / "parking" — misprofiles the
matrices.

This pass links them after persistence:

* a fragment matching the profile's ``table_caption_re`` gains a
  ``citation_label`` ("Table 1A") and a ``citation_path``
  ("Part I > [Table 1A]", the bracket-segment convention of
  ``pipeline.citation_repath.context_segment``);
* the ``source_table`` rows that follow the caption (same page, or up to
  ``table_caption_page_span`` pages after it, stopping at the next caption)
  gain ``parent_fragment_id`` and ``caption``.

Everything bylaw-specific — whether the pass runs at all, the caption regex,
the page span — is declared on the bylaw's :class:`~layer1.profiles.
ParsingProfile`. This module carries only the bylaw-agnostic mechanics and is
shared by the ingest pipeline (``ingest_file`` runs it after persistence) and
the ``scripts/backfill_table_citations.py`` backfill (heals already-ingested
corpora).

Safety properties:

* **Idempotent, write-on-diff.** A second run over linked data writes nothing.
* **Never overwrites.** Non-NULL ``citation_path`` / ``citation_label`` /
  ``caption`` / ``parent_fragment_id`` values are left untouched.
* **Conservative on ambiguity.** A page holding several captions pairs k-th
  caption with k-th table only when the counts match; otherwise the page's
  captions are skipped and counted (a missing link is recoverable, a wrong
  legal citation is not).
* **Collision-safe.** ``uq_fragment_citation_path`` is checked before any
  path write; collisions are skipped and counted, never raised mid-run.
* **Revertible.** Before-values of every touched row are recorded on the
  returned :class:`LinkStats` (``touched``); :func:`revert_table_captions`
  restores them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import Document, SourceFragment, SourceTable
from layer1.models.enums import FragmentType
from layer1.profiles import ParsingProfile, get_parsing_profile

# Fragment types a caption block may have been persisted as. The common case
# is the coverage-sweep PROSE fallback; docling occasionally promotes a
# caption line to a HEADING (e.g. "Table 10:" in the Regional Centre LUB).
_CAPTION_FRAGMENT_TYPES = (FragmentType.PROSE, FragmentType.HEADING)


@dataclass
class LinkStats:
    """Outcome of one :func:`link_table_captions` run."""

    documents: int = 0
    captions_seen: int = 0
    captions_linked: int = 0
    tables_claimed: int = 0
    ambiguous_skipped: int = 0
    collisions_skipped: int = 0
    already_linked: int = 0
    writes: int = 0
    dry_run: bool = False
    # caption -> claimed-tables mapping, one entry per caption candidate, for
    # human review of a dry run before any apply.
    mapping: list[dict[str, Any]] = field(default_factory=list)
    # Before-values of every row actually written, keyed by row id. Consumed
    # by revert_table_captions and serialized to the backfill sidecar.
    touched: dict[str, dict[int, dict[str, Any]]] = field(
        default_factory=lambda: {"fragments": {}, "tables": {}}
    )

    def summary_line(self) -> str:
        mode = "DRY-RUN " if self.dry_run else ""
        return (
            f"{mode}table-caption linking: {self.documents} document(s), "
            f"{self.captions_seen} caption(s) seen, {self.captions_linked} linked, "
            f"{self.tables_claimed} table(s) claimed, {self.writes} write(s), "
            f"{self.ambiguous_skipped} ambiguous skipped, "
            f"{self.collisions_skipped} collision(s) skipped, "
            f"{self.already_linked} already linked"
        )


def link_table_captions(
    session: Session,
    *,
    document_id: int | None = None,
    profile: ParsingProfile | str | None = None,
    dry_run: bool = False,
) -> LinkStats:
    """Link table-caption fragments to their tables for one or all documents.

    ``profile`` decides whether and how the pass runs (see module docstring).
    A profile without ``table_caption_re`` disables the pass — the stats come
    back empty. ``document_id=None`` processes every document in the session's
    database under the single supplied profile; callers healing a mixed corpus
    should scope to one document per call.
    """
    stats = LinkStats(dry_run=dry_run)
    resolved = get_parsing_profile(profile)
    if resolved.table_caption_re is None:
        return stats

    doc_ids: list[int]
    if document_id is not None:
        doc_ids = [document_id]
    else:
        doc_ids = list(session.execute(select(Document.id).order_by(Document.id)).scalars())

    for doc_id in doc_ids:
        _link_document(session, stats, doc_id, resolved, dry_run=dry_run)
    return stats


def revert_table_captions(
    session: Session, touched: dict[str, dict[int | str, dict[str, Any]]]
) -> int:
    """Restore the before-values recorded by a previous applied run.

    ``touched`` is the :attr:`LinkStats.touched` payload (possibly round-
    tripped through JSON, so row keys may be strings). Returns the number of
    rows restored.
    """
    restored = 0
    for fragment_id, before in touched.get("fragments", {}).items():
        fragment = session.get(SourceFragment, int(fragment_id))
        if fragment is None:
            continue
        fragment.citation_label = before.get("citation_label")
        fragment.citation_path = before.get("citation_path")
        restored += 1
    for table_id, before in touched.get("tables", {}).items():
        table = session.get(SourceTable, int(table_id))
        if table is None:
            continue
        table.parent_fragment_id = before.get("parent_fragment_id")
        table.caption = before.get("caption")
        restored += 1
    session.flush()
    return restored


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _link_document(
    session: Session,
    stats: LinkStats,
    document_id: int,
    profile: ParsingProfile,
    *,
    dry_run: bool,
) -> None:
    caption_re = profile.table_caption_re
    assert caption_re is not None  # guarded by caller

    fragments = (
        session.execute(
            select(SourceFragment)
            .where(SourceFragment.document_id == document_id)
            .order_by(SourceFragment.page_start, SourceFragment.id)
        )
        .scalars()
        .all()
    )
    tables = (
        session.execute(
            select(SourceTable)
            .where(SourceTable.document_id == document_id)
            .order_by(SourceTable.page_start, SourceTable.id)
        )
        .scalars()
        .all()
    )
    if not fragments or not tables:
        return

    captions = [
        (fragment, match)
        for fragment in fragments
        if fragment.fragment_type in _CAPTION_FRAGMENT_TYPES
        and fragment.text
        and (match := caption_re.match(fragment.text.strip())) is not None
    ]
    if not captions:
        return

    stats.documents += 1
    stats.captions_seen += len(captions)

    existing_paths = {
        fragment.citation_path for fragment in fragments if fragment.citation_path
    }
    claimed_table_ids: set[int] = set()
    caption_pages = sorted({fragment.page_start for fragment, _ in captions})
    captions_by_page: dict[int, list[tuple[SourceFragment, Any]]] = {}
    for fragment, match in captions:
        captions_by_page.setdefault(fragment.page_start, []).append((fragment, match))
    tables_by_page: dict[int, list[SourceTable]] = {}
    for table in tables:
        tables_by_page.setdefault(table.page_start, []).append(table)

    for page in caption_pages:
        page_captions = captions_by_page[page]
        if len(page_captions) > 1:
            # Several captions on one page: page granularity cannot express
            # within-page ordering across the fragment/table id sequences, so
            # only the unambiguous k-th-to-k-th pairing is accepted.
            page_tables = [
                t for t in tables_by_page.get(page, []) if t.id not in claimed_table_ids
            ]
            if len(page_tables) != len(page_captions):
                stats.ambiguous_skipped += len(page_captions)
                for fragment, match in page_captions:
                    stats.mapping.append(
                        _mapping_entry(document_id, fragment, match, [], "ambiguous_skip")
                    )
                continue
            for (fragment, match), table in zip(page_captions, page_tables):
                _apply_caption(
                    session, stats, document_id, fragment, match, [table],
                    existing_paths, fragments, dry_run=dry_run,
                )
                claimed_table_ids.add(table.id)
            continue

        fragment, match = page_captions[0]
        next_caption_page = next((p for p in caption_pages if p > page), None)
        upper = page + profile.table_caption_page_span + 1
        if next_caption_page is not None:
            upper = min(upper, next_caption_page)
        run_tables = [
            table
            for table in tables
            if page <= table.page_start < upper and table.id not in claimed_table_ids
        ]
        _apply_caption(
            session, stats, document_id, fragment, match, run_tables,
            existing_paths, fragments, dry_run=dry_run,
        )
        claimed_table_ids.update(table.id for table in run_tables)


def _apply_caption(
    session: Session,
    stats: LinkStats,
    document_id: int,
    fragment: SourceFragment,
    match: Any,
    run_tables: list[SourceTable],
    existing_paths: set[str],
    fragments: list[SourceFragment],
    *,
    dry_run: bool,
) -> None:
    label = (match.group(1) if match.groups() else match.group(0).rstrip(": ")).strip()
    prefix = _part_prefix(fragments, fragment)
    path = f"{prefix} > [{label}]" if prefix else f"[{label}]"

    action = "linked"
    fragment_before = {
        "citation_label": fragment.citation_label,
        "citation_path": fragment.citation_path,
    }
    fragment_written = False
    if fragment.citation_path is not None:
        # Never overwrite an addressed fragment (ours from a prior run, or the
        # hierarchy's). It still anchors its tables.
        stats.already_linked += 1
        action = "already_linked"
        path = fragment.citation_path
    elif path in existing_paths:
        stats.collisions_skipped += 1
        action = "collision_skip"
    else:
        if not dry_run:
            fragment.citation_label = label
            fragment.citation_path = path
        existing_paths.add(path)
        stats.captions_linked += 1
        stats.writes += 1
        fragment_written = True

    table_ids: list[int] = []
    for table in run_tables:
        table_before = {
            "parent_fragment_id": table.parent_fragment_id,
            "caption": table.caption,
        }
        wrote = False
        if table.parent_fragment_id is None:
            if not dry_run:
                table.parent_fragment_id = fragment.id
            wrote = True
        if table.caption is None and fragment.text:
            if not dry_run:
                table.caption = fragment.text.strip()
            wrote = True
        if wrote:
            stats.tables_claimed += 1
            stats.writes += 1
            if not dry_run:
                stats.touched["tables"][table.id] = table_before
        table_ids.append(table.id)

    if fragment_written and not dry_run:
        stats.touched["fragments"][fragment.id] = fragment_before
    if not dry_run:
        session.flush()
    stats.mapping.append(
        _mapping_entry(document_id, fragment, match, table_ids, action, path=path)
    )


def _part_prefix(
    fragments: list[SourceFragment], caption: SourceFragment
) -> str | None:
    """First path segment of the nearest preceding addressed fragment.

    Gives the caption a plausible hierarchical parent ("Part I" for a caption
    that follows "Part I > 39"). ``None`` when nothing before the caption is
    addressed — the path is then the bare bracket segment.
    """
    best: str | None = None
    for fragment in fragments:  # already ordered by (page_start, id)
        if (fragment.page_start, fragment.id) >= (caption.page_start, caption.id):
            break
        if fragment.citation_path:
            best = fragment.citation_path
    if not best:
        return None
    return best.split(" > ", 1)[0].strip() or None


def _mapping_entry(
    document_id: int,
    fragment: SourceFragment,
    match: Any,
    table_ids: list[int],
    action: str,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    label = (match.group(1) if match.groups() else match.group(0).rstrip(": ")).strip()
    return {
        "document_id": document_id,
        "caption_fragment_id": fragment.id,
        "page": fragment.page_start,
        "label": label,
        "citation_path": path,
        "text": (fragment.text or "")[:80],
        "table_ids": table_ids,
        "action": action,
    }
