"""Coverage for scripts/snapshot-before-migration.sh (ABS-499).

The labelled snapshot is the fence that stands between a data migration and an
unrecoverable dev database. Its contracts:

1. A tag produces a labelled dump under `labelled/`, and the path is the only
   thing on stdout so callers can capture it.
2. Labelled snapshots are **rotation-exempt** — the nightly day-of-week job
   must never overwrite one. This is the property the whole ticket exists for.
3. Two snapshots with the same tag coexist (a snapshot is never clobbered by a
   later one).
4. A failing `pg_dump` exits nonzero and leaves no artifact — the caller is
   expected to abort rather than migrate blind.

As in tests/test_backup_dev_db.py, `docker` is faked via a temp PATH shim
rather than spinning up a real container.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO_ROOT / "scripts" / "snapshot-before-migration.sh"
BACKUP = REPO_ROOT / "scripts" / "backup-dev-db.sh"

FAKE_DUMP_BYTES = b"PGDMP-fake-payload-for-tests\n"


def _write_fake_docker(bin_dir: Path, *, running: bool = True, dump_fails: bool = False) -> Path:
    state = "true" if running else "false"
    dump_rc = "1" if dump_fails else "0"
    script = f"""#!/usr/bin/env bash
# Fake docker shim used by tests/test_snapshot_before_migration.py.
case "$1" in
  inspect)
    echo "{state}"
    exit 0
    ;;
  exec)
    if [ "{state}" != "true" ]; then
      echo "Error: container not running" >&2
      exit 1
    fi
    if [ "{dump_rc}" != "0" ]; then
      echo "pg_dump: error: connection failed" >&2
      exit 1
    fi
    printf '%s' "PGDMP-fake-payload-for-tests"
    printf '\\n'
    exit 0
    ;;
  *)
    echo "fake docker: unsupported verb $1" >&2
    exit 2
    ;;
esac
"""
    path = bin_dir / "docker"
    path.write_text(script)
    path.chmod(0o755)
    return path


def _run(script: Path, backup_dir: Path, bin_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["BYLAW_BACKUP_DIR"] = str(backup_dir)
    env["BYLAW_PG_CONTAINER"] = "fake-container"
    return subprocess.run(
        [str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def fake_env(tmp_path: Path) -> tuple[Path, Path]:
    backup_dir = tmp_path / "backups"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return backup_dir, bin_dir


def test_writes_labelled_snapshot_and_prints_path(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir)

    result = _run(SNAPSHOT, backup_dir, bin_dir, "abs-488-repath")

    assert result.returncode == 0, result.stderr
    printed = Path(result.stdout.strip())
    assert printed.exists(), result.stdout
    assert printed.parent == backup_dir / "labelled"
    assert printed.name.startswith("layer1-abs-488-repath-")
    assert printed.suffix == ".dump"
    assert printed.read_bytes() == FAKE_DUMP_BYTES
    assert not list(printed.parent.glob("*.tmp"))
    assert "OK: wrote" in (backup_dir / "backup.log").read_text()


def test_labelled_snapshot_survives_the_nightly_rotation(fake_env: tuple[Path, Path]) -> None:
    """The whole point: day-of-week rotation must not overwrite a labelled dump."""
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir)

    snapshot = Path(_run(SNAPSHOT, backup_dir, bin_dir, "abs-480-backfill").stdout.strip())
    assert snapshot.exists()
    # Mark the snapshot so an overwrite is detectable even at identical size.
    snapshot.write_bytes(b"PRE-MIGRATION-STATE")

    # Run the rotating nightly backup for every slot in the cycle.
    for _ in range(8):
        rotated = _run(BACKUP, backup_dir, bin_dir)
        assert rotated.returncode == 0, rotated.stderr

    assert snapshot.exists(), "nightly rotation deleted the labelled snapshot"
    assert snapshot.read_bytes() == b"PRE-MIGRATION-STATE", (
        "nightly rotation overwrote the labelled snapshot"
    )
    # And the rotation slot itself is a *different* file, in the parent dir.
    dow_dump = backup_dir / f"layer1-{datetime.now().strftime('%a')}.dump"
    assert dow_dump.exists()
    assert dow_dump != snapshot


def test_repeated_tags_do_not_clobber_each_other(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir)

    first = Path(_run(SNAPSHOT, backup_dir, bin_dir, "same-tag").stdout.strip())
    first.write_bytes(b"FIRST")
    # Filenames carry a whole-second stamp; make the second run land in a new one.
    subprocess.run(["sleep", "1.1"], check=True)
    second = Path(_run(SNAPSHOT, backup_dir, bin_dir, "same-tag").stdout.strip())

    assert first != second
    assert first.read_bytes() == b"FIRST"
    assert second.read_bytes() == FAKE_DUMP_BYTES


def test_tag_is_sanitised_for_the_filesystem(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir)

    result = _run(SNAPSHOT, backup_dir, bin_dir, "../../etc/passwd upgrade")

    assert result.returncode == 0, result.stderr
    written = Path(result.stdout.strip())
    assert written.parent == backup_dir / "labelled"
    assert ".." not in written.name


def test_missing_tag_is_a_usage_error(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir)

    result = _run(SNAPSHOT, backup_dir, bin_dir)

    assert result.returncode == 64
    assert not (backup_dir / "labelled").exists()


def test_dump_failure_exits_nonzero_and_leaves_nothing(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, dump_fails=True)

    result = _run(SNAPSHOT, backup_dir, bin_dir, "abs-499")

    assert result.returncode != 0
    assert not list((backup_dir / "labelled").glob("*.dump"))
    assert not list((backup_dir / "labelled").glob("*.tmp"))
    assert "pg_dump failed" in (backup_dir / "backup.log").read_text()


def test_container_not_running_refuses_to_snapshot(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=False)

    result = _run(SNAPSHOT, backup_dir, bin_dir, "abs-499")

    assert result.returncode != 0
    assert not list(backup_dir.glob("labelled/*.dump"))
    assert "is not running" in (backup_dir / "backup.log").read_text()
