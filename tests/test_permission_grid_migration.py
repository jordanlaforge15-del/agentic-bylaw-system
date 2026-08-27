"""The ABS-520 repair has to reach an environment nobody runs a script against.

ABS-526: the fix shipped as code plus a hand-run backfill, and production got
only the code half — it kept answering "the permission could not be extracted"
where the by-law prints a blank cell, for a release cycle, with every test
green. The delivery mechanism is now the ``0027_permission_grid_backfill``
Alembic migration, which every deploy and every e2e stack boot runs.

These tests drive that migration module itself — through a real
:class:`~alembic.migration.MigrationContext`, the same object ``alembic
upgrade`` builds — over a corpus in exactly the ragged shape a pre-ABS-520
parser left behind.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select

from layer1.db.base import Document, SourceTable, SourceTableCell
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics, resolve_permission_cell
from layer1.semantic.permission_grid import GRID_FILL_KEY, is_grid_filled

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    REPO_ROOT / "alembic" / "versions" / "0027_permission_grid_backfill.py"
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


# Table 1B's shape as a blank-dropping parser leaves it: the townhouse row is
# right-truncated at its one conditional marker, so ER-2, ER-1 and CH-1 — the
# three cells the by-law prints blank — are simply absent.
RAGGED_CELLS = [
    (0, 0, "Residential"),
    *[(0, index, zone) for index, zone in enumerate(ZONES, start=1)],
    (1, 0, "Single-unit dwelling use"),
    *[(1, index, DOT) for index in range(1, len(ZONES) + 1)],
    (2, 0, "Townhouse dwelling use"),
    (2, 1, "⑮"),
]


def _load_migration():
    """Import the migration by path — ``alembic/versions`` is not a package."""
    spec = importlib.util.spec_from_file_location(
        "abs526_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(url: str, direction: str) -> None:
    """Run one direction of the migration the way alembic runs it."""
    module = _load_migration()
    engine = create_engine(url, future=True)
    with engine.begin() as connection, Operations.context(
        MigrationContext.configure(connection)
    ):
        getattr(module, direction)()
    engine.dispose()


def _seed_ragged(url: str) -> int:
    create_all(url)
    with session_scope(url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="rc.pdf",
            file_hash="abs526-migration",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(UTC),
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
        # Classify the table, then strip the fills enrichment just made: that
        # is production's state — profiled at ingest, before the repair existed.
        enrich_document_semantics(session, document_id=document.id)
        for cell in _cells(session, table.id):
            if (cell.metadata_json or {}).get(GRID_FILL_KEY):
                session.delete(cell)
        session.flush()
        return table.id


def _cells(session, table_id: int) -> list[SourceTableCell]:
    return list(
        session.execute(
            select(SourceTableCell).where(SourceTableCell.table_id == table_id)
        )
        .scalars()
        .all()
    )


@pytest.fixture()
def ragged_corpus(tmp_path: Path) -> tuple[str, int]:
    url = f"sqlite:///{tmp_path / 'abs526-migration.sqlite'}"
    return url, _seed_ragged(url)


def test_the_prohibition_is_unreadable_before_the_migration(ragged_corpus):
    url, table_id = ragged_corpus
    with session_scope(url) as session:
        resolved = resolve_permission_cell(
            session, table_id=table_id, use_name="Townhouse dwelling use", zone="ER-2"
        )
    assert resolved is not None, "the axes bind; only the cell is missing"
    assert resolved.get("permission_marker") == "unknown", (
        "the defect being migrated away: a flat prohibition served as unreadable"
    )


def test_upgrade_materializes_the_dropped_blanks(ragged_corpus):
    url, table_id = ragged_corpus
    _run(url, "upgrade")

    with session_scope(url) as session:
        filled = [cell for cell in _cells(session, table_id) if is_grid_filled(cell)]
        resolved = resolve_permission_cell(
            session, table_id=table_id, use_name="Townhouse dwelling use", zone="ER-2"
        )
    assert len(filled) == 3, "ER-2, ER-1 and CH-1 on the townhouse row"
    assert resolved["permission_marker"] == "not_permitted"


def test_upgrade_is_idempotent(ragged_corpus):
    url, table_id = ragged_corpus
    _run(url, "upgrade")
    with session_scope(url) as session:
        after_first = len(_cells(session, table_id))

    _run(url, "upgrade")
    with session_scope(url) as session:
        after_second = len(_cells(session, table_id))

    assert after_first == after_second, "a re-run must create nothing"


def test_downgrade_removes_only_what_the_upgrade_created(ragged_corpus):
    url, table_id = ragged_corpus
    with session_scope(url) as session:
        as_parsed = {
            (cell.row_index, cell.col_index, cell.text)
            for cell in _cells(session, table_id)
        }

    _run(url, "upgrade")
    _run(url, "downgrade")

    with session_scope(url) as session:
        after = {
            (cell.row_index, cell.col_index, cell.text)
            for cell in _cells(session, table_id)
        }
    assert after == as_parsed, "the reversal returns the corpus the parser stored"


def test_upgrade_is_a_no_op_without_a_permission_matrix(tmp_path: Path):
    """CI, a fresh deploy before its first ingest, most e2e worktrees."""
    url = f"sqlite:///{tmp_path / 'abs526-empty.sqlite'}"
    create_all(url)

    _run(url, "upgrade")  # must not raise

    with session_scope(url) as session:
        assert session.execute(select(SourceTableCell)).scalars().all() == []


def test_the_migration_extends_the_current_head():
    """A second head would make ``alembic upgrade head`` fail on deploy."""
    module = _load_migration()
    assert module.revision == "0027_permission_grid_backfill"
    assert module.down_revision == "0026_drop_parcel_zone_code"
    revisions = {
        path.stem.split("_", 1)[0]: path
        for path in (REPO_ROOT / "alembic" / "versions").glob("*.py")
    }
    assert "0028" not in revisions, (
        "a later migration exists: re-point it or this one, or the chain forks"
    )
