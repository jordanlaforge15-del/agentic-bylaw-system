"""Tests for ABS-162: context, budget, and feedback improvements.

Covers:
- A.1: Linear MCP read tools in ALLOWED_TOOLS + DEV_AGENT_SYSTEM_PROMPT + continuation prompt
- B.1: --max-budget-usd raised to 200; wall-clock timeout fires for initial and continuation
- C:   Playwright JSON reporter parsing; failing test names in feedback; fallback on missing file
- D:   Prior agent reasoning extracted from jsonl and included in continuation prompt
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
# Shared helpers (reuse pattern from test_agent_watchdog.py)
# ---------------------------------------------------------------------------

class _FakeProcess:
    """Minimal stand-in for asyncio.subprocess.Process."""

    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.returncode: int | None = None
        self.signals: list[int] = []
        self.killed = False
        self._wait_event = asyncio.Event()

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

    @property
    def pid(self) -> int:
        return 12345


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
        completed_at="2026-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# ABS-171 — DEV_AGENT_SYSTEM_PROMPT: worktree isolation rule
# ---------------------------------------------------------------------------


class TestSystemPromptWorktreeIsolation:
    """The agent prompt must include an explicit rule forbidding writes to the
    main checkout.  Without this, agents can drift to absolute paths under the
    project root, poisoning the index and blocking all subsequent merges.
    """

    def test_prompt_contains_worktree_isolation_rule(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        prompt_lower = DEV_AGENT_SYSTEM_PROMPT.lower()
        assert "worktree isolation" in prompt_lower or "worktree" in prompt_lower

    def test_prompt_forbids_main_checkout_writes(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "never write to" in DEV_AGENT_SYSTEM_PROMPT.lower() or \
               "must stay within" in DEV_AGENT_SYSTEM_PROMPT.lower()

    def test_prompt_warns_about_index_corruption(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "corrupt" in DEV_AGENT_SYSTEM_PROMPT.lower() or \
               "blocks" in DEV_AGENT_SYSTEM_PROMPT.lower()

    def test_prompt_includes_wrong_right_examples(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "WRONG" in DEV_AGENT_SYSTEM_PROMPT
        assert "RIGHT" in DEV_AGENT_SYSTEM_PROMPT

    def test_prompt_mentions_worktree_path_pattern(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert ".claude/worktrees/" in DEV_AGENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# A.1 — ALLOWED_TOOLS includes Linear MCP read tools, not write tools
# ---------------------------------------------------------------------------

class TestAllowedToolsLinearMCP:
    def test_get_issue_in_allowed_tools(self):
        from scripts.night_manager.config import ALLOWED_TOOLS, LINEAR_MCP_PREFIX
        assert f"{LINEAR_MCP_PREFIX}get_issue" in ALLOWED_TOOLS

    def test_list_comments_in_allowed_tools(self):
        from scripts.night_manager.config import ALLOWED_TOOLS, LINEAR_MCP_PREFIX
        assert f"{LINEAR_MCP_PREFIX}list_comments" in ALLOWED_TOOLS

    def test_save_issue_not_in_allowed_tools(self):
        from scripts.night_manager.config import ALLOWED_TOOLS
        assert "save_issue" not in ALLOWED_TOOLS

    def test_save_comment_not_in_allowed_tools(self):
        from scripts.night_manager.config import ALLOWED_TOOLS
        assert "save_comment" not in ALLOWED_TOOLS

    def test_save_document_not_in_allowed_tools(self):
        from scripts.night_manager.config import ALLOWED_TOOLS
        assert "save_document" not in ALLOWED_TOOLS


# ---------------------------------------------------------------------------
# A.1 — DEV_AGENT_SYSTEM_PROMPT: may-read clause + may-not-write clause
# ---------------------------------------------------------------------------

class TestSystemPromptLinearMCPClauses:
    def test_prompt_contains_may_read_clause(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        # The "may READ" clause must mention get_issue
        assert "get_issue" in DEV_AGENT_SYSTEM_PROMPT
        assert "list_comments" in DEV_AGENT_SYSTEM_PROMPT
        # Should say something about reading
        lower = DEV_AGENT_SYSTEM_PROMPT.lower()
        assert "read" in lower or "may" in lower

    def test_prompt_contains_may_not_write_clause(self):
        from scripts.night_manager.config import DEV_AGENT_SYSTEM_PROMPT
        assert "save_issue" in DEV_AGENT_SYSTEM_PROMPT
        assert "save_comment" in DEV_AGENT_SYSTEM_PROMPT
        assert "save_document" in DEV_AGENT_SYSTEM_PROMPT
        # "NOT write" or similar must appear
        assert "NOT write" in DEV_AGENT_SYSTEM_PROMPT or "may NOT" in DEV_AGENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# A.1 — CONTINUATION_PROMPT_TEMPLATE directive
# ---------------------------------------------------------------------------

class TestContinuationPromptDirective:
    def test_template_contains_mcp_read_directive(self):
        from scripts.night_manager.agent import CONTINUATION_PROMPT_TEMPLATE
        from scripts.night_manager.config import LINEAR_MCP_PREFIX
        assert f"{LINEAR_MCP_PREFIX}get_issue" in CONTINUATION_PROMPT_TEMPLATE

    def test_template_does_not_contain_save_issue(self):
        from scripts.night_manager.agent import CONTINUATION_PROMPT_TEMPLATE
        assert "save_issue" not in CONTINUATION_PROMPT_TEMPLATE

    def test_template_formats_identifier(self):
        """The identifier placeholder must appear so agents can call get_issue."""
        from scripts.night_manager.agent import CONTINUATION_PROMPT_TEMPLATE
        assert "{identifier}" in CONTINUATION_PROMPT_TEMPLATE


# ---------------------------------------------------------------------------
# B.1 — --max-budget-usd is "200" in all three spawn command builders
# ---------------------------------------------------------------------------

class TestMaxBudgetUSD:
    async def test_spawn_agent_uses_200_budget(self, tmp_path: Path):
        from scripts.night_manager.agent import spawn_agent
        from scripts.night_manager.config import NMConfig
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-TEST",
            title="Budget test",
            branch="agent/abs-test",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
            log_file=str(tmp_path / "test.jsonl"),
            worktree=str(tmp_path),
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
        config.agent_token_limit = 10.0

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec", fake_subprocess):
            with patch("scripts.night_manager.agent._schedule_wall_clock_watchdog"):
                await spawn_agent(issue, config)

        flat_cmd = [str(a) for a in captured_cmd]
        idx = flat_cmd.index("--max-budget-usd")
        assert flat_cmd[idx + 1] == "200", (
            f"Expected --max-budget-usd 200, got {flat_cmd[idx + 1]}"
        )

    async def test_spawn_continuation_uses_200_budget(self, tmp_path: Path):
        from scripts.night_manager.agent import _spawn_continuation
        from scripts.night_manager.config import NMConfig

        issue = _make_issue(tmp_path)

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
        config.agent_token_limit = 10.0

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec", fake_subprocess):
            with patch("scripts.night_manager.agent._schedule_wall_clock_watchdog"):
                await _spawn_continuation(issue, config, "fix this")

        flat_cmd = [str(a) for a in captured_cmd]
        idx = flat_cmd.index("--max-budget-usd")
        assert flat_cmd[idx + 1] == "200"

    async def test_resume_agent_uses_200_budget(self, tmp_path: Path):
        """resume_agent mid-flight path (no completed_at) uses 200 budget."""
        from scripts.night_manager.agent import resume_agent
        from scripts.night_manager.config import NMConfig
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-TEST",
            title="Resume test",
            branch="agent/abs-test",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
            log_file=str(tmp_path / "test.jsonl"),
            worktree=str(tmp_path),
            session_id="live-session",
            completed_at=None,  # mid-flight path
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
        config.agent_token_limit = 10.0

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec", fake_subprocess):
            with patch("scripts.night_manager.agent._schedule_wall_clock_watchdog"):
                await resume_agent(issue, config, "feedback")

        flat_cmd = [str(a) for a in captured_cmd]
        idx = flat_cmd.index("--max-budget-usd")
        assert flat_cmd[idx + 1] == "200"


# ---------------------------------------------------------------------------
# B.1 — wall-clock timeout fires, logs ERROR, marks issue failed
# ---------------------------------------------------------------------------

class TestWallClockTimeout:
    async def test_initial_timeout_kills_and_marks_failed(self, tmp_path: Path):
        """Wall-clock watchdog for initial spawn kills proc and marks issue failed."""
        from scripts.night_manager.agent import _schedule_wall_clock_watchdog
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-TIMEOUT",
            title="Timeout test",
            branch="agent/abs-timeout",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
            log_file=str(tmp_path / "test.jsonl"),
            worktree=str(tmp_path),
        )

        proc = _FakeProcess()
        # Patch asyncio.sleep so the watchdog fires "instantly"
        real_sleep = asyncio.sleep

        async def instant_sleep(secs: float) -> None:
            await real_sleep(0)

        with patch("scripts.night_manager.agent.asyncio.sleep", instant_sleep):
            task = _schedule_wall_clock_watchdog(proc, issue, max_minutes=1)
            await asyncio.wait_for(task, timeout=5)

        assert signal.SIGTERM in proc.signals, (
            f"Expected SIGTERM from wall-clock kill, got {proc.signals}"
        )
        assert issue.status == "failed"
        assert issue.error == "agent_timeout"

    async def test_wall_clock_timeout_logs_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ):
        """Wall-clock timeout emits an ERROR log with the expected message."""
        from scripts.night_manager.agent import _schedule_wall_clock_watchdog
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-TIMEOUT",
            title="Log test",
            branch="agent/abs-timeout",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
            log_file=str(tmp_path / "test.jsonl"),
            worktree=str(tmp_path),
        )

        proc = _FakeProcess()
        real_sleep = asyncio.sleep

        async def instant_sleep(secs: float) -> None:
            await real_sleep(0)

        with caplog.at_level(logging.ERROR, logger="night_manager.agent"):
            with patch("scripts.night_manager.agent.asyncio.sleep", instant_sleep):
                task = _schedule_wall_clock_watchdog(proc, issue, max_minutes=42)
                await asyncio.wait_for(task, timeout=5)

        error_lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("timed out" in m for m in error_lines), (
            f"Expected 'timed out' ERROR log; got {error_lines}"
        )
        assert any("ABS-TIMEOUT" in m for m in error_lines)
        assert any("42min" in m for m in error_lines)

    async def test_watchdog_no_op_when_proc_already_exited(self, tmp_path: Path):
        """If the process finishes before the timeout, the watchdog does nothing."""
        from scripts.night_manager.agent import _schedule_wall_clock_watchdog
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-OK",
            title="No-op test",
            branch="agent/abs-ok",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
            log_file=str(tmp_path / "test.jsonl"),
            worktree=str(tmp_path),
        )

        proc = _FakeProcess()
        proc.returncode = 0  # already exited

        real_sleep = asyncio.sleep

        async def instant_sleep(secs: float) -> None:
            await real_sleep(0)

        with patch("scripts.night_manager.agent.asyncio.sleep", instant_sleep):
            task = _schedule_wall_clock_watchdog(proc, issue, max_minutes=1)
            await asyncio.wait_for(task, timeout=5)

        assert proc.signals == [], "No signals should be sent if process already exited"
        assert issue.status != "failed"


# ---------------------------------------------------------------------------
# C — Playwright JSON parsing: failing test names extracted correctly
# ---------------------------------------------------------------------------

SAMPLE_PLAYWRIGHT_JSON = {
    "suites": [
        {
            "title": "",
            "file": "e2e/smoke/01-home.spec.ts",
            "suites": [
                {
                    "title": "home page",
                    "suites": [],
                    "specs": [
                        {
                            "title": "loads the page",
                            "tests": [
                                {"results": [{"status": "passed"}]},
                            ],
                        },
                        {
                            "title": "shows nav items",
                            "tests": [
                                {"results": [{"status": "failed"}]},
                            ],
                        },
                    ],
                }
            ],
            "specs": [],
        },
        {
            "title": "",
            "file": "e2e/functional/auth/clerk-mock.spec.ts",
            "suites": [],
            "specs": [
                {
                    "title": "clerk fallback when JWT missing",
                    "tests": [
                        {"results": [{"status": "timedOut"}]},
                    ],
                },
            ],
        },
    ]
}


class TestExtractFailingTests:
    def test_extracts_failing_test_names(self, tmp_path: Path):
        from scripts.night_manager.reviewer import _extract_failing_tests

        worktree = tmp_path
        results_dir = worktree / "web" / "test-results"
        results_dir.mkdir(parents=True)
        (results_dir / "results.json").write_text(
            json.dumps(SAMPLE_PLAYWRIGHT_JSON), encoding="utf-8"
        )

        output = _extract_failing_tests(str(worktree))

        assert "shows nav items" in output
        assert "clerk fallback when JWT missing" in output
        assert "01-home.spec.ts" in output
        assert "clerk-mock.spec.ts" in output
        # Includes isolation invocation hint
        assert "npx playwright test" in output

    def test_does_not_include_passing_tests(self, tmp_path: Path):
        from scripts.night_manager.reviewer import _extract_failing_tests

        worktree = tmp_path
        results_dir = worktree / "web" / "test-results"
        results_dir.mkdir(parents=True)
        (results_dir / "results.json").write_text(
            json.dumps(SAMPLE_PLAYWRIGHT_JSON), encoding="utf-8"
        )

        output = _extract_failing_tests(str(worktree))
        assert "loads the page" not in output

    def test_missing_results_file_returns_empty(self, tmp_path: Path):
        from scripts.night_manager.reviewer import _extract_failing_tests

        output = _extract_failing_tests(str(tmp_path))
        assert output == ""

    def test_malformed_json_returns_empty(self, tmp_path: Path):
        from scripts.night_manager.reviewer import _extract_failing_tests

        worktree = tmp_path
        results_dir = worktree / "web" / "test-results"
        results_dir.mkdir(parents=True)
        (results_dir / "results.json").write_text("not json at all", encoding="utf-8")

        output = _extract_failing_tests(str(worktree))
        assert output == ""

    def test_no_failing_tests_returns_empty(self, tmp_path: Path):
        from scripts.night_manager.reviewer import _extract_failing_tests

        all_pass = {
            "suites": [
                {
                    "file": "e2e/smoke/01-home.spec.ts",
                    "suites": [],
                    "specs": [
                        {
                            "title": "loads",
                            "tests": [{"results": [{"status": "passed"}]}],
                        }
                    ],
                }
            ]
        }
        worktree = tmp_path
        results_dir = worktree / "web" / "test-results"
        results_dir.mkdir(parents=True)
        (results_dir / "results.json").write_text(json.dumps(all_pass), encoding="utf-8")

        output = _extract_failing_tests(str(worktree))
        assert output == ""


# ---------------------------------------------------------------------------
# D — Prior agent reasoning extracted from jsonl
# ---------------------------------------------------------------------------

class TestExtractPriorReasoning:
    def test_extracts_text_blocks(self, tmp_path: Path):
        from scripts.night_manager.agent import _extract_prior_reasoning

        log_file = tmp_path / "ABS-999.jsonl"
        events = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "I will start by reading the file."}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "The bug is in line 42."}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                },
            },
        ]
        with open(log_file, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

        result = _extract_prior_reasoning(str(log_file))

        assert "I will start by reading the file." in result
        assert "The bug is in line 42." in result
        # Tool_use blocks should not appear as raw text
        assert "tool_use" not in result

    def test_missing_log_file_returns_empty(self, tmp_path: Path):
        from scripts.night_manager.agent import _extract_prior_reasoning

        result = _extract_prior_reasoning(str(tmp_path / "nonexistent.jsonl"))
        assert result == ""

    def test_empty_log_file_returns_empty(self, tmp_path: Path):
        from scripts.night_manager.agent import _extract_prior_reasoning

        log_file = tmp_path / "empty.jsonl"
        log_file.write_text("", encoding="utf-8")

        result = _extract_prior_reasoning(str(log_file))
        assert result == ""

    def test_tail_50_lines_respected(self, tmp_path: Path):
        """Only the last 50 text blocks are included."""
        from scripts.night_manager.agent import _extract_prior_reasoning

        log_file = tmp_path / "long.jsonl"
        with open(log_file, "w") as f:
            for i in range(100):
                event = {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"step {i}"}],
                    },
                }
                f.write(json.dumps(event) + "\n")

        result = _extract_prior_reasoning(str(log_file), tail_lines=50)

        # step 0–49 are early; step 50–99 are the tail
        assert "step 99" in result
        assert "step 50" in result
        assert "step 0" not in result
        assert "step 49" not in result

    async def test_continuation_prompt_includes_prior_reasoning(self, tmp_path: Path):
        """_spawn_continuation embeds prior reasoning in the prompt."""
        from scripts.night_manager.agent import _spawn_continuation
        from scripts.night_manager.config import NMConfig

        issue = _make_issue(tmp_path)
        # Write some reasoning to the log file
        log_file = Path(issue.log_file)
        event = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "The fix requires editing pyproject.toml."}],
            },
        }
        log_file.write_text(json.dumps(event) + "\n", encoding="utf-8")

        captured_prompt: list[str] = []

        async def fake_subprocess(*args, **kwargs) -> MagicMock:
            # The prompt is the last positional arg
            captured_prompt.append(args[-1])
            proc = MagicMock()
            proc.pid = 99
            proc.returncode = None
            proc.stdout = asyncio.StreamReader()
            return proc

        config = MagicMock(spec=NMConfig)
        config.agent_model = "sonnet"
        config.agent_effort = "high"
        config.agent_token_limit = 10.0

        with patch("scripts.night_manager.agent.asyncio.create_subprocess_exec", fake_subprocess):
            with patch("scripts.night_manager.agent._schedule_wall_clock_watchdog"):
                await _spawn_continuation(issue, config, "e2e failed")

        assert captured_prompt, "No prompt captured"
        prompt = captured_prompt[0]
        assert "The fix requires editing pyproject.toml." in prompt
        assert "Prior agent reasoning" in prompt


# ---------------------------------------------------------------------------
# Stale-branch recovery: setup_worktree must purge a leftover agent/* branch
# from a prior crashed NM run instead of crashing the whole orchestrator.
# ---------------------------------------------------------------------------

class TestSetupWorktreeStaleBranchRecovery:
    """A prior NM crash can leave `agent/ABS-XXX-*` on a stale dev SHA with no
    worktree pointing at it. The next run must purge that orphan and create
    a fresh worktree off current dev — otherwise the second `git worktree add
    -b <branch> dev` fails with "branch already exists" and tears down NM.
    """

    @pytest.mark.asyncio
    async def test_purges_stale_branch_then_creates_worktree(self, tmp_path: Path):
        from scripts.night_manager import agent as agent_mod

        calls: list[list[str]] = []
        # Simulated git state: branch exists; worktree path does not.
        state = {"branch_exists": True, "wt_created": False}

        async def fake_git(*args: str) -> tuple[int, bytes, bytes]:
            calls.append(list(args))
            if args[:3] == ("rev-parse", "--verify", "--quiet"):
                return (0, b"", b"") if state["branch_exists"] else (1, b"", b"")
            if args[:2] == ("rev-parse", "--short"):
                return (0, b"deadbee\n", b"")
            if args[:2] == ("branch", "-D"):
                state["branch_exists"] = False
                return (0, b"Deleted branch.\n", b"")
            if args[0] == "worktree" and args[1] == "add":
                if state["branch_exists"]:
                    return (
                        128, b"",
                        b"fatal: a branch named '...' already exists\n",
                    )
                state["wt_created"] = True
                return (0, b"Preparing worktree\n", b"")
            return (0, b"", b"")

        # Worktree dir must NOT pre-exist or setup_worktree short-circuits.
        worktree_root = tmp_path / "worktrees"
        worktree_root.mkdir()

        with patch.object(agent_mod, "_git", fake_git), \
             patch.object(agent_mod, "WORKTREE_ROOT", worktree_root), \
             patch.object(agent_mod, "_setup_worktree_deps", AsyncMock()):
            issue = _make_issue(tmp_path, identifier="ABS-203")
            issue.worktree = ""  # force the "not yet created" path
            wt = await agent_mod.setup_worktree(issue, MagicMock())

        # Branch was purged before worktree add ran.
        cmd_names = [tuple(c[:2]) for c in calls]
        assert ("branch", "-D") in cmd_names, calls
        assert ("worktree", "add") in cmd_names, calls
        branch_d_idx = cmd_names.index(("branch", "-D"))
        wt_add_idx = cmd_names.index(("worktree", "add"))
        assert branch_d_idx < wt_add_idx, "branch -D must precede worktree add"
        assert state["wt_created"] is True
        assert wt == worktree_root / "nm-abs-203"

    @pytest.mark.asyncio
    async def test_no_purge_when_branch_absent(self, tmp_path: Path):
        from scripts.night_manager import agent as agent_mod

        calls: list[list[str]] = []

        async def fake_git(*args: str) -> tuple[int, bytes, bytes]:
            calls.append(list(args))
            if args[:3] == ("rev-parse", "--verify", "--quiet"):
                return (1, b"", b"")  # branch absent
            if args[0] == "worktree" and args[1] == "add":
                return (0, b"Preparing worktree\n", b"")
            return (0, b"", b"")

        worktree_root = tmp_path / "worktrees"
        worktree_root.mkdir()

        with patch.object(agent_mod, "_git", fake_git), \
             patch.object(agent_mod, "WORKTREE_ROOT", worktree_root), \
             patch.object(agent_mod, "_setup_worktree_deps", AsyncMock()):
            issue = _make_issue(tmp_path, identifier="ABS-555")
            issue.worktree = ""
            await agent_mod.setup_worktree(issue, MagicMock())

        cmd_names = [tuple(c[:2]) for c in calls]
        assert ("branch", "-D") not in cmd_names, "must not delete a branch that isn't there"

    @pytest.mark.asyncio
    async def test_raises_when_branch_checked_out_elsewhere(self, tmp_path: Path):
        """If `git branch -D` fails (branch is checked out in another worktree
        — e.g. a parallel NM run), surface the error rather than silently
        racing for the same branch."""
        from scripts.night_manager import agent as agent_mod

        async def fake_git(*args: str) -> tuple[int, bytes, bytes]:
            if args[:3] == ("rev-parse", "--verify", "--quiet"):
                return (0, b"", b"")
            if args[:2] == ("rev-parse", "--short"):
                return (0, b"cafef00\n", b"")
            if args[:2] == ("branch", "-D"):
                return (
                    1, b"",
                    b"error: Cannot delete branch 'agent/ABS-9' checked out at '/path'\n",
                )
            return (0, b"", b"")

        worktree_root = tmp_path / "worktrees"
        worktree_root.mkdir()

        with patch.object(agent_mod, "_git", fake_git), \
             patch.object(agent_mod, "WORKTREE_ROOT", worktree_root), \
             patch.object(agent_mod, "_setup_worktree_deps", AsyncMock()):
            issue = _make_issue(tmp_path, identifier="ABS-9")
            issue.worktree = ""
            with pytest.raises(RuntimeError, match="Cannot reclaim branch"):
                await agent_mod.setup_worktree(issue, MagicMock())
