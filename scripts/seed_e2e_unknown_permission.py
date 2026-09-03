"""Seed a permission matrix with unreadable cells for the ABS-483 e2e spec.

Creates a dedicated Document ("Unknown Permission Test By-law") with one
permission-matrix-captioned table that carries BOTH extraction-failure shapes
alongside the ordinary markers, so the spec can prove they stay distinct:

* (Restaurant use, DD)  → ``●``      permitted
* (Restaurant use, DH)  → **no cell at all** — the row the table parser lost.
  Nothing is seeded at (1, 2); that gap is the fixture.
* (Restaurant use, COR) → ``""``     blank → not_permitted (the symbol
  matrix's own convention — must NOT drift to unknown)
* (Office use, DH)      → ``U+F0AA`` an unmapped private-use glyph: symbol-font
  content this bylaw's profile cannot decode → unknown
* (Office use, DD/COR)  → ``●``      permitted (a clean second row)

A *separate* document from the ABS-277/278/279 seeds so running enrichment here
(which clears+rebuilds a document's semantic layer) can't disturb their
fixtures.

Idempotent: re-running is a no-op when the rows already exist.
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import sys

from sqlalchemy import select

from layer1.db.base import Document, SourceTable, SourceTableCell, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus

DOCUMENT_FILE_HASH = "e2e-unknown-permission-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Unknown Permission Test By-law"

TABLE_CAPTION = "Table 1U: Permitted uses by zone — Unknown state"

# An unmapped private-use codepoint: the shape a *new* symbol font takes when
# its glyph has no entry in the bylaw's permitted/ignored codepoint sets.
UNMAPPED_GLYPH = chr(0xF0AA)

# (row, col, text, row_header_path, col_header_path)
# NOTE the deliberate hole at (1, 2) — see the module docstring.
TABLE_CELLS = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "DD", None, "DD"),
    (0, 2, "DH", None, "DH"),
    (0, 3, "COR", None, "COR"),
    (1, 0, "Restaurant use", "Restaurant use", None),
    (1, 1, "●", "Restaurant use", "DD"),  # permitted
    # (1, 2) intentionally absent → unknown (extraction dropped the cell)
    (1, 3, "", "Restaurant use", "COR"),  # blank → not_permitted
    (2, 0, "Office use", "Office use", None),
    (2, 1, "●", "Office use", "DD"),
    (2, 2, UNMAPPED_GLYPH, "Office use", "DH"),  # undecodable → unknown
    (2, 3, "●", "Office use", "COR"),
]


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601483)
        )

    document = _get_or_create_document(session)
    table = _ensure_table(session, document.id, TABLE_CAPTION, 60, 61, TABLE_CELLS)
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
        source_path="e2e/unknown_permission.pdf",
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
        metadata_json={"parser": "docling", "seed": "e2e-unknown-permission"},
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
    """Converge the grid on ``cell_specs``.

    Unlike the sibling seeds this also DELETES any cell sitting at a position
    the spec list omits: the (1, 2) hole IS the fixture, so a re-seed against a
    persistent e2e DB must not inherit a stray cell there (nor an older text at
    a position whose content changed).
    """
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


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_unknown_permission: "
        f"document={ids['document_id']} table={ids['table_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
