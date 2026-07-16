"""Seed a permission matrix for the ABS-278 axis-binding e2e spec.

Creates a dedicated Document ("Axis Binding Test By-law") with one
permission-matrix-captioned table whose:

* columns 1-2 carry clean zone headers (DD, DH) — exercises straight axis
  binding (AC1);
* column 3's header is a positional index ("3") and the zone code "COR" leaks
  into one of its data cells — exercises header-bleed correction (AC3);
* rows are use phrases (Restaurant use, Office use, Cluster housing use) —
  exercises row→use binding (AC2);
* a trailing "General provisions" row + the positional header exercise the FR5
  unmapped-label logging path.

A *separate* document from the ABS-277 seed so running enrichment here (which
clears+rebuilds a document's semantic layer) can't disturb the marker-recovery
spec's fixtures.

Idempotent: re-running is a no-op when the rows already exist.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from layer1.db.base import Document, SourceTable, SourceTableCell, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus

DOCUMENT_FILE_HASH = "e2e-axis-binding-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Axis Binding Test By-law"

TABLE_CAPTION = "Table 1Z: Permitted uses by zone — Axis Binding"

# (row, col, text, row_header_path, col_header_path)
TABLE_CELLS = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "DD", None, "DD"),
    (0, 2, "DH", None, "DH"),
    (0, 3, "3", None, "3"),  # positional header — no zone code
    (1, 0, "Restaurant use", "Restaurant use", None),
    (1, 1, "●", "Restaurant use", "DD"),
    (1, 2, "●", "Restaurant use", "DH"),
    (1, 3, "●", "Restaurant use", "3"),
    (2, 0, "Office use", "Office use", None),
    (2, 1, "●", "Office use", "DD"),
    (2, 2, "●", "Office use", "DH"),
    # Header-bleed: a bare zone code sitting in a data cell of the positional
    # column. The enrichment pass must re-attribute "COR" to column 3.
    (2, 3, "COR", "Office use", "3"),
    (3, 0, "Cluster housing use", "Cluster housing use", None),
    (3, 1, "●", "Cluster housing use", "DD"),
    (3, 2, "③", "Cluster housing use", "DH"),
    (3, 3, "●", "Cluster housing use", "3"),
    # Unmapped row label (neither use nor section) — FR5 logging.
    (4, 0, "General provisions", "General provisions", None),
]


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601164)
        )

    document = _get_or_create_document(session)
    table = _ensure_table(session, document.id, TABLE_CAPTION, 50, 51, TABLE_CELLS)
    session.flush()
    return {"document_id": document.id, "table_id": table.id}


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH))
        .scalars()
        .first()
    )
    if document is not None:
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/axis_binding.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=80,
        parser_version="e2e-seed",
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
        metadata_json={"parser": "docling", "seed": "e2e-axis-binding"},
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


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_axis_binding: "
        f"document={ids['document_id']} table={ids['table_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
