"""Seed the two shapes ABS-523 fixes: a two-marker cell and a stemless clause.

Both are on the path a permitted-use fact takes to the reader, and together
they produced TC-023 — a developer at an ER-3 address told twelve units were
not achievable and sent to a rezoning the by-law does not require.

**Shape 1 — a permission cell with more than one marker.** Table 1B's
(ER-3, Multi-unit dwelling use) cell reads ``⑮ ㉒``. ⑮ is a Halifax Grain
Elevator carve-out irrelevant to the address; ㉒ is the footnote authorising
more than 8 units in ER-3 under Section 63 or Subsection 233(3). Enrichment
kept the first marker and discarded the rest, so the structured channel
reported the carve-out and the route was gone. No other matrix fixture in the
suite has a multi-marker cell — every one is single-glyph, so none can see this.

**Shape 2 — a clause whose path parent names no fragment.** s.233(3)'s stem was
never given a ``citation_path``. Its clauses were, and they read::

    Part V > 233 > [An addition to an existing main building ... to contain] > (b)

Nothing stands at that bracketed segment, so provision completion gave up and
the clause arrived as the bare words "more than 8 dwelling units in an ER-3
zone" — no stem, no sibling, no section. The model filled the hole by inventing
a site-plan-approval reading that appears nowhere in the corpus.

The bracketed segment is derived here by calling ``context_segment`` on the stem
text, exactly as the ingest does. Hard-coding the string would let the fixture
and the ingest drift apart and the spec would then be grading a path shape the
corpus no longer produces.

The clauses are parented to the *heading*, not to the section — the ABS-521
mis-parentage, reproduced because it is what makes the tree walk useless here
and the path the only route.

A *separate* document from every other seed so running enrichment here (which
clears and rebuilds a document's semantic layer) cannot disturb theirs.

Idempotent: re-running converges the fragments, the table's parentage and the
grid.
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import sys

from sqlalchemy import select

from layer1.db.base import (
    Document,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.citation_repath import context_segment

DOCUMENT_FILE_HASH = "e2e-abs523-footnote-retention-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Footnote Retention Test By-law"

TABLE_CAPTION = "Table 1F: Permitted uses by zone — footnote retention fixture"
TABLE_PAGE = 50

PARENT_CITATION_PATH = "Part I > [Table 1F]"
PARENT_CITATION_LABEL = "Table 1F"
PARENT_TEXT = (
    "Part I — Table 1F: the uses permitted in each residential zone are as set "
    "out in the following table."
)

DOT = "●"  # permitted as-of-right
GRAIN_ELEVATOR = "⑮"  # the carve-out that used to be the only survivor
CONVERSION_ROUTE = "㉒"  # the footnote that authorises the units

#: The two legends, verbatim from the Regional Centre LUB. ㉒ is the only
#: statement in the corpus of how the ER-3 unit cap interacts with the two
#: routes that exceed it, and it was reaching nobody.
FOOTNOTE_LEGENDS: list[tuple[int, str]] = [
    (
        15,
        "⑮ Use is permitted, except within the Halifax Grain Elevator (HGE) "
        "Special Area, as shown on Schedule 3F.",
    ),
    (
        22,
        "㉒ A multi-unit dwelling use that contains up to 8 dwelling units is "
        "permitted in the ER-3 zone, in accordance with Section 231.3, and a "
        "multi-unit dwelling use that contains more than 8 units is permitted "
        "in the ER-3 zone in accordance with Section 63 or Subsection 233(3).",
    ),
]

# (row, col, text, row_header_path, col_header_path).
#
# The fixture cell is (Multi-unit dwelling use, ER-3): two markers, both
# binding. Everything else is deliberately ordinary — single-marker and dot
# cells, so the spec can show that a one-marker cell is unchanged and the
# difference it asserts is the second ordinal, not the projection's shape.
TABLE_CELLS: list[tuple[int, int, str, str | None, str | None]] = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "ER-3", None, "ER-3"),
    (0, 2, "ER-2", None, "ER-2"),
    (1, 0, "Multi-unit dwelling use", "Multi-unit dwelling use", None),
    (1, 1, f"{GRAIN_ELEVATOR} {CONVERSION_ROUTE}", "Multi-unit dwelling use", "ER-3"),
    (1, 2, GRAIN_ELEVATOR, "Multi-unit dwelling use", "ER-2"),
    (2, 0, "Single-unit dwelling use", "Single-unit dwelling use", None),
    (2, 1, DOT, "Single-unit dwelling use", "ER-3"),
    (2, 2, DOT, "Single-unit dwelling use", "ER-2"),
]

SECTION_PATH = "Part V > 233"
SECTION_TEXT = (
    "233 (1) Excluding any structure below 0.6 metres above the average "
    "finished grade, a low-density dwelling use, or any public use, no "
    "building shall exceed:"
)
CLAUSE_WIDTH = (
    "(a) except as provided in Subsection 233(2) or 233(3), a building width "
    "of 20.0 metres; and"
)
CLAUSE_DEPTH = "(b) a building depth of 30.0 metres."
TOWNHOUSE_LIST_ITEM = (
    "The maximum building width of a townhouse block is 64.0 metres and the "
    "maximum number of permitted townhouse units in a townhouse block located "
    "in a ER-3 Zone is eight."
)
#: s.233(3). The stem the ingest quoted into a path segment and never pathed.
ADDITION_STEM = (
    "An addition to an existing main building shall only be permitted in the "
    "rear yard but shall not exceed the building width or footprint of the "
    "existing main building, if the addition causes the main building to "
    "contain"
)
ADDITION_ER2 = "(a) more than 2 dwelling units in an ER-2 zone; or"
ADDITION_ER3 = "(b) more than 8 dwelling units in an ER-3 zone."

#: Derived, never written out: the ingest computes the segment from the stem's
#: text and so does this. See the module docstring.
ADDITION_SEGMENT = context_segment(ADDITION_STEM)
ADDITION_CONTAINER_PATH = f"{SECTION_PATH} > {ADDITION_SEGMENT}"


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601523)
        )

    document = _get_or_create_document(session)
    parent = _ensure_table_parent(session, document.id)
    table = _ensure_table(session, document.id, parent.id)
    _ensure_footnote_legends(session, document.id)
    section_id = _ensure_addition_provision(session, document.id)
    session.flush()
    return {
        "document_id": document.id,
        "table_id": table.id,
        "fragment_id": parent.id,
        "section_id": section_id,
    }


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(
            select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
        )
        .scalars()
        .first()
    )
    if document is not None:
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/abs523_footnote_retention.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=260,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _upsert_fragment(
    session,
    document_id: int,
    *,
    reading_order: int,
    text: str,
    fragment_type: FragmentType,
    citation_label: str | None = None,
    citation_path: str | None = None,
    parent_fragment_id: int | None = None,
    page: int = TABLE_PAGE,
) -> SourceFragment:
    """Converge one fragment, keyed on its reading-order slot in this document.

    Keyed on reading order rather than on ``citation_path`` because half of
    these fragments have no citation path — which is the entire point of the
    fixture.
    """
    fragment = (
        session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.reading_order_start == reading_order,
            )
        )
        .scalars()
        .first()
    )
    if fragment is None:
        fragment = SourceFragment(
            document_id=document_id,
            reading_order_start=reading_order,
            reading_order_end=reading_order,
            source_block_ids_json=[],
            metadata_json={},
        )
        session.add(fragment)
    fragment.fragment_type = fragment_type
    fragment.citation_label = citation_label
    fragment.citation_path = citation_path
    fragment.parent_fragment_id = parent_fragment_id
    fragment.page_start = page
    fragment.page_end = page
    fragment.text = text
    fragment.parse_status = ParseStatus.PARSED
    fragment.confidence = 1.0
    session.flush()
    return fragment


def _ensure_table_parent(session, document_id: int) -> SourceFragment:
    return _upsert_fragment(
        session,
        document_id,
        reading_order=100,
        text=PARENT_TEXT,
        fragment_type=FragmentType.SECTION,
        citation_label=PARENT_CITATION_LABEL,
        citation_path=PARENT_CITATION_PATH,
    )


def _ensure_footnote_legends(session, document_id: int) -> None:
    """The legend lines under the table, typed PROSE as the real corpus has them.

    ABS-280: the Regional Centre's legend rows were classified PROSE, not
    FOOTNOTE, and the condition-text matcher keys off the *leading* glyph rather
    than the type for exactly that reason. Typing them FOOTNOTE here would seed
    a corpus kinder than the real one.
    """
    for index, (_ordinal, text) in enumerate(FOOTNOTE_LEGENDS):
        _upsert_fragment(
            session,
            document_id,
            reading_order=110 + index,
            text=text,
            fragment_type=FragmentType.PROSE,
        )


def _ensure_addition_provision(session, document_id: int) -> int:
    """s.233 as the corpus holds it: two pathed clauses, then a stemless limb."""
    heading = _upsert_fragment(
        session,
        document_id,
        reading_order=200,
        text="Maximum Building Dimensions",
        fragment_type=FragmentType.HEADING,
        page=238,
    )
    section = _upsert_fragment(
        session,
        document_id,
        reading_order=201,
        text=SECTION_TEXT,
        fragment_type=FragmentType.SECTION,
        citation_label="233",
        citation_path=SECTION_PATH,
        page=238,
    )
    # Pathed under s.233, parented to the heading — the ABS-521 shape.
    _upsert_fragment(
        session,
        document_id,
        reading_order=202,
        text=CLAUSE_WIDTH,
        fragment_type=FragmentType.CLAUSE,
        citation_label="(a)",
        citation_path=f"{SECTION_PATH} > (a)",
        parent_fragment_id=heading.id,
        page=238,
    )
    depth = _upsert_fragment(
        session,
        document_id,
        reading_order=203,
        text=CLAUSE_DEPTH,
        fragment_type=FragmentType.CLAUSE,
        citation_label="(b)",
        citation_path=f"{SECTION_PATH} > (b)",
        parent_fragment_id=heading.id,
        page=238,
    )
    # Two unpathed list items sit between the section's own clauses and the
    # addition clauses. The second is the stem; the first is here so the stem
    # is not merely the fragment immediately before its clauses.
    townhouse = _upsert_fragment(
        session,
        document_id,
        reading_order=204,
        text=TOWNHOUSE_LIST_ITEM,
        fragment_type=FragmentType.LIST_ITEM,
        parent_fragment_id=depth.id,
        page=238,
    )
    stem = _upsert_fragment(
        session,
        document_id,
        reading_order=205,
        text=ADDITION_STEM,
        fragment_type=FragmentType.LIST_ITEM,
        parent_fragment_id=townhouse.id,
        page=238,
    )
    for offset, (label, text) in enumerate(
        (("(a)", ADDITION_ER2), ("(b)", ADDITION_ER3))
    ):
        _upsert_fragment(
            session,
            document_id,
            reading_order=206 + offset,
            text=text,
            fragment_type=FragmentType.CLAUSE,
            citation_label=label,
            citation_path=f"{ADDITION_CONTAINER_PATH} > {label}",
            parent_fragment_id=heading.id,
            page=238,
        )
    # Returned so a caller can assert the stem is unreachable by path: it has
    # none, which is why completion is the only route to it.
    assert stem.citation_path is None
    return section.id


def _ensure_table(session, document_id: int, parent_fragment_id: int) -> SourceTable:
    table = (
        session.execute(
            select(SourceTable).where(
                SourceTable.document_id == document_id,
                SourceTable.caption == TABLE_CAPTION,
            )
        )
        .scalars()
        .first()
    )
    if table is None:
        table = SourceTable(
            document_id=document_id,
            caption=TABLE_CAPTION,
            page_start=TABLE_PAGE,
            page_end=TABLE_PAGE,
            parse_status=ParseStatus.PARSED,
            parent_fragment_id=parent_fragment_id,
            metadata_json={"parser": "docling", "seed": "e2e-abs523-footnotes"},
        )
        session.add(table)
        session.flush()
    else:
        table.parent_fragment_id = parent_fragment_id
    _ensure_cells(session, table)
    session.flush()
    return table


def _ensure_cells(session, table: SourceTable) -> None:
    wanted = {
        (row, col): (text, row_header, col_header)
        for row, col, text, row_header, col_header in TABLE_CELLS
    }
    for cell in (
        session.execute(
            select(SourceTableCell).where(SourceTableCell.table_id == table.id)
        )
        .scalars()
        .all()
    ):
        position = (cell.row_index, cell.col_index)
        if position not in wanted:
            session.delete(cell)
            continue
        text, row_header, col_header = wanted[position]
        cell.text = text
        cell.row_header_path = row_header
        cell.col_header_path = col_header
        # Cleared so enrichment re-derives the annotation. An inherited
        # single-ordinal ``footnote`` from a pre-ABS-523 run would grade the
        # unfixed code as fixed.
        cell.metadata_json = {}
    session.flush()

    existing = {
        (row, col)
        for row, col in session.execute(
            select(SourceTableCell.row_index, SourceTableCell.col_index).where(
                SourceTableCell.table_id == table.id
            )
        ).all()
    }
    for row_index, col_index, text, row_header, col_header in TABLE_CELLS:
        if (row_index, col_index) in existing:
            continue
        session.add(
            SourceTableCell(
                table_id=table.id,
                row_index=row_index,
                col_index=col_index,
                row_header_path=row_header,
                col_header_path=col_header,
                text=text,
                metadata_json={},
            )
        )


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_abs523_footnote_retention: "
        f"document={ids['document_id']} table={ids['table_id']} "
        f"fragment={ids['fragment_id']} section={ids['section_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
