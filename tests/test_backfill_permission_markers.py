"""Integration coverage for the permission-marker backfill (ABS-277).

Builds a fixture permission-matrix table mirroring the real Table 1057 (a mix
of symbol-font ● dots stored as ``U+F098``, symbol-font padding ``U+F020``,
circled-number conditional markers, and empty "not permitted" cells), runs the
backfill against an in-memory SQLite DB, and verifies:

* the count of ``permission_marker == "permitted"`` cells equals the number of
  ``U+F098`` occurrences in the value grid (AC5);
* re-running the backfill is idempotent — identical metadata, no further
  updates (AC6).
"""
from __future__ import annotations

import importlib.util as _importlib_util
import sys as _sys
from pathlib import Path

from sqlalchemy import select

from layer1.db.base import Document, SourceTable, SourceTableCell, utcnow
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus

# scripts/ isn't a package; load the backfill module via importlib (same
# pattern as tests/test_backfill_parcels.py).
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "backfill_permission_markers.py"
)
_spec = _importlib_util.spec_from_file_location("backfill_permission_markers", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_backfill_mod = _importlib_util.module_from_spec(_spec)
_sys.modules[_spec.name] = _backfill_mod
_spec.loader.exec_module(_backfill_mod)

backfill = _backfill_mod.backfill

DOT = ""  # symbol-font ● "permitted as-of-right"
PAD = ""  # symbol-font space (padding)

CAPTION = "Table 1A: Permitted uses by zone — Residential"

# (row, col, text) — header row 0 + row-label col 0, value grid otherwise.
# Mirrors table 1057: dots stored as U+F098, padded with U+F020, a couple of
# circled-number conditionals, and blanks for not-permitted.
CELL_SPECS = [
    (0, 0, "Use"),
    (0, 1, "DD"),
    (0, 2, "DH"),
    (0, 3, "ER-3"),
    (1, 0, "Low-density dwelling use"),
    (1, 1, PAD + DOT + PAD),  # permitted
    (1, 2, DOT),              # permitted
    (1, 3, ""),               # not permitted
    (2, 0, "Multi-unit dwelling use"),
    (2, 1, DOT),              # permitted
    (2, 2, "③"),              # conditional, footnote 3
    (2, 3, PAD),              # padding only -> not permitted
    (3, 0, "Restaurant use"),
    (3, 1, ""),               # not permitted
    (3, 2, "⑮"),              # conditional, footnote 15
    (3, 3, DOT),              # permitted
]

# U+F098 occurrences in the value grid (col_index >= 1, row_index >= 1).
EXPECTED_PERMITTED = sum(
    1 for r, c, t in CELL_SPECS if r >= 1 and c >= 1 and DOT in t
)


def _seed(db_url: str) -> int:
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Fixture Bylaw",
            source_path="fixture.pdf",
            file_hash="abs277-fixture",
            mime_type="application/pdf",
            page_count=80,
            parser_version="test",
            ingestion_timestamp=utcnow(),
        )
        session.add(document)
        session.flush()
        table = SourceTable(
            document_id=document.id,
            caption=CAPTION,
            page_start=42,
            page_end=43,
            parse_status=ParseStatus.PARSED,
            metadata_json={"parser": "docling"},
        )
        session.add(table)
        session.flush()
        for row_idx, col_idx, text in CELL_SPECS:
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_idx,
                    col_index=col_idx,
                    text=text,
                )
            )
        session.flush()
        return table.id


def _markers_by_cell(db_url: str, table_id: int) -> dict[tuple[int, int], dict]:
    with session_scope(db_url) as session:
        cells = (
            session.execute(
                select(SourceTableCell).where(SourceTableCell.table_id == table_id)
            )
            .scalars()
            .all()
        )
        return {
            (c.row_index, c.col_index): dict(c.metadata_json or {}) for c in cells
        }


def test_backfill_recovers_permitted_count(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'abs277.db'}"
    table_id = _seed(db_url)

    with session_scope(db_url) as session:
        stats = backfill(session)

    # AC5: permitted count == number of U+F098 value cells.
    assert stats.marker_counts["permitted"] == EXPECTED_PERMITTED
    assert EXPECTED_PERMITTED == 4

    markers = _markers_by_cell(db_url, table_id)
    # Raw text is never clobbered; headers are left untouched.
    assert "permission_marker" not in markers[(0, 0)]
    assert "permission_marker" not in markers[(1, 0)]
    # Spot-check the value grid.
    assert markers[(1, 1)] == {"permission_marker": "permitted"}
    assert markers[(1, 3)] == {"permission_marker": "not_permitted"}
    assert markers[(2, 2)] == {"permission_marker": "conditional", "footnote": 3}
    assert markers[(2, 3)] == {"permission_marker": "not_permitted"}
    assert markers[(3, 2)] == {"permission_marker": "conditional", "footnote": 15}


def test_backfill_is_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'abs277.db'}"
    table_id = _seed(db_url)

    with session_scope(db_url) as session:
        first = backfill(session)
    after_first = _markers_by_cell(db_url, table_id)

    with session_scope(db_url) as session:
        second = backfill(session)
    after_second = _markers_by_cell(db_url, table_id)

    # AC6: second run changes nothing and produces identical metadata.
    assert first.cells_updated > 0
    assert second.cells_updated == 0
    assert after_first == after_second


def test_backfill_dry_run_writes_nothing(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'abs277.db'}"
    table_id = _seed(db_url)

    with session_scope(db_url) as session:
        stats = backfill(session, dry_run=True)

    assert stats.marker_counts["permitted"] == EXPECTED_PERMITTED
    markers = _markers_by_cell(db_url, table_id)
    assert all("permission_marker" not in meta for meta in markers.values())
