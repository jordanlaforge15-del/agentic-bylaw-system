"""The table retrieval channel: rank cells, not just the prose beside them (ABS-500).

``RetrievalService.search`` used to rank ``source_fragment`` rows and nothing
else. The text channel scored fragments; the spatial channel scored linked geo
datasets and resolved them back to a *fragment*; tables reached the model only
as ``related_tables`` hung off a fragment that had already ranked. So a
dimensional standard that lives in a matrix — "Maximum Required Lot Coverage
(%)" against a column of zones — was reachable only if some neighbouring prose
fragment happened to rank first. "What is the maximum height in HR-2", the
archetypal product question, had no direct route to the cell that answers it.

This module supplies that route. It scores ``source_table`` / ``source_table_cell``
directly and hands the result back as a third channel for
``_merge_channel_scores`` to fuse.

Binding, not keyword matching
-----------------------------
A dimensional matrix is addressed on two axes, and enrichment already recorded
what each axis means: ``table_semantic_profile`` classifies the table
(``dimensional_matrix``, ``parking_matrix``, …) and ``table_axis_binding``
binds a row or column index to a ``semantic_entity`` — the zone ``R-1``, the
standard ``minimum lot frontage``. A zone-scoped query therefore *binds* to a
column rather than keyword-matching the text inside it, which is what makes
the channel able to answer for a cell whose own text is "35%" and contains no
query term at all.

Where the axes were never bound, the channel falls back to matching the query
against the table caption and the header cells (row 0 / column 0). The stored
``row_header_path`` / ``col_header_path`` columns are deliberately *not* used
as text: across the ingested corpus they hold the header's numeric index, not
its label, so reading them as prose would score nothing and hide the gap.

Citation shape
--------------
See ``docs/ABS-500-TABLE-CHANNEL.md``. In short: a cell is not a
``source_fragment`` and ``RetrievalMatch`` is fragment-shaped, so a ranked cell
is cited through its table's **anchor fragment** — the provision that
introduces the table — and carries a :class:`TableCellMatch` naming the table,
the cell, and the axis labels that address it. That mirrors ``_table_citation``,
which already cites a permitted-use matrix through the same anchor.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from layer1.db.base import (
    SemanticEntity,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
)

# ----------------------------------------------------------------------
# Weights. Deliberately expressed on the same ladder the fragment scorer
# uses (own text +4, citation label +8, citation path +12) so a fused score
# means the same thing whichever channel produced it.
# ----------------------------------------------------------------------

#: A query term in the table's caption. A caption is the table's citable
#: identity ("Table 10: Maximum required lot coverage …"), so it scores at the
#: citation-label rung rather than the body-text rung.
CAPTION_TOKEN_SCORE = 8.0

#: A query term in an axis label — the row or column header that addresses the
#: cell. Stronger than body text (the header is how the cell is *named*),
#: weaker than a semantic binding (the header is still keyword evidence).
AXIS_LABEL_TOKEN_SCORE = 6.0

#: A query term in the cell's own text.
CELL_TOKEN_SCORE = 4.0

#: Axis labels in this corpus run from "ER-3 Zone" to a 24-word sentence
#: ("Number of Pedestrian Entrances for Grade-Oriented Premises Along
#: Streetwalls in a DD,DH, CEN-2, … Zone (Section 364)"). Uncapped, the long
#: one accumulates more term credit than a semantic binding is worth and every
#: query lands on the same three rows. Three terms is enough to distinguish
#: "maximum required lot coverage" from "minimum lot frontage"; past that the
#: label is being rewarded for its length.
MAX_AXIS_LABEL_TOKENS = 3

#: The query names an entity that enrichment bound to this axis index. This is
#: a structural claim about *which column answers the question*, not a term
#: overlap, so it is set one step above the ceiling term overlap can reach
#: (``AXIS_LABEL_TOKEN_SCORE * MAX_AXIS_LABEL_TOKENS`` = 18): a column the
#: corpus says is the R-1 column must not be outranked by a column whose
#: heading happens to repeat three of the query's words.
AXIS_BINDING_SCORE = 20.0

#: A table has to be addressed on *both* axes before it beats prose that states
#: the standard in words — a bound axis plus at least one term on the other.
#: One axis alone ("something about lot coverage", "something about ER-1") is a
#: table worth attaching, not a table worth ranking, and admitting those floods
#: the top of every zone-scoped query with the 56 unclassified tables in the
#: corpus.
CHANNEL_THRESHOLD = AXIS_BINDING_SCORE + AXIS_LABEL_TOKEN_SCORE



@dataclass(frozen=True)
class AxisLabel:
    """One addressable row or column of a table.

    ``entity_type`` / ``entity_name`` are populated only where enrichment bound
    the axis to a semantic entity; ``label`` is always the text as printed.
    """

    axis: str
    index: int
    label: str
    entity_type: str | None = None
    entity_name: str | None = None

    @property
    def haystack(self) -> str:
        """The label lower-cased, which is what the query patterns expect.

        ``query_token_patterns`` compiles case-*sensitive* patterns over an
        already-lower-cased query, and the fragment scorer lower-cases the
        fragment before matching. Table text arrives as printed ("Maximum
        Required Lot Coverage (%)"), so folding here is what makes a term mean
        the same thing in this channel as in the text channel.
        """
        return (self.label or "").lower()


@dataclass(frozen=True)
class TableCellHit:
    """One ranked cell, with everything needed to cite it."""

    table_id: int
    document_id: int
    caption: str | None
    page_start: int
    page_end: int
    anchor_fragment_id: int | None
    profile_type: str | None
    row_index: int
    col_index: int
    row_label: str | None
    col_label: str | None
    text: str
    score: float
    #: Human-readable statement of *why* this cell was addressed, e.g.
    #: "row bound to zone 'R-1'". Empty when the cell was reached by keyword
    #: match alone.
    bound_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableChannelResult:
    """Scores keyed by anchor fragment, plus the cells that earned them."""

    scores: dict[int, float]
    hits: dict[int, list[TableCellHit]]

    @classmethod
    def empty(cls) -> TableChannelResult:
        return cls(scores={}, hits={})


class TableIndex:
    """Tables, cells, axis labels and anchors for one document scope.

    Built once and cached on the service. The corpus holds 96 tables and 5,624
    cells, so the whole channel is a handful of full reads — cheaper than the
    per-request fragment scan the text channel already performs, and far
    cheaper than issuing four queries per candidate table per request.
    """

    def __init__(
        self,
        *,
        tables: list[SourceTable],
        cells: dict[int, list[SourceTableCell]],
        axes: dict[int, list[AxisLabel]],
        anchors: dict[int, int | None],
        profiles: dict[int, str],
    ) -> None:
        self.tables = tables
        self.cells = cells
        self.axes = axes
        self.anchors = anchors
        self.profiles = profiles

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def build(cls, session, document_ids) -> TableIndex:
        scope = list(document_ids) if document_ids is not None else None

        table_stmt = select(SourceTable)
        if scope is not None:
            table_stmt = table_stmt.where(SourceTable.document_id.in_(scope))
        tables = list(session.execute(table_stmt.order_by(SourceTable.id)).scalars().all())
        if not tables:
            return cls(tables=[], cells={}, axes={}, anchors={}, profiles={})

        table_ids = [table.id for table in tables]

        cells: dict[int, list[SourceTableCell]] = defaultdict(list)
        for cell in (
            session.execute(
                select(SourceTableCell)
                .where(SourceTableCell.table_id.in_(table_ids))
                .order_by(
                    SourceTableCell.table_id,
                    SourceTableCell.row_index,
                    SourceTableCell.col_index,
                )
            )
            .scalars()
            .all()
        ):
            cells[cell.table_id].append(cell)

        profiles: dict[int, str] = {}
        for table_id, profile_type in session.execute(
            select(TableSemanticProfile.table_id, TableSemanticProfile.profile_type).where(
                TableSemanticProfile.table_id.in_(table_ids)
            )
        ).all():
            # A table may carry several profiles; the first non-"unknown" one
            # is the classification worth reporting.
            if profile_type and (table_id not in profiles or profiles[table_id] == "unknown"):
                profiles[table_id] = profile_type

        axes = cls._build_axes(session, table_ids, cells)
        anchors = cls._build_anchors(session, tables)
        return cls(tables=tables, cells=cells, axes=axes, anchors=anchors, profiles=profiles)

    @staticmethod
    def _build_axes(
        session, table_ids: list[int], cells: dict[int, list[SourceTableCell]]
    ) -> dict[int, list[AxisLabel]]:
        """Axis labels per table: bound axes first, header cells for the rest.

        Enrichment binds only the axes it could resolve to an entity, so a
        table typically has bindings for its zone rows and its standard columns
        and nothing for the axis it never classified. The header cells fill the
        gap, which is what keeps an unclassified ``key_value_table`` reachable
        by its printed headings.
        """
        axes: dict[int, list[AxisLabel]] = defaultdict(list)
        seen: dict[int, set[tuple[str, int]]] = defaultdict(set)

        for binding, entity in session.execute(
            select(TableAxisBinding, SemanticEntity)
            .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
            .where(TableAxisBinding.table_id.in_(table_ids))
            .order_by(TableAxisBinding.table_id, TableAxisBinding.axis, TableAxisBinding.index)
        ).all():
            key = (binding.axis, binding.index)
            if key in seen[binding.table_id]:
                continue
            seen[binding.table_id].add(key)
            axes[binding.table_id].append(
                AxisLabel(
                    axis=binding.axis,
                    index=binding.index,
                    label=binding.raw_label or entity.canonical_name,
                    entity_type=entity.entity_type,
                    entity_name=entity.canonical_name,
                )
            )

        for table_id, table_cells in cells.items():
            for cell in table_cells:
                if cell.row_index == 0 and cell.col_index > 0:
                    key = ("column", cell.col_index)
                elif cell.col_index == 0 and cell.row_index > 0:
                    key = ("row", cell.row_index)
                else:
                    continue
                if key in seen[table_id] or not (cell.text or "").strip():
                    continue
                seen[table_id].add(key)
                axes[table_id].append(AxisLabel(axis=key[0], index=key[1], label=cell.text))
        return axes

    @staticmethod
    def _build_anchors(session, tables: list[SourceTable]) -> dict[int, int | None]:
        """Resolve each table to the fragment that introduces it.

        ``parent_fragment_id`` when the ingest set one. It usually did not —
        63 of the 96 tables in the dev corpus have no parent, and every table in
        the Halifax Mainland by-law is parentless — so the fallback reads the
        docling block ordering both rows carry: the anchor is the fragment with
        the greatest source block id *before* the table's own. That is literally
        "the provision immediately preceding this table on the page", which is
        the provision a reader would cite the table under. Spot-checked against
        the corpus: the parentless Mainland matrix on page 66 anchors to
        ``Schedule A > 28C > 28AB(1)`` — "Buildings … in an R-2P Zone shall
        comply with the following requirements:".
        """
        anchors: dict[int, int | None] = {}
        by_document: dict[int, list[SourceTable]] = defaultdict(list)
        for table in tables:
            if table.parent_fragment_id is not None:
                anchors[table.id] = table.parent_fragment_id
            else:
                by_document[table.document_id].append(table)

        for document_id, parentless in by_document.items():
            ordered = _fragment_block_order(session, document_id)
            for table in parentless:
                anchors[table.id] = _preceding_fragment(ordered, table)
        return anchors


def _fragment_block_order(session, document_id: int) -> list[tuple[int, int, int]]:
    """``[(block_id, page_start, fragment_id)]`` for one document, ascending.

    ``source_block_ids_json`` is a JSON array on every fragment; the maximum
    entry is where the fragment *ends* in the parser's reading order, which is
    what a table that follows it must sort after.
    """
    rows = session.execute(
        select(
            SourceFragment.id,
            SourceFragment.page_start,
            SourceFragment.source_block_ids_json,
        ).where(SourceFragment.document_id == document_id)
    ).all()
    ordered: list[tuple[int, int, int]] = []
    for fragment_id, page_start, block_ids in rows:
        numeric = [int(value) for value in (block_ids or []) if str(value).isdigit()]
        if not numeric:
            continue
        ordered.append((max(numeric), page_start, fragment_id))
    ordered.sort()
    return ordered


def _preceding_fragment(
    ordered: list[tuple[int, int, int]], table: SourceTable
) -> int | None:
    """The fragment immediately before ``table`` in reading order, or None."""
    block_id = (table.metadata_json or {}).get("source_block_id")
    if isinstance(block_id, int) and ordered:
        best: int | None = None
        for candidate_block, _page, fragment_id in ordered:
            if candidate_block >= block_id:
                break
            best = fragment_id
        if best is not None:
            return best
    # No block ordering (a non-docling ingest): fall back to the last fragment
    # that starts on or before the table's first page.
    best_page: int | None = None
    for _candidate_block, page_start, fragment_id in ordered:
        if page_start <= table.page_start:
            best_page = fragment_id
    return best_page


def table_channel_scores(
    index: TableIndex,
    *,
    token_patterns,
    zones_named: frozenset[str],
    discriminating: frozenset[str] = frozenset(),
    document_ids: set[int] | None = None,
) -> TableChannelResult:
    """Score every in-scope table cell; return the best cell per anchor fragment.

    ``token_patterns`` is the request's ``query_token_patterns`` — the same
    tuple the text channel scores with, so a term means the same thing in both
    channels. ``zones_named`` is what :mod:`bylaw_retrieval.retrieval.binding`
    read out of the query.

    Only the strongest cell of a table is returned. A dimensional query has one
    answer, and returning the whole column would spend the caller's context on
    the 20 zones it did not ask about — the rest of the table is still one hop
    away through ``related_tables``.
    """
    if not index.tables or not token_patterns:
        return TableChannelResult.empty()

    in_scope = [
        table
        for table in index.tables
        if document_ids is None or table.document_id in document_ids
    ]
    if not in_scope:
        return TableChannelResult.empty()

    # Score on the terms rare enough in this corpus to carry scope, and only
    # those. Without the cut the channel ranks on "in", "a", "for" and "zone",
    # which every long row label carries, so the *longest* label wins every
    # query regardless of what it says. The set is the text channel's own —
    # a term means the same thing in both channels, and its document frequency
    # is a property of the corpus, so measuring it twice would be measuring it
    # differently.
    if discriminating:
        token_patterns = tuple(
            (token, pattern) for token, pattern in token_patterns if token in discriminating
        )
    if not token_patterns:
        return TableChannelResult.empty()

    scores: dict[int, float] = {}
    hits: dict[int, list[TableCellHit]] = {}

    for table in in_scope:
        anchor = index.anchors.get(table.id)
        if anchor is None:
            # A table nothing can cite is a table the model cannot ground an
            # answer in. Attach-by-fragment still reaches it; ranking it would
            # produce an uncitable match.
            continue

        caption = (table.caption or "").lower()
        caption_matched = sum(
            1 for _token, pattern in token_patterns if pattern.search(caption)
        )
        # Capped for the same reason axis labels are: a long caption is not
        # more relevant than a short one, and uncapped it lets a captioned
        # table about the wrong zone outrank the table whose axis is *bound*
        # to the zone asked for. 40 of the corpus's 96 tables carry no caption
        # at all — every Mainland table — so an uncapped caption term would
        # also be a systematic advantage for one of the two by-laws.
        caption_score = CAPTION_TOKEN_SCORE * min(caption_matched, MAX_AXIS_LABEL_TOKENS)

        row_axis, col_axis = _axis_scores(index.axes.get(table.id, ()), token_patterns, zones_named)
        if not row_axis or not col_axis:
            continue

        best = _best_cell(
            table=table,
            cells=index.cells.get(table.id, ()),
            row_axis=row_axis,
            col_axis=col_axis,
            token_patterns=token_patterns,
            caption_score=caption_score,
            anchor=anchor,
            profile_type=index.profiles.get(table.id),
        )
        if best is None or best.score < CHANNEL_THRESHOLD:
            continue

        existing = scores.get(anchor)
        if existing is None or best.score > existing:
            scores[anchor] = best.score
            hits[anchor] = [best]
    return TableChannelResult(scores=scores, hits=hits)


def _is_value_cell(text: str, row_label: str | None, col_label: str | None) -> bool:
    """True when ``text`` is a value a reader could quote back.

    Two exclusions, both of them cells the ingest produces in quantity:

    * **Marker-only cells.** A permission matrix marks its cells with symbols
      from a private-use font range (``\\uf020``, ``\\uf098``) and with circled
      digits keyed to a footnote legend (``④``); stripped of whitespace they
      are non-empty but carry nothing a reader can quote back. The marker's
      *meaning* is what ``get_permitted_use`` resolves, and it does so through
      the permission-marker vocabulary — not by quoting the glyph. The test is
      ``isdecimal() or isalpha()`` rather than ``isalnum()`` precisely because
      ``"④".isalnum()`` is True: a circled digit is a numeral to Python and a
      footnote reference to a by-law.
    * **Header cells.** A header repeated as its own axis label is the
      question, not the answer: ranking "ER-3" as the value found under the
      column "ER-3" tells the reader nothing.
    """
    stripped = text.strip()
    if not any(
        character.isdecimal() or character.isalpha() for character in stripped
    ):
        return False
    folded = stripped.casefold()
    return folded not in {
        (label or "").strip().casefold() for label in (row_label, col_label)
    }


def _axis_scores(
    labels,
    token_patterns,
    zones_named: frozenset[str],
) -> tuple[dict[int, tuple[float, str | None, str | None]], dict[int, tuple[float, str | None, str | None]]]:
    """Score each row and column index. ``{index: (score, label, bound_by)}``."""
    rows: dict[int, tuple[float, str | None, str | None]] = {}
    cols: dict[int, tuple[float, str | None, str | None]] = {}
    for label in labels:
        bound_by: str | None = None
        if (
            label.entity_type == "zone"
            and label.entity_name
            and label.entity_name in zones_named
        ):
            score = AXIS_BINDING_SCORE
            bound_by = f"{label.axis} bound to zone {label.entity_name!r}"
        else:
            matched = sum(
                1 for _token, pattern in token_patterns if pattern.search(label.haystack)
            )
            score = AXIS_LABEL_TOKEN_SCORE * min(matched, MAX_AXIS_LABEL_TOKENS)
        if score <= 0:
            continue
        target = rows if label.axis == "row" else cols
        current = target.get(label.index)
        if current is None or score > current[0]:
            target[label.index] = (score, label.label, bound_by)
    return rows, cols


def _best_cell(
    *,
    table: SourceTable,
    cells,
    row_axis,
    col_axis,
    token_patterns,
    caption_score: float,
    anchor: int,
    profile_type: str | None,
) -> TableCellHit | None:
    """The highest-scoring addressable cell of one table."""
    best: TableCellHit | None = None
    for cell in cells:
        row = row_axis.get(cell.row_index)
        col = col_axis.get(cell.col_index)
        if row is None or col is None:
            continue
        text = cell.text or ""
        if not _is_value_cell(text, row[1], col[1]):
            continue
        cell_score = CELL_TOKEN_SCORE * sum(
            1 for _token, pattern in token_patterns if pattern.search(text.lower())
        )
        score = caption_score + row[0] + col[0] + cell_score
        if best is not None and score <= best.score:
            continue
        bound_by = tuple(reason for reason in (row[2], col[2]) if reason)
        best = TableCellHit(
            table_id=table.id,
            document_id=table.document_id,
            caption=table.caption,
            page_start=table.page_start,
            page_end=table.page_end,
            anchor_fragment_id=anchor,
            profile_type=profile_type,
            row_index=cell.row_index,
            col_index=cell.col_index,
            row_label=row[1],
            col_label=col[1],
            text=text,
            score=score,
            bound_by=bound_by,
        )
    return best
