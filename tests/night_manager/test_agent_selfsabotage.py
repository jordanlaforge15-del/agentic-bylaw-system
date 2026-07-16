"""Tests for ABS-202: agent self-sabotage prevention.

Covers:
- Layer 1a: Default agent prompt contains E2E DELEGATION, SPEC PRESERVATION,
  NO BACKGROUND POLLING, and CLEAN EXIT rules.
- Layer 1b: Haiku-class agents receive the prescriptive 5-step recipe extension;
  non-haiku models do not.
- Layer 2: Success-with-cleanup detection — monitor_agent returns exit_code=0
  when the agent emitted a success result before the wrapper was killed.
- Layer 3: Pre-merge e2e coverage gate — check_e2e_coverage blocks merges when
  production files changed but no web/e2e/ files are in the diff.
- Regression: ABS-182 scenario simulation — haiku agent with success-with-cleanup
  + missing e2e coverage is caught by the gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers (consistent with test_agent_watchdog.py patterns)
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.returncode: int | None = None
        self.signals: list[int] = []
        self.killed = False
        self._wait_event = asyncio.Event()

    def feed(self, payload: dict) -> None:
        self.stdout.feed_data((json.dumps(payload) + "\n").encode("utf-8"))

    def close_stdout(self) -> None:
        self.stdout.feed_eof()

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.returncode = -sig
        self._wait_event.set()

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.returncode = -9
        self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        return self.returncode or 0

    def finish_cleanly(self, code: int = 0) -> None:
        self.returncode = code
        self._wait_event.set()
        self.close_stdout()

    @property
    def pid(self) -> int:
        return 12345


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def _patch_loop_clock(loop: asyncio.AbstractEventLoop, clock: _FakeClock) -> None:
    loop.time = clock.now  # type: ignore[assignment]


def _make_issue(tmp_path: Path, identifier: str = "ABS-999") -> Any:
    from scripts.night_manager.state import IssuePorts, IssueState
    log_file = tmp_path / f"{identifier}.jsonl"
    return IssueState(
        identifier=identifier,
        title="Test issue",
        branch=f"agent/{identifier.lower()}",
        ports=IssuePorts(pg=5499, api=8099, web=3099),
        log_file=str(log_file),
        worktree=str(tmp_path),
        session_id="test-session-id",
    )


def _result_success(text: str = "all good") -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "duration_ms": 1234,
    }


def _assistant_text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def _make_fast_sleep(real_sleep, on_iter: dict[int, Any]):
    counter = {"n": 0}

    async def _fast_sleep(_secs):
        counter["n"] += 1
        action = on_iter.get(counter["n"])
        if action is not None:
            action()
        await real_sleep(0)
        await real_sleep(0)

    return _fast_sleep


# ---------------------------------------------------------------------------
# Layer 1a — Default agent prompt guardrails
# ---------------------------------------------------------------------------


class TestDefaultPromptGuardrails:
    """DEV_AGENT_SYSTEM_PROMPT must forbid make e2e, Monitor polling,
    spec self-deletion, and background process leaks."""

    def test_prompt_forbids_make_e2e(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "do NOT run `make e2e`" in DEV_AGENT_SYSTEM_PROMPT

    def test_prompt_forbids_npm_run_e2e(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "`npm run e2e`" in DEV_AGENT_SYSTEM_PROMPT

    def test_prompt_contains_e2e_delegation_rule(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "E2E DELEGATION" in DEV_AGENT_SYSTEM_PROMPT
        assert "reviewer will run `make e2e` for you" in DEV_AGENT_SYSTEM_PROMPT

    def test_prompt_contains_spec_preservation_rule(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "SPEC PRESERVATION" in DEV_AGENT_SYSTEM_PROMPT
        assert "Do NOT delete a file you previously wrote" in DEV_AGENT_SYSTEM_PROMPT

    def test_prompt_contains_no_background_polling_rule(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "NO BACKGROUND POLLING" in DEV_AGENT_SYSTEM_PROMPT
        assert "Do NOT use the `Monitor` tool" in DEV_AGENT_SYSTEM_PROMPT

    def test_prompt_contains_clean_exit_rule(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "CLEAN EXIT" in DEV_AGENT_SYSTEM_PROMPT
        assert "Do not spawn background processes" in DEV_AGENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Layer 1b — Haiku prompt extension
# ---------------------------------------------------------------------------


class TestHaikuPromptExtension:
    """When model == 'haiku', the system prompt gets the prescriptive recipe."""

    def test_haiku_extension_contains_5_step_recipe(self):
        from scripts.night_manager.config import HAIKU_AGENT_PROMPT_EXTENSION
        assert "Execute exactly this sequence and stop" in HAIKU_AGENT_PROMPT_EXTENSION
        assert "Read the files in the touch profile" in HAIKU_AGENT_PROMPT_EXTENSION
        assert "Write **one** Playwright spec" in HAIKU_AGENT_PROMPT_EXTENSION
        assert "git add -A && git commit" in HAIKU_AGENT_PROMPT_EXTENSION
        assert "Done. Committed <sha>." in HAIKU_AGENT_PROMPT_EXTENSION

    def test_haiku_extension_forbids_monitor(self):
        from scripts.night_manager.config import HAIKU_AGENT_PROMPT_EXTENSION
        assert "Forbidden tools: `Monitor`" in HAIKU_AGENT_PROMPT_EXTENSION

    def test_haiku_extension_forbids_make_e2e(self):
        from scripts.night_manager.config import HAIKU_AGENT_PROMPT_EXTENSION
        assert "`make e2e`" in HAIKU_AGENT_PROMPT_EXTENSION

    async def test_spawn_agent_appends_haiku_extension(self, tmp_path: Path):
        """When model=haiku, the --append-system-prompt includes the extension."""
        from scripts.night_manager.agent import spawn_agent
        from scripts.night_manager.config import (
            HAIKU_AGENT_PROMPT_EXTENSION,
            NMConfig,
        )
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-HAIKU",
            title="Haiku test",
            branch="agent/abs-haiku",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
            log_file=str(tmp_path / "test.jsonl"),
            worktree=str(tmp_path),
            agent_model="haiku",
        )

        captured_cmd: list[str] = []

        async def fake_subprocess(*args, **kwargs) -> MagicMock:
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 99
            proc.returncode = None
            proc.stdout = asyncio.StreamReader()
            return proc

        config = MagicMock(spec=NMConfig)
        config.agent_model = "haiku"
        config.agent_effort = "high"

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec", fake_subprocess):
            with patch("scripts.night_manager.agent._schedule_wall_clock_watchdog"):
                await spawn_agent(issue, config)

        flat_cmd = [str(a) for a in captured_cmd]
        idx = flat_cmd.index("--append-system-prompt")
        system_prompt = flat_cmd[idx + 1]
        assert "Execute exactly this sequence" in system_prompt
        assert "Forbidden tools: `Monitor`" in system_prompt

    async def test_spawn_agent_no_extension_for_sonnet(self, tmp_path: Path):
        """When model=sonnet, the haiku extension is NOT appended."""
        from scripts.night_manager.agent import spawn_agent
        from scripts.night_manager.config import NMConfig
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-SONNET",
            title="Sonnet test",
            branch="agent/abs-sonnet",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
            log_file=str(tmp_path / "test.jsonl"),
            worktree=str(tmp_path),
            agent_model="sonnet",
        )

        captured_cmd: list[str] = []

        async def fake_subprocess(*args, **kwargs) -> MagicMock:
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 99
            proc.returncode = None
            proc.stdout = asyncio.StreamReader()
            return proc

        config = MagicMock(spec=NMConfig)
        config.agent_model = "sonnet"
        config.agent_effort = "high"

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec", fake_subprocess):
            with patch("scripts.night_manager.agent._schedule_wall_clock_watchdog"):
                await spawn_agent(issue, config)

        flat_cmd = [str(a) for a in captured_cmd]
        idx = flat_cmd.index("--append-system-prompt")
        system_prompt = flat_cmd[idx + 1]
        assert "Execute exactly this sequence" not in system_prompt
        assert "Forbidden tools: `Monitor`" not in system_prompt

    async def test_spawn_continuation_appends_haiku_extension(self, tmp_path: Path):
        """_spawn_continuation also appends the haiku extension."""
        from scripts.night_manager.agent import _spawn_continuation
        from scripts.night_manager.config import NMConfig
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-HCONT",
            title="Haiku continuation",
            branch="agent/abs-hcont",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
            log_file=str(tmp_path / "test.jsonl"),
            worktree=str(tmp_path),
            session_id="old-session",
            completed_at="2026-01-01T00:00:00Z",
            agent_model="haiku",
        )

        captured_cmd: list[str] = []

        async def fake_subprocess(*args, **kwargs) -> MagicMock:
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 99
            proc.returncode = None
            proc.stdout = asyncio.StreamReader()
            return proc

        config = MagicMock(spec=NMConfig)
        config.agent_model = "haiku"
        config.agent_effort = "high"

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec", fake_subprocess):
            with patch("scripts.night_manager.agent._schedule_wall_clock_watchdog"):
                await _spawn_continuation(issue, config, "fix this")

        flat_cmd = [str(a) for a in captured_cmd]
        idx = flat_cmd.index("--append-system-prompt")
        system_prompt = flat_cmd[idx + 1]
        assert "Execute exactly this sequence" in system_prompt


# ---------------------------------------------------------------------------
# Layer 2 — Success-with-cleanup detection in monitor_agent
# ---------------------------------------------------------------------------


class TestSuccessWithCleanup:
    """When the agent emits a success result but the wrapper is killed by
    the post-success idle watchdog, monitor_agent must return exit_code=0."""

    async def test_sigterm_after_success_returns_zero(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ):
        """Reproduces ABS-202: agent emits success result, wrapper hangs due
        to leaked Monitor background tasks, idle watchdog SIGTERMs it.
        monitor_agent must return 0, not -SIGTERM."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path, identifier="ABS-182")
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Done — all changes committed."))
        proc.feed(_result_success("Committed abc1234."))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(6 * 60)},  # past 5-min threshold
        )

        with caplog.at_level(logging.INFO, logger="night_manager.agent"):
            with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
                exit_code, output = await monitor_agent(
                    proc, issue,
                    timeout_minutes=20,
                    bash_tool_budget_minutes=30,
                    post_success_idle_minutes=5,
                )

        assert exit_code == 0, (
            f"Expected exit_code=0 after success-with-cleanup; got {exit_code}"
        )

        messages = [r.getMessage() for r in caplog.records]
        cleanup_lines = [m for m in messages if "success-with-cleanup" in m]
        assert cleanup_lines, (
            f"Expected 'success-with-cleanup' log line; got {messages!r}"
        )
        assert "ABS-182" in cleanup_lines[0]

    async def test_normal_failure_still_returns_nonzero(self, tmp_path: Path):
        """An agent that fails without emitting a success result must still
        return a non-zero exit code — success-with-cleanup only applies when
        success_result_at is set."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Trying something..."))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(25 * 60)},  # past 20-min default
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            exit_code, _ = await monitor_agent(
                proc, issue,
                timeout_minutes=20,
                bash_tool_budget_minutes=30,
                post_success_idle_minutes=5,
            )

        assert exit_code != 0, (
            "Agent without success result must NOT get the cleanup override"
        )

    async def test_clean_exit_after_success_still_returns_zero(self, tmp_path: Path):
        """Happy path: agent emits success and exits cleanly within grace.
        exit_code should be 0 without needing the cleanup override."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Quick fix done."))
        proc.feed(_result_success("done"))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: (clock.advance(30), proc.finish_cleanly(0))},
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            exit_code, _ = await monitor_agent(
                proc, issue,
                timeout_minutes=20,
                bash_tool_budget_minutes=30,
                post_success_idle_minutes=5,
            )

        assert exit_code == 0


# ---------------------------------------------------------------------------
# Layer 3 — Pre-merge e2e coverage gate
# ---------------------------------------------------------------------------


class TestE2ECoverageGate:
    """check_e2e_coverage blocks merges with production changes but no
    web/e2e/ files in the diff."""

    def test_production_change_with_e2e_passes(self):
        from scripts.night_manager.reviewer import check_e2e_coverage
        ok, msg = check_e2e_coverage([
            "web/app/components/chat-shell.tsx",
            "web/e2e/functional/abs182-sidebar.spec.ts",
        ])
        assert ok is True
        assert msg == ""

    def test_production_change_without_e2e_fails(self):
        from scripts.night_manager.reviewer import check_e2e_coverage
        ok, msg = check_e2e_coverage([
            "web/app/components/chat-shell.tsx",
        ])
        assert ok is False
        assert "without e2e coverage" in msg
        assert "chat-shell.tsx" in msg

    def test_test_only_change_passes(self):
        from scripts.night_manager.reviewer import check_e2e_coverage
        ok, msg = check_e2e_coverage([
            "tests/advisor/test_something.py",
        ])
        assert ok is True

    def test_e2e_only_change_passes(self):
        from scripts.night_manager.reviewer import check_e2e_coverage
        ok, msg = check_e2e_coverage([
            "web/e2e/functional/new-test.spec.ts",
        ])
        assert ok is True

    def test_empty_diff_passes(self):
        from scripts.night_manager.reviewer import check_e2e_coverage
        ok, msg = check_e2e_coverage([])
        assert ok is True

    def test_spec_written_then_deleted_scenario(self):
        """Simulates ABS-182: agent wrote a spec then rm'd it. The diff
        shows only the production change (spec was added+removed within
        the branch, so git diff dev...HEAD doesn't show it)."""
        from scripts.night_manager.reviewer import check_e2e_coverage
        ok, msg = check_e2e_coverage([
            "web/app/components/chat-shell.tsx",
        ])
        assert ok is False
        assert "without e2e coverage" in msg

    def test_multiple_production_files_lists_them_all(self):
        from scripts.night_manager.reviewer import check_e2e_coverage
        ok, msg = check_e2e_coverage([
            "web/app/components/chat-shell.tsx",
            "advisor/routers/session.py",
        ])
        assert ok is False
        assert "chat-shell.tsx" in msg
        assert "session.py" in msg

    def test_mixed_production_and_unit_test_without_e2e_fails(self):
        from scripts.night_manager.reviewer import check_e2e_coverage
        ok, msg = check_e2e_coverage([
            "web/app/components/chat-shell.tsx",
            "tests/advisor/test_session.py",
        ])
        assert ok is False


# ---------------------------------------------------------------------------
# Regression — ABS-182 scenario simulation
# ---------------------------------------------------------------------------


class TestABS182RegressionScenario:
    """End-to-end simulation of the ABS-182 failure mode on a haiku agent.

    The scenario:
    1. Haiku agent makes a fix and writes a spec, then deletes the spec.
    2. Agent emits success result, wrapper hangs (Monitor tasks).
    3. Post-success idle watchdog SIGTERMs after 5m.

    With the ABS-202 fix:
    - monitor_agent returns exit_code=0 (success-with-cleanup)
    - Agent lifecycle routes to review (not failure)
    - code_review's e2e coverage gate catches the missing spec
    """

    async def test_haiku_agent_reaches_review_gate(self, tmp_path: Path):
        """monitor_agent returns 0 for the wrapped-killed-after-success case,
        so the lifecycle would route to mark_completed → review."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path, identifier="ABS-182")
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("(No further action - task complete)"))
        proc.feed(_result_success("All work done."))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(6 * 60)},
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            exit_code, output = await monitor_agent(
                proc, issue,
                timeout_minutes=20,
                bash_tool_budget_minutes=30,
                post_success_idle_minutes=5,
            )

        assert exit_code == 0, "Agent should reach review gate, not fail"

    def test_missing_spec_caught_by_coverage_gate(self):
        """The e2e coverage gate blocks the merge when the spec was deleted."""
        from scripts.night_manager.reviewer import check_e2e_coverage

        # After the agent's spec deletion, git diff dev...HEAD shows only
        # the production change (spec was added+removed within branch).
        changed_files = ["web/app/components/chat-shell.tsx"]
        ok, msg = check_e2e_coverage(changed_files)
        assert ok is False
        assert "without e2e coverage" in msg

    async def test_haiku_prompt_would_have_forbidden_make_e2e(self):
        """The haiku prompt extension forbids the commands the agent ran."""
        from scripts.night_manager.config import (
            DEV_AGENT_SYSTEM_PROMPT,
            HAIKU_AGENT_PROMPT_EXTENSION,
        )
        full_prompt = DEV_AGENT_SYSTEM_PROMPT + HAIKU_AGENT_PROMPT_EXTENSION
        assert "make e2e" in full_prompt
        assert "Monitor" in full_prompt
        assert "Forbidden tools: `Monitor`" in full_prompt
        assert "Forbidden commands: `make e2e`" in full_prompt
