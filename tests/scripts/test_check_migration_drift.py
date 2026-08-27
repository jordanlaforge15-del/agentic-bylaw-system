"""Coverage for scripts/check_migration_drift.py (ABS-499).

The DM3.0 post-mortem found the dev DB stamped ``0025_signup_grant_unique``
while ``0026_drop_parcel_zone_code`` was still pending — data migrations
applied, schema migration not. Nothing reported it. These tests drive the
comparison against this branch's real ``alembic/versions`` directory, so the
split state the ticket describes is exactly what the first test asserts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from layer1.db.migration_drift import compute_drift, drift_report, load_script_directory
from scripts.check_migration_drift import main


def _stamped_sqlite_db(tmp_path: Path, *revisions: str) -> str:
    """A database stamped like alembic would stamp it, for the read path."""
    db = tmp_path / "drift.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(255) PRIMARY KEY)")
        conn.executemany("INSERT INTO alembic_version VALUES (?)", [(r,) for r in revisions])
    return f"sqlite:///{db}"


@pytest.fixture(scope="module")
def script_directory():
    return load_script_directory()


def test_reports_the_dm3_split_state(script_directory) -> None:
    """0025 applied, 0026 pending — the state ABS-499 was written against.

    Asserted relative to the chain, not against a frozen list: every migration
    added after 0026 lengthens what is pending from 0025, and that is not this
    test's subject.
    """
    report = compute_drift(("0025_signup_grant_unique",), script_directory)

    revisions = [item.revision for item in report.pending]
    assert report.is_behind
    assert revisions[0] == "0026_drop_parcel_zone_code"
    assert revisions[-1] == script_directory.get_heads()[0]
    rendered = report.render()
    assert "alembic_version : 0025_signup_grant_unique" in rendered
    assert f"BEHIND — {len(revisions)} migration(s) pending" in rendered
    assert "0026_drop_parcel_zone_code" in rendered
    assert "alembic/versions/0026_drop_parcel_zone_code.py" in rendered


def test_head_revision_is_in_sync(script_directory) -> None:
    (head,) = script_directory.get_heads()
    report = compute_drift((head,), script_directory)

    assert not report.is_behind
    assert report.pending == ()
    assert "in sync" in report.render()


def test_never_stamped_database_is_behind_by_everything(script_directory) -> None:
    report = compute_drift((), script_directory)

    assert report.is_behind
    revisions = [item.revision for item in report.pending]
    # Ordered base -> head, so the reader can apply top to bottom.
    assert revisions[0] == "0001_initial_layer1_schema"
    assert revisions[-1] == script_directory.get_heads()[0]
    assert "<none — never stamped>" in report.render()


def test_pending_entries_carry_the_migration_docstring(script_directory) -> None:
    report = compute_drift(("0025_signup_grant_unique",), script_directory)

    pending = report.pending[0]
    assert "parcel.zone_code" in pending.summary


def test_unknown_revision_is_reported_not_swallowed(script_directory) -> None:
    """A DB ahead of this branch is a different failure and must say so."""
    report = compute_drift(("9999_from_the_future",), script_directory)

    assert not report.is_behind
    assert report.error is not None
    assert "9999_from_the_future" in report.error
    assert "ahead of" in report.render()


def test_reads_the_split_state_off_a_real_database(tmp_path: Path) -> None:
    """The read path, end to end: stamped table in, verdict out."""
    report = drift_report(_stamped_sqlite_db(tmp_path, "0025_signup_grant_unique"))

    assert report.is_behind
    assert report.pending[0].revision == "0026_drop_parcel_zone_code"
    assert "0026_drop_parcel_zone_code" in report.summary_line()


def test_a_database_with_no_alembic_version_reads_as_never_stamped(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()

    report = drift_report(f"sqlite:///{db}")

    assert report.error is None
    assert report.current == ()
    assert report.is_behind


def test_unreachable_database_reports_rather_than_raises() -> None:
    report = drift_report("postgresql+psycopg://nobody@127.0.0.1:1/nothing")

    assert report.error is not None
    assert not report.is_behind
    assert "undetermined" in report.summary_line()


def test_main_exits_1_when_behind(tmp_path: Path, capsys) -> None:
    rc = main(["--database-url", _stamped_sqlite_db(tmp_path, "0025_signup_grant_unique")])

    assert rc == 1
    assert "BEHIND" in capsys.readouterr().out


def test_unreachable_database_exits_2(capsys) -> None:
    rc = main(["--database-url", "postgresql+psycopg://nobody@127.0.0.1:1/nothing"])

    assert rc == 2
    assert "could not read alembic_version" in capsys.readouterr().out


def test_exit_zero_downgrades_the_exit_code(capsys) -> None:
    rc = main(
        [
            "--database-url",
            "postgresql+psycopg://nobody@127.0.0.1:1/nothing",
            "--exit-zero",
        ]
    )

    assert rc == 0
    assert "could not read alembic_version" in capsys.readouterr().out
