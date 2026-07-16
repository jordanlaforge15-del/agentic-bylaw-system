"""Seed a permission matrix for the ABS-279 permitted-use retrieval e2e spec.

Creates a dedicated Document ("Permitted Use Test By-law") with one
permission-matrix-captioned table whose cells exercise every permission
class the Phase-3 resolver must surface:

* (Restaurant use, DD)  → ``●``  permitted
* (Restaurant use, DH)  → ``③``  conditional, joined to a footnote fragment
* (Restaurant use, COR) → ``""`` blank → not_permitted
* (Office use, *)       → ``●``  permitted (a second clean use row)
* (Multi-unit dwelling use, DD/DH) → ``●`` permitted (ABS-351: the residential
  row a near-miss use term — "Multiple-unit dwelling", "Dwelling unit" — must
  resolve to or be suggested for)

A FOOTNOTE fragment carrying the ③ glyph supplies the condition text the
conditional cell joins to.

A *separate* document from the ABS-277/278 seeds so running enrichment here
(which clears+rebuilds a document's semantic layer) can't disturb their
fixtures.

Idempotent: re-running is a no-op when the rows already exist.
"""
from __future__ import annotations

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

DOCUMENT_FILE_HASH = "e2e-permitted-use-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Permitted Use Test By-law"

TABLE_CAPTION = "Table 1P: Permitted uses by zone — Phase 3"

# (row, col, text, row_header_path, col_header_path)
TABLE_CELLS = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "DD", None, "DD"),
    (0, 2, "DH", None, "DH"),
    (0, 3, "COR", None, "COR"),
    (1, 0, "Restaurant use", "Restaurant use", None),
    (1, 1, "●", "Restaurant use", "DD"),  # permitted
    (1, 2, "③", "Restaurant use", "DH"),  # conditional → footnote 3
    (1, 3, "", "Restaurant use", "COR"),  # blank → not_permitted
    (2, 0, "Office use", "Office use", None),
    (2, 1, "●", "Office use", "DD"),
    (2, 2, "●", "Office use", "DH"),
    (2, 3, "●", "Office use", "COR"),
    # ABS-351: a residential row whose canonical spelling ("Multi-unit dwelling
    # use") differs from the human-style near misses an agent types.
    (3, 0, "Multi-unit dwelling use", "Multi-unit dwelling use", None),
    (3, 1, "●", "Multi-unit dwelling use", "DD"),
    (3, 2, "●", "Multi-unit dwelling use", "DH"),
    (3, 3, "", "Multi-unit dwelling use", "COR"),
]

FOOTNOTE_TEXT = (
    "③ Use is permitted subject to a maximum gross floor area of 100 square "
    "metres and frontage on a collector street."
)


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601165)
        )

    document = _get_or_create_document(session)
    table = _ensure_table(session, document.id, TABLE_CAPTION, 45, 46, TABLE_CELLS)
    _ensure_footnote(session, document.id)
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
        source_path="e2e/permitted_use.pdf",
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
        metadata_json={"parser": "docling", "seed": "e2e-permitted-use"},
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


def _ensure_footnote(session, document_id: int) -> None:
    existing = (
        session.execute(
            select(SourceFragment.id).where(
                SourceFragment.document_id == document_id,
                SourceFragment.fragment_type == FragmentType.FOOTNOTE,
                SourceFragment.text == FOOTNOTE_TEXT,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return
    session.add(
        SourceFragment(
            document_id=document_id,
            fragment_type=FragmentType.FOOTNOTE,
            page_start=46,
            page_end=46,
            reading_order_start=900,
            reading_order_end=900,
            text=FOOTNOTE_TEXT,
            parse_status=ParseStatus.PARSED,
            confidence=0.9,
        )
    )


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_permitted_use: "
        f"document={ids['document_id']} table={ids['table_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
