"""Seed TC-005 permission-matrix fixture for ABS-280 e2e regression.

Creates a dedicated Document ("TC-005 ABS-280 Permitted Use By-law") with a
Table 1A–captioned permission matrix whose cells cover the two TC-005 use-zone
pairs:

* (multi-unit dwelling use, HR-2)  → ``●``  permitted   (TC-005 T3)
* (home occupation use,    HR-2)  → ``""`` blank        → not_permitted (TC-005 T5)
* (home occupation use,    DD)    → ``●``  permitted    (zone-specificity contrast)

A separate document from the ABS-279 seed so running enrichment on it can't
disturb the existing Phase-3 fixtures.

Idempotent: re-running is a no-op when rows already exist.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from layer1.db.base import Document, SourceTable, SourceTableCell, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus

DOCUMENT_FILE_HASH = "e2e-tc005-abs280-permitted-use"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "TC-005 ABS-280 Permitted Use By-law"

TABLE_CAPTION = "Table 1A: Permitted uses by zone — Residential"

# (row, col, text, row_header_path, col_header_path)
TABLE_CELLS = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "HR-2", None, "HR-2"),
    (0, 2, "DD", None, "DD"),
    # multi-unit dwelling — permitted in both zones
    (1, 0, "multi-unit dwelling use", "multi-unit dwelling use", None),
    (1, 1, "●", "multi-unit dwelling use", "HR-2"),
    (1, 2, "●", "multi-unit dwelling use", "DD"),
    # home occupation — NOT permitted in HR-2 (TC-005 T5); permitted in DD
    (2, 0, "home occupation use", "home occupation use", None),
    (2, 1, "", "home occupation use", "HR-2"),   # blank → not_permitted
    (2, 2, "●", "home occupation use", "DD"),    # permitted (zone contrast)
]


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2804601280)
        )

    document = _get_or_create_document(session)
    table = _ensure_table(session, document.id)
    session.flush()
    return {"document_id": document.id, "table_id": table.id}


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(
            select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
        )
        .scalars()
        .first()
    )
    if document is not None:
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/tc005_permitted_use.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=20,
        parser_version="e2e-seed",
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_table(session, document_id: int) -> SourceTable:
    existing = (
        session.execute(
            select(SourceTable).where(
                SourceTable.document_id == document_id,
                SourceTable.caption == TABLE_CAPTION,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        _ensure_cells(session, existing)
        session.flush()
        return existing
    table = SourceTable(
        document_id=document_id,
        caption=TABLE_CAPTION,
        page_start=12,
        page_end=13,
        parse_status=ParseStatus.PARSED,
        metadata_json={"seed": "e2e-tc005-abs280"},
    )
    session.add(table)
    session.flush()
    _ensure_cells(session, table)
    session.flush()
    return table


def _ensure_cells(session, table: SourceTable) -> None:
    existing_positions = {
        (cell.row_index, cell.col_index)
        for cell in session.execute(
            select(SourceTableCell.row_index, SourceTableCell.col_index).where(
                SourceTableCell.table_id == table.id
            )
        ).all()
    }
    for row_idx, col_idx, text, row_header, col_header in TABLE_CELLS:
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
                metadata_json={},
            )
        )


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_tc005_permitted_use: "
        f"document={ids['document_id']} table={ids['table_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
