"""Tests for ABS-166: NM re-opens Linear tickets when a merge is reverted.

Covers:
- IssueState.mark_reverted — correct field mutations
- reviewer.revert_merge — returns (bool, msg, sha) and captures the revert SHA
- main._backfill_reverted_for_stale_merges — detects historical merged+reverted
  issues and flips them to Backlog in Linear
- main._merge_and_regression_loop — on regression, calls Linear Backlog update
  and posts a comment before retrying or giving up
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.night_manager.state import IssuePorts, IssueState, NMState
from scripts.night_manager.main import (
    _backfill_reverted_for_stale_merges,
    _merge_and_regression_loop,
)
from scripts.night_manager.config import NMConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_issue(
    identifier: str = "ABS-166-test",
    status: str = "merged",
    linear_id: str = "fake-linear-id",
) -> IssueState:
    return IssueState(
        identifier=identifier,
        title=f"{identifier} title",
        branch=f"agent/{identifier.lower()}",
        status=status,
        linear_id=linear_id,
        ports=IssuePorts(pg=5499, api=8099, web=3099),
    )


def _make_state(*issues: IssueState) -> NMState:
    state = NMState(run_id="nm-test", started_at="2026-01-01T00:00:00Z")
    for issue in issues:
        state.issues[issue.identifier] = issue
    return state


def _make_linear(backlog_id: str = "backlog-state-id") -> AsyncMock:
    linear = AsyncMock()
    linear.update_issue_state = AsyncMock()
    linear.post_comment = AsyncMock()
    return linear


# ---------------------------------------------------------------------------
# IssueState.mark_reverted
# ---------------------------------------------------------------------------


class TestMarkReverted:
    def test_sets_status_reverted(self):
        issue = _make_issue(status="merged")
        issue.mark_reverted("abc1234")
        assert issue.status == "reverted"

    def test_clears_merged_at(self):
        issue = _make_issue(status="merged")
        issue.merged_at = "2026-01-01T00:00:00Z"
        issue.mark_reverted()
        assert issue.merged_at is None

    def test_error_contains_sha(self):
        issue = _make_issue(status="merged")
        issue.mark_reverted("deadbeef")
        assert "deadbeef" in (issue.error or "")

    def test_error_without_sha(self):
        issue = _make_issue(status="merged")
        issue.mark_reverted()
        assert issue.error is not None
        assert "reverted" in issue.error

    def test_clears_rate_limited(self):
        issue = _make_issue(status="merged")
        issue.rate_limited = True
        issue.mark_reverted()
        assert issue.rate_limited is False

    def test_not_in_backfill_done(self):
        """reverted issues must NOT be re-pushed to Done by the Done backfill.

        The Done backfill only processes status == 'merged'; after mark_reverted
        the status is 'reverted', so it's invisible to that sweep.
        """
        issue = _make_issue(status="merged")
        issue.mark_reverted("sha")
        assert issue.status != "merged"


# ---------------------------------------------------------------------------
# reviewer.revert_merge — SHA capture
# ---------------------------------------------------------------------------


class TestRevertMergeShaCapture:
    """revert_merge must return the short SHA of the revert commit."""

    @pytest.mark.asyncio
    async def test_returns_sha_on_success(self, tmp_path: Path):
        """Drive against a real local git repo to verify SHA capture end-to-end."""
        from scripts.night_manager import reviewer as rev_mod

        repo = tmp_path / "repo"
        _init_repo(repo)
        # Add a commit so HEAD has something to revert
        (repo / "feature.txt").write_text("feature\n")
        _run(repo, "git", "add", "feature.txt")
        _run(repo, "git", "commit", "-q", "-m", "feature commit")

        # Patch REPO_ROOT to point at our local fixture
        with patch.object(rev_mod, "REPO_ROOT", repo):
            issue = _make_issue()
            ok, msg, sha = await rev_mod.revert_merge(issue)

        assert ok is True
        assert sha  # non-empty
        assert len(sha) <= 12  # short SHA

    @pytest.mark.asyncio
    async def test_returns_empty_sha_on_failure(self, tmp_path: Path):
        """If git revert fails, SHA is empty and ok is False.

        We simulate the failure by patching asyncio.create_subprocess_exec so
        the revert process exits non-zero, which is more reliable than relying
        on a specific git error condition that varies across git versions.
        """
        import asyncio
        from scripts.night_manager import reviewer as rev_mod

        class _FailProc:
            returncode = 1
            async def communicate(self):
                return b"", b"error: not a merge commit"

        repo = tmp_path / "repo"
        _init_repo(repo)

        orig_exec = asyncio.create_subprocess_exec

        async def _failing_exec(*cmd, **kwargs):
            if "revert" in cmd:
                return _FailProc()
            return await orig_exec(*cmd, **kwargs)

        with (
            patch.object(rev_mod, "REPO_ROOT", repo),
            patch.object(rev_mod.asyncio, "create_subprocess_exec", side_effect=_failing_exec),
        ):
            issue = _make_issue()
            ok, msg, sha = await rev_mod.revert_merge(issue)

        assert ok is False
        assert sha == ""


# ---------------------------------------------------------------------------
# main._backfill_reverted_for_stale_merges
# ---------------------------------------------------------------------------


class TestBackfillRevertedForStaleMerges:
    """Startup backfill: merged issues whose merge was reverted on dev get
    flipped to reverted + Linear Backlog."""

    @pytest.mark.asyncio
    async def test_flips_merged_to_reverted_when_revert_exists(
        self, tmp_path: Path
    ):
        from scripts.night_manager import main as main_mod

        # Build a git repo that looks like: merge commit + revert commit on dev
        repo = tmp_path / "repo"
        _init_repo(repo)
        # Simulate a "Merge ABS-116: ..." commit
        (repo / "feature.txt").write_text("feature\n")
        _run(repo, "git", "add", "feature.txt")
        _run(repo, "git", "commit", "-q", "-m", "Merge ABS-116: subsection labels")
        # Simulate a revert of that merge
        (repo / "feature.txt").write_text("base\n")
        _run(repo, "git", "add", "feature.txt")
        _run(repo, "git", "commit", "-q", "-m", 'Revert "Merge ABS-116: subsection labels"')

        issue = _make_issue(identifier="ABS-116", status="merged")
        state = _make_state(issue)
        linear = _make_linear()
        workflow_states = {"Backlog": "backlog-id", "Done": "done-id"}

        with patch.object(main_mod, "REPO_ROOT", repo):
            updated = await _backfill_reverted_for_stale_merges(
                state, linear, workflow_states
            )

        assert "ABS-116" in updated
        assert state.issues["ABS-116"].status == "reverted"
        linear.update_issue_state.assert_awaited_once_with("fake-linear-id", "backlog-id")
        linear.post_comment.assert_awaited_once()
        comment_body = linear.post_comment.call_args[0][1]
        assert "Merge reverted" in comment_body
        assert "Backlog" in comment_body

    @pytest.mark.asyncio
    async def test_skips_merged_without_revert(self, tmp_path: Path):
        from scripts.night_manager import main as main_mod

        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "feature.txt").write_text("feature\n")
        _run(repo, "git", "add", "feature.txt")
        _run(repo, "git", "commit", "-q", "-m", "Merge ABS-200: clean merge no revert")

        issue = _make_issue(identifier="ABS-200", status="merged")
        state = _make_state(issue)
        linear = _make_linear()
        workflow_states = {"Backlog": "backlog-id"}

        with patch.object(main_mod, "REPO_ROOT", repo):
            updated = await _backfill_reverted_for_stale_merges(
                state, linear, workflow_states
            )

        assert updated == []
        assert state.issues["ABS-200"].status == "merged"
        linear.update_issue_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_non_merged_statuses(self, tmp_path: Path):
        from scripts.night_manager import main as main_mod

        repo = tmp_path / "repo"
        _init_repo(repo)
        # Even if there's a Revert commit, non-merged issues are untouched
        _run(repo, "git", "commit", "-q", "--allow-empty",
             "-m", 'Revert "Merge ABS-300: something"')

        for status in ("queued", "in_progress", "failed", "blocked", "reverted"):
            issue = _make_issue(identifier="ABS-300", status=status)
            state = _make_state(issue)
            linear = _make_linear()

            with patch.object(main_mod, "REPO_ROOT", repo):
                updated = await _backfill_reverted_for_stale_merges(
                    state, linear, {"Backlog": "backlog-id"}
                )

            assert updated == [], f"Should skip status={status}"
            linear.update_issue_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_backlog_state_returns_empty(self):
        from scripts.night_manager import main as main_mod

        issue = _make_issue(status="merged")
        state = _make_state(issue)
        linear = _make_linear()

        updated = await _backfill_reverted_for_stale_merges(
            state, linear, {}  # no Backlog key
        )
        assert updated == []

    @pytest.mark.asyncio
    async def test_linear_failure_does_not_raise(self, tmp_path: Path):
        """API errors during backfill must be swallowed (logged, not raised)."""
        from scripts.night_manager import main as main_mod

        repo = tmp_path / "repo"
        _init_repo(repo)
        _run(repo, "git", "commit", "-q", "--allow-empty",
             "-m", 'Revert "Merge ABS-99: something"')

        issue = _make_issue(identifier="ABS-99", status="merged")
        state = _make_state(issue)
        linear = _make_linear()
        linear.update_issue_state.side_effect = RuntimeError("API error")

        with patch.object(main_mod, "REPO_ROOT", repo):
            # Must not raise even though Linear call fails
            updated = await _backfill_reverted_for_stale_merges(
                state, linear, {"Backlog": "backlog-id"}
            )

        # State was still flipped (Linear error is non-fatal)
        assert "ABS-99" in updated
        assert state.issues["ABS-99"].status == "reverted"


# ---------------------------------------------------------------------------
# main._merge_and_regression_loop — Linear update on revert
# ---------------------------------------------------------------------------


class TestMergeAndRegressionLoopRevert:
    """When regression is detected, the loop reverts and reopens Linear.

    _merge_and_regression_loop does local imports inside the function body
    (`from .reviewer import ...`, `from .agent import ...`), so we patch
    at the reviewer / agent module level, not at main.
    """

    _REVIEWER = "scripts.night_manager.reviewer"
    _AGENT = "scripts.night_manager.agent"

    def _stub_issue(self) -> IssueState:
        issue = _make_issue(identifier="ABS-166", status="reviewing")
        issue.worktree = "/fake/worktree"
        return issue

    @pytest.mark.asyncio
    async def test_linear_backlog_called_on_revert(self):
        """Regression on first attempt → revert → Linear Backlog update + comment."""
        issue = self._stub_issue()
        state = _make_state(issue)
        linear = _make_linear()
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)
        workflow_states = {"Backlog": "backlog-id", "Done": "done-id"}

        with (
            patch(f"{self._REVIEWER}.merge_to_dev", new=AsyncMock(return_value=(True, "ok"))),
            patch(f"{self._REVIEWER}.run_dev_e2e", new=AsyncMock(return_value=(False, "failed output"))),
            patch(f"{self._REVIEWER}.revert_merge", new=AsyncMock(return_value=(True, "reverted", "abc1234"))),
            patch(f"{self._REVIEWER}.restore_worktree_branch", new=AsyncMock()),
            patch(f"{self._REVIEWER}.run_e2e", new=AsyncMock(return_value=(False, "still failing"))),
            patch(f"{self._AGENT}.resume_agent", new=AsyncMock()),
            patch(f"{self._AGENT}.monitor_agent", new=AsyncMock(return_value=(1, ""))),
            patch(f"{self._AGENT}.teardown_e2e_stack", new=AsyncMock()),
        ):
            result = await _merge_and_regression_loop(
                issue, config, state, linear, workflow_states
            )

        assert result is False
        # Linear must have been flipped to Backlog
        linear.update_issue_state.assert_any_await("fake-linear-id", "backlog-id")
        # A comment explaining the revert must have been posted
        comment_calls = linear.post_comment.await_args_list
        revert_comments = [
            c for c in comment_calls
            if "Merge reverted" in str(c)
        ]
        assert revert_comments, "Expected at least one 'Merge reverted' comment"
        assert "abc1234" in str(revert_comments[0])

    @pytest.mark.asyncio
    async def test_state_marked_reverted_after_revert(self):
        """issue.status becomes 'reverted' (not 'merged') right after a revert.

        The final exhaustion of retries overwrites with 'failed', so we assert
        that the Linear Backlog update was called (which only happens when
        reverted=True from revert_merge).
        """
        issue = self._stub_issue()
        state = _make_state(issue)
        linear = _make_linear()
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)
        workflow_states = {"Backlog": "backlog-id"}

        with (
            patch(f"{self._REVIEWER}.merge_to_dev", new=AsyncMock(return_value=(True, "ok"))),
            patch(f"{self._REVIEWER}.run_dev_e2e", new=AsyncMock(return_value=(False, "fail"))),
            patch(f"{self._REVIEWER}.revert_merge", new=AsyncMock(return_value=(True, "reverted", "sha999"))),
            patch(f"{self._REVIEWER}.restore_worktree_branch", new=AsyncMock()),
            patch(f"{self._REVIEWER}.run_e2e", new=AsyncMock(return_value=(False, "still failing"))),
            patch(f"{self._AGENT}.resume_agent", new=AsyncMock()),
            patch(f"{self._AGENT}.monitor_agent", new=AsyncMock(return_value=(1, ""))),
            patch(f"{self._AGENT}.teardown_e2e_stack", new=AsyncMock()),
        ):
            await _merge_and_regression_loop(
                issue, config, state, linear, workflow_states
            )

        # The Backlog update fires on the first revert — assert it happened.
        assert linear.update_issue_state.await_count >= 1
        backlog_calls = [
            c for c in linear.update_issue_state.await_args_list
            if "backlog-id" in str(c)
        ]
        assert backlog_calls, "Expected Backlog state update after revert"

    @pytest.mark.asyncio
    async def test_no_linear_update_when_revert_fails(self):
        """If revert_merge itself fails, do not call Linear Backlog update."""
        issue = self._stub_issue()
        state = _make_state(issue)
        linear = _make_linear()
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)
        workflow_states = {"Backlog": "backlog-id"}

        with (
            patch(f"{self._REVIEWER}.merge_to_dev", new=AsyncMock(return_value=(True, "ok"))),
            patch(f"{self._REVIEWER}.run_dev_e2e", new=AsyncMock(return_value=(False, "fail"))),
            patch(f"{self._REVIEWER}.revert_merge", new=AsyncMock(return_value=(False, "revert failed", ""))),
            patch(f"{self._REVIEWER}.restore_worktree_branch", new=AsyncMock()),
            patch(f"{self._REVIEWER}.run_e2e", new=AsyncMock(return_value=(False, "still failing"))),
            patch(f"{self._AGENT}.resume_agent", new=AsyncMock()),
            patch(f"{self._AGENT}.monitor_agent", new=AsyncMock(return_value=(1, ""))),
            patch(f"{self._AGENT}.teardown_e2e_stack", new=AsyncMock()),
        ):
            await _merge_and_regression_loop(
                issue, config, state, linear, workflow_states
            )

        # No Backlog update should have fired (revert returned False)
        backlog_calls = [
            c for c in linear.update_issue_state.await_args_list
            if "backlog-id" in str(c)
        ]
        assert backlog_calls == []

    @pytest.mark.asyncio
    async def test_reopen_without_workflow_states(self):
        """Loop works even when workflow_states is None (no Linear update fired)."""
        issue = self._stub_issue()
        state = _make_state(issue)
        linear = _make_linear()
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        with (
            patch(f"{self._REVIEWER}.merge_to_dev", new=AsyncMock(return_value=(True, "ok"))),
            patch(f"{self._REVIEWER}.run_dev_e2e", new=AsyncMock(return_value=(False, "fail"))),
            patch(f"{self._REVIEWER}.revert_merge", new=AsyncMock(return_value=(True, "reverted", "sha"))),
            patch(f"{self._REVIEWER}.restore_worktree_branch", new=AsyncMock()),
            patch(f"{self._REVIEWER}.run_e2e", new=AsyncMock(return_value=(False, "still failing"))),
            patch(f"{self._AGENT}.resume_agent", new=AsyncMock()),
            patch(f"{self._AGENT}.monitor_agent", new=AsyncMock(return_value=(1, ""))),
            patch(f"{self._AGENT}.teardown_e2e_stack", new=AsyncMock()),
        ):
            # Must not raise even without workflow_states
            result = await _merge_and_regression_loop(
                issue, config, state, linear, None
            )

        assert result is False  # exhausted retries as expected
