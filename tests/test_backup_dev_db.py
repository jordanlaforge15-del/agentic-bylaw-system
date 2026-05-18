"""Coverage for scripts/backup-dev-db.sh.

The script wraps `docker exec ... pg_dump`. Spinning up real Docker in
pytest is overkill, so each test puts a fake `docker` shim on PATH that
simulates either a running container with a tiny dump payload, or a
missing/stopped container. We assert the script's three contracts:

1. Happy path writes a per-DOW dump file with the streamed bytes intact.
2. Re-running on the same day overwrites in place (no second file).
3. Container-not-running exits nonzero and leaves no dump artifact.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "backup-dev-db.sh"

FAKE_DUMP_BYTES = b"PGDMP-fake-payload-for-tests\n"


def _write_fake_docker(bin_dir: Path, *, running: bool) -> Path:
    """Drop a `docker` shim into bin_dir that the script will find via PATH."""
    state = "true" if running else "false"
    script = f"""#!/usr/bin/env bash
# Fake docker shim used by tests/test_backup_dev_db.py.
case "$1" in
  inspect)
    # `docker inspect --format '{{{{.State.Running}}}}' <name>`
    echo "{state}"
    exit 0
    ;;
  exec)
    if [ "{state}" != "true" ]; then
      echo "Error: container not running" >&2
      exit 1
    fi
    # Stream a deterministic byte payload that stands in for pg_dump output.
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


def _run_backup(backup_dir: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["BYLAW_BACKUP_DIR"] = str(backup_dir)
    env["BYLAW_PG_CONTAINER"] = "fake-container"
    return subprocess.run(
        [str(SCRIPT)],
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


def test_writes_dump_keyed_by_day_of_week(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)

    result = _run_backup(backup_dir, bin_dir)

    assert result.returncode == 0, result.stderr
    expected_name = f"layer1-{datetime.now().strftime('%a')}.dump"
    dump = backup_dir / expected_name
    assert dump.exists(), f"expected {dump} to exist; got {list(backup_dir.iterdir())}"
    # The script must stream pg_dump bytes through unchanged.
    assert dump.read_bytes() == FAKE_DUMP_BYTES
    # No .tmp file should be left behind on success.
    assert not (backup_dir / f"{expected_name}.tmp").exists()
    # Log line was appended.
    log = (backup_dir / "backup.log").read_text()
    assert "OK: wrote" in log


def test_rerun_overwrites_in_place(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)

    first = _run_backup(backup_dir, bin_dir)
    assert first.returncode == 0
    second = _run_backup(backup_dir, bin_dir)
    assert second.returncode == 0

    # Only one dump file for today — rotation slot, not append.
    dumps = sorted(p.name for p in backup_dir.glob("layer1-*.dump"))
    assert len(dumps) == 1, dumps


def test_container_not_running_fails_loudly(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=False)

    result = _run_backup(backup_dir, bin_dir)

    assert result.returncode != 0
    assert not list(backup_dir.glob("layer1-*.dump"))
    log = (backup_dir / "backup.log").read_text()
    assert "is not running" in log
