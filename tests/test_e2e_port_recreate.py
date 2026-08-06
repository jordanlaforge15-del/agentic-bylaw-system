"""Tests for ABS-170: e2e-up.sh must detect a stale postgres container
whose published port no longer matches the requested PG_PORT and remove
it before `docker compose up`. e2e-down.sh must remove the postgres
container (not just stop web/fastapi) so the next launch is clean.

ABS-428 update: the container under management is now the DEDICATED
ephemeral e2e instance (`postgres-e2e`), and e2e-down destroys its data
volume too — the stale-port rm and the teardown rm both carry `-v`.

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


REPO_ROOT = Path(__file__).resolve().parents[1]


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
        assert "Removing stale postgres-e2e container (port was 5440, now 5433)" \
            in result.stdout
        docker_log = (tmp_path / "docker.log").read_text()
        # Must have issued the rm command before the next `compose up`
        # would attach to the stale container. -v included: the e2e
        # instance is ephemeral, stale data has no value (ABS-428).
        rm_idx = docker_log.find("compose rm -fsv postgres-e2e")
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
        assert "Removing stale postgres-e2e container" not in result.stdout
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
        assert "Removing stale postgres-e2e container" not in result.stdout
        docker_log = (tmp_path / "docker.log").read_text()
        assert "compose rm" not in docker_log


class TestE2eDownDestroysE2ePostgres:
    """ABS-428: e2e-down.sh must destroy the dedicated e2e Postgres —
    `docker compose rm -fsv postgres-e2e` (container + anonymous
    volumes) plus removal of the named `layer1-postgres-e2e` volume —
    so the next e2e-up boots a pristine instance. The dev `postgres`
    service must never be addressed."""

    def test_down_destroys_e2e_container_and_volume(self, tmp_path: Path):
        fake_bin = _make_fake_docker(tmp_path, stale_port="5433")
        result = _run_script(
            "e2e-down.sh",
            tmp_path=tmp_path,
            fake_bin=fake_bin,
            stop_at="down",
        )
        assert result.returncode == 0, f"stderr={result.stderr}"
        assert "Destroying e2e Postgres container + volume" in result.stdout
        docker_log = (tmp_path / "docker.log").read_text()
        assert "compose rm -fsv postgres-e2e" in docker_log, \
            f"docker calls: {docker_log}"
        # The named-volume sweep must have run (fake docker reports no
        # volumes, so the script logs the nothing-to-remove branch).
        assert "volume ls" in docker_log, f"docker calls: {docker_log}"
        assert "no e2e Postgres volume to remove" in result.stdout

    def test_down_never_touches_dev_postgres(self, tmp_path: Path):
        """No compose invocation may address the dev `postgres` service —
        removing it (or its volume) would take down the dev stack."""
        fake_bin = _make_fake_docker(tmp_path, stale_port="5433")
        _run_script("e2e-down.sh", tmp_path=tmp_path, fake_bin=fake_bin, stop_at="down")
        docker_log = (tmp_path / "docker.log").read_text()
        for line in docker_log.splitlines():
            if "compose" in line:
                # every service-addressed call must target postgres-e2e
                assert " postgres " not in f"{line} ", \
                    f"dev postgres service addressed: {line}"


# ---------------------------------------------------------------------------
# ABS-175: wait_for_port settle-time check
# ---------------------------------------------------------------------------


class TestWaitForPortSettleCheck:
    """ABS-175: `wait_for_port` must catch the bind-then-exit pattern
    (next dev port collision, uvicorn import failure, etc.) instead of
    declaring victory the instant the socket is open."""

    def _run_wait_for_port(
        self,
        tmp_path: Path,
        *,
        is_listening_returns: bool,
        pidfile_pid: str | None,
        port: str = "12345",
        timeout: str = "3",
    ) -> subprocess.CompletedProcess:
        """Source e2e-up.sh, stub is_listening to return the configured
        value, optionally point a pidfile at the configured PID, then
        invoke wait_for_port directly."""
        src = (REPO_ROOT / "scripts" / "e2e-up.sh").read_text()
        # Hijack main() — we only want wait_for_port + its deps available.
        is_listening_stub = (
            "is_listening() { return 0; }" if is_listening_returns
            else "is_listening() { return 1; }"
        )
        pidfile_setup = ""
        pidfile_arg = ""
        if pidfile_pid is not None:
            pid_path = tmp_path / "fake.pid"
            pid_path.write_text(pidfile_pid)
            pidfile_setup = ""  # already written
            pidfile_arg = str(pid_path)

        # Make WAIT_FOR_PORT_SETTLE_SECS=0 to keep the test fast.
        epilogue = (
            f"{is_listening_stub}\n"
            f"export WAIT_FOR_PORT_SETTLE_SECS=0\n"
            f"export LOG_DIR={tmp_path}\n"
            f"mkdir -p {tmp_path}\n"
            f"wait_for_port {port} testlabel {timeout} {pidfile_arg!r}\n"
        )
        patched = src.replace('main "$@"', epilogue)
        script_path = tmp_path / "patched_e2e_up.sh"
        script_path.write_text(patched)
        script_path.chmod(0o755)
        return subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_bind_then_exit_detected_via_dead_pid(self, tmp_path: Path):
        """If the recorded pidfile points at a non-existent PID by the
        time we re-check, wait_for_port must return non-zero and the
        error must mention the failing PID — not declare victory."""
        # PID 999999999 doesn't exist on macOS/Linux.
        result = self._run_wait_for_port(
            tmp_path,
            is_listening_returns=True,
            pidfile_pid="999999999",
        )
        assert result.returncode != 0, (
            f"expected non-zero exit but got success.\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "bound briefly then exited" in result.stderr, \
            f"missing error message; stderr={result.stderr}"
        assert "999999999" in result.stderr, \
            f"error should mention the failing PID; stderr={result.stderr}"

    def test_live_pid_with_open_port_succeeds(self, tmp_path: Path):
        """Happy path: socket is open AND the recorded PID is alive →
        wait_for_port returns 0 and prints the 'is up' line."""
        # Use our own PID — guaranteed alive.
        result = self._run_wait_for_port(
            tmp_path,
            is_listening_returns=True,
            pidfile_pid=str(os.getpid()),
        )
        assert result.returncode == 0, (
            f"expected success but got rc={result.returncode}.\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "testlabel on :12345 is up" in result.stdout

    def test_no_pidfile_falls_back_gracefully(self, tmp_path: Path):
        """When no pidfile is supplied (old callers / fallback), the
        check still re-verifies the port is bound — but doesn't gate
        on PID liveness. Backward-compatible."""
        result = self._run_wait_for_port(
            tmp_path,
            is_listening_returns=True,
            pidfile_pid=None,
        )
        assert result.returncode == 0, \
            f"backward-compat should pass; rc={result.returncode}"


# ---------------------------------------------------------------------------
# ABS-176: stop_pid survivor cleanup
# ---------------------------------------------------------------------------


class TestStopPidSurvivorCleanup:
    """ABS-176: when SIGTERM to the recorded PID succeeds but the port
    is still bound (uvicorn child outlived the wrapper shell), stop_pid
    must lsof for the survivor and kill it directly."""

    def _run_stop_pid(
        self,
        tmp_path: Path,
        *,
        port: str = "12345",
        recorded_pid: str | None = None,  # None → spawn a real sleep
        lsof_returns: str = "",  # PID lsof reports after the kill
    ) -> subprocess.CompletedProcess:
        """Source e2e-down.sh and call stop_pid directly with a
        controlled environment. lsof is shimmed via PATH so the test
        determines what the survivor PID looks like.

        When ``recorded_pid`` is None, the helper spawns a real
        ``sleep 600`` and uses its PID — so the primary kill in
        stop_pid actually has something live to terminate, matching
        the production scenario (live wrapper, surviving child)."""
        # Use a dead PID for the recorded pidfile so the primary kill
        # path takes the "nothing to kill" branch (fast, no waiting).
        # The post-kill port verification (ABS-176) runs regardless
        # of whether the primary kill happened, which is what we test.
        if recorded_pid is None:
            recorded_pid = "999999999"
        # Shim lsof so the test controls what stop_pid "sees" on the
        # port. The shim returns the configured survivor on the first
        # N-1 calls; after the SIGTERM to the survivor, the (N)th call
        # onward returns nothing (port freed) so the wait-loop exits
        # cleanly without hitting the SIGKILL path.
        bin_dir = tmp_path / "fake_bin"
        bin_dir.mkdir(exist_ok=True)
        fake_lsof = bin_dir / "lsof"
        # Calls happen in this order:
        #   1. stop_pid's "lsof fallback" when pidfile PID is dead — uses -tnP
        #   2. stop_pid's "ABS-176 survivor probe" — uses -tnP
        #   3..N. The survivor-kill wait-loop — uses no extra args
        # We need (1) and (2) to BOTH return the survivor PID so the
        # fallback assignment AND the survivor probe see it. Then
        # subsequent calls return nothing so the wait-loop exits.
        fake_lsof.write_text(
            "#!/usr/bin/env bash\n"
            f"state_file='{tmp_path}/lsof_called.txt'\n"
            f"survivor='{lsof_returns}'\n"
            "calls=0\n"
            "if [[ -f \"$state_file\" ]]; then calls=$(cat \"$state_file\"); fi\n"
            "echo $((calls + 1)) > \"$state_file\"\n"
            "if [[ $calls -lt 2 && -n \"$survivor\" ]]; then\n"
            "  if [[ \"$*\" == *'-tnP'* ]]; then echo \"$survivor\"; fi\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n"  # no listener (port freed by survivor-kill)
        )
        fake_lsof.chmod(0o755)

        src = (REPO_ROOT / "scripts" / "e2e-down.sh").read_text()
        # Drop the `log "Stopping..."; stop_pid ...` cascade and just
        # invoke stop_pid with our controlled args.
        pidfile = tmp_path / "fake.pid"
        pidfile.write_text(recorded_pid)
        epilogue = f"stop_pid {pidfile!s} testlabel {port}\n"
        # Replace everything from the first `log "Stopping Next.js"` to end.
        marker = 'log "Stopping Next.js"'
        idx = src.find(marker)
        assert idx != -1
        patched = src[:idx] + epilogue
        script_path = tmp_path / "patched_e2e_down.sh"
        script_path.write_text(patched)
        script_path.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        }
        return subprocess.run(
            ["bash", str(script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_survivor_after_kill_is_targeted(self, tmp_path: Path):
        """When stop_pid kills the recorded PID but a survivor still
        binds the port, stop_pid lsofs for it and kills it directly.
        The new log line must surface so the operator can see it."""
        # Use our own PID as the survivor — guaranteed not to exist by
        # the time we check (we don't actually want to kill ourselves;
        # the shimmed lsof returns this PID once, then nothing, so
        # stop_pid's loop sees "port freed").
        result = self._run_stop_pid(
            tmp_path,
            lsof_returns="999999998",  # not our actual pid; kill no-ops
        )
        assert result.returncode == 0, f"stderr={result.stderr}"
        assert "still bound after SIGTERM" in result.stdout, \
            f"survivor-kill log missing; stdout={result.stdout}"
        assert "killing survivor PID 999999998" in result.stdout

    def test_no_survivor_clean_exit(self, tmp_path: Path):
        """Happy path: SIGTERM works, no survivor — no extra log line,
        clean rc=0."""
        result = self._run_stop_pid(
            tmp_path,
            lsof_returns="",  # lsof returns no listener
        )
        assert result.returncode == 0
        assert "still bound after SIGTERM" not in result.stdout
