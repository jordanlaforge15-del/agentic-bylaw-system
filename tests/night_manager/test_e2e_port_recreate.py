"""Tests for ABS-170: e2e-up.sh must detect a stale postgres container
whose published port no longer matches the requested PG_PORT and remove
it before `docker compose up`. e2e-down.sh must remove the postgres
container (not just stop web/fastapi) so the next launch is clean.

Both scripts are bash; we test them by invoking them with a PATH that
shadows `docker` with a fake binary whose behavior we control. We
short-circuit before the real `ensure_postgres` work by exporting an
env signal that the fake binary uses to decide what to print, and we
exit the script via `start_fastapi` failing fast (no .venv) so we only
exercise the parts under test.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_fake_docker(tmp_path: Path, stale_port: str | None) -> Path:
    """Write a fake `docker` binary that the scripts will invoke instead
    of the real one. The fake responds to the exact subcommands
    e2e-up.sh / e2e-down.sh issue against `docker compose ps`,
    `docker port`, `docker compose rm`, `docker compose exec`, and
    `docker compose up`. Behavior is keyed off ``stale_port``:

    * ``None``  → no existing container; `compose ps -aq postgres` is empty.
    * non-None  → existing container whose `docker port` reports
      ``0.0.0.0:<stale_port>``, simulating the bug.

    Every invocation is logged to ``tmp_path/docker.log`` so the test can
    assert call ordering.
    """
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "docker"
    stale_arg = stale_port or ""
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"log='{tmp_path}/docker.log'\n"
        f"stale_port='{stale_arg}'\n"
        "echo \"docker $*\" >> \"$log\"\n"
        "case \"$1 $2\" in\n"
        "  'compose version')\n"
        "    echo 'Docker Compose version v2.0.0'; exit 0;;\n"
        "  'compose ps')\n"
        "    if [[ \"$*\" == *'-aq postgres'* ]]; then\n"
        "      if [[ -n \"$stale_port\" ]]; then echo 'fakecontainer123'; fi\n"
        "      exit 0\n"
        "    fi;;\n"
        "  'inspect fakecontainer123')\n"
        "    if [[ -n \"$stale_port\" ]]; then echo \"$stale_port\"; fi\n"
        "    exit 0;;\n"
        "  'compose rm')\n"
        "    exit 0;;\n"
        "  'compose exec')\n"
        "    # ensure_postgres calls pg_isready via compose exec — pretend\n"
        "    # postgres is healthy so the script returns early at the\n"
        "    # 'already healthy' branch and we don't try to actually boot.\n"
        "    exit 0;;\n"
        "  'compose up')\n"
        "    exit 0;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    return bin_dir


def _run_script(
    script: str,
    *,
    tmp_path: Path,
    fake_bin: Path,
    extra_env: dict[str, str] | None = None,
    stop_at: str = "ensure_postgres",
) -> subprocess.CompletedProcess:
    """Write a patched copy of the script to ``tmp_path`` (so
    ``BASH_SOURCE[0]`` and the SCRIPT_DIR / REPO_ROOT computation
    resolve naturally), shadow ``docker`` via PATH, and replace
    ``main "$@"`` so we only exercise the function(s) under test.
    The patched script lives in a scripts/ subdir of a fake repo
    root that contains the minimal files e2e-up.sh demands
    (``web/.env.local.example``, ``.venv/bin/python``)."""
    fake_root = tmp_path / "fake_repo"
    (fake_root / "scripts").mkdir(parents=True, exist_ok=True)
    (fake_root / "web").mkdir(parents=True, exist_ok=True)
    (fake_root / "web" / ".env.local.example").write_text("# fake\n")
    (fake_root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (fake_root / ".venv" / "bin" / "python").write_text("#!/bin/sh\nexit 0\n")
    (fake_root / ".venv" / "bin" / "python").chmod(0o755)

    src = (REPO_ROOT / "scripts" / script).read_text()
    if stop_at == "ensure_postgres":
        epilogue = "ensure_compose_prereqs\nensure_postgres\n"
        patched = src.replace('main "$@"', epilogue)
    elif stop_at == "down":
        # Stub stop_pid so the test doesn't exit on `set -euo pipefail`
        # from lsof returning 1 against unbound ports. The thing under
        # test is the `docker_compose_cmd rm -fs postgres` at the end.
        epilogue = ""
        patched = src.replace(
            "stop_pid() {",
            "stop_pid() { return 0; }\n_orig_stop_pid() {",
        )
    else:
        epilogue = ""
        patched = src
    patched_path = fake_root / "scripts" / script
    patched_path.write_text(patched)
    patched_path.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", str(patched_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


class TestE2eUpPortMismatchDetection:
    """e2e-up.sh ensure_postgres must remove a stale container whose
    published port differs from PG_PORT, then let `docker compose up`
    create a fresh one with the correct port mapping."""

    def test_stale_port_triggers_rm(self, tmp_path: Path):
        fake_bin = _make_fake_docker(tmp_path, stale_port="5440")
        result = _run_script(
            "e2e-up.sh",
            tmp_path=tmp_path,
            fake_bin=fake_bin,
            extra_env={"PG_PORT": "5433"},
        )
        assert result.returncode == 0, f"stderr={result.stderr}"
        assert "Removing stale postgres container (port was 5440, now 5433)" \
            in result.stdout
        docker_log = (tmp_path / "docker.log").read_text()
        # Must have issued the rm command before the next `compose up`
        # would attach to the stale container.
        rm_idx = docker_log.find("compose rm -fs postgres")
        assert rm_idx != -1, f"docker calls: {docker_log}"

    def test_matching_port_skips_rm(self, tmp_path: Path):
        """When existing container's port already matches PG_PORT, no rm
        is issued — the 'already healthy — reusing' fast path is intact."""
        fake_bin = _make_fake_docker(tmp_path, stale_port="5433")
        result = _run_script(
            "e2e-up.sh",
            tmp_path=tmp_path,
            fake_bin=fake_bin,
            extra_env={"PG_PORT": "5433"},
        )
        assert result.returncode == 0
        assert "Removing stale postgres container" not in result.stdout
        docker_log = (tmp_path / "docker.log").read_text()
        assert "compose rm" not in docker_log

    def test_no_container_skips_rm(self, tmp_path: Path):
        """When there's no existing container, no rm is issued — the
        first-launch path is unchanged."""
        fake_bin = _make_fake_docker(tmp_path, stale_port=None)
        result = _run_script(
            "e2e-up.sh",
            tmp_path=tmp_path,
            fake_bin=fake_bin,
            extra_env={"PG_PORT": "5433"},
        )
        assert result.returncode == 0
        assert "Removing stale postgres container" not in result.stdout
        docker_log = (tmp_path / "docker.log").read_text()
        assert "compose rm" not in docker_log


class TestE2eDownRemovesPostgresContainer:
    """e2e-down.sh must end with `docker compose rm -fs postgres` so
    that the next e2e-up can bind a different PG_PORT cleanly. Volume
    is preserved (no -v) so DB content survives container recreation."""

    def test_down_issues_rm_postgres(self, tmp_path: Path):
        fake_bin = _make_fake_docker(tmp_path, stale_port="5433")
        result = _run_script(
            "e2e-down.sh",
            tmp_path=tmp_path,
            fake_bin=fake_bin,
            stop_at="down",
        )
        assert result.returncode == 0, f"stderr={result.stderr}"
        assert "Removing Postgres container" in result.stdout
        docker_log = (tmp_path / "docker.log").read_text()
        assert "compose rm -fs postgres" in docker_log, \
            f"docker calls: {docker_log}"

    def test_down_does_not_remove_volume(self, tmp_path: Path):
        """The rm must NOT pass -v — that would nuke the named volume
        and lose the DB content between launches on the same PG_PORT."""
        fake_bin = _make_fake_docker(tmp_path, stale_port="5433")
        _run_script("e2e-down.sh", tmp_path=tmp_path, fake_bin=fake_bin, stop_at="down")
        docker_log = (tmp_path / "docker.log").read_text()
        for line in docker_log.splitlines():
            if "compose rm" in line:
                assert " -v" not in line, \
                    f"rm with -v would destroy named volume: {line}"
