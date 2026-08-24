"""Seed a ragged, geometry-bearing permission matrix for the ABS-520 e2e spec.

Creates a dedicated Document ("Ragged Grid Test By-law") whose Table 1B-shaped
permission matrix reproduces the two defects that live side by side on page 48
of the Regional Centre LUB — and which must be treated *differently*:

1. **Ragged rows.** The parser stores a cell only where a text run landed, so a
   blank cell (the by-law's "not permitted") is simply absent:

       Townhouse dwelling use | ⑮

   ER-2, ER-1 and CH-1 are blank in the by-law and missing from the grid. Those
   are safe to materialize, and (Townhouse dwelling use, ER-2) must come back
   ``not_permitted`` with a citation instead of "undetermined".

2. **A row the parser actually lost.** "Cluster housing use" has no label cell
   at all, and its ● dots were absorbed into the following section-header row —
   they sit in a y band that overlaps no row label. That is a genuine
   extraction gap, so the rows around it must be refused and keep their ABS-483
   ``unknown``. Filling them would fabricate a prohibition.

Geometry is the fixture. Every cell carries the ``bbox_json`` the real parser
stores, because the repair is allowed to fill a cell only when the geometry
shows the row lost nothing — and the ABS-484 fixture, which carries no bboxes
at all, must therefore keep every one of its holes.

A *separate* document from the ABS-277/278/279/483/484 seeds so running
enrichment here (which clears and rebuilds a document's semantic layer) cannot
disturb theirs.

Idempotent: re-running converges the grid, deleting any cell that drifted into
a hole position — against a persistent e2e DB an inherited cell would quietly
turn the regression green.
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

DOCUMENT_FILE_HASH = "e2e-ragged-permission-grid-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Ragged Grid Test By-law"

TABLE_CAPTION = "Table 1R: Permitted uses by zone — ragged grid"

DOT = chr(0xF098)  # symbol-font ● "permitted as-of-right"
FOOTNOTE_15 = "⑮"  # ⑮ conditional

ZONES = ["ER-3", "ER-2", "ER-1", "CH-1"]

# Page geometry, matching the real Table 1B on page 48: an 11pt row pitch, ~50pt
# column pitch, and markers whose box sits ~1pt above the row label's.
_ROW_PITCH = 11.0
_ROW_HEIGHT = 8.0
_FIRST_ROW_TOP = 104.0
_LABEL_X0, _LABEL_X1 = 77.0, 190.0
_COL_X0 = 306.0
_COL_PITCH = 50.0


def _label_bbox(printed_row: int) -> dict:
    top = _FIRST_ROW_TOP + printed_row * _ROW_PITCH
    return {"x0": _LABEL_X0, "x1": _LABEL_X1, "y0": top, "y1": top + _ROW_HEIGHT}


def _marker_bbox(printed_row: int, col_index: int) -> dict:
    top = _FIRST_ROW_TOP + printed_row * _ROW_PITCH - 1.0
    left = _COL_X0 + (col_index - 1) * _COL_PITCH
    return {"x0": left, "x1": left + 9.0, "y0": top, "y1": top + _ROW_HEIGHT}


# (row_index, col_index, text, printed_row) — the printed row is what the
# geometry says, and it is NOT always the grid row: that divergence is the
# second defect. Every (row, col) absent from this list is a cell the parser
# never stored.
def _cell_specs() -> list[tuple[int, int, str, dict]]:
    specs: list[tuple[int, int, str, dict]] = [
        (0, 0, "Residential", _label_bbox(0)),
    ]
    for index, zone in enumerate(ZONES, start=1):
        specs.append((0, index, zone, _marker_bbox(0, index)))

    # Row 1 — fully extracted, nothing to fill.
    specs.append((1, 0, "Single-unit dwelling use", _label_bbox(1)))
    for index in range(1, len(ZONES) + 1):
        specs.append((1, index, DOT, _marker_bbox(1, index)))

    # Row 2 — THE defect: one conditional marker, three dropped blanks.
    specs.append((2, 0, "Townhouse dwelling use", _label_bbox(2)))
    specs.append((2, 1, FOOTNOTE_15, _marker_bbox(2, 1)))

    # Row 3 — a stored blank beside a stored dot: already answerable, and it
    # must not be double-counted as something to materialize.
    specs.append((3, 0, "Backyard suite use", _label_bbox(3)))
    specs.append((3, 1, DOT, _marker_bbox(3, 1)))
    specs.append((3, 2, "", _marker_bbox(3, 2)))

    # Printed row 4 is "Cluster housing use" — the row the parser LOST. No
    # label cell exists for it; its two dots were attached to the section
    # header below, where their y band matches nothing.
    specs.append((4, 0, "Commercial", _label_bbox(5)))
    specs.append((4, 1, DOT, _marker_bbox(4, 1)))
    specs.append((4, 2, DOT, _marker_bbox(4, 2)))

    # Row 5 — a normal use row after the damage. Its distance from the section
    # header is one printed line, so the repair may resume here.
    specs.append((5, 0, "Office use", _label_bbox(6)))
    specs.append((5, 1, DOT, _marker_bbox(6, 1)))

    return specs


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601520)
        )

    document = _get_or_create_document(session)
    table = _ensure_table(session, document.id, TABLE_CAPTION, 48, 48, _cell_specs())
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
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/ragged_permission_grid.pdf",
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


def _ensure_table(
    session,
    document_id: int,
    caption: str,
    page_start: int,
    page_end: int,
    cell_specs: list[tuple[int, int, str, dict]],
) -> SourceTable:
    table = (
        session.execute(
            select(SourceTable).where(
                SourceTable.document_id == document_id,
                SourceTable.caption == caption,
            )
        )
        .scalars()
        .first()
    )
    if table is None:
        table = SourceTable(
            document_id=document_id,
            caption=caption,
            page_start=page_start,
            page_end=page_end,
            parse_status=ParseStatus.PARSED,
            metadata_json={"parser": "docling", "seed": "e2e-ragged-permission-grid"},
        )
        session.add(table)
        session.flush()
    _ensure_cells(session, table, cell_specs)
    session.flush()
    return table


def _ensure_cells(
    session, table: SourceTable, cell_specs: list[tuple[int, int, str, dict]]
) -> None:
    """Converge the grid on ``cell_specs``, deleting anything at a hole.

    Deleting is the important half: a previous run's materialized cells sit in
    exactly the positions this fixture needs empty, so leaving them would make
    the regression pass without the repair ever running.
    """
    wanted = {(row, col): (text, bbox) for row, col, text, bbox in cell_specs}
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
        text, bbox = wanted[position]
        cell.text = text
        cell.bbox_json = bbox
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
    for row_index, col_index, text, bbox in cell_specs:
        if (row_index, col_index) in existing:
            continue
        session.add(
            SourceTableCell(
                table_id=table.id,
                row_index=row_index,
                col_index=col_index,
                row_header_path=None,
                col_header_path=None,
                text=text,
                bbox_json=bbox,
                metadata_json={},
            )
        )


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_ragged_permission_grid: "
        f"document={ids['document_id']} table={ids['table_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
