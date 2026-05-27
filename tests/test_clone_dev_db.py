"""Coverage for scripts/clone-dev-db.sh and scripts/destroy-clone-db.sh.

Both scripts wrap Docker Compose and docker exec. Spinning up real Docker in
pytest is overkill, so each test injects a fake `docker` shim on PATH that
logs every invocation and simulates success. We assert the scripts' contracts:

clone-dev-db.sh
  1. Happy path: writes .env.clone.<name> with a valid DATABASE_URL.
  2. Port argument is reflected in the DATABASE_URL.
  3. Most-recent dump is selected when multiple files exist.
  4. Missing backup dir (no dump files) exits nonzero.
  5. Missing experiment name exits nonzero.

destroy-clone-db.sh
  6. Calls `docker compose -p <name> ... down -v`.
  7. Missing experiment name exits nonzero.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLONE_SCRIPT = REPO_ROOT / "scripts" / "clone-dev-db.sh"
DESTROY_SCRIPT = REPO_ROOT / "scripts" / "destroy-clone-db.sh"

FAKE_DUMP_BYTES = b"PGDMP-fake-payload\n"


def _write_fake_docker(bin_dir: Path, call_log: Path) -> Path:
    """Drop a `docker` shim that logs its args and always exits 0."""
    script = f"""#!/usr/bin/env bash
# Fake docker shim used by tests/test_clone_dev_db.py.
printf '%s\\n' "$*" >> "{call_log}"
case "$1" in
  compose)
    exit 0
    ;;
  exec)
    # docker exec [-i] <container> <cmd> [args...]
    shift
    while [[ "${{1:-}}" == -* ]]; do shift; done
    shift  # container name
    case "${{1:-}}" in
      pg_isready) exit 0 ;;
      dropdb)     exit 0 ;;
      createdb)   exit 0 ;;
      pg_restore)
        cat >/dev/null  # consume stdin (the dump file)
        exit 0
        ;;
    esac
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


def _make_dump(backup_dir: Path, name: str = "layer1-Mon.dump") -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dump = backup_dir / name
    dump.write_bytes(FAKE_DUMP_BYTES)
    return dump


def _run_clone(
    tmp_path: Path,
    bin_dir: Path,
    *,
    experiment: str = "test-exp",
    port: str = "5499",
    backup_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    if backup_dir is not None:
        env["BYLAW_BACKUP_DIR"] = str(backup_dir)
    return subprocess.run(
        [str(CLONE_SCRIPT), experiment, port],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
    )


def _run_destroy(
    bin_dir: Path,
    *,
    experiment: str = "test-exp",
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return subprocess.run(
        [str(DESTROY_SCRIPT), experiment],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def fake_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    backup_dir = tmp_path / "backups"
    bin_dir = tmp_path / "bin"
    call_log = tmp_path / "docker_calls.log"
    bin_dir.mkdir()
    _write_fake_docker(bin_dir, call_log)
    return backup_dir, bin_dir, call_log


def test_clone_writes_env_file(fake_env: tuple[Path, Path, Path], tmp_path: Path) -> None:
    backup_dir, bin_dir, _ = fake_env
    _make_dump(backup_dir)

    result = _run_clone(tmp_path, bin_dir, experiment="my-exp", port="5499", backup_dir=backup_dir)

    assert result.returncode == 0, result.stderr
    env_file = tmp_path / ".env.clone.my-exp"
    assert env_file.exists(), f"expected {env_file}; stdout: {result.stdout}"
    content = env_file.read_text()
    assert "DATABASE_URL=" in content
    assert "postgresql+psycopg://" in content
    assert "my-exp" not in content  # URL uses localhost, not the experiment name


def test_clone_port_in_database_url(fake_env: tuple[Path, Path, Path], tmp_path: Path) -> None:
    backup_dir, bin_dir, _ = fake_env
    _make_dump(backup_dir)

    result = _run_clone(tmp_path, bin_dir, experiment="port-test", port="5477", backup_dir=backup_dir)

    assert result.returncode == 0, result.stderr
    env_file = tmp_path / ".env.clone.port-test"
    assert ":5477/" in env_file.read_text()


def test_clone_selects_most_recent_dump(
    fake_env: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    backup_dir, bin_dir, call_log = fake_env
    _make_dump(backup_dir, "layer1-Mon.dump")
    import time; time.sleep(0.05)
    _make_dump(backup_dir, "layer1-Tue.dump")

    result = _run_clone(tmp_path, bin_dir, experiment="pick-test", port="5499", backup_dir=backup_dir)

    assert result.returncode == 0, result.stderr
    assert "layer1-Tue.dump" in result.stdout


def test_clone_no_dump_fails(fake_env: tuple[Path, Path, Path], tmp_path: Path) -> None:
    backup_dir, bin_dir, _ = fake_env
    # backup_dir does not exist — no dumps available

    result = _run_clone(tmp_path, bin_dir, experiment="no-dump", port="5499", backup_dir=backup_dir)

    assert result.returncode != 0
    assert "no dump files" in result.stderr


def test_clone_no_experiment_name_fails(fake_env: tuple[Path, Path, Path], tmp_path: Path) -> None:
    _, bin_dir, _ = fake_env
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    result = subprocess.run(
        [str(CLONE_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Usage" in result.stderr


def test_destroy_calls_compose_down(fake_env: tuple[Path, Path, Path]) -> None:
    _, bin_dir, call_log = fake_env

    result = _run_destroy(bin_dir, experiment="my-clone")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text()
    # The shim logs args as a single line per invocation.
    assert "down" in calls
    assert "-v" in calls


def test_destroy_no_experiment_name_fails(fake_env: tuple[Path, Path, Path]) -> None:
    _, bin_dir, _ = fake_env
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    result = subprocess.run(
        [str(DESTROY_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Usage" in result.stderr
