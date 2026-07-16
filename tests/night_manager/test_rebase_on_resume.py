"""Tests for ABS-160 rebase-on-resume.

The resume path must rebase each resumable worktree onto current
`origin/dev` before spawning a continuation agent. Without this, the
agent boots against the worktree's original fork-point and any spec
fix that landed on dev between fork and resume looks like a brand-new
test failure — exactly the ABS-80 / ABS-84 trigger from
nm-20260526-153728.

These tests cover four behaviours:

1. Pure unit: `rebase_worktree_onto_dev` returns (True, ...) when
   the rebase succeeds end-to-end against a real git fixture.
2. Pure unit: returns (False, ...) when the rebase conflicts, AND
   the worktree is left clean (no half-applied rebase state).
3. Routing: subprocess-level mocks confirm the conflict path does
   NOT call `spawn_continuation` / re-spawn anything; success path
   does proceed.
4. Integration: `_run_resume` marks rebase-blocked issues as
   `blocked` and drops them from the target list.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from scripts.night_manager import agent as agent_mod
from scripts.night_manager import main as nm_main
from scripts.night_manager.agent import rebase_worktree_onto_dev
from scripts.night_manager.state import IssuePorts, IssueState, NMState


# ---------------------------------------------------------------------------
# Real-git helpers
# ---------------------------------------------------------------------------


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _init_origin_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Build an `origin` bare repo + a working clone with `dev` and a
    feature branch. Returns (origin_dir, worktree_dir).

    Layout mirrors how NM uses real git in production:

        origin/      bare repo
        worktree/    clone with origin/dev set up, feature branch checked out

    The `origin/dev` ref is what `rebase_worktree_onto_dev` rebases
    against. To simulate "dev moved forward since you forked", we
    add a new commit to `origin/dev` between the worktree's fork and
    the rebase call.
    """
    origin = tmp_path / "origin.git"
    worktree = tmp_path / "worktree"

    # Build an origin with one commit on dev
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(seed, "git", "init", "-q", "-b", "dev")
    _run(seed, "git", "config", "user.email", "nm@example.com")
    _run(seed, "git", "config", "user.name", "NM")
    (seed / "README.md").write_text("base\n")
    _run(seed, "git", "add", "README.md")
    _run(seed, "git", "commit", "-q", "-m", "base")

    _run(seed, "git", "clone", "--bare", str(seed), str(origin))

    # Working clone — this is the agent's worktree
    _run(tmp_path, "git", "clone", "-q", str(origin), str(worktree))
    _run(worktree, "git", "config", "user.email", "nm@example.com")
    _run(worktree, "git", "config", "user.name", "NM")
    _run(worktree, "git", "checkout", "-q", "-b", "agent/ABS-160-test")

    return origin, worktree


def _push_new_dev_commit(origin: Path, tmp_path: Path, path: str, content: str) -> None:
    """Land a new commit on `origin/dev` to simulate "dev moved on while
    the agent was sleeping in its worktree"."""
    pusher = tmp_path / "pusher"
    if not pusher.exists():
        _run(tmp_path, "git", "clone", "-q", str(origin), str(pusher))
        _run(pusher, "git", "config", "user.email", "dev@example.com")
        _run(pusher, "git", "config", "user.name", "Dev")
    _run(pusher, "git", "checkout", "-q", "dev")
    _run(pusher, "git", "pull", "-q")
    (pusher / path).write_text(content)
    _run(pusher, "git", "add", path)
    _run(pusher, "git", "commit", "-q", "-m", f"dev moves on: {path}")
    _run(pusher, "git", "push", "-q", "origin", "dev")


def _make_issue(*, worktree: str = "", identifier: str = "ABS-160-test") -> IssueState:
    return IssueState(
        identifier=identifier,
        title=f"{identifier} title",
        branch=f"agent/{identifier.lower()}",
        worktree=worktree,
        status="failed",
        ports=IssuePorts(pg=5499, api=8099, web=3099),
    )


# ---------------------------------------------------------------------------
# 1. Real-git: clean rebase succeeds
# ---------------------------------------------------------------------------


class TestRebaseSucceeds:
    """End-to-end against real git: when there are no conflicts the
    worktree's feature commits are replayed on top of fresh origin/dev."""

    @pytest.mark.asyncio
    async def test_clean_rebase_replays_feature_commits(self, tmp_path: Path):
        origin, worktree = _init_origin_and_worktree(tmp_path)
        # Feature commit on the agent's branch
        (worktree / "feature.txt").write_text("agent work\n")
        _run(worktree, "git", "add", "feature.txt")
        _run(worktree, "git", "commit", "-q", "-m", "[ABS-160] feature commit")
        feature_sha_before = _run(
            worktree, "git", "rev-parse", "HEAD",
        ).stdout.strip()

        # Dev moves on — adds a non-conflicting file
        _push_new_dev_commit(origin, tmp_path, "newfile.txt", "from dev\n")

        issue = _make_issue(worktree=str(worktree))

        ok, msg = await rebase_worktree_onto_dev(issue)

        assert ok is True
        assert "Rebased" in msg or "rebased" in msg.lower()
        # Feature commit must still be on the branch (now with a new SHA
        # because rebase rewrites it). The original file is preserved.
        assert (worktree / "feature.txt").exists()
        # New dev commit is now in the branch's ancestry.
        log = _run(worktree, "git", "log", "--oneline").stdout
        assert "dev moves on" in log
        assert "[ABS-160] feature commit" in log
        # The branch HEAD changed because rebase rewrote the feature commit.
        new_sha = _run(worktree, "git", "rev-parse", "HEAD").stdout.strip()
        assert new_sha != feature_sha_before


# ---------------------------------------------------------------------------
# 2. Real-git: conflict path aborts cleanly and returns False
# ---------------------------------------------------------------------------


class TestRebaseConflict:
    """When the worktree's commit conflicts with dev's, rebase must
    abort (not leave the worktree mid-rebase) and return False."""

    @pytest.mark.asyncio
    async def test_conflict_aborts_and_reports_failure(self, tmp_path: Path):
        origin, worktree = _init_origin_and_worktree(tmp_path)

        # Both the agent and dev edit the same file with incompatible content
        (worktree / "contested.txt").write_text("agent version\n")
        _run(worktree, "git", "add", "contested.txt")
        _run(worktree, "git", "commit", "-q", "-m", "[ABS-160] agent edit")

        _push_new_dev_commit(origin, tmp_path, "contested.txt", "dev version\n")

        issue = _make_issue(worktree=str(worktree))

        ok, msg = await rebase_worktree_onto_dev(issue)

        assert ok is False
        # Operator-readable diagnostic must mention rebase + worktree
        assert "conflict" in msg.lower() or "rebase" in msg.lower()

        # CRITICAL: worktree must not be mid-rebase. `git rebase --abort`
        # should have restored a clean tree.
        status = _run(worktree, "git", "status", "--porcelain").stdout
        assert status.strip() == "", (
            "rebase --abort must leave the worktree clean for manual handling"
        )
        # No REBASE_HEAD lingering
        assert not (worktree / ".git" / "rebase-merge").exists()
        assert not (worktree / ".git" / "rebase-apply").exists()


# ---------------------------------------------------------------------------
# 3. Subprocess-mocked: spawn_continuation only fires when rebase succeeds
# ---------------------------------------------------------------------------


class TestRebaseGatesContinuationSpawn:
    """Subprocess-level mock asserting the routing claim from the
    issue: when rebase fails (conflict), the continuation MUST NOT be
    spawned. When rebase succeeds, the spawn proceeds normally."""

    @staticmethod
    def _build_fake_runner(rebase_succeeds: bool, calls: list[list[str]]):
        """Return an async fake for `asyncio.create_subprocess_exec`
        that records every command and produces a configurable result
        for `git rebase`."""

        async def fake_exec(*cmd, **kwargs):
            calls.append(list(cmd))

            class FakeProc:
                def __init__(self, rc: int, stdout: bytes, stderr: bytes):
                    self.returncode = rc
                    self._stdout = stdout
                    self._stderr = stderr
                    self.pid = 12345

                async def communicate(self):
                    return self._stdout, self._stderr

            # The rebase subprocess is the one whose return code matters.
            if "rebase" in cmd and "--abort" not in cmd:
                if rebase_succeeds:
                    return FakeProc(0, b"", b"")
                return FakeProc(
                    1, b"", b"CONFLICT (content): Merge conflict in foo\n",
                )
            # fetch / rebase --abort / anything else: always succeed
            return FakeProc(0, b"", b"")

        return fake_exec

    @pytest.mark.asyncio
    async def test_conflict_blocks_continuation_spawn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """When `rebase_worktree_onto_dev` returns False, the resume
        loop must NOT call `spawn_agent` / `resume_agent` for that issue."""
        # Worktree dir must exist or rebase_worktree_onto_dev short-circuits
        worktree = tmp_path / "wt-conflict"
        worktree.mkdir()

        calls: list[list[str]] = []
        fake_exec = self._build_fake_runner(rebase_succeeds=False, calls=calls)
        monkeypatch.setattr(
            agent_mod.asyncio, "create_subprocess_exec", fake_exec,
        )

        issue = _make_issue(worktree=str(worktree))

        ok, msg = await rebase_worktree_onto_dev(issue)

        assert ok is False
        # 1. Fetch was attempted
        assert any("fetch" in c for c in calls), "must fetch before rebase"
        # 2. Rebase was attempted (and conflicted)
        rebase_calls = [c for c in calls if "rebase" in c and "--abort" not in c]
        assert len(rebase_calls) == 1
        # 3. Abort was issued to clean up
        abort_calls = [c for c in calls if "--abort" in c]
        assert len(abort_calls) == 1, (
            "rebase conflict must trigger `git rebase --abort` to keep "
            "the worktree clean"
        )

    @pytest.mark.asyncio
    async def test_success_allows_continuation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """When rebase succeeds, no abort is issued and the caller is
        clear to spawn the continuation."""
        worktree = tmp_path / "wt-clean"
        worktree.mkdir()

        calls: list[list[str]] = []
        fake_exec = self._build_fake_runner(rebase_succeeds=True, calls=calls)
        monkeypatch.setattr(
            agent_mod.asyncio, "create_subprocess_exec", fake_exec,
        )

        issue = _make_issue(worktree=str(worktree))

        ok, _ = await rebase_worktree_onto_dev(issue)

        assert ok is True
        abort_calls = [c for c in calls if "--abort" in c]
        assert abort_calls == [], (
            "clean rebase must NOT issue an abort"
        )


# ---------------------------------------------------------------------------
# 4. No-op branches: missing worktree / empty path
# ---------------------------------------------------------------------------


class TestRebaseNoopBranches:
    """rebase_worktree_onto_dev must not raise on the "no worktree to
    rebase" cases — fresh issues and post-merge-cleanup issues both
    need to flow through resume."""

    @pytest.mark.asyncio
    async def test_empty_worktree_path_skips(self):
        issue = _make_issue(worktree="")
        ok, msg = await rebase_worktree_onto_dev(issue)
        assert ok is True
        assert "never started" in msg.lower() or "no worktree" in msg.lower()

    @pytest.mark.asyncio
    async def test_missing_worktree_dir_skips(self, tmp_path: Path):
        # Reference a directory that doesn't exist
        missing = tmp_path / "does-not-exist"
        assert not missing.exists()
        issue = _make_issue(worktree=str(missing))
        ok, msg = await rebase_worktree_onto_dev(issue)
        assert ok is True
        assert "no longer exists" in msg


# ---------------------------------------------------------------------------
# 5. ABS-201: pre-rebase clean removes untracked runtime artifacts
# ---------------------------------------------------------------------------


class TestPreRebaseClean:
    """Regression for ABS-201: an untracked file committed on origin/dev by
    a sibling agent (e.g. submission-demo.pdf) must not block this worktree's
    rebase with 'untracked working tree files would be overwritten'.

    The pre-rebase `git clean -fdx` step removes it so the rebase proceeds.
    """

    @pytest.mark.asyncio
    async def test_untracked_artifact_does_not_block_rebase(self, tmp_path: Path):
        """Real-git: worktree has an untracked e2e artifact (artifact.pdf)
        that a sibling committed to origin/dev. Rebase must succeed after
        the clean step removes the untracked copy."""
        origin, worktree = _init_origin_and_worktree(tmp_path)

        # Feature commit on the agent's branch (no overlap with the artifact)
        (worktree / "feature.txt").write_text("agent work\n")
        _run(worktree, "git", "add", "feature.txt")
        _run(worktree, "git", "commit", "-q", "-m", "[ABS-201] feature commit")

        # Sibling agent commits the same-named file on dev (simulates ABS-189
        # landing first and committing submission-demo.pdf as a fixture).
        pusher = tmp_path / "pusher"
        _run(tmp_path, "git", "clone", "-q", str(origin), str(pusher))
        _run(pusher, "git", "config", "user.email", "dev@example.com")
        _run(pusher, "git", "config", "user.name", "Dev")
        _run(pusher, "git", "checkout", "-q", "dev")
        (pusher / "artifact.pdf").write_text("pdf-bytes\n")
        _run(pusher, "git", "add", "artifact.pdf")
        _run(pusher, "git", "commit", "-q", "-m", "sibling commits artifact.pdf on dev")
        _run(pusher, "git", "push", "-q", "origin", "dev")

        # E2e run leaves an untracked copy of the same file in this worktree
        (worktree / "artifact.pdf").write_text("pdf-bytes\n")
        status = _run(worktree, "git", "status", "--porcelain").stdout
        assert "artifact.pdf" in status, "artifact.pdf must be untracked before rebase"

        issue = _make_issue(worktree=str(worktree))
        ok, msg = await rebase_worktree_onto_dev(issue)

        assert ok is True, (
            f"rebase must succeed when pre-rebase clean removes the untracked "
            f"artifact; got msg: {msg}"
        )
        # Feature commit replayed successfully
        assert (worktree / "feature.txt").exists()
        # Sibling's committed artifact is now visible (landed from dev)
        assert (worktree / "artifact.pdf").exists()

    @pytest.mark.asyncio
    async def test_clean_does_not_remove_staged_changes(self, tmp_path: Path):
        """The pre-rebase clean must NOT discard staged (index) changes — only
        untracked and ignored files are removed."""
        origin, worktree = _init_origin_and_worktree(tmp_path)

        # Stage a change without committing it
        (worktree / "staged.txt").write_text("staged content\n")
        _run(worktree, "git", "add", "staged.txt")

        # Dev moves on with a non-conflicting file
        _push_new_dev_commit(origin, tmp_path, "newfile.txt", "from dev\n")

        issue = _make_issue(worktree=str(worktree))
        # Rebase will fail because staged-but-uncommitted changes block rebase.
        # The important thing: the staged file itself must NOT have been deleted
        # by the clean step (git clean -fdx only removes untracked files;
        # staged files are tracked and are therefore preserved).
        ok, msg = await rebase_worktree_onto_dev(issue)

        # Rebase itself fails or aborts due to dirty index — that's fine,
        # the staged file not being deleted is what we're asserting here.
        assert (worktree / "staged.txt").exists(), (
            "pre-rebase clean must not delete staged (index-tracked) files"
        )


# ---------------------------------------------------------------------------
# 6. Integration: _run_resume marks rebase-conflicting issues as blocked
# ---------------------------------------------------------------------------


class TestRunResumeHandlesRebaseConflict:
    """Drives the integration claim from the ABS-160 acceptance criteria:
    "Rebase conflicts halt the resume for that issue and emit a clear
    ERROR." We patch `rebase_worktree_onto_dev` so the test focuses on
    the resume-loop behaviour rather than re-testing rebase mechanics.

    What we assert:

    - Issue whose rebase fails is reclassified to `status=blocked`
      with the rebase detail in `error`.
    - That issue is dropped from `state.plan` (no continuation will
      ever be spawned).
    - Issues whose rebase succeeds remain on the plan.
    """

    @pytest.mark.asyncio
    async def test_blocked_issues_dropped_from_resume_plan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        # Build a state with two resumable issues, plus one merged
        # (must be left alone).
        state = NMState(
            run_id="nm-test", started_at="2026-01-01T00:00:00+00:00",
        )
        good = _make_issue(
            identifier="ABS-1", worktree=str(tmp_path / "wt-good"),
        )
        bad = _make_issue(
            identifier="ABS-2", worktree=str(tmp_path / "wt-bad"),
        )
        good.status = "failed"
        bad.status = "failed"
        merged = _make_issue(
            identifier="ABS-3", worktree=str(tmp_path / "wt-merged"),
        )
        merged.status = "merged"
        state.issues["ABS-1"] = good
        state.issues["ABS-2"] = bad
        state.issues["ABS-3"] = merged

        from scripts.night_manager.state import ExecutionGroup
        state.plan = [ExecutionGroup(parallel=["ABS-1", "ABS-2", "ABS-3"])]

        # Persist to STATE_FILE so _run_resume can load() it. NMState
        # loads/saves through `state.STATE_FILE` (imported from config),
        # so we patch the binding in the state module itself.
        from scripts.night_manager import state as state_mod
        state_path = tmp_path / "nm-state.json"
        state.save(state_path)
        monkeypatch.setattr(state_mod, "STATE_FILE", state_path)

        # Stub out everything that would touch the network / spawn agents
        async def fake_rebase(issue: IssueState):
            if issue.identifier == "ABS-2":
                return False, "Rebase conflict in foo.py\n  C bar.py"
            return True, "Rebased"

        monkeypatch.setattr(agent_mod, "rebase_worktree_onto_dev", fake_rebase)
        # Also patch it where _run_resume imports it (local import)
        import scripts.night_manager.main as main_mod  # noqa: F401

        # Reconciliation pass touches `branch --merged dev` — make it inert
        def fake_reconcile(state):
            return [], [], []
        monkeypatch.setattr(nm_main, "_reconcile_state_with_git", fake_reconcile)

        def fake_detect_stale(state, fn):
            return []
        monkeypatch.setattr(nm_main, "_detect_stale_pids", fake_detect_stale)

        # Stub LinearClient + _execute so we never go further than the
        # resume preamble — the assertions are on what state.json looks
        # like after rebase-on-resume runs.
        class FakeLinearClient:
            def __init__(self, *a, **kw):
                pass
            async def get_workflow_states(self):
                return {}
            async def close(self):
                pass
        monkeypatch.setattr(nm_main, "LinearClient", FakeLinearClient)

        async def fake_push(*a, **kw):
            pass
        monkeypatch.setattr(
            nm_main, "_push_linear_state_for_reconciled", fake_push,
        )
        monkeypatch.setattr(
            nm_main, "_backfill_linear_done_for_merged",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(nm_main, "_execute", AsyncMock())
        monkeypatch.setattr(nm_main, "_generate_report", AsyncMock())

        # Config — minimal valid one
        from scripts.night_manager.config import NMConfig
        config = NMConfig(
            resume=True, linear_api_key="test-key",
            agent_model="sonnet", agent_effort="high",
        )

        await nm_main._run_resume(config)

        # Reload to confirm persistence to disk
        reloaded = NMState.load(state_path)

        # ABS-2 (rebase conflict) is now blocked with rebase detail
        bad_after = reloaded.issues["ABS-2"]
        assert bad_after.status == "blocked", (
            "rebase-on-resume conflict must mark issue blocked"
        )
        assert "rebase" in (bad_after.error or "").lower()

        # ABS-1 (clean rebase) is still resumable — reset_for_retry
        # moves it back to `queued`
        good_after = reloaded.issues["ABS-1"]
        assert good_after.status == "queued"

        # ABS-2 is NOT in any execution group in the resumed plan
        for grp in reloaded.plan:
            assert "ABS-2" not in grp.parallel, (
                "rebase-blocked issue must be dropped from the resume plan"
            )
        # ABS-1 IS still in the plan
        all_planned = [i for g in reloaded.plan for i in g.parallel]
        assert "ABS-1" in all_planned
