"""Seed TC-005 permission-matrix fixture for ABS-280 e2e regression.

Creates a dedicated Document ("TC-005 ABS-280 Permitted Use By-law") with a
Table 1A–captioned permission matrix whose cells cover the TC-005 use-zone
pairs:

* (multi-unit dwelling use, HR-2)  → ``●``  permitted    (TC-005 T3)
* (home occupation use,    HR-2)  → ``⑮`` conditional    (TC-005 T5; footnote 15)
* (home occupation use,    DD)    → ``●``  permitted     (zone-specificity contrast)

Ground truth: per doc 4 Table 1A, home occupation in HR-2 is **conditional** —
the cell carries footnote ⑮ ("Use is permitted, except within the Halifax Grain
Elevator Special Area..."), not a blank. The footnote legend is seeded as a
PROSE fragment (mirroring the real, mis-typed ingest) so the resolver's
type-agnostic legend match populates ``condition_text`` (AC2).

A separate document from the ABS-279 seed so running enrichment on it can't
disturb the existing Phase-3 fixtures.

Idempotent: re-running is a no-op when rows already exist.
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

DOCUMENT_FILE_HASH = "e2e-tc005-abs280-permitted-use"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "TC-005 ABS-280 Permitted Use By-law"

TABLE_CAPTION = "Table 1A: Permitted uses by zone — Residential"

# ● = permitted marker; ⑮ = circled-15 conditional footnote (U+246E).
PERMITTED = "●"
COND15 = "⑮"

# Footnote-15 legend; PROSE-typed to mirror the real Regional Centre ingest, so
# the resolver's leading-glyph legend match (not a FOOTNOTE-type filter) is what
# surfaces the condition text. Carries the Halifax Grain Elevator carve-out (AC2).
FOOTNOTE_LEGEND = (
    f"{COND15} Use is permitted, except within the Halifax Grain Elevator (HGE) "
    "Special Area, as shown on Schedule 3F."
)
FOOTNOTE_PAGE = 14

# (row, col, text, row_header_path, col_header_path)
TABLE_CELLS = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "HR-2", None, "HR-2"),
    (0, 2, "DD", None, "DD"),
    # multi-unit dwelling — permitted in both zones
    (1, 0, "multi-unit dwelling use", "multi-unit dwelling use", None),
    (1, 1, PERMITTED, "multi-unit dwelling use", "HR-2"),
    (1, 2, PERMITTED, "multi-unit dwelling use", "DD"),
    # home occupation — CONDITIONAL in HR-2 (TC-005 T5, footnote ⑮); permitted in DD
    (2, 0, "home occupation use", "home occupation use", None),
    (2, 1, COND15, "home occupation use", "HR-2"),   # ⑮ → conditional
    (2, 2, PERMITTED, "home occupation use", "DD"),  # permitted (zone contrast)
]


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2804601280)
        )

    document = _get_or_create_document(session)
    table = _ensure_table(session, document.id)
    _ensure_footnote_legend(session, document.id)
    session.flush()
    return {"document_id": document.id, "table_id": table.id}


def _ensure_footnote_legend(session, document_id: int) -> None:
    """Seed the footnote ⑮ legend as a PROSE fragment (idempotent)."""
    existing = (
        session.execute(
            select(SourceFragment.id).where(
                SourceFragment.document_id == document_id,
                SourceFragment.text == FOOTNOTE_LEGEND,
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
            page_start=FOOTNOTE_PAGE,
            page_end=FOOTNOTE_PAGE,
            text=FOOTNOTE_LEGEND,
            parse_status=ParseStatus.PARSED,
        )
    )


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
        source_path="e2e/tc005_permitted_use.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=20,
        parser_version="e2e-seed",
        retrieval_enabled=True,
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
