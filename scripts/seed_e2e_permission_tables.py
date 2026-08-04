"""Seed permission tables (Table 1A / 1B) for the e2e table-retrieval spec.

Creates a Document with two SourceTable rows carrying the caption pattern
the retrieval layer searches for (``Table 1%Permitted uses by zone%``),
each with a small grid of SourceTableCell rows.

Idempotent: re-running is a no-op when the rows already exist.
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import sys

from sqlalchemy import select

from layer1.db.base import (
    Document,
    SourceTable,
    SourceTableCell,
    TableSemanticProfile,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.semantic.permission_markers import (
    PERMISSION_MATRIX_PROFILE,
    annotate_permission_matrix_table,
)

# ABS-277: the authoritative "permitted" dot in the real bylaw is the embedded
# symbol-font ●, stored as a Private Use Area codepoint (U+F098) that reads as
# blank downstream. Seed it verbatim (plus a U+F020 symbol-space padding
# example and a circled-number conditional) so the e2e exercises the recovery
# into metadata_json.permission_marker.
PUA_DOT = ""  # symbol-font ● "permitted as-of-right"
PUA_SPACE = ""  # symbol-font space (padding)


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
    # ABS-277: row whose markers come from the symbol font. DD = U+F098 dot
    # (permitted), DH = circled-three conditional (footnote 3), ER-3 = empty
    # (not permitted). These are the cells the recovery pass must classify.
    (4, 0, "Home occupation use", "Home occupation use", None),
    (4, 1, PUA_SPACE + PUA_DOT + PUA_SPACE, "Home occupation use", "DD"),
    (4, 2, "③", "Home occupation use", "DH"),
    (4, 3, "", "Home occupation use", "ER-3"),
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
    # ABS-281: detection keys off the permission_matrix semantic profile, not the
    # caption. Ensure each seeded table carries the profile the structural
    # classifier would have written, then recover markers.
    # ABS-277: marker recovery into metadata_json.permission_marker. Idempotent,
    # so this runs every seed (including when the tables already exist on a
    # persisted layer1_test DB). Expire the cells relationship first so it
    # reloads every row (cells are added via session.add, not append).
    for table in (table_1a, table_1b):
        _ensure_permission_matrix_profile(session, table)
        session.expire(table, ["cells", "semantic_profiles"])
        annotate_permission_matrix_table(table)
    session.flush()
    return {"document_id": document.id, "table_1a_id": table_1a.id, "table_1b_id": table_1b.id}


def _ensure_permission_matrix_profile(session, table: SourceTable) -> None:
    """Attach a ``permission_matrix`` profile to ``table`` if absent (idempotent)."""
    existing = (
        session.execute(
            select(TableSemanticProfile.id).where(
                TableSemanticProfile.table_id == table.id,
                TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return
    session.add(
        TableSemanticProfile(
            table_id=table.id,
            profile_type=PERMISSION_MATRIX_PROFILE,
            row_axis_type="use",
            column_axis_type="zone",
            value_type="permission_marker",
            confidence=0.9,
            review_status="auto_accepted",
            metadata_json={"seed": "e2e-permission-tables"},
        )
    )
    session.flush()


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH))
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
        source_path="e2e/regional_centre_lub.pdf",
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
        # Table already seeded (layer1_test persists across runs); make sure
        # any newly added cell specs (e.g. the ABS-277 symbol-font row) are
        # present without duplicating existing ones.
        _ensure_cells(session, existing, cell_specs)
        session.flush()
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
        "seed_e2e_permission_tables: "
        f"document={ids['document_id']} "
        f"table_1a={ids['table_1a_id']} table_1b={ids['table_1b_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
