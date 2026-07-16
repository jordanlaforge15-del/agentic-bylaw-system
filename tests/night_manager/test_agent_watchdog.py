"""Tests for the Night Manager agent watchdog.

Three behaviors, motivated by the ABS-121 incident (agent killed mid-`make e2e`):

1. The default stall threshold is 20 min and is overridable via NM_AGENT_STALL_MIN.
2. While an unterminated `tool_use` for `Bash` is in flight, the watchdog gives
   that call an extended budget (default 30 min) rather than killing at the
   standard threshold — `make e2e` legitimately emits no stdout for ~9 min.
3. When the watchdog does decide to kill, it emits a forensics line containing
   the last assistant text + last tool_use name+input to the main NM log, so a
   post-mortem doesn't require re-parsing the per-issue jsonl.

Style mirrors test_merge_to_dev.py — real fixtures over subprocess mocks. The
watchdog reads from a real `asyncio.StreamReader` we feed bytes into. We
swap `loop.time()` for a fake clock to control elapsed wall-clock without
actually sleeping, and patch the watchdog's `asyncio.sleep(30)` to a tiny
real sleep so each iteration runs in microseconds.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — fake subprocess + fake clock
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Minimal stand-in for asyncio.subprocess.Process.

    - `stdout` is a real `asyncio.StreamReader` we feed bytes into.
    - `returncode` flips when `_finish()` is called by the test.
    - `send_signal` / `kill` / `wait` cooperate so the watchdog can complete
      its kill path without hanging.
    """

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
        # Simulate cooperative exit on SIGTERM.
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


class _FakeClock:
    """Monotonic-style clock the test advances explicitly.

    Patched onto the event loop via `loop.time = clock.now`. Because
    `monitor_agent` only calls `loop.time()` (not `time.monotonic()`),
    swapping this in gives us deterministic elapsed-time control.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def _patch_loop_clock(loop: asyncio.AbstractEventLoop, clock: _FakeClock) -> None:
    loop.time = clock.now  # type: ignore[assignment]


def _make_issue(tmp_path: Path, identifier: str = "ABS-999") -> Any:
    """Build a minimal IssueState pointed at a tmp log file."""
    from scripts.night_manager.state import IssuePorts, IssueState

    log_file = tmp_path / f"{identifier}.jsonl"
    return IssueState(
        identifier=identifier,
        title="Watchdog test",
        branch=f"agent/{identifier}",
        ports=IssuePorts(pg=5499, api=8099, web=3099),
        log_file=str(log_file),
    )


def _assistant_tool_use(tool_id: str, name: str, input_obj: dict) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": name, "input": input_obj}
            ],
        },
    }


def _assistant_text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def _user_tool_result(tool_id: str, content: str = "ok") -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": content}
            ],
        },
    }


def _make_fast_sleep(
    real_sleep,
    on_iter: dict[int, Any],
):
    """Build a patched `asyncio.sleep` for the watchdog.

    - Calls `real_sleep(0)` every iteration (just yields to the event loop).
    - `on_iter` maps 1-based iteration number → callable run *before* the yield.
      Tests use this to advance the fake clock or finish the process.
    """
    counter = {"n": 0}

    async def _fast_sleep(_secs):
        counter["n"] += 1
        action = on_iter.get(counter["n"])
        if action is not None:
            action()
        await real_sleep(0)
        # Give the watchdog one more event-loop tick to evaluate its branch.
        await real_sleep(0)

    return _fast_sleep


# ---------------------------------------------------------------------------
# 1) Default stall threshold + env-var override
# ---------------------------------------------------------------------------


class TestStallThresholdConfig:
    def test_default_is_20_minutes(self):
        from scripts.night_manager import config as config_mod

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NM_AGENT_STALL_MIN", None)
            reloaded = importlib.reload(config_mod)
            try:
                assert reloaded.STUCK_TIMEOUT_MINUTES == 20
            finally:
                importlib.reload(reloaded)

    def test_env_override_applies(self):
        from scripts.night_manager import config as config_mod

        with patch.dict(os.environ, {"NM_AGENT_STALL_MIN": "45"}):
            reloaded = importlib.reload(config_mod)
            try:
                assert reloaded.STUCK_TIMEOUT_MINUTES == 45
            finally:
                os.environ.pop("NM_AGENT_STALL_MIN", None)
                importlib.reload(reloaded)

    def test_bash_budget_default_is_30(self):
        from scripts.night_manager import config as config_mod

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NM_BASH_TOOL_BUDGET_MIN", None)
            reloaded = importlib.reload(config_mod)
            try:
                assert reloaded.BASH_TOOL_BUDGET_MINUTES == 30
            finally:
                importlib.reload(reloaded)


# ---------------------------------------------------------------------------
# 2) Bash-aware grace — long Bash tool_use does not get killed at the
#    standard threshold.
# ---------------------------------------------------------------------------


class TestBashAwareGrace:
    async def test_bash_tool_use_extends_grace_past_default_threshold(
        self, tmp_path: Path,
    ):
        """Reproduces ABS-121: last event is an unterminated Bash tool_use
        for `make e2e`. With a 5-min standard threshold the old watchdog
        would kill the agent at t=5m; the fix gives Bash an extended budget
        (we use 30) and only kills if that ceiling is exceeded.
        """
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(
            _assistant_tool_use(
                "tool-1", "Bash",
                {"command": "make e2e", "description": "Run full e2e suite"},
            )
        )
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={
                1: lambda: clock.advance(12 * 60),  # past 5-min default
                2: lambda: proc.finish_cleanly(0),   # agent finishes before any kill
            },
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            exit_code, _ = await monitor_agent(
                proc, issue,
                timeout_minutes=5, bash_tool_budget_minutes=30,
            )

        assert proc.signals == [], (
            f"watchdog must NOT signal during a still-running Bash tool_use; "
            f"got signals={proc.signals}"
        )
        assert exit_code == 0

    async def test_bash_tool_use_killed_when_budget_exceeded(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ):
        """The Bash grace is not infinite — past `bash_tool_budget_minutes`
        the watchdog still SIGTERMs."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Running the full suite now."))
        proc.feed(_assistant_tool_use("tool-1", "Bash", {"command": "make e2e"}))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(35 * 60)},  # past 30-min budget
        )

        with caplog.at_level(logging.WARNING, logger="night_manager.agent"):
            with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
                exit_code, _ = await monitor_agent(
                    proc, issue,
                    timeout_minutes=5, bash_tool_budget_minutes=30,
                )

        assert signal.SIGTERM in proc.signals
        assert exit_code == -signal.SIGTERM

    async def test_non_bash_tool_use_uses_default_threshold(
        self, tmp_path: Path,
    ):
        """A non-Bash tool_use (e.g. Read) doesn't get the extended budget."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_tool_use("tool-1", "Read", {"file_path": "/tmp/x"}))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(6 * 60)},  # past 5-min default
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            await monitor_agent(
                proc, issue, timeout_minutes=5, bash_tool_budget_minutes=30,
            )

        assert signal.SIGTERM in proc.signals, (
            "Read tool_use shouldn't get Bash's extended budget"
        )

    async def test_terminated_bash_tool_does_not_keep_extended_budget(
        self, tmp_path: Path,
    ):
        """Once a Bash tool_result arrives, the pending state clears — a
        later idle period falls back to the standard threshold."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_tool_use("tool-1", "Bash", {"command": "ls"}))
        proc.feed(_user_tool_result("tool-1", content="file1\nfile2"))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(6 * 60)},
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            await monitor_agent(
                proc, issue, timeout_minutes=5, bash_tool_budget_minutes=30,
            )

        assert signal.SIGTERM in proc.signals, (
            "After Bash terminates, default threshold applies — kill expected"
        )


# ---------------------------------------------------------------------------
# 3) Forensics snapshot — kill logs last assistant text + last tool_use.
# ---------------------------------------------------------------------------


class TestKillForensics:
    async def test_kill_emits_last_assistant_text_and_tool_use(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ):
        """When the watchdog kills, the main NM log must contain enough
        forensic context to skip the jsonl-reparse step in post-mortem.
        """
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path, identifier="ABS-121")
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Running the full e2e suite again."))
        proc.feed(
            _assistant_tool_use(
                "toolu_01HWpaFM8CUichnMsJpivnwY", "Bash",
                {
                    "command": "export PG_PORT=5489 && make e2e 2>&1",
                    "description": "Run full e2e suite with fresh dependencies",
                },
            )
        )
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(35 * 60)},  # past 30-min Bash budget
        )

        with caplog.at_level(logging.WARNING, logger="night_manager.agent"):
            with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
                await monitor_agent(
                    proc, issue, timeout_minutes=5, bash_tool_budget_minutes=30,
                )

        messages = [r.getMessage() for r in caplog.records]
        forensics = [m for m in messages if "stall forensics" in m]
        assert forensics, (
            f"expected a 'stall forensics' log line; got messages={messages!r}"
        )
        line = forensics[0]

        assert "ABS-121" in line
        assert "Running the full e2e suite again." in line
        assert "Bash" in line
        assert "make e2e" in line
        assert "elapsed=" in line

    async def test_forensics_handles_no_tool_use(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ):
        """If the agent stalled with only an assistant text and no pending
        tool_use, the forensics line must still emit (last_tool_use=(none))."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Thinking about the architecture..."))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(10 * 60)},  # past 5-min default
        )

        with caplog.at_level(logging.WARNING, logger="night_manager.agent"):
            with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
                await monitor_agent(
                    proc, issue, timeout_minutes=5, bash_tool_budget_minutes=30,
                )

        messages = [r.getMessage() for r in caplog.records]
        forensics = [m for m in messages if "stall forensics" in m]
        assert forensics
        line = forensics[0]
        assert "last_tool_use=(none)" in line
        assert "Thinking about the architecture..." in line

    def test_truncate_helper(self):
        from scripts.night_manager.agent import _truncate

        assert _truncate("short", 100) == "short"
        long = "x" * 600
        out = _truncate(long, 500)
        assert len(out) == 503  # 500 chars + "..."
        assert out.endswith("...")


# ---------------------------------------------------------------------------
# 4) Post-success idle watchdog (ABS-159) — kill the wrapper when it stays
#    alive after a `result subtype: success` event.
# ---------------------------------------------------------------------------


def _result_success(text: str = "all good") -> dict:
    """Build a Claude Code terminal `result` event with success semantics."""
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "duration_ms": 1234,
    }


def _result_error() -> dict:
    return {
        "type": "result",
        "subtype": "error",
        "is_error": True,
        "result": "boom",
        "duration_ms": 1234,
    }


class TestPostSuccessIdleWatchdog:
    async def test_wrapper_killed_after_success_result_when_idle(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ):
        """Reproduces ABS-159: agent wrote a success `result` block to its
        jsonl 6 minutes ago, but the wrapper is still alive because an
        orphaned child (e.g. `playwright show-report` on :9323) is holding
        the stdout pipe open. The watchdog must SIGTERM the wrapper and
        log the expected warning.
        """
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path, identifier="ABS-84")
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Done — all 160 tests passed."))
        proc.feed(_result_success("All work done."))
        # NOTE: closing stdout here models the wrapper's stdout being
        # *drained* (no more output) but the wrapper process itself still
        # alive — the realistic ABS-84 case. The reader loop exits cleanly
        # while the watchdog still has to decide to SIGTERM via the
        # post-success idle branch.
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={1: lambda: clock.advance(6 * 60)},  # 6 min > 5 min threshold
        )

        with caplog.at_level(logging.WARNING, logger="night_manager.agent"):
            with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
                exit_code, _ = await monitor_agent(
                    proc, issue,
                    timeout_minutes=20,
                    bash_tool_budget_minutes=30,
                    post_success_idle_minutes=5,
                )

        assert signal.SIGTERM in proc.signals, (
            f"expected SIGTERM after post-success idle; got signals={proc.signals}"
        )
        # ABS-202: monitor_agent now overrides exit_code to 0 when a success
        # result was already emitted (success-with-cleanup detection).
        assert exit_code == 0, (
            f"expected exit_code=0 after success-with-cleanup; got {exit_code}"
        )

        messages = [r.getMessage() for r in caplog.records]
        warn_lines = [
            m for m in messages
            if "wrapper idle" in m and "after success result" in m
        ]
        assert warn_lines, (
            f"expected 'wrapper idle ... after success result' warning; "
            f"got messages={messages!r}"
        )
        assert "ABS-84" in warn_lines[0]
        assert "killing wrapper" in warn_lines[0]

    async def test_happy_path_agent_unaffected(self, tmp_path: Path):
        """Sub-5-min agent that emits a success result and immediately
        closes stdout (the normal happy path) must NOT be SIGTERMed.
        """
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Quick win shipped."))
        proc.feed(_result_success("done"))
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={
                # Only 1 min after the success event — well inside the 5-min
                # grace. Process finishes cleanly before any kill path runs.
                1: lambda: (clock.advance(60), proc.finish_cleanly(0)),
            },
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            exit_code, _ = await monitor_agent(
                proc, issue,
                timeout_minutes=20,
                bash_tool_budget_minutes=30,
                post_success_idle_minutes=5,
            )

        assert proc.signals == [], (
            f"happy-path wrapper must not be signalled; got {proc.signals}"
        )
        assert exit_code == 0

    async def test_error_result_does_not_arm_watchdog(self, tmp_path: Path):
        """A `result` block with is_error=true is not a success — the
        post-success watchdog must NOT arm on it. The default stuck-timeout
        path still applies if the agent then idles, but the kill reason
        must be the standard 'stuck for N minutes', not 'wrapper idle
        after success result'.
        """
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path, identifier="ABS-ERROR")
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_assistant_text("Tried, failed."))
        proc.feed(_result_error())
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={
                # 6 min — past the 5-min post-success threshold, but well
                # under the 20-min default. So nothing should happen.
                1: lambda: (clock.advance(6 * 60), proc.finish_cleanly(1)),
            },
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            exit_code, _ = await monitor_agent(
                proc, issue,
                timeout_minutes=20,
                bash_tool_budget_minutes=30,
                post_success_idle_minutes=5,
            )

        assert proc.signals == [], (
            f"error-result wrapper must not trigger post-success kill; "
            f"got signals={proc.signals}"
        )
        # The fixture closed cleanly with exit code 1.
        assert exit_code == 1

    async def test_post_success_grace_window_respected(self, tmp_path: Path):
        """Within the post-success grace window the wrapper must NOT be
        killed — only after it elapses. Confirms the threshold boundary."""
        from scripts.night_manager.agent import monitor_agent

        proc = _FakeProcess()
        issue = _make_issue(tmp_path)
        loop = asyncio.get_event_loop()
        clock = _FakeClock()
        _patch_loop_clock(loop, clock)
        real_sleep = asyncio.sleep

        proc.feed(_result_success())
        proc.close_stdout()

        fast_sleep = _make_fast_sleep(
            real_sleep,
            on_iter={
                1: lambda: clock.advance(3 * 60),  # 3 min < 5 min — safe
                # Now finish cleanly so the test terminates.
                2: lambda: proc.finish_cleanly(0),
            },
        )

        with patch("scripts.night_manager.agent.asyncio.sleep", fast_sleep):
            exit_code, _ = await monitor_agent(
                proc, issue,
                timeout_minutes=20,
                bash_tool_budget_minutes=30,
                post_success_idle_minutes=5,
            )

        assert proc.signals == []
        assert exit_code == 0


# ---------------------------------------------------------------------------
# 5) System-prompt rule (ABS-159) — the constructed prompt must contain a
#    recognizable substring of the new long-running-server rule.
# ---------------------------------------------------------------------------


class TestSystemPromptLongRunningServerRule:
    def test_prompt_contains_long_running_server_rule(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT

        # Recognizable fragments from the rule body — assert both the
        # category guard and a concrete example land in the prompt.
        assert "long-running server" in DEV_AGENT_SYSTEM_PROMPT
        assert "playwright show-report" in DEV_AGENT_SYSTEM_PROMPT

    def test_prompt_mentions_safe_alternatives(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT

        # Agent must be told where to read results without serving them.
        assert "web/test-results/.last-run.json" in DEV_AGENT_SYSTEM_PROMPT
        assert "do not serve them" in DEV_AGENT_SYSTEM_PROMPT

    def test_prompt_mentions_timeout_workaround(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT

        # The escape hatch: prefix with `timeout 30s` if you really must.
        assert "timeout 30s" in DEV_AGENT_SYSTEM_PROMPT
