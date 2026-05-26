"""Integration tests for `reviewer.merge_to_dev` and its helpers.

These tests use real `git init` repositories under `tmp_path` rather than
mocking `subprocess` — mocking subprocess is exactly the abstraction that
hid the silent-stderr bug (git writes content-conflict errors to stdout,
not stderr, and the original code only decoded stderr). A real git makes
the streams behave authentically.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.night_manager import reviewer
from scripts.night_manager.reviewer import (
    _assert_clean_worktree,
    _combine_streams,
    merge_to_dev,
)
from scripts.night_manager.state import IssuePorts, IssueState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git/shell command synchronously inside `cwd`. Helper for fixtures."""
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(path: Path) -> None:
    """Initialise a bare-bones git repo with one commit on `dev`."""
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "-q", "-b", "dev")
    _run(path, "git", "config", "user.email", "nm-test@example.com")
    _run(path, "git", "config", "user.name", "NM Test")
    (path / "README.md").write_text("base\n")
    _run(path, "git", "add", "README.md")
    _run(path, "git", "commit", "-q", "-m", "base")


def _make_issue(branch: str = "agent/ABS-999-test") -> IssueState:
    return IssueState(
        identifier="ABS-999",
        title="Test merge",
        branch=branch,
        ports=IssuePorts(pg=5499, api=8099, web=3099),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh repo on branch `dev` with a single base commit."""
    path = tmp_path / "repo"
    _init_repo(path)
    return path


# ---------------------------------------------------------------------------
# _combine_streams — pure function, exhaustive cases
# ---------------------------------------------------------------------------


class TestCombineStreams:
    def test_both_empty(self):
        assert _combine_streams(b"", b"") == ""

    def test_only_stderr(self):
        assert _combine_streams(b"", b"err\n") == "err"

    def test_only_stdout(self):
        # This is the case that hid Bug 2.
        assert _combine_streams(b"CONFLICT (content): Merge conflict\n", b"") == (
            "CONFLICT (content): Merge conflict"
        )

    def test_both_present(self):
        # stderr first, then stdout — stderr is conventionally the primary
        # error channel; stdout is the supplement.
        combined = _combine_streams(b"out body\n", b"err header\n")
        assert combined == "err header\nout body"

    def test_handles_non_utf8(self):
        # Garbled bytes must not raise.
        combined = _combine_streams(b"\xff\xfe stdout", b"\xff\xfe stderr")
        assert "stdout" in combined
        assert "stderr" in combined


# ---------------------------------------------------------------------------
# _assert_clean_worktree — uses real git status against a real repo
# ---------------------------------------------------------------------------


class TestAssertCleanWorktree:
    async def test_clean_repo_returns_clean(self, repo: Path):
        clean, msg = await _assert_clean_worktree(str(repo))
        assert clean is True
        assert msg == ""

    async def test_untracked_file_is_dirty(self, repo: Path):
        # Reproduces the ABS-68 scenario: untracked docker-compose.production.yml.
        (repo / "docker-compose.production.yml").write_text("services: {}\n")

        clean, msg = await _assert_clean_worktree(str(repo))
        assert clean is False
        assert "docker-compose.production.yml" in msg
        assert "refusing to merge" in msg
        # Operator needs the cleanup command in the error.
        assert "git -C" in msg

    async def test_modified_tracked_file_is_dirty(self, repo: Path):
        # Reproduces the ABS-6 scenario: web/tsconfig.json modified in place.
        (repo / "README.md").write_text("modified\n")

        clean, msg = await _assert_clean_worktree(str(repo))
        assert clean is False
        assert "README.md" in msg

    async def test_staged_change_is_dirty(self, repo: Path):
        (repo / "new.txt").write_text("staged\n")
        _run(repo, "git", "add", "new.txt")

        clean, msg = await _assert_clean_worktree(str(repo))
        assert clean is False
        assert "new.txt" in msg


# ---------------------------------------------------------------------------
# merge_to_dev — end-to-end behaviour against a real repo
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_with_agent_branch(repo: Path) -> tuple[Path, str]:
    """Repo with an `agent/ABS-999-test` branch that adds one file.

    Mirrors the night-manager topology: `dev` has the base commit, the
    agent's feature branch has one extra commit on top of `dev`.
    """
    branch = "agent/ABS-999-test"
    _run(repo, "git", "checkout", "-q", "-b", branch)
    (repo / "feature.txt").write_text("from agent\n")
    _run(repo, "git", "add", "feature.txt")
    _run(repo, "git", "commit", "-q", "-m", "feature commit")
    _run(repo, "git", "checkout", "-q", "dev")
    return repo, branch


def _patch_repo_root(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """Point reviewer.REPO_ROOT at the test repo for the duration of the test."""
    monkeypatch.setattr(reviewer, "REPO_ROOT", repo)


def _skip_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the `git pull`/`git push origin dev` steps — no remote in tests."""
    monkeypatch.setattr(
        reviewer,
        "merge_to_dev",
        reviewer.merge_to_dev,  # leave as-is; we'll filter via env instead
    )


class TestMergeToDev:
    async def test_refuses_when_target_dirty_with_untracked(
        self,
        repo_with_agent_branch: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Bug 1 regression: dirty worktree => merge refused with clear stderr.

        Replicates the ABS-68 night-run scenario: an untracked file
        (docker-compose.production.yml) sits in the target worktree, the
        feature branch would create or overwrite it on merge, and the merge
        must refuse cleanly without ever invoking `git merge`.
        """
        repo, branch = repo_with_agent_branch
        # Set up the exact untracked file from the ABS-68 failure.
        (repo / "docker-compose.production.yml").write_text("services: {}\n")

        _patch_repo_root(monkeypatch, repo)
        issue = _make_issue(branch=branch)

        # Stub out the network steps so we exercise the precondition only.
        # The precondition fires first; pull/push should never run.
        with patch.object(
            asyncio,
            "create_subprocess_exec",
            wraps=asyncio.create_subprocess_exec,
        ) as spy:
            success, msg = await merge_to_dev(issue)

        assert success is False
        # Error mentions the untracked file and tells operator how to inspect.
        assert "docker-compose.production.yml" in msg
        assert "refusing to merge" in msg
        # No `git merge` was attempted — only `git status --porcelain`.
        invoked = [call.args for call in spy.call_args_list]
        commands = [tuple(args) for args in invoked]
        assert any(
            "status" in c and "--porcelain" in c for c in commands
        ), f"expected status --porcelain, got {commands}"
        assert not any(
            "merge" in c for c in commands
        ), f"merge must NOT run when target is dirty; got {commands}"

    async def test_refuses_when_target_dirty_with_modified_tracked(
        self,
        repo_with_agent_branch: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Bug 1 regression: modified tracked file also blocks merge.

        Replicates the ABS-6 scenario: `web/tsconfig.json` modified in place.
        """
        repo, branch = repo_with_agent_branch
        (repo / "README.md").write_text("dirty\n")

        _patch_repo_root(monkeypatch, repo)
        issue = _make_issue(branch=branch)

        success, msg = await merge_to_dev(issue)

        assert success is False
        assert "README.md" in msg

    async def test_merge_conflict_surfaces_stdout(
        self,
        repo_with_agent_branch: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Bug 2 regression: content-conflict error must reach the caller.

        Constructs a real merge conflict (both sides modify the same file)
        and confirms the returned message contains the CONFLICT line that
        git emits on stdout. Without `_combine_streams`, this string was
        being silently dropped — see the empty-stderr ABS-134/128/133
        failures in `.night-manager/nm-20260525-214132.log`.
        """
        repo, branch = repo_with_agent_branch

        # Make the agent branch and dev modify the SAME file in conflicting ways.
        _run(repo, "git", "checkout", "-q", branch)
        (repo / "README.md").write_text("agent side\n")
        _run(repo, "git", "commit", "-aq", "-m", "agent edit README")

        _run(repo, "git", "checkout", "-q", "dev")
        (repo / "README.md").write_text("dev side\n")
        _run(repo, "git", "commit", "-aq", "-m", "dev edit README")

        # Patch out the network step so the test doesn't depend on a remote.
        # We do this by skipping `git pull --ff-only origin dev` via a fake
        # "origin" remote pointing at the repo itself.
        _run(repo, "git", "remote", "add", "origin", str(repo))
        # Ensure dev tracks origin/dev — populate the remote ref via push.
        # Note: pushing to a non-bare repo's current branch is rejected, so
        # instead create a bare mirror.
        bare = repo.parent / "remote.git"
        _run(repo, "git", "clone", "--bare", "-q", str(repo), str(bare))
        _run(repo, "git", "remote", "set-url", "origin", str(bare))
        _run(repo, "git", "fetch", "-q", "origin")
        _run(repo, "git", "branch", "--set-upstream-to=origin/dev", "dev")

        _patch_repo_root(monkeypatch, repo)
        issue = _make_issue(branch=branch)

        success, msg = await merge_to_dev(issue)

        assert success is False, "merge with conflicting README must fail"
        # The actual conflict line lives on git's stdout; if _combine_streams
        # is regressed back to stderr-only this assertion fires.
        assert "CONFLICT" in msg or "Automatic merge failed" in msg, (
            f"expected git's content-conflict message in the error; got: {msg!r}"
        )
        # Confirm the offending file is in the report.
        assert "README.md" in msg

    async def test_successful_merge_on_clean_repo(
        self,
        repo_with_agent_branch: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Happy path: clean repo + non-conflicting branch => merge succeeds."""
        repo, branch = repo_with_agent_branch

        # Bare remote so `git pull --ff-only` and `git push` both work.
        bare = repo.parent / "remote.git"
        _run(repo, "git", "clone", "--bare", "-q", str(repo), str(bare))
        _run(repo, "git", "remote", "add", "origin", str(bare))
        _run(repo, "git", "fetch", "-q", "origin")
        _run(repo, "git", "branch", "--set-upstream-to=origin/dev", "dev")

        _patch_repo_root(monkeypatch, repo)
        issue = _make_issue(branch=branch)

        success, msg = await merge_to_dev(issue)

        assert success is True, f"clean merge should succeed; got: {msg!r}"
        assert "ABS-999" in msg
        # Verify the commit landed on dev.
        log_out = _run(repo, "git", "log", "--oneline", "dev")
        assert "Merge ABS-999" in log_out.stdout
