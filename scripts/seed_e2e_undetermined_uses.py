"""Seed a matrix-with-holes zone corpus for the ABS-484 e2e spec.

Creates a dedicated Document ("Undetermined Uses Test By-law") whose permission
matrix carries UNKNOWN cells beside ordinary ones, so a zone profile built over
it has to distinguish "the bylaw prohibits this" from "we could not read this":

* column ``DD``  — one hole (Restaurant use), the rest determinate. The hole
  must surface under ``uses.undetermined``, never under ``not_permitted``.
* column ``DH``  — the same hole, but a P/N prose row for DH states the
  permission the lost cell would have carried. The prose fallback has to
  resolve it, so Restaurant use lands in ``permitted`` with the prose citation.
* column ``COR`` — header only, every data cell missing. Nothing determinate is
  produced, so the profile must claim no confidence and cite nothing for uses.

The holes ARE the fixture: no cell is seeded at those positions, which is the
extraction failure ABS-483 made producible (``permission_marker='unknown'``).

A *separate* document from the ABS-277/278/279/483 seeds so running enrichment
here (which clears+rebuilds a document's semantic layer) can't disturb theirs.

Idempotent: re-running converges the grid, including re-deleting any cell that
drifted into a hole position — against a persistent e2e DB an inherited cell
would quietly turn the regression green.
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

DOCUMENT_FILE_HASH = "e2e-undetermined-uses-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Undetermined Uses Test By-law"

TABLE_CAPTION = "Table 1V: Permitted uses by zone — Undetermined state"

# (row, col, text, row_header_path, col_header_path)
# NOTE the deliberate holes — see the module docstring. Every (row, col) absent
# from this list is a cell the parser lost.
TABLE_CELLS = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "DD", None, "DD"),
    (0, 2, "DH", None, "DH"),
    (0, 3, "COR", None, "COR"),
    (1, 0, "Restaurant use", "Restaurant use", None),
    # (1, 1) and (1, 2) intentionally absent → unknown in DD and DH
    (2, 0, "Office use", "Office use", None),
    (2, 1, "●", "Office use", "DD"),
    (2, 2, "●", "Office use", "DH"),
    (3, 0, "Multi-unit dwelling use", "Multi-unit dwelling use", None),
    (3, 1, "", "Multi-unit dwelling use", "DD"),  # blank → not_permitted
    (3, 2, "", "Multi-unit dwelling use", "DH"),
    (4, 0, "Daycare use", "Daycare use", None),
    (4, 1, "●", "Daycare use", "DD"),
    (4, 2, "●", "Daycare use", "DH"),
    # The whole COR column below the header is absent.
]

# The other reading of the same permissions: a P/N prose row, present for DH
# only. This is what the matrix path must consult for DH's undetermined use —
# and what DD, which has no such row, correctly fails to find.
PROSE_ZONE = "DH"
PROSE_CITATION_PATH = "Table 1B > DH > Use Permissions"
PROSE_TEXT = "Use Permissions DH permitted uses: restaurant P daycare P"


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601484)
        )

    document = _get_or_create_document(session)
    table = _ensure_table(session, document.id, TABLE_CAPTION, 70, 71, TABLE_CELLS)
    _ensure_prose(session, document.id)
    session.flush()
    return {"document_id": document.id, "table_id": table.id}


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH))
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
        source_path="e2e/undetermined_uses.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=80,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_table(
    session, document_id: int, caption: str, page_start: int, page_end: int,
    cell_specs: list[tuple[int, int, str, str | None, str | None]],
) -> SourceTable:
    existing = (
        session.execute(
            select(SourceTable).where(
                SourceTable.document_id == document_id,
                SourceTable.caption == caption,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        _ensure_cells(session, existing, cell_specs)
        session.flush()
        return existing
    table = SourceTable(
        document_id=document_id,
        caption=caption,
        page_start=page_start,
        page_end=page_end,
        parse_status=ParseStatus.PARSED,
        metadata_json={"parser": "docling", "seed": "e2e-undetermined-uses"},
    )
    session.add(table)
    session.flush()
    _ensure_cells(session, table, cell_specs)
    session.flush()
    return table


def _ensure_cells(
    session, table: SourceTable,
    cell_specs: list[tuple[int, int, str, str | None, str | None]],
) -> None:
    """Converge the grid on ``cell_specs``, deleting anything at a hole."""
    wanted = {(row, col): text for row, col, text, _rh, _ch in cell_specs}
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
        elif cell.text != wanted[position]:
            cell.text = wanted[position]
    session.flush()

    existing_positions = {
        (cell.row_index, cell.col_index)
        for cell in session.execute(
            select(SourceTableCell.row_index, SourceTableCell.col_index).where(
                SourceTableCell.table_id == table.id
            )
        ).all()
    }
    for row_idx, col_idx, text, row_header, col_header in cell_specs:
        if (row_idx, col_idx) in existing_positions:
            continue
        session.add(
            SourceTableCell(
                table_id=table.id,
                row_index=row_idx,
                col_index=col_idx,
                row_header_path=row_header,
                col_header_path=col_header,
                text=text,
            )
        )


def _ensure_prose(session, document_id: int) -> SourceFragment:
    existing = (
        session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_path == PROSE_CITATION_PATH,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        existing.text = PROSE_TEXT
        session.flush()
        return existing
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SECTION,
        citation_label=PROSE_ZONE,
        citation_path=PROSE_CITATION_PATH,
        page_start=72,
        page_end=72,
        reading_order_start=500,
        reading_order_end=500,
        text=PROSE_TEXT,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_undetermined_uses: "
        f"document={ids['document_id']} table={ids['table_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
