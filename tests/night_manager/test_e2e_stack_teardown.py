"""Tests for ABS-161: e2e stack teardown on agent exit + orphan reaper.

Two behaviors:

1. After any agent exit (success, failure, stuck-kill, post-success-idle),
   NM invokes e2e-down.sh with the issue's port env vars so uvicorn /
   Next.js processes don't outlive the agent.

2. At NM startup, an orphan reaper scans the NM port ranges for TCP
   listeners whose PPID is 1 (reparented to init after their parent
   died) and SIGTERMs them.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


def _make_issue(
    tmp_path: Path,
    identifier: str = "ABS-161",
    pg: int = 5440,
    api: int = 8009,
    web: int = 3009,
) -> Any:
    from scripts.night_manager.state import IssuePorts, IssueState

    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)
    log_file = tmp_path / f"{identifier}.jsonl"
    return IssueState(
        identifier=identifier,
        title="Teardown test",
        branch=f"agent/{identifier}",
        ports=IssuePorts(pg=pg, api=api, web=web),
        worktree=str(worktree),
        log_file=str(log_file),
    )


# ---------------------------------------------------------------------------
# 1) teardown_e2e_stack — invokes e2e-down.sh with correct env
# ---------------------------------------------------------------------------


class TestTeardownE2eStack:
    @pytest.mark.asyncio
    async def test_calls_e2e_down_with_port_env(self, tmp_path: Path):
        """teardown_e2e_stack must invoke e2e-down.sh with the issue's
        port environment variables so the lsof fallback targets the
        right listeners."""
        from scripts.night_manager.agent import teardown_e2e_stack

        issue = _make_issue(tmp_path)
        captured_env: dict[str, str] = {}
        captured_cwd: str = ""

        async def fake_subprocess(*args, **kwargs):
            nonlocal captured_env, captured_cwd
            captured_env = kwargs.get("env", {})
            captured_cwd = kwargs.get("cwd", "")
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_proc.kill = MagicMock()
            return mock_proc

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                    side_effect=fake_subprocess):
            await teardown_e2e_stack(issue)

        assert captured_env.get("PG_PORT") == "5440"
        assert captured_env.get("E2E_FASTAPI_PORT") == "8009"
        assert captured_env.get("E2E_WEB_PORT") == "3009"
        assert captured_env.get("E2E_API_URL") == "http://127.0.0.1:8009"
        assert captured_env.get("E2E_BASE_URL") == "http://localhost:3009"
        assert captured_cwd == str(tmp_path / "worktree")

    @pytest.mark.asyncio
    async def test_falls_back_to_repo_root_when_worktree_gone(self, tmp_path: Path):
        """If the worktree directory was already removed, teardown should
        fall back to REPO_ROOT as cwd (e2e-down.sh uses lsof by port)."""
        from scripts.night_manager.agent import teardown_e2e_stack

        issue = _make_issue(tmp_path)
        issue.worktree = str(tmp_path / "nonexistent")
        captured_cwd: str = ""

        async def fake_subprocess(*args, **kwargs):
            nonlocal captured_cwd
            captured_cwd = kwargs.get("cwd", "")
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_proc.kill = MagicMock()
            return mock_proc

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                    side_effect=fake_subprocess):
            await teardown_e2e_stack(issue)

        from scripts.night_manager.config import REPO_ROOT
        assert captured_cwd == str(REPO_ROOT)

    @pytest.mark.asyncio
    async def test_noop_when_no_ports(self, tmp_path: Path):
        """No-op when the issue has no ports allocated."""
        from scripts.night_manager.agent import teardown_e2e_stack
        from scripts.night_manager.state import IssueState

        issue = IssueState(
            identifier="ABS-999",
            title="No ports",
            branch="agent/ABS-999",
            ports=None,
        )

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec") as mock:
            await teardown_e2e_stack(issue)

        mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_nonzero_exit_gracefully(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ):
        """A failing e2e-down.sh logs a warning but does not raise."""
        from scripts.night_manager.agent import teardown_e2e_stack
        import logging

        issue = _make_issue(tmp_path)

        async def fake_subprocess(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                return_value=(b"", b"some error output"),
            )
            mock_proc.returncode = 1
            mock_proc.kill = MagicMock()
            return mock_proc

        with caplog.at_level(logging.WARNING, logger="night_manager.agent"):
            with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                        side_effect=fake_subprocess):
                await teardown_e2e_stack(issue)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("e2e-down" in r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_handles_timeout_gracefully(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ):
        """If e2e-down hangs, teardown kills it and logs a warning."""
        from scripts.night_manager.agent import teardown_e2e_stack
        import logging

        issue = _make_issue(tmp_path)

        async def fake_subprocess(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(
                side_effect=asyncio.TimeoutError(),
            )
            mock_proc.returncode = None
            mock_proc.kill = MagicMock()
            return mock_proc

        with caplog.at_level(logging.WARNING, logger="night_manager.agent"):
            with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                        side_effect=fake_subprocess):
                # Patch wait_for to propagate the TimeoutError from communicate
                await teardown_e2e_stack(issue)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("timed out" in r.getMessage() for r in warnings)


# ---------------------------------------------------------------------------
# 2) _run_agent_lifecycle integration — teardown called after monitor_agent
# ---------------------------------------------------------------------------


class TestLifecycleTeardownIntegration:
    @pytest.mark.asyncio
    async def test_teardown_called_after_successful_agent(self, tmp_path: Path):
        """After a successful agent run, teardown_e2e_stack must be called."""
        issue = _make_issue(tmp_path)
        issue.linear_id = "lin-123"

        teardown_calls: list[str] = []

        real_teardown = None

        async def fake_teardown(iss):
            teardown_calls.append(iss.identifier)

        async def fake_monitor(proc, iss, timeout):
            return 0, "All done"

        state = MagicMock()
        linear = AsyncMock()
        workflow_states = {"In Review": "state-id"}

        with patch("scripts.night_manager.agent.monitor_agent", fake_monitor), \
             patch("scripts.night_manager.agent.teardown_e2e_stack", fake_teardown):
            from scripts.night_manager.main import _run_agent_lifecycle
            await _run_agent_lifecycle(
                MagicMock(), issue, MagicMock(), state, linear, workflow_states,
            )

        assert issue.identifier in teardown_calls

    @pytest.mark.asyncio
    async def test_teardown_called_after_failed_agent(self, tmp_path: Path):
        """After a failed agent run, teardown_e2e_stack must still be called."""
        issue = _make_issue(tmp_path)
        issue.linear_id = "lin-456"

        teardown_calls: list[str] = []

        async def fake_teardown(iss):
            teardown_calls.append(iss.identifier)

        async def fake_monitor(proc, iss, timeout):
            return 1, "Something broke"

        state = MagicMock()
        linear = AsyncMock()
        workflow_states = {}

        with patch("scripts.night_manager.agent.monitor_agent", fake_monitor), \
             patch("scripts.night_manager.agent.teardown_e2e_stack", fake_teardown):
            from scripts.night_manager.main import _run_agent_lifecycle
            await _run_agent_lifecycle(
                MagicMock(), issue, MagicMock(), state, linear, workflow_states,
            )

        assert issue.identifier in teardown_calls


# ---------------------------------------------------------------------------
# 3) Orphan reaper — scans NM port ranges, kills PPID=1 listeners
# ---------------------------------------------------------------------------


LSOF_HEADER = "COMMAND     PID   USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"


class TestOrphanReaper:
    @pytest.mark.asyncio
    async def test_reaps_ppid1_listener_on_nm_port(self):
        """A listener on an NM API port with PPID=1 should be killed."""
        from scripts.night_manager.agent import reap_orphan_listeners

        lsof_output = (
            LSOF_HEADER
            + "uvicorn   12345  user   3u  IPv4  0x1234  0t0  TCP 127.0.0.1:8009 (LISTEN)\n"
        )

        call_log: list[tuple] = []

        async def fake_subprocess(*args, **kwargs):
            cmd = args
            mock_proc = AsyncMock()
            mock_proc.kill = MagicMock()

            if cmd[0] == "lsof":
                mock_proc.communicate = AsyncMock(
                    return_value=(lsof_output.encode(), b""),
                )
                mock_proc.returncode = 0
            elif cmd[0] == "ps":
                mock_proc.communicate = AsyncMock(
                    return_value=(b"    1", b""),
                )
                mock_proc.returncode = 0
            else:
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_proc.returncode = 0
            return mock_proc

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                    side_effect=fake_subprocess), \
             patch("scripts.night_manager.agent.os.kill") as mock_kill:
            reaped = await reap_orphan_listeners()

        assert len(reaped) == 1
        assert reaped[0] == (12345, 8009)
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_skips_non_orphan_listener(self):
        """A listener on an NM port with PPID != 1 should NOT be killed."""
        from scripts.night_manager.agent import reap_orphan_listeners

        lsof_output = (
            LSOF_HEADER
            + "uvicorn   12345  user   3u  IPv4  0x1234  0t0  TCP 127.0.0.1:8009 (LISTEN)\n"
        )

        async def fake_subprocess(*args, **kwargs):
            cmd = args
            mock_proc = AsyncMock()
            mock_proc.kill = MagicMock()

            if cmd[0] == "lsof":
                mock_proc.communicate = AsyncMock(
                    return_value=(lsof_output.encode(), b""),
                )
                mock_proc.returncode = 0
            elif cmd[0] == "ps":
                mock_proc.communicate = AsyncMock(
                    return_value=(b"  999", b""),
                )
                mock_proc.returncode = 0
            return mock_proc

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                    side_effect=fake_subprocess), \
             patch("scripts.night_manager.agent.os.kill") as mock_kill:
            reaped = await reap_orphan_listeners()

        assert reaped == []
        mock_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_listener_outside_nm_ranges(self):
        """A PPID=1 listener on a port outside NM ranges is ignored."""
        from scripts.night_manager.agent import reap_orphan_listeners

        lsof_output = (
            LSOF_HEADER
            + "nginx     99999  user   3u  IPv4  0x1234  0t0  TCP *:80 (LISTEN)\n"
        )

        async def fake_subprocess(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.kill = MagicMock()
            if args[0] == "lsof":
                mock_proc.communicate = AsyncMock(
                    return_value=(lsof_output.encode(), b""),
                )
                mock_proc.returncode = 0
            elif args[0] == "ps":
                mock_proc.communicate = AsyncMock(return_value=(b"    1", b""))
                mock_proc.returncode = 0
            return mock_proc

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                    side_effect=fake_subprocess), \
             patch("scripts.night_manager.agent.os.kill") as mock_kill:
            reaped = await reap_orphan_listeners()

        assert reaped == []
        mock_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_reaps_multiple_orphans(self):
        """Multiple orphan listeners across different NM port ranges."""
        from scripts.night_manager.agent import reap_orphan_listeners

        lsof_output = (
            LSOF_HEADER
            + "uvicorn   111  user   3u  IPv4  0x1  0t0  TCP 127.0.0.1:8009 (LISTEN)\n"
            + "node      222  user   3u  IPv4  0x2  0t0  TCP *:3005 (LISTEN)\n"
            + "postgres  333  user   3u  IPv4  0x3  0t0  TCP 127.0.0.1:5440 (LISTEN)\n"
            + "nginx     444  user   3u  IPv4  0x4  0t0  TCP *:80 (LISTEN)\n"
        )

        async def fake_subprocess(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.kill = MagicMock()
            if args[0] == "lsof":
                mock_proc.communicate = AsyncMock(
                    return_value=(lsof_output.encode(), b""),
                )
                mock_proc.returncode = 0
            elif args[0] == "ps":
                mock_proc.communicate = AsyncMock(return_value=(b"    1", b""))
                mock_proc.returncode = 0
            return mock_proc

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                    side_effect=fake_subprocess), \
             patch("scripts.night_manager.agent.os.kill") as mock_kill:
            reaped = await reap_orphan_listeners()

        reaped_pids = {pid for pid, _ in reaped}
        assert 111 in reaped_pids  # API port 8009
        assert 222 in reaped_pids  # WEB port 3005
        assert 333 in reaped_pids  # PG port 5440
        assert 444 not in reaped_pids  # port 80 — not NM range
        assert len(reaped) == 3

    @pytest.mark.asyncio
    async def test_lsof_failure_returns_empty(self):
        """If lsof fails (e.g. not installed), reaper returns empty."""
        from scripts.night_manager.agent import reap_orphan_listeners

        async def fake_subprocess(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.kill = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 1
            return mock_proc

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                    side_effect=fake_subprocess):
            reaped = await reap_orphan_listeners()

        assert reaped == []

    @pytest.mark.asyncio
    async def test_no_listeners_returns_empty(self):
        """If no listeners are found, reaper returns empty."""
        from scripts.night_manager.agent import reap_orphan_listeners

        async def fake_subprocess(*args, **kwargs):
            mock_proc = AsyncMock()
            mock_proc.kill = MagicMock()
            mock_proc.communicate = AsyncMock(
                return_value=(LSOF_HEADER.encode(), b""),
            )
            mock_proc.returncode = 0
            return mock_proc

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec",
                    side_effect=fake_subprocess):
            reaped = await reap_orphan_listeners()

        assert reaped == []


# ---------------------------------------------------------------------------
# 4) Port-range helper
# ---------------------------------------------------------------------------


class TestPortInNmRange:
    def test_api_port_in_range(self):
        from scripts.night_manager.agent import _port_in_nm_range
        assert _port_in_nm_range(8002)
        assert _port_in_nm_range(8051)
        assert not _port_in_nm_range(8052)
        assert not _port_in_nm_range(8001)

    def test_pg_port_in_range(self):
        from scripts.night_manager.agent import _port_in_nm_range
        assert _port_in_nm_range(5433)
        assert _port_in_nm_range(5482)
        assert not _port_in_nm_range(5483)
        assert not _port_in_nm_range(5432)

    def test_web_port_in_range(self):
        from scripts.night_manager.agent import _port_in_nm_range
        assert _port_in_nm_range(3002)
        assert _port_in_nm_range(3051)
        assert not _port_in_nm_range(3052)
        assert not _port_in_nm_range(3001)

    def test_random_port_not_in_range(self):
        from scripts.night_manager.agent import _port_in_nm_range
        assert not _port_in_nm_range(80)
        assert not _port_in_nm_range(443)
        assert not _port_in_nm_range(9323)
