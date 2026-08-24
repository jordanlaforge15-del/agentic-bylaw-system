"""The ABS-520 corpus-integrity guard has to fail before it can pass.

``scripts/verify_permission_grid_integrity.py`` is the thing standing between a
re-ingest and a silent return of the defect: a parser build that drops blank
cells again produces no error, no empty result and no failing test anywhere
else. So these tests run the guard over a ragged corpus twice — once as
ingested by a parser that drops blanks, once after the repair — and require it
to say different things.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import Document, SourceTable, SourceTableCell, TableSemanticProfile
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics
from layer1.semantic.permission_grid import GRID_FILL_KEY
from layer1.semantic.permission_markers import PERMISSION_MATRIX_PROFILE

from scripts.backfill_permission_grid import backfill, undetermined_counts
from scripts.verify_permission_grid_integrity import (
    check_fillable_gaps,
    check_named_cells,
)

DOT = chr(0xF098)  # symbol-font ● "permitted as-of-right"
ZONES = ["ER-3", "ER-2", "ER-1", "CH-1"]

_ROW_PITCH = 11.0
_ROW_HEIGHT = 8.0


def _label_bbox(row: int) -> dict:
    top = 104.0 + row * _ROW_PITCH
    return {"x0": 77.0, "x1": 190.0, "y0": top, "y1": top + _ROW_HEIGHT}


def _marker_bbox(row: int, col: int) -> dict:
    top = 104.0 + row * _ROW_PITCH - 1.0
    left = 306.0 + (col - 1) * 50.0
    return {"x0": left, "x1": left + 9.0, "y0": top, "y1": top + _ROW_HEIGHT}


# The three cells verify_permission_grid_integrity names, in the ragged shape a
# blank-dropping parser produces: ER-2, ER-1 and CH-1 are absent from the
# townhouse row because the by-law prints them blank.
RAGGED_CELLS = [
    (0, 0, "Residential"),
    *[(0, index, zone) for index, zone in enumerate(ZONES, start=1)],
    (1, 0, "Single-unit dwelling use"),
    *[(1, index, DOT) for index in range(1, len(ZONES) + 1)],
    (2, 0, "Townhouse dwelling use"),
    (2, 1, "⑮"),
]


@pytest.fixture()
def ragged_corpus(tmp_path: Path) -> str:
    """A parsed-but-ragged permission matrix, enrichment's densify disabled."""
    url = f"sqlite:///{tmp_path / 'abs520-guard.sqlite'}"
    create_all(url)
    with session_scope(url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="rc.pdf",
            file_hash="abs520-guard",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        table = SourceTable(
            document_id=document.id,
            caption="Table 1B: Permitted uses by zone",
            page_start=48,
            page_end=48,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(table)
        session.flush()
        for row_index, col_index, text in RAGGED_CELLS:
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=None,
                    col_header_path=None,
                    text=text,
                    bbox_json=(
                        _label_bbox(row_index)
                        if col_index == 0
                        else _marker_bbox(row_index, col_index)
                    ),
                    metadata_json={},
                )
            )
        session.flush()
        enrich_document_semantics(session, document_id=document.id)
        # Enrichment densifies at ingest. Strip the fills back out to stand in
        # for a corpus ingested before this repair existed (or by a parser
        # build that dropped blanks again) — that is the state the guard has to
        # catch.
        for cell in (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id == table.id)
            .all()
        ):
            if (cell.metadata_json or {}).get(GRID_FILL_KEY):
                session.delete(cell)
        session.flush()
    return url


def test_the_seeded_matrix_classifies_as_a_permission_matrix(ragged_corpus):
    with session_scope(ragged_corpus) as session:
        profiles = (
            session.query(TableSemanticProfile)
            .filter(TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE)
            .count()
        )
    assert profiles == 1, "the guard is only meaningful over a classified matrix"


def test_g1_fails_on_a_corpus_whose_blank_cells_were_dropped(ragged_corpus):
    with session_scope(ragged_corpus) as session:
        fillable, lines, _reasons = check_fillable_gaps(session)
    assert fillable == 3, "ER-2, ER-1 and CH-1 are missing from the townhouse row"
    assert lines, "the guard names the table it found the gaps in"


def test_g2_fails_on_the_attested_cell_before_the_repair(ragged_corpus):
    with session_scope(ragged_corpus) as session:
        results = {
            (named.use, named.zone): (actual, ok)
            for named, actual, ok in check_named_cells(session)
        }
    actual, ok = results[("Townhouse dwelling use", "ER-2")]
    assert not ok
    assert actual == "unknown", "the defect: a prohibition reads as unreadable"
    # The cells the parser DID store are unaffected either way.
    assert results[("Townhouse dwelling use", "ER-3")][1] is True
    assert results[("Single-unit dwelling use", "CH-1")][1] is True


def test_both_guards_pass_after_the_backfill(ragged_corpus):
    with session_scope(ragged_corpus) as session:
        before = undetermined_counts(session, ["ER-2"])["ER-2"]
        stats = backfill(session, dry_run=False)
        session.flush()
        after = undetermined_counts(session, ["ER-2"])["ER-2"]
        fillable, _lines, _reasons = check_fillable_gaps(session)
        results = check_named_cells(session)

    assert stats.cells_filled == 3
    assert fillable == 0
    assert all(ok for _named, _actual, ok in results)
    assert (before, after) == (1, 0), "the blast radius is reportable, and moves"


def test_a_dry_run_reports_without_repairing(ragged_corpus):
    with session_scope(ragged_corpus) as session:
        stats = backfill(session, dry_run=True)
        fillable, _lines, _reasons = check_fillable_gaps(session)
    assert stats.cells_filled == 3
    assert fillable == 3, "nothing was written"
