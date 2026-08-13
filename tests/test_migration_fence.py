"""Coverage for layer1.db.migration_fence and the entry points it fences (ABS-499).

Three things have to hold, and each has a test here:

1. **Scope.** The fence engages for the local dev database and nothing else —
   the e2e stack's ``layer1_test`` on a per-worktree port, a clone, or prod must
   not trigger a multi-hundred-megabyte dump.
2. **Refusal.** When a snapshot is required and cannot be taken, the caller
   aborts *before its first write* rather than migrating blind.
3. **Wiring.** The entry points that mutate dev data actually call the fence,
   ahead of any database work. Tested by running the real scripts as
   subprocesses with a fake snapshot script on ``BYLAW_SNAPSHOT_SCRIPT``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from layer1.db.migration_fence import (
    SnapshotFenceError,
    fence_or_abort,
    snapshot_before_migration,
    targets_dev_database,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

DEV_URL = "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"


@pytest.fixture(autouse=True)
def _clean_fence_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fence behaviour is env-driven; start every test from a known state."""
    for var in (
        "BYLAW_SKIP_MIGRATION_SNAPSHOT",
        "BYLAW_FORCE_MIGRATION_SNAPSHOT",
        "BYLAW_SNAPSHOT_SCRIPT",
        "BYLAW_DEV_PG_PORT",
        "BYLAW_PG_DB",
        # Set on every GitHub Actions runner, where the fence is off by
        # design — these tests assert the fence's behaviour, not CI's.
        "GITHUB_ACTIONS",
    ):
        monkeypatch.delenv(var, raising=False)


def _fake_snapshot_script(tmp_path: Path, *, succeeds: bool = True) -> tuple[Path, Path]:
    """A stand-in for scripts/snapshot-before-migration.sh.

    Records the tag it was called with into a sentinel file, so a test can tell
    *whether* and *with what* the fence fired without touching Docker.
    """
    sentinel = tmp_path / "snapshot-calls.txt"
    dump = tmp_path / "labelled" / "layer1-fake.dump"
    body = f"""#!/usr/bin/env bash
printf '%s\\n' "$1" >> {sentinel}
if [ "{"1" if succeeds else "0"}" != "1" ]; then
  echo "ERROR: container 'agentic-bylaw-system-postgres-1' is not running" >&2
  exit 1
fi
mkdir -p "$(dirname {dump})"
printf 'PGDMP-fake' > {dump}
printf '%s\\n' {dump}
"""
    script = tmp_path / "fake-snapshot.sh"
    script.write_text(body)
    script.chmod(0o755)
    return script, sentinel


# --------------------------------------------------------------------------
# 1. Scope gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        DEV_URL,
        "postgresql+psycopg://layer1:layer1@127.0.0.1:5432/layer1",
    ],
)
def test_dev_database_is_in_scope(url: str) -> None:
    assert targets_dev_database(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # e2e stack: its own database, on its own per-worktree port.
        "postgresql+psycopg://layer1:layer1@localhost:5433/layer1_test",
        "postgresql+psycopg://layer1:layer1@localhost:5438/layer1_test",
        # a throwaway clone (scripts/clone-dev-db.sh picks 5440+)
        "postgresql+psycopg://layer1:layer1@localhost:5441/layer1",
        # production, behind a container hostname
        "postgresql+psycopg://layer1:secret@bylaw-postgres:5432/layer1",
        # unit-test sqlite
        "sqlite:///./scratch.db",
    ],
)
def test_everything_else_is_out_of_scope(url: str) -> None:
    assert targets_dev_database(url) is False


def test_out_of_scope_target_never_runs_the_script(tmp_path: Path, monkeypatch) -> None:
    script, sentinel = _fake_snapshot_script(tmp_path)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))

    assert snapshot_before_migration("e2e", database_url="sqlite:///./scratch.db") is None
    assert not sentinel.exists()


def test_force_flag_engages_the_fence_off_the_dev_db(tmp_path: Path, monkeypatch) -> None:
    script, sentinel = _fake_snapshot_script(tmp_path)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))
    monkeypatch.setenv("BYLAW_FORCE_MIGRATION_SNAPSHOT", "1")

    result = snapshot_before_migration("clone-run", database_url="sqlite:///./scratch.db")

    assert result is not None and result.exists()
    assert sentinel.read_text().strip() == "clone-run"


def test_github_actions_is_out_of_scope(tmp_path: Path, monkeypatch) -> None:
    """CI's DSN is byte-identical to the dev laptop's, on a throwaway container."""
    script, sentinel = _fake_snapshot_script(tmp_path)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert snapshot_before_migration("ci-migrate", database_url=DEV_URL) is None
    assert not sentinel.exists()


def test_skip_flag_beats_the_force_flag(tmp_path: Path, monkeypatch) -> None:
    script, sentinel = _fake_snapshot_script(tmp_path)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))
    monkeypatch.setenv("BYLAW_FORCE_MIGRATION_SNAPSHOT", "1")
    monkeypatch.setenv("BYLAW_SKIP_MIGRATION_SNAPSHOT", "1")

    assert snapshot_before_migration("opt-out", database_url=DEV_URL) is None
    assert not sentinel.exists()


# --------------------------------------------------------------------------
# 2. Refusal
# --------------------------------------------------------------------------


def test_snapshot_is_taken_with_the_callers_tag(tmp_path: Path, monkeypatch) -> None:
    script, sentinel = _fake_snapshot_script(tmp_path)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))

    result = snapshot_before_migration("repath-citation-paths", database_url=DEV_URL)

    assert result is not None and result.exists()
    assert sentinel.read_text().strip() == "repath-citation-paths"


def test_failed_snapshot_raises_rather_than_returning(tmp_path: Path, monkeypatch) -> None:
    script, _ = _fake_snapshot_script(tmp_path, succeeds=False)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))

    with pytest.raises(SnapshotFenceError) as excinfo:
        snapshot_before_migration("repath-citation-paths", database_url=DEV_URL)

    assert "refusing to mutate" in str(excinfo.value)
    assert "is not running" in str(excinfo.value)


def test_missing_snapshot_script_is_a_refusal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(tmp_path / "nope.sh"))

    with pytest.raises(SnapshotFenceError, match="not found"):
        snapshot_before_migration("backfill-parcels", database_url=DEV_URL)


def test_fence_or_abort_exits_cleanly(tmp_path: Path, monkeypatch, capsys) -> None:
    script, _ = _fake_snapshot_script(tmp_path, succeeds=False)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))

    with pytest.raises(SystemExit) as excinfo:
        fence_or_abort("backfill-parcels", database_url=DEV_URL)

    assert excinfo.value.code == 3
    assert "ABORT:" in capsys.readouterr().err


def test_a_behind_database_is_called_out_at_snapshot_time(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """Applying data migrations onto a pending schema migration is the DM3.0 trap."""
    import logging
    import sqlite3

    db = tmp_path / "behind.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(255) PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version VALUES ('0025_signup_grant_unique')")

    script, _ = _fake_snapshot_script(tmp_path)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))
    monkeypatch.setenv("BYLAW_FORCE_MIGRATION_SNAPSHOT", "1")

    with caplog.at_level(logging.WARNING):
        snapshot_before_migration("backfill-parcels", database_url=f"sqlite:///{db}")

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("MIGRATION DRIFT" in message for message in warnings), warnings
    assert any("0026_drop_parcel_zone_code" in message for message in warnings), warnings


def test_no_drift_warning_when_the_database_is_at_head(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    import logging
    import sqlite3

    from layer1.db.migration_drift import load_script_directory

    (head,) = load_script_directory().get_heads()
    db = tmp_path / "at-head.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(255) PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (head,))

    script, _ = _fake_snapshot_script(tmp_path)
    monkeypatch.setenv("BYLAW_SNAPSHOT_SCRIPT", str(script))
    monkeypatch.setenv("BYLAW_FORCE_MIGRATION_SNAPSHOT", "1")

    with caplog.at_level(logging.WARNING):
        snapshot_before_migration("backfill-parcels", database_url=f"sqlite:///{db}")

    assert not [r for r in caplog.records if "MIGRATION DRIFT" in r.getMessage()]


# --------------------------------------------------------------------------
# 3. Wiring — the real entry points, run as subprocesses
# --------------------------------------------------------------------------


def _run_entry_point(
    argv: list[str],
    tmp_path: Path,
    *,
    snapshot_succeeds: bool,
    cwd: Path = REPO_ROOT,
) -> tuple[subprocess.CompletedProcess, Path]:
    script, sentinel = _fake_snapshot_script(tmp_path, succeeds=snapshot_succeeds)
    env = os.environ.copy()
    env["BYLAW_SNAPSHOT_SCRIPT"] = str(script)
    # Force the fence on so the test never needs (or risks) the real dev DB.
    env["BYLAW_FORCE_MIGRATION_SNAPSHOT"] = "1"
    env.pop("BYLAW_SKIP_MIGRATION_SNAPSHOT", None)
    env.pop("GITHUB_ACTIONS", None)
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'scratch.db'}"
    # backfill_user_emails refuses to run at all without one; it never leaves
    # the process because the fence aborts first.
    env.setdefault("CLERK_SECRET_KEY", "sk_test_fence_probe")
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    return result, sentinel


WRITE_ENTRY_POINTS = [
    pytest.param(
        ["scripts/repath_citation_paths.py"],
        "repath-citation-paths",
        id="repath",
    ),
    pytest.param(
        ["scripts/backfill_duplicate_citation_path_status.py", "--apply"],
        "backfill-duplicate-citation-path-status",
        id="backfill-duplicate-status",
    ),
    pytest.param(
        ["scripts/backfill_parcels.py"],
        "backfill-parcels",
        id="backfill-parcels",
    ),
    pytest.param(
        ["scripts/backfill_permission_markers.py"],
        "backfill-permission-markers",
        id="backfill-permission-markers",
    ),
    pytest.param(
        ["scripts/backfill_table_profiles.py"],
        "backfill-table-profiles",
        id="backfill-table-profiles",
    ),
    pytest.param(
        ["scripts/backfill_table_citations.py", "--profile", "halifax"],
        "backfill-table-citations",
        id="backfill-table-citations",
    ),
    pytest.param(
        ["scripts/backfill_bylaw_area_attribution.py"],
        "backfill-bylaw-area-attribution",
        id="backfill-area-attribution",
    ),
    pytest.param(
        ["scripts/backfill_user_emails.py", "--apply"],
        "backfill-user-emails",
        id="backfill-user-emails",
    ),
]


@pytest.mark.parametrize(("argv", "tag"), WRITE_ENTRY_POINTS)
def test_entry_point_snapshots_before_writing(
    argv: list[str], tag: str, tmp_path: Path
) -> None:
    """The snapshot fires, with this script's tag, before any DB work."""
    result, sentinel = _run_entry_point(
        [sys.executable, *argv], tmp_path, snapshot_succeeds=True
    )

    assert sentinel.exists(), f"{argv[0]} never called the snapshot fence:\n{result.stderr}"
    assert sentinel.read_text().strip() == tag
    # The scripts go on to fail against the empty scratch DB — that is fine and
    # beside the point. What matters is the ordering: nothing can have been
    # written before the snapshot, because the snapshot came first.


@pytest.mark.parametrize(("argv", "tag"), WRITE_ENTRY_POINTS)
def test_entry_point_aborts_when_the_snapshot_fails(
    argv: list[str], tag: str, tmp_path: Path
) -> None:
    result, sentinel = _run_entry_point(
        [sys.executable, *argv], tmp_path, snapshot_succeeds=False
    )

    assert sentinel.read_text().strip() == tag
    assert result.returncode == 3, f"expected a clean abort, got:\n{result.stderr}"
    assert "ABORT:" in result.stderr
    assert "Traceback" not in result.stderr
    # Nothing was created in the scratch database — the abort landed before the
    # first connection, let alone the first write.
    assert not (tmp_path / "scratch.db").exists()


DRY_RUN_ENTRY_POINTS = [
    pytest.param(["scripts/repath_citation_paths.py", "--dry-run"], id="repath"),
    pytest.param(
        ["scripts/backfill_duplicate_citation_path_status.py", "--dry-run"],
        id="backfill-duplicate-status",
    ),
    pytest.param(["scripts/backfill_parcels.py", "--dry-run"], id="backfill-parcels"),
    pytest.param(
        ["scripts/backfill_permission_markers.py", "--dry-run"],
        id="backfill-permission-markers",
    ),
    pytest.param(
        ["scripts/backfill_table_profiles.py", "--dry-run"], id="backfill-table-profiles"
    ),
    pytest.param(["scripts/backfill_user_emails.py"], id="backfill-user-emails"),
]

REVERT_ENTRY_POINTS = [
    pytest.param(
        ["scripts/repath_citation_paths.py", "--revert", "sidecar.json"],
        "repath-citation-paths-revert",
        id="repath-revert",
    ),
    pytest.param(
        ["scripts/backfill_table_citations.py", "--revert", "sidecar.json"],
        "backfill-table-citations-revert",
        id="backfill-table-citations-revert",
    ),
]


@pytest.mark.parametrize(("argv", "tag"), REVERT_ENTRY_POINTS)
def test_reverts_are_fenced_too(argv: list[str], tag: str, tmp_path: Path) -> None:
    """Restoring a sidecar is a corpus-wide write; it gets the same fence."""
    result, sentinel = _run_entry_point(
        [sys.executable, *argv], tmp_path, snapshot_succeeds=False
    )

    assert sentinel.read_text().strip() == tag
    assert result.returncode == 3
    assert "ABORT:" in result.stderr


@pytest.mark.parametrize("argv", DRY_RUN_ENTRY_POINTS)
def test_dry_runs_are_not_fenced(argv: list[str], tmp_path: Path) -> None:
    """A dry run writes nothing, so it must not cost a snapshot."""
    _, sentinel = _run_entry_point([sys.executable, *argv], tmp_path, snapshot_succeeds=True)

    assert not sentinel.exists(), f"{argv[0]} snapshotted for a dry run"


def test_alembic_upgrade_aborts_when_the_snapshot_fails(tmp_path: Path) -> None:
    """`alembic upgrade` must not reach its first DDL without a snapshot."""
    db = tmp_path / "alembic-scratch.db"
    script, sentinel = _fake_snapshot_script(tmp_path, succeeds=False)
    env = os.environ.copy()
    env["BYLAW_SNAPSHOT_SCRIPT"] = str(script)
    env["BYLAW_FORCE_MIGRATION_SNAPSHOT"] = "1"
    env.pop("BYLAW_SKIP_MIGRATION_SNAPSHOT", None)
    env.pop("GITHUB_ACTIONS", None)
    env["DATABASE_URL"] = f"sqlite:///{db}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert result.returncode == 3, f"expected a clean abort, got:\n{result.stderr}"
    assert sentinel.read_text().strip() == "alembic-upgrade-from-unstamped"
    assert "ABORT:" in result.stderr
    assert "refusing to mutate the dev database" in result.stderr
    # alembic never got as far as stamping a version, so no migration ran.
    if db.exists():
        import sqlite3

        with sqlite3.connect(db) as conn:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert "alembic_version" not in tables


def test_alembic_read_only_commands_are_not_fenced(tmp_path: Path) -> None:
    """`alembic current` inspects; it must not trigger a dump."""
    script, sentinel = _fake_snapshot_script(tmp_path, succeeds=True)
    env = os.environ.copy()
    env["BYLAW_SNAPSHOT_SCRIPT"] = str(script)
    env["BYLAW_FORCE_MIGRATION_SNAPSHOT"] = "1"
    env.pop("BYLAW_SKIP_MIGRATION_SNAPSHOT", None)
    env.pop("GITHUB_ACTIONS", None)
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'current-scratch.db'}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert not sentinel.exists()
