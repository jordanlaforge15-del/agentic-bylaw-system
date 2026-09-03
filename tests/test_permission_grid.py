"""Unit coverage for the ragged-permission-matrix repair (ABS-520).

The defect: the PDF parser stores a ``source_table_cell`` only where a text run
landed, so a blank cell — which is how a symbol matrix spells "not permitted" —
is simply absent. Retrieval reads the absence as ``unknown`` and reports a real
prohibition as "could not be extracted".

The repair materializes those blanks, and the whole question is *when it is
allowed to*. These tests pin both halves:

* the ragged row is filled and resolves to ``not_permitted``;
* every way the geometry can show content was lost — column drift, an orphaned
  marker, a dropped row label, a reprinted header, no bboxes at all — refuses
  the row and leaves ABS-483's ``unknown`` in place.

The geometry in ``_grid`` mirrors the real Table 1B page 48 layout: rows on a
~11pt pitch, columns ~50pt apart, markers sitting ~1pt above their label's box.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import Document, SourceTable, SourceTableCell
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics, resolve_permission_cell
from layer1.semantic.permission_grid import (
    GRID_FILL_ABSENT_CELL,
    GRID_FILL_KEY,
    REASON_COLUMN_DRIFT,
    REASON_FOREIGN_CONTENT,
    REASON_NO_GEOMETRY,
    REASON_ORPHAN_CELL,
    REASON_ROW_PITCH_GAP,
    REASON_UNLABELLED_ROW,
    audit_permission_grid,
    densify_permission_matrix,
)

DOT = chr(0xF098)  # symbol-font ● "permitted as-of-right"

ROW_PITCH = 11.0
ROW_HEIGHT = 8.0
LABEL_X0, LABEL_X1 = 77.0, 190.0
COL_X0 = 300.0
COL_PITCH = 50.0


class _Cell:
    """A ``SourceTableCell`` stand-in: the audit only reads these four fields."""

    def __init__(self, row_index, col_index, text, bbox_json=None, metadata_json=None):
        self.row_index = row_index
        self.col_index = col_index
        self.text = text
        self.bbox_json = bbox_json
        self.metadata_json = metadata_json or {}


def _bbox(x0: float, x1: float, y0: float, y1: float) -> dict:
    return {"x0": x0, "x1": x1, "y0": y0, "y1": y1}


def _label_bbox(row: int) -> dict:
    top = 100.0 + row * ROW_PITCH
    return _bbox(LABEL_X0, LABEL_X1, top, top + ROW_HEIGHT)


def _marker_bbox(row: int, col: int) -> dict:
    top = 100.0 + row * ROW_PITCH - 1.0
    left = COL_X0 + (col - 1) * COL_PITCH
    return _bbox(left, left + 9.0, top, top + ROW_HEIGHT)


def _grid(rows: list[tuple[str, dict[int, str]]], *, columns: int = 4) -> list[_Cell]:
    """Build a ragged grid: ``rows`` gives each label and only its *present* cells.

    Row 0 is the column-header row, so a caller's rows start at index 1 — the
    same shape ``annotate_value_cells`` and the axis binder assume.
    """
    cells = [_Cell(0, 0, "Residential", _label_bbox(0))]
    for col in range(1, columns + 1):
        cells.append(_Cell(0, col, f"Z-{col}", _marker_bbox(0, col)))
    for offset, (label, present) in enumerate(rows):
        row = offset + 1
        cells.append(_Cell(row, 0, label, _label_bbox(row)))
        for col, text in present.items():
            cells.append(_Cell(row, col, text, _marker_bbox(row, col)))
    return cells


# ---------------------------------------------------------------------------
# The defect, and the repair
# ---------------------------------------------------------------------------


def test_ragged_row_is_filled_across_its_missing_columns():
    # Exactly Table 1B page 48: one conditional marker, four dropped blanks.
    cells = _grid([("Townhouse dwelling use", {1: "⑮"})])

    audit = audit_permission_grid(cells)

    assert audit.table_reason is None
    assert audit.sound_row_indices == [1]
    assert audit.gaps == [(1, 2), (1, 3), (1, 4)]
    assert audit.refused == []


def test_a_present_blank_cell_is_not_a_gap():
    # A blank the parser *did* store already classifies as not_permitted, so it
    # must not be double-counted as something to materialize.
    cells = _grid([("Townhouse dwelling use", {1: "⑮", 2: "", 3: "", 4: ""})])

    assert audit_permission_grid(cells).gaps == []


def test_fill_is_idempotent_and_keeps_the_geometry_readable():
    """A second pass must find nothing — and must not read as ungeometried.

    Filled cells carry no bbox. Were they fed back into the geometry checks the
    table would refuse itself on the next audit, and the integrity guard would
    report a repaired corpus as broken.
    """
    cells = _grid([("Townhouse dwelling use", {1: "⑮"})])
    filled = [
        _Cell(row, col, "", None, {GRID_FILL_KEY: GRID_FILL_ABSENT_CELL})
        for row, col in audit_permission_grid(cells).gaps
    ]

    second = audit_permission_grid(cells + filled)

    assert second.table_reason is None
    assert second.gaps == []
    assert second.sound_row_indices == [1]


# ---------------------------------------------------------------------------
# Every way the geometry refuses a row
# ---------------------------------------------------------------------------


def test_column_drift_refuses_the_whole_table():
    cells = _grid([("Townhouse dwelling use", {1: DOT}), ("Office use", {2: DOT})])
    # Push the row-2 marker back under column 1's band: the two columns' x
    # ranges now overlap, so no cell's position can be trusted.
    for cell in cells:
        if (cell.row_index, cell.col_index) == (2, 2):
            cell.bbox_json = _marker_bbox(2, 1)

    audit = audit_permission_grid(cells)

    assert audit.table_reason == REASON_COLUMN_DRIFT
    assert audit.gaps == []
    assert audit.refused, "the residue is reported, not silently dropped"


def test_an_orphaned_marker_refuses_the_rows_bracketing_it():
    """The real Table 1B failure: a dropped row whose dots landed elsewhere.

    "Cluster housing use" is missing from page 48 and its two ● dots were
    attached to the following section-header row. The dots' y-band betrays it.
    """
    cells = _grid(
        [
            ("Model suite use", {}),
            ("Commercial", {1: DOT}),
            ("Broadcast and production studio use", {}),
        ]
    )
    # Sit the row-2 marker in the empty band between rows 1 and 2 — where the
    # dropped row's label would have been.
    for cell in cells:
        if (cell.row_index, cell.col_index) == (2, 1):
            cell.bbox_json = _bbox(COL_X0, COL_X0 + 9.0, 105.5, 113.5)

    audit = audit_permission_grid(cells)

    assert audit.row_reasons[1] == REASON_ORPHAN_CELL
    assert audit.row_reasons[2] == REASON_ORPHAN_CELL
    # The row past the damage is still sound: the refusal is local.
    assert 3 in audit.sound_row_indices


def test_a_row_pitch_gap_refuses_both_neighbours():
    cells = _grid([("Model suite use", {}), ("Cluster housing use", {})])
    # Drop the second label a full row lower: a label went missing between them.
    for cell in cells:
        if (cell.row_index, cell.col_index) == (2, 0):
            cell.bbox_json = _label_bbox(3)

    audit = audit_permission_grid(cells)

    assert audit.row_reasons[1] == REASON_ROW_PITCH_GAP
    assert audit.row_reasons[2] == REASON_ROW_PITCH_GAP
    assert audit.gaps == []


def test_a_reprinted_column_header_is_not_a_data_row():
    # Table 1A reprints its zone headers above each section; those rows carry
    # words, not markers, and must not be filled with prohibitions.
    cells = _grid([("Commercial", {1: "Z-1", 2: "Z-2"}), ("Office use", {1: DOT})])

    audit = audit_permission_grid(cells)

    assert audit.row_reasons[1] == REASON_FOREIGN_CONTENT
    assert audit.gaps == [(2, 2), (2, 3), (2, 4)]


def test_an_unlabelled_marker_row_refuses_its_neighbours():
    cells = _grid([("Model suite use", {}), ("Office use", {1: DOT})])
    # Strip the second row's label: its markers now belong to no use at all.
    cells = [c for c in cells if (c.row_index, c.col_index) != (2, 0)]

    audit = audit_permission_grid(cells)

    assert audit.row_reasons[2] == REASON_UNLABELLED_ROW
    assert audit.row_reasons[1] == REASON_UNLABELLED_ROW


def test_a_split_header_row_does_not_refuse_the_row_below_it():
    """A header spilled across two grid rows loses no data row with it.

    Table 1B page 48 splits its reprinted zone header over two rows. Refusing
    the following use row for that would cost real coverage, so an unlabelled
    row whose cells are all *words* refuses only itself.
    """
    # Grid rows 1 and 2 are the two halves of ONE printed row, so they share a
    # y band; the use row below sits on the next printed line.
    cells = _grid([("Commercial", {1: "Z-1"})])
    cells.append(_Cell(2, 2, "Z-2", _marker_bbox(1, 2)))
    cells.append(_Cell(3, 0, "Broadcast use", _label_bbox(2)))

    audit = audit_permission_grid(cells)

    # Both halves of the split header are refused...
    assert 1 in audit.row_reasons
    assert audit.row_reasons[2] == REASON_UNLABELLED_ROW
    # ...and the use row beneath them keeps its coverage.
    assert 3 in audit.sound_row_indices
    assert (3, 1) in audit.gaps


def test_no_geometry_fills_nothing():
    """The ABS-484 fixture seeds cells without bboxes; its holes must survive.

    Without geometry there is no evidence the row is intact, so an extraction
    gap stays an extraction gap.
    """
    cells = _grid([("Townhouse dwelling use", {1: "⑮"})])
    for cell in cells:
        cell.bbox_json = None

    audit = audit_permission_grid(cells)

    assert audit.table_reason == REASON_NO_GEOMETRY
    assert audit.gaps == []


# ---------------------------------------------------------------------------
# End to end through enrichment and the matrix resolver
# ---------------------------------------------------------------------------

RAGGED_MATRIX = [
    ("Single-unit dwelling use", {1: "⑮", 2: "⑮", 3: DOT, 4: DOT}),
    ("Semi-detached dwelling use", {1: "⑮", 2: "⑮"}),
    ("Townhouse dwelling use", {1: "⑮"}),
    ("Multi-unit dwelling use", {1: "⑮", 2: DOT}),
]

ZONES = ["ER-3", "ER-2", "ER-1", "CH-1"]


@pytest.fixture()
def ragged_corpus(tmp_path: Path) -> dict:
    """A Table 1B-shaped ragged matrix, enriched exactly as an ingest would."""
    db_path = tmp_path / "abs520.sqlite"
    url = f"sqlite:///{db_path}"
    create_all(url)
    with session_scope(url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="rc.pdf",
            file_hash="abs520-ragged-grid",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        table = SourceTable(
            document_id=document.id,
            caption=None,
            page_start=48,
            page_end=48,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(table)
        session.flush()
        header = [_Cell(0, 0, "Residential", _label_bbox(0))]
        for index, zone in enumerate(ZONES, start=1):
            header.append(_Cell(0, index, zone, _marker_bbox(0, index)))
        body = _grid(RAGGED_MATRIX, columns=len(ZONES))
        # Replace the placeholder headers with the real zone codes.
        cells = header + [cell for cell in body if cell.row_index > 0]
        for cell in cells:
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=cell.row_index,
                    col_index=cell.col_index,
                    row_header_path=None,
                    col_header_path=None,
                    text=cell.text,
                    bbox_json=cell.bbox_json,
                    metadata_json={},
                )
            )
        session.flush()
        enrich_document_semantics(session, document_id=document.id)
        ids = {"document_id": document.id, "table_id": table.id}
    return {"url": url, **ids}


def _permission(session, table_id: int, use: str, zone: str) -> str | None:
    resolved = resolve_permission_cell(
        session, table_id=table_id, use_name=use, zone=zone
    )
    return None if resolved is None else resolved.get("permission_marker")


def test_enrichment_fills_the_ragged_grid_so_er2_townhouse_resolves(ragged_corpus):
    with session_scope(ragged_corpus["url"]) as session:
        table_id = ragged_corpus["table_id"]
        # The cell ABS-520 is named for: absent from the parse, prohibited by
        # the by-law, previously served as "undetermined".
        assert _permission(session, table_id, "Townhouse dwelling use", "ER-2") == (
            "not_permitted"
        )
        # The markers that WERE extracted are untouched.
        assert _permission(session, table_id, "Townhouse dwelling use", "ER-3") == (
            "conditional"
        )
        assert _permission(session, table_id, "Single-unit dwelling use", "CH-1") == (
            "permitted"
        )


def test_filled_cells_are_labelled_and_the_originals_are_not(ragged_corpus):
    with session_scope(ragged_corpus["url"]) as session:
        cells = (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id == ragged_corpus["table_id"])
            .all()
        )
        filled = [c for c in cells if (c.metadata_json or {}).get(GRID_FILL_KEY)]
        assert filled, "the ragged rows were materialized"
        for cell in filled:
            assert cell.metadata_json[GRID_FILL_KEY] == GRID_FILL_ABSENT_CELL
            assert cell.text == ""
            assert cell.metadata_json["permission_marker"] == "not_permitted"
        # Nothing the parser produced is relabelled as a fill.
        parsed = [c for c in cells if c.bbox_json is not None]
        assert all(
            (c.metadata_json or {}).get(GRID_FILL_KEY) is None for c in parsed
        )


def test_re_enrichment_creates_no_further_cells(ragged_corpus):
    with session_scope(ragged_corpus["url"]) as session:
        before = (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id == ragged_corpus["table_id"])
            .count()
        )
        enrich_document_semantics(session, document_id=ragged_corpus["document_id"])
        after = (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id == ragged_corpus["table_id"])
            .count()
        )
    assert after == before


def test_densify_reports_without_writing_when_apply_is_false(ragged_corpus):
    with session_scope(ragged_corpus["url"]) as session:
        table = session.get(SourceTable, ragged_corpus["table_id"])
        cells = (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id == table.id)
            .all()
        )
        # Strip the fills so there is something left to report.
        parsed = [c for c in cells if (c.metadata_json or {}).get(GRID_FILL_KEY) is None]
        audit = densify_permission_matrix(session, table, parsed, apply=False)
        assert audit.gaps
        assert (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id == table.id)
            .count()
            == len(cells)
        )
