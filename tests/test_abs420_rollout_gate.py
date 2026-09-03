"""The rollout gate in scripts/abs420_rollout_gate.py (ABS-420).

The rollout's whole safety argument is one sentence: migration 0024's backfill
enables exactly what the recency resolver already selected, so retrieval
behaviour does not change. That sentence is true *of the corpus it was measured
against*. Every test here is a way production could have stopped being that
corpus between the measurement and the maintenance window — a re-ingest, an
amendment, a deleted duplicate, a hand-run enable — and each one has to stop
the run rather than produce a confident-looking enabled set nobody checked.

The second half pins the gate's prediction to the migration's actual SQL: the
window function in 0024 is executed against a real database and its winners
compared with ``predict_backfill_winners``. A gate that predicts by a
different rule than the migration applies is worse than no gate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = REPO_ROOT / "alembic" / "versions" / "0024_document_retrieval_enabled.py"

from scripts.abs420_rollout_gate import (  # noqa: E402
    EXPECTED_INVENTORY,
    INTENDED_ENABLED_IDS,
    DocumentRow,
    gate_preflight,
    gate_verify,
    parse_inventory,
    predict_backfill_winners,
)


def dump(rows: tuple[DocumentRow, ...] | list[DocumentRow], *, enabled: set[int] | None = None) -> str:
    """Render rows the way ``psql -At -F'|'`` does."""
    lines = []
    for row in rows:
        fields = [
            str(row.id),
            row.municipality,
            row.bylaw_name,
            row.ingestion_timestamp,
            row.parser_version,
        ]
        if enabled is not None:
            fields.append("t" if row.id in enabled else "f")
        lines.append("|".join(fields))
    return "\n".join(lines) + "\n"


PROD_DUMP = dump(EXPECTED_INVENTORY)


# --- preflight: is production still the corpus we planned against? ----------


def test_the_measured_production_inventory_passes_preflight():
    code, lines = gate_preflight(PROD_DUMP)
    assert code == 0, lines
    assert "will enable: [2, 4, 5]" in "\n".join(lines)
    assert "no post-migration curation needed" in "\n".join(lines)


def test_a_new_ingest_stops_the_preflight():
    # An amendment ingested after the measurement changes which document wins
    # the backfill — the exact case where "behaviour is preserved" stops being
    # a checked claim.
    moved = [
        *EXPECTED_INVENTORY,
        DocumentRow(9, "HRM", "Regional Centre Land Use By-Law", "2026-08-20 10:00:00.000000+00", "docling"),
    ]
    code, lines = gate_preflight(dump(moved))
    assert code == 1
    assert any("was ingested after the rollout was planned" in line for line in lines)


def test_a_deleted_document_stops_the_preflight():
    code, lines = gate_preflight(dump([r for r in EXPECTED_INVENTORY if r.id != 1]))
    assert code == 1
    assert any("no longer in the corpus" in line for line in lines)


def test_a_re_ingest_under_the_same_id_stops_the_preflight():
    # Same ids, same names, different ingestion timestamp: a re-ingest in
    # place. Counting rows would wave this through.
    reingested = [
        DocumentRow(r.id, r.municipality, r.bylaw_name, "2026-08-01 00:00:00.000000+00", r.parser_version)
        if r.id == 4
        else r
        for r in EXPECTED_INVENTORY
    ]
    code, lines = gate_preflight(dump(reingested))
    assert code == 1
    assert any("ingestion_timestamp" in line for line in lines)


def test_a_reparsed_document_stops_the_preflight():
    # parser_version is what separates document 1 from document 2. If it
    # moved, the "recency and quality agree" finding no longer holds.
    reparsed = [
        DocumentRow(r.id, r.municipality, r.bylaw_name, r.ingestion_timestamp, "docling")
        if r.id == 1
        else r
        for r in EXPECTED_INVENTORY
    ]
    code, lines = gate_preflight(dump(reparsed))
    assert code == 1
    assert any("parser_version" in line for line in lines)


def test_garbage_input_is_an_error_not_a_verdict():
    with pytest.raises(ValueError, match="unparseable inventory line"):
        parse_inventory("this is not a psql dump\n")


def test_the_psql_row_count_footer_is_ignored():
    code, _ = gate_preflight(PROD_DUMP + "(4 rows)\n")
    assert code == 0


# --- the prediction itself --------------------------------------------------


def test_newest_ingest_wins_per_bylaw():
    assert predict_backfill_winners(list(EXPECTED_INVENTORY)) == set(INTENDED_ENABLED_IDS)


def test_ties_are_broken_by_highest_id():
    same_instant = [
        DocumentRow(7, "HRM", "Tie By-law", "2026-05-01 00:00:00.000000+00", "docling"),
        DocumentRow(8, "HRM", "Tie By-law", "2026-05-01 00:00:00.000000+00", "docling"),
    ]
    assert predict_backfill_winners(same_instant) == {8}


# --- verify: are the flags on disk the ones we predicted? -------------------


def test_the_predicted_flags_pass_verification():
    code, lines = gate_verify(dump(EXPECTED_INVENTORY, enabled={2, 4, 5}))
    assert code == 0, lines
    assert "Rollout verified" in "\n".join(lines)


def test_a_dump_without_the_flag_column_stops_verification():
    code, lines = gate_verify(PROD_DUMP)
    assert code == 1
    assert any("migration 0024 has not run" in line for line in lines)


def test_the_stale_duplicate_winning_the_backfill_stops_verification():
    # The failure the issue's curation step exists for: document 1, the
    # pymupdf-fallback ingest, enabled instead of the docling one.
    code, lines = gate_verify(dump(EXPECTED_INVENTORY, enabled={1, 4, 5}))
    assert code == 1
    text_out = "\n".join(lines)
    assert "disable: [1]" in text_out
    assert "enable:  [2]" in text_out


def test_both_versions_enabled_stops_verification():
    code, lines = gate_verify(dump(EXPECTED_INVENTORY, enabled={1, 2, 4, 5}))
    assert code == 1
    assert any("disable: [1]" in line for line in lines)


def test_an_empty_enabled_set_stops_verification():
    # Fail-closed retrieval: zero enabled documents answers every question
    # with nothing found, which reads as a corpus outage, not a config error.
    code, lines = gate_verify(dump(EXPECTED_INVENTORY, enabled=set()))
    assert code == 1
    assert any("enable:  [2, 4, 5]" in line for line in lines)


def test_a_renamed_bylaw_stops_verification_before_the_enabled_check():
    # ABS-434's collision shape — two enabled documents whose names differ
    # only by a hyphen — cannot survive the drift check that runs first, and
    # that is why gate_verify carries no collision check of its own. This
    # pins that ordering: rename a by-law and the gate stops on the rename,
    # not on a plausible-looking enabled set.
    renamed = [
        DocumentRow(r.id, r.municipality, "Halifax Peninsula Land-Use By-law", r.ingestion_timestamp, r.parser_version)
        if r.id == 4
        else r
        for r in EXPECTED_INVENTORY
    ]
    code, lines = gate_verify(dump(renamed, enabled={2, 4, 5}))
    assert code == 1
    assert any("bylaw_name" in line for line in lines)


# --- the prediction and the migration must be the same rule -----------------


def _load_migration():
    """Import 0024 by path — ``alembic/versions`` is not a package."""
    spec = importlib.util.spec_from_file_location("abs420_migration_0024", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backfill_sql() -> str:
    """The migration's own backfill statement, captured rather than copied.

    ``upgrade()`` runs three operations; only the middle one is the backfill.
    Recording ``op.execute`` calls gets the real SQL string without re-running
    ``add_column``/``alter_column``, neither of which SQLite supports.
    """
    module = _load_migration()
    captured: list[str] = []

    class _Recorder:
        def add_column(self, *args, **kwargs) -> None:
            pass

        def alter_column(self, *args, **kwargs) -> None:
            pass

        def execute(self, statement) -> None:
            captured.append(str(statement))

    module.op = _Recorder()
    module.upgrade()
    assert len(captured) == 1, f"expected one op.execute in 0024, captured {len(captured)}"
    return captured[0]


@pytest.fixture()
def corpus_db(tmp_path: Path) -> str:
    """Production's four documents in a SQLite table shaped like ``document``."""
    url = f"sqlite:///{tmp_path / 'abs420.sqlite'}"
    engine = create_engine(url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE document (id INTEGER PRIMARY KEY, municipality TEXT, "
                "bylaw_name TEXT, ingestion_timestamp TEXT, retrieval_enabled BOOLEAN)"
            )
        )
        for row in EXPECTED_INVENTORY:
            connection.execute(
                text(
                    "INSERT INTO document (id, municipality, bylaw_name, ingestion_timestamp) "
                    "VALUES (:id, :m, :b, :t)"
                ),
                {"id": row.id, "m": row.municipality, "b": row.bylaw_name, "t": row.ingestion_timestamp},
            )
    engine.dispose()
    return url


def test_the_migrations_own_sql_selects_what_the_gate_predicts(corpus_db: str):
    engine = create_engine(corpus_db, future=True)
    with engine.begin() as connection:
        connection.execute(text(_backfill_sql()))
        enabled = {
            row[0]
            for row in connection.execute(text("SELECT id FROM document WHERE retrieval_enabled"))
        }
    engine.dispose()

    assert enabled == predict_backfill_winners(list(EXPECTED_INVENTORY))
    assert enabled == set(INTENDED_ENABLED_IDS)


def test_the_migration_module_still_runs_through_alembics_operations(tmp_path: Path):
    """The capture above must not be the only thing that ever loads 0024.

    If ``upgrade()`` stops being three ops — or the backfill moves into
    ``add_column``'s server_default — the recorder's assertion fires here
    rather than in production.
    """
    module = _load_migration()
    assert module.revision == "0024_document_retrieval_enabled"
    assert module.down_revision == "0023_token_wallet"

    engine = create_engine(f"sqlite:///{tmp_path / 'ops.sqlite'}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE document (id INTEGER PRIMARY KEY, municipality TEXT, "
                "bylaw_name TEXT, ingestion_timestamp TEXT)"
            )
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            # add_column and the backfill run for real here; alter_column
            # cannot, because SQLite has no ALTER COLUMN ... SET DEFAULT.
            # Reaching that error is what proves the first two ops went
            # through real alembic Operations rather than the recorder above.
            with pytest.raises(OperationalError, match="DEFAULT"):
                module.upgrade()
        enabled = {
            row[0]
            for row in connection.execute(text("SELECT id FROM document WHERE retrieval_enabled"))
        }
    engine.dispose()
    assert enabled == set()  # the table was empty; the backfill enabled nothing
