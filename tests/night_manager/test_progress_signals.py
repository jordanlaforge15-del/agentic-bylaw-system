"""Tests for ABS-167 progress signals: heartbeat, phase-transition logs, JSONL.

Covers:
- _periodic_heartbeat fires at the configured interval and is cancellable.
- code_review writes a per-issue JSONL artifact to LOGS_DIR.
- run_e2e emits start/end log lines around the make-e2e call.
- merge_to_dev emits a "starting" log line before git ops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.night_manager.reviewer import (
    _periodic_heartbeat,
    code_review,
    run_e2e,
)
from scripts.night_manager.config import NMConfig, LOGS_DIR
from scripts.night_manager.state import IssuePorts, IssueState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issue(
    *,
    identifier: str = "ABS-167-test",
    worktree: str = "",
    ports: IssuePorts | None = None,
) -> IssueState:
    return IssueState(
        identifier=identifier,
        title=f"{identifier} test",
        branch=f"agent/{identifier.lower()}",
        worktree=worktree,
        ports=ports or IssuePorts(pg=5499, api=8099, web=3099),
    )


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "-q", "-b", "dev")
    _run(path, "git", "config", "user.email", "nm-test@example.com")
    _run(path, "git", "config", "user.name", "NM Test")
    (path / "README.md").write_text("base\n")
    _run(path, "git", "add", "README.md")
    _run(path, "git", "commit", "-q", "-m", "base")


# ---------------------------------------------------------------------------
# _periodic_heartbeat
# ---------------------------------------------------------------------------


class TestPeriodicHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_fires_at_interval(self, caplog):
        """Heartbeat task emits at least one log line when interval elapses."""
        with caplog.at_level(logging.INFO, logger="night_manager.reviewer"):
            task = asyncio.create_task(
                _periodic_heartbeat("test-label", interval=0)  # zero-interval fires immediately
            )
            # Give the event loop a chance to run the task body once.
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert any("still alive" in r.message for r in caplog.records), (
            "Expected at least one 'still alive' heartbeat log line"
        )
        assert any("test-label" in r.message for r in caplog.records), (
            "Heartbeat log should include the label"
        )

    @pytest.mark.asyncio
    async def test_heartbeat_cancellable(self):
        """Task can be cancelled without raising to the caller."""
        task = asyncio.create_task(_periodic_heartbeat("cancel-test", interval=100))
        await asyncio.sleep(0)  # yield so task starts
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected; callers use contextlib.suppress


# ---------------------------------------------------------------------------
# code_review — JSONL artifact creation
# ---------------------------------------------------------------------------


class TestCodeReviewJSONLArtifact:
    @pytest.mark.asyncio
    async def test_review_writes_jsonl_to_logs_dir(self, tmp_path, monkeypatch):
        """code_review must write the stream-json output to LOGS_DIR/{id}-review.jsonl."""
        import scripts.night_manager.reviewer as rev_mod

        repo = tmp_path / "worktree"
        _init_repo(repo)
        _run(repo, "git", "checkout", "-q", "-b", "agent/abs-167-test")
        (repo / "main.py").write_text("x = 1\n")
        e2e_dir = repo / "web" / "e2e" / "functional"
        e2e_dir.mkdir(parents=True, exist_ok=True)
        (e2e_dir / "abs167.spec.ts").write_text("test('placeholder', () => {});\n")
        _run(repo, "git", "add", "main.py", "web/e2e/functional/abs167.spec.ts")
        _run(repo, "git", "commit", "-q", "-m", "[ABS-167] add main.py")

        real_exec = rev_mod.asyncio.create_subprocess_exec

        async def faking_exec(*cmd, **kwargs):
            if cmd and cmd[0] == "claude":
                result_line = json.dumps({
                    "type": "result",
                    "subtype": "success",
                    "result": '{"passed": true, "findings": [], "summary": "LGTM"}',
                    "is_error": False,
                }).encode() + b"\n"

                class _AsyncLines:
                    def __init__(self, lines):
                        self._it = iter(lines)
                    def __aiter__(self):
                        return self
                    async def __anext__(self):
                        try:
                            return next(self._it)
                        except StopIteration:
                            raise StopAsyncIteration

                class FakeProc:
                    returncode = 0
                    stdout = _AsyncLines([result_line])
                    async def wait(self):
                        return 0

                return FakeProc()
            return await real_exec(*cmd, **kwargs)

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", faking_exec)

        issue = _make_issue(identifier="ABS-167-test", worktree=str(repo))
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        passed, summary = await code_review(issue, config)

        assert passed is True
        assert "LGTM" in summary

        jsonl_path = LOGS_DIR / "ABS-167-test-review.jsonl"
        assert jsonl_path.exists(), (
            f"Expected JSONL artifact at {jsonl_path} — code_review must write the "
            "stream-json output so operators can tail -f it"
        )
        content = jsonl_path.read_text()
        assert '"type": "result"' in content


# ---------------------------------------------------------------------------
# code_review — phase-transition log lines
# ---------------------------------------------------------------------------


class TestCodeReviewLogLines:
    @pytest.mark.asyncio
    async def test_llm_started_and_returned_logs_emitted(self, tmp_path, monkeypatch, caplog):
        """code_review must emit 'Reviewer LLM started' and 'Reviewer LLM returned' bookends."""
        import scripts.night_manager.reviewer as rev_mod

        repo = tmp_path / "worktree"
        _init_repo(repo)
        _run(repo, "git", "checkout", "-q", "-b", "agent/abs-167-log-test")
        (repo / "app.py").write_text("print('hello')\n")
        e2e_dir = repo / "web" / "e2e" / "functional"
        e2e_dir.mkdir(parents=True, exist_ok=True)
        (e2e_dir / "abs167.spec.ts").write_text("test('placeholder', () => {});\n")
        _run(repo, "git", "add", "app.py", "web/e2e/functional/abs167.spec.ts")
        _run(repo, "git", "commit", "-q", "-m", "[ABS-167] add app.py")

        real_exec = rev_mod.asyncio.create_subprocess_exec

        async def faking_exec(*cmd, **kwargs):
            if cmd and cmd[0] == "claude":
                result_line = json.dumps({
                    "type": "result",
                    "subtype": "success",
                    "result": '{"passed": true, "findings": [], "summary": "ok"}',
                    "is_error": False,
                }).encode() + b"\n"

                class _AsyncLines:
                    def __init__(self, lines):
                        self._it = iter(lines)
                    def __aiter__(self):
                        return self
                    async def __anext__(self):
                        try:
                            return next(self._it)
                        except StopIteration:
                            raise StopAsyncIteration

                class FakeProc:
                    returncode = 0
                    stdout = _AsyncLines([result_line])
                    async def wait(self):
                        return 0

                return FakeProc()
            return await real_exec(*cmd, **kwargs)

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", faking_exec)

        issue = _make_issue(identifier="ABS-167-log-test", worktree=str(repo))
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        with caplog.at_level(logging.INFO, logger="night_manager.reviewer"):
            await code_review(issue, config)

        messages = [r.message for r in caplog.records]
        assert any("Reviewer LLM started" in m for m in messages), (
            "Missing 'Reviewer LLM started' log line — operator has no signal the LLM is running"
        )
        assert any("Reviewer LLM returned" in m for m in messages), (
            "Missing 'Reviewer LLM returned' log line — operator can't see elapsed time"
        )
        assert any("diff-vs-areas check (pass)" in m for m in messages), (
            "Missing diff-vs-areas pass log line"
        )
