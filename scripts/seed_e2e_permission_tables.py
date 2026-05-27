"""Seed permission tables (Table 1A / 1B) for the e2e table-retrieval spec.

Creates a Document with two SourceTable rows carrying the caption pattern
the retrieval layer searches for (``Table 1%Permitted uses by zone%``),
each with a small grid of SourceTableCell rows.

Idempotent: re-running is a no-op when the rows already exist.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from layer1.db.base import Document, SourceTable, SourceTableCell, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus


DOCUMENT_FILE_HASH = "e2e-permission-tables-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Regional Centre Land Use By-law"


TABLE_1A_CAPTION = "Table 1A: Permitted uses by zone — Residential"
TABLE_1B_CAPTION = "Table 1B: Permitted uses by zone — Mixed Use"

TABLE_1A_CELLS = [
    # Header row
    (0, 0, "Use", None, "Use"),
    (0, 1, "DD", None, "DD"),
    (0, 2, "DH", None, "DH"),
    (0, 3, "ER-3", None, "ER-3"),
    # Data rows
    (1, 0, "Low-density dwelling use", "Low-density dwelling use", None),
    (1, 1, "●", "Low-density dwelling use", "DD"),
    (1, 2, "●", "Low-density dwelling use", "DH"),
    (1, 3, "●", "Low-density dwelling use", "ER-3"),
    (2, 0, "Multi-unit dwelling use", "Multi-unit dwelling use", None),
    (2, 1, "●", "Multi-unit dwelling use", "DD"),
    (2, 2, "○", "Multi-unit dwelling use", "DH"),
    (2, 3, "●", "Multi-unit dwelling use", "ER-3"),
    (3, 0, "Restaurant use", "Restaurant use", None),
    (3, 1, "○", "Restaurant use", "DD"),
    (3, 2, "●", "Restaurant use", "DH"),
    (3, 3, "○", "Restaurant use", "ER-3"),
]

TABLE_1B_CELLS = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "CEN-2", None, "CEN-2"),
    (0, 2, "COR", None, "COR"),
    (1, 0, "Office use", "Office use", None),
    (1, 1, "●", "Office use", "CEN-2"),
    (1, 2, "●", "Office use", "COR"),
    (2, 0, "Restaurant use", "Restaurant use", None),
    (2, 1, "●", "Restaurant use", "CEN-2"),
    (2, 2, "○", "Restaurant use", "COR"),
]


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text
        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601163))

    document = _get_or_create_document(session)
    table_1a = _ensure_table(session, document.id, TABLE_1A_CAPTION, 42, 43, TABLE_1A_CELLS)
    table_1b = _ensure_table(session, document.id, TABLE_1B_CAPTION, 44, 45, TABLE_1B_CELLS)
    session.flush()
    return {"document_id": document.id, "table_1a_id": table_1a.id, "table_1b_id": table_1b.id}


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
        source_path="e2e/regional_centre_lub.pdf",
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
        return existing
    table = SourceTable(
        document_id=document_id,
        caption=caption,
        page_start=page_start,
        page_end=page_end,
        parse_status=ParseStatus.PARSED,
        metadata_json={"parser": "docling", "seed": "e2e-permission-tables"},
    )
    session.add(table)
    session.flush()
    for row_idx, col_idx, text, row_header, col_header in cell_specs:
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
    session.flush()
    return table


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_permission_tables: "
        f"document={ids['document_id']} "
        f"table_1a={ids['table_1a_id']} table_1b={ids['table_1b_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
