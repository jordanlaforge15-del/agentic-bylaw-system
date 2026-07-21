"""Seed a Mainland-LUB-shaped doc for the ABS-283 permitted-use e2e spec.

The Halifax Mainland LUB does NOT use the Regional Centre symbol-font ●
permission-matrix convention. It lists permitted uses as prose under each
zone's section, e.g.:

    "62EA(1) The following uses shall be permitted in any ICH Zone:
     (1) Single Unit Dwellings
     (2) Open Space Uses
     62EA(2) No person shall in any ICH Zone carry out ... any development for
     any purpose other than ... the uses set out in subsection (1)."

It also carries section/amendment-history tables (section-number rows,
amendment-date + council-case columns) that the structural classifier used to
false-positive as a permission matrix. This seed reproduces both shapes so the
e2e spec can assert:

* the amendment table is NOT classified ``permission_matrix`` (0 matrices), and
* a Mainland ``(use, zone)`` query resolves via the section-prose path.

A *separate* document (distinct ``file_hash``/``bylaw_name``) from the other
e2e seeds, so running enrichment here (which clears+rebuilds a document's
semantic layer) can't disturb their fixtures.

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

DOCUMENT_FILE_HASH = "e2e-mainland-permitted-use-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Mainland Permitted Use Test By-law"

# The prose permitted-use section (the Mainland convention — no symbol dots).
PERMITTED_USE_SECTION_TEXT = (
    "62EA(1) The following uses shall be permitted in any ICH Zone:\n"
    "(1) Single Unit Dwellings\n"
    "(2) Open Space Uses\n"
    "62EA(2) No person shall in any ICH Zone carry out, or cause or permit to "
    "be carried out, any development for any purpose other than one or more of "
    "the uses set out in subsection (1)."
)

# The amendment/section-history table — the false-positive shape. Section-number
# rows, one stray zone-bearing column ("DD"), plus amendment-date and
# council-case columns a permission matrix would never carry.
AMENDMENT_TABLE_CAPTION = "Section amendment history"
AMENDMENT_TABLE_CELLS = [
    (0, 0, "Section", None, "Section"),
    (0, 1, "DD", None, "DD"),
    (0, 2, "Sep 1/20", None, "Sep 1/20"),
    (0, 3, "Case 21162", None, "Case 21162"),
    (1, 0, "14QB(2)", "14QB(2)", None),
    (1, 1, "x", "14QB(2)", "DD"),
    (1, 2, "", "14QB(2)", "Sep 1/20"),
    (1, 3, "", "14QB(2)", "Case 21162"),
    (2, 0, "62(1)", "62(1)", None),
    (2, 1, "", "62(1)", "DD"),
    (2, 2, "x", "62(1)", "Sep 1/20"),
    (2, 3, "", "62(1)", "Case 21162"),
    (3, 0, "28AO(1)", "28AO(1)", None),
    (3, 1, "", "28AO(1)", "DD"),
    (3, 2, "", "28AO(1)", "Sep 1/20"),
    (3, 3, "x", "28AO(1)", "Case 21162"),
]


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601166)
        )

    document = _get_or_create_document(session)
    _ensure_permitted_use_section(session, document.id)
    table = _ensure_table(
        session,
        document.id,
        AMENDMENT_TABLE_CAPTION,
        199,
        199,
        AMENDMENT_TABLE_CELLS,
    )
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
        # Converge the publish flag on re-seed: rows created before
        # ABS-413 (or left disabled by the migration backfill) must
        # still end up retrieval-enabled in the persistent e2e DB.
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/mainland_permitted_use.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=400,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_permitted_use_section(session, document_id: int) -> None:
    existing = (
        session.execute(
            select(SourceFragment.id).where(
                SourceFragment.document_id == document_id,
                SourceFragment.fragment_type == FragmentType.PROSE,
                SourceFragment.text == PERMITTED_USE_SECTION_TEXT,
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
            fragment_type=FragmentType.PROSE,
            citation_path="Section 62EA(1)",
            citation_label="62EA(1)",
            page_start=133,
            page_end=133,
            reading_order_start=600,
            reading_order_end=600,
            text=PERMITTED_USE_SECTION_TEXT,
            parse_status=ParseStatus.PARSED,
            confidence=0.9,
        )
    )


def _ensure_table(
    session,
    document_id: int,
    caption: str,
    page_start: int,
    page_end: int,
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
        metadata_json={"parser": "docling", "seed": "e2e-mainland-permitted-use"},
    )
    session.add(table)
    session.flush()
    _ensure_cells(session, table, cell_specs)
    session.flush()
    return table


def _ensure_cells(
    session,
    table: SourceTable,
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
        "seed_e2e_mainland_permitted_use: "
        f"document={ids['document_id']} table={ids['table_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
