"""Seed a permission matrix that CAN be cited by name, for the ABS-524 spec.

Every other permission-matrix fixture in the suite (ABS-484, ABS-520) seeds a
table with no parent fragment, so its citation comes back path-less and the
compact projection falls through to the label-and-pages branch. That is a real
corpus shape — captions that were never backfilled — but it is not the one
TC-022 failed on.

On the live Regional Centre corpus Table 1B *does* have a parent fragment, so
its citation carries a ``citation_path`` **and** a ``citation_label``. The
projection used to treat the label as a fallback for a missing path, so a
path-bearing table citation reached the model as::

    {"citation_path": "Part I > [Table 1B]", "backs": ["uses"]}

— the quotable string "Table 1B" nowhere in it, recoverable only by parsing.
The permission it granted was then stated to the user unattributed in 2 of 5
recorded TC-022 runs.

This fixture reproduces that shape: a parented table (``Part I > [Table 1T]``,
label ``Table 1T``, page 48) over a fully-extracted grid, so the spec asserts on
what the model reads rather than on how the grid was repaired. Nothing here is
ragged and nothing is missing — the ABS-483/484/520 gap behaviours have their
own fixtures and this one must not restate them.

A *separate* document from the ABS-277/278/279/483/484/520 seeds so running
enrichment here (which clears and rebuilds a document's semantic layer) cannot
disturb theirs.

Idempotent: re-running converges the fragment, the table's parentage and the
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

DOCUMENT_FILE_HASH = "e2e-abs524-use-attribution-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Use Attribution Test By-law"

TABLE_CAPTION = "Table 1T: Permitted uses by zone — attribution fixture"
TABLE_PAGE = 48

# The parent fragment is the whole point: it is what gives the table's citation
# a path, and a path is what used to suppress the label.
PARENT_CITATION_PATH = "Part I > [Table 1T]"
PARENT_CITATION_LABEL = "Table 1T"
PARENT_TEXT = (
    "Part I — Table 1T: the uses permitted in each residential zone are as "
    "set out in the following table."
)

DOT = "●"  # permitted as-of-right

ZONES = ["ER-3", "ER-2", "COR"]

# (row, col, text, row_header_path, col_header_path). ER-3 and ER-2 are fully
# extracted: the permission each cell states is unambiguous, so the only thing
# under test there is what survives the projection.
#
# COR is header-only — every data cell absent, ABS-484's all-holes column. It
# determines nothing, so it cites nothing, and a block that cites nothing must
# claim no attribution: an empty ``cite_as``, or an instruction to name a source
# that isn't there, is an invitation to invent one.
TABLE_CELLS: list[tuple[int, int, str, str | None, str | None]] = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "ER-3", None, "ER-3"),
    (0, 2, "ER-2", None, "ER-2"),
    (0, 3, "COR", None, "COR"),
    (1, 0, "Townhouse dwelling use", "Townhouse dwelling use", None),
    (1, 1, DOT, "Townhouse dwelling use", "ER-3"),
    (1, 2, "", "Townhouse dwelling use", "ER-2"),  # blank → not_permitted
    (2, 0, "Single-unit dwelling use", "Single-unit dwelling use", None),
    (2, 1, DOT, "Single-unit dwelling use", "ER-3"),
    (2, 2, DOT, "Single-unit dwelling use", "ER-2"),
]


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601524)
        )

    document = _get_or_create_document(session)
    parent = _ensure_parent_fragment(session, document.id)
    table = _ensure_table(session, document.id, parent.id)
    session.flush()
    return {
        "document_id": document.id,
        "table_id": table.id,
        "fragment_id": parent.id,
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
        source_path="e2e/abs524_use_attribution.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=60,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_parent_fragment(session, document_id: int) -> SourceFragment:
    fragment = (
        session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_path == PARENT_CITATION_PATH,
            )
        )
        .scalars()
        .first()
    )
    if fragment is not None:
        fragment.citation_label = PARENT_CITATION_LABEL
        fragment.text = PARENT_TEXT
        session.flush()
        return fragment
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SECTION,
        citation_label=PARENT_CITATION_LABEL,
        citation_path=PARENT_CITATION_PATH,
        page_start=TABLE_PAGE,
        page_end=TABLE_PAGE,
        reading_order_start=100,
        reading_order_end=100,
        text=PARENT_TEXT,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment


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
            metadata_json={"parser": "docling", "seed": "e2e-abs524-use-attribution"},
        )
        session.add(table)
        session.flush()
    else:
        # The parentage IS the fixture — an inherited table without it would
        # quietly grade the old projection as fixed.
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
        "seed_e2e_abs524_use_attribution: "
        f"document={ids['document_id']} table={ids['table_id']} "
        f"fragment={ids['fragment_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
