"""Tests for `main._run_resume` and its helpers — resume-logic gaps fix.

Real git fixtures, no `subprocess.run` mocking — mirrors the style of
`test_merge_to_dev.py`. Faking subprocess would defeat the purpose of
testing the git-reconciliation pass: it MUST work against actual
`git branch --merged` / `merge-base` output, including the
nm-20260526-004610 zombie-branch case (ABS-129 / ABS-135 — tip equals
merge-base after a revert).

The four scenarios this file exercises map 1:1 to the gaps described
in the Night Manager resume-fix task:

- `is_resumable` returns True for `reviewing` (Gap 1).
- `--resume-queued` opens up `queued`; without it, queued is skipped
  (Gap 1 + Gap 3 — opt-in to avoid re-doing out-of-band merges).
- Sanity-check pass reclassifies a branch already on dev as `merged`
  (Gap 3 — the ABS-125 case).
- Sanity-check pass marks a zombie (tip == merge-base) as `blocked`
  (Gap 3 — the ABS-129 / ABS-135 case).
- Stale PID detected; resume reclassifies so re-spawn fires (Gap 2).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.night_manager import main as nm_main
from scripts.night_manager.agent import is_pid_alive
from scripts.night_manager.config import parse_args
from scripts.night_manager.state import (
    ExecutionGroup,
    IssuePorts,
    IssueState,
    NMState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "-q", "-b", "dev")
    _run(path, "git", "config", "user.email", "nm-test@example.com")
    _run(path, "git", "config", "user.name", "NM Test")
    (path / "README.md").write_text("base\n")
    _run(path, "git", "add", "README.md")
    _run(path, "git", "commit", "-q", "-m", "base")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh repo on `dev` with one base commit."""
    path = tmp_path / "repo"
    _init_repo(path)
    return path


def _make_issue(
    identifier: str = "ABS-999",
    branch: str = "agent/ABS-999-test",
    status: str = "queued",
    pid: int = 0,
) -> IssueState:
    return IssueState(
        identifier=identifier,
        title=f"{identifier} title",
        branch=branch,
        status=status,
        pid=pid,
        ports=IssuePorts(pg=5499, api=8099, web=3099),
    )


# ---------------------------------------------------------------------------
# Gap 1: is_resumable widens to include `reviewing`
# ---------------------------------------------------------------------------


class TestIsResumable:
    def test_reviewing_is_resumable(self):
        """A `reviewing` issue (killed mid-e2e) must be picked up.

        Regression: in the old code `is_resumable` returned False for
        `reviewing`, so ABS-121 from run nm-20260526-004610 was silently
        skipped on `--resume`.
        """
        issue = _make_issue(status="reviewing")
        assert issue.is_resumable is True

    def test_failed_is_resumable(self):
        assert _make_issue(status="failed").is_resumable is True

    def test_blocked_is_resumable(self):
        assert _make_issue(status="blocked").is_resumable is True

    def test_queued_not_resumable_by_default(self):
        """Queued is NOT in the default resumable set — must be opt-in."""
        assert _make_issue(status="queued").is_resumable is False

    def test_in_progress_not_resumable(self):
        # The stale-PID pass turns in_progress→failed; in_progress itself
        # is not directly resumable.
        assert _make_issue(status="in_progress").is_resumable is False

    def test_merged_not_resumable(self):
        assert _make_issue(status="merged").is_resumable is False


# ---------------------------------------------------------------------------
# CLI flag: --resume-queued
# ---------------------------------------------------------------------------


class TestResumeQueuedFlag:
    def test_flag_default_off(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINEAR_API_KEY", "test-key")
        cfg = parse_args(["--resume"])
        assert cfg.resume is True
        assert cfg.resume_queued is False

    def test_flag_opt_in(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINEAR_API_KEY", "test-key")
        cfg = parse_args(["--resume", "--resume-queued"])
        assert cfg.resume_queued is True


# ---------------------------------------------------------------------------
# Gap 3: state-vs-git reconciliation
# ---------------------------------------------------------------------------


class TestReconcileWithGit:
    """Verify _reconcile_state_with_git against real git operations."""

    def _make_state(self, **issues_kwargs) -> NMState:
        state = NMState(run_id="nm-test", started_at="2026-01-01T00:00:00+00:00")
        for ident, kw in issues_kwargs.items():
            state.issues[ident] = _make_issue(identifier=ident, **kw)
        return state

    def test_branch_already_on_dev_reclassified_as_merged(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """ABS-125 scenario: branch was merged outside NM.

        If we naively re-ran with --resume-queued, NM would re-do the
        already-merged work. The sanity-check pass catches this.
        """
        branch = "agent/ABS-125-merged-outside-nm"
        _run(repo, "git", "checkout", "-q", "-b", branch)
        (repo / "feature.txt").write_text("done\n")
        _run(repo, "git", "add", "feature.txt")
        _run(repo, "git", "commit", "-q", "-m", "feature")
        _run(repo, "git", "checkout", "-q", "dev")
        # Merge the branch into dev (the "out-of-band" merge).
        _run(repo, "git", "merge", "--no-ff", "-q", branch, "-m", "Merge ABS-125")

        monkeypatch.setattr(nm_main, "REPO_ROOT", repo)
        state = self._make_state(**{"ABS-125": {"branch": branch, "status": "queued"}})

        reconciled = nm_main._reconcile_state_with_git(state)

        assert "ABS-125" in reconciled
        assert state.issues["ABS-125"].status == "merged"
        assert state.issues["ABS-125"].merged_at is not None
        assert state.issues["ABS-125"].error is None

    def test_zombie_branch_marked_blocked(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """ABS-129 / ABS-135 scenario: merged then reverted.

        After `git revert -m 1` the branch's tip is unchanged but the
        merge-base with dev now equals the tip — the branch has nothing
        new to offer. Sanity-check pass must catch this so the operator
        knows to re-plan rather than retry.
        """
        branch = "agent/ABS-135-zombie"
        _run(repo, "git", "checkout", "-q", "-b", branch)
        (repo / "feature.txt").write_text("from agent\n")
        _run(repo, "git", "add", "feature.txt")
        _run(repo, "git", "commit", "-q", "-m", "feature commit")
        _run(repo, "git", "checkout", "-q", "dev")
        merge_out = _run(
            repo, "git", "merge", "--no-ff", "-q", branch,
            "-m", "Merge ABS-135",
        )
        # Revert the merge — recreates the ABS-135 zombie shape.
        merge_sha = _run(repo, "git", "rev-parse", "HEAD").stdout.strip()
        _run(repo, "git", "revert", "-m", "1", "--no-edit", merge_sha)

        # Sanity: branch tip == merge-base with dev (the defining shape).
        tip = _run(repo, "git", "rev-parse", branch).stdout.strip()
        base = _run(repo, "git", "merge-base", "dev", branch).stdout.strip()
        assert tip == base, "fixture is wrong — should be a zombie"

        monkeypatch.setattr(nm_main, "REPO_ROOT", repo)
        state = self._make_state(**{"ABS-135": {"branch": branch, "status": "failed"}})

        reconciled = nm_main._reconcile_state_with_git(state)

        assert "ABS-135" in reconciled
        issue = state.issues["ABS-135"]
        assert issue.status == "blocked"
        assert "zombie" in (issue.error or "").lower()

    def test_clean_unmerged_branch_untouched(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """A normal failed-mid-work branch must NOT be reclassified.

        If reconciliation triggers spuriously on healthy in-flight work,
        we'd silently throw away genuine failures.
        """
        branch = "agent/ABS-200-real-failure"
        _run(repo, "git", "checkout", "-q", "-b", branch)
        (repo / "x.txt").write_text("wip\n")
        _run(repo, "git", "add", "x.txt")
        _run(repo, "git", "commit", "-q", "-m", "wip — incomplete")
        _run(repo, "git", "checkout", "-q", "dev")

        monkeypatch.setattr(nm_main, "REPO_ROOT", repo)
        state = self._make_state(**{
            "ABS-200": {"branch": branch, "status": "failed"},
        })

        reconciled = nm_main._reconcile_state_with_git(state)

        assert reconciled == []
        assert state.issues["ABS-200"].status == "failed"

    def test_merged_issue_skipped(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """`merged` status is terminal — reconciliation must skip it."""
        monkeypatch.setattr(nm_main, "REPO_ROOT", repo)
        state = self._make_state(**{
            "ABS-99": {"branch": "agent/whatever", "status": "merged"},
        })
        # Branch doesn't exist — would normally raise. Just confirm no churn.
        reconciled = nm_main._reconcile_state_with_git(state)
        assert reconciled == []
        assert state.issues["ABS-99"].status == "merged"


# ---------------------------------------------------------------------------
# Gap 2: stale-PID detection
# ---------------------------------------------------------------------------


class TestStalePIDDetection:
    def test_is_pid_alive_for_current_process(self):
        """Sanity check: the test runner's own PID is alive."""
        import os
        assert is_pid_alive(os.getpid()) is True

    def test_is_pid_alive_for_dead_pid(self):
        """A PID that never existed reports as dead.

        We use the very large PID 2^31 - 1 — vastly out of any plausible
        running-process range on POSIX systems. Doing this rather than
        forking + reaping a child keeps the test deterministic and
        avoids zombie children on test re-runs.
        """
        assert is_pid_alive(2**31 - 1) is False

    def test_zero_pid_not_alive(self):
        assert is_pid_alive(0) is False

    def test_detect_stale_pids_reclassifies(self):
        """A reviewing issue with a dead PID must move to `failed`.

        Without this, the resume loop would either skip the issue
        (because reviewing was non-resumable) or — once Gap 1 is fixed
        — try to monitor a corpse. The reclassification ensures the
        resume path takes the re-spawn branch.
        """
        state = NMState(run_id="nm-test", started_at="2026-01-01T00:00:00+00:00")
        state.issues["ABS-121"] = _make_issue(
            identifier="ABS-121", status="reviewing", pid=99999,
        )
        state.issues["ABS-115"] = _make_issue(
            identifier="ABS-115", status="in_progress", pid=99998,
        )
        # Live issue (uses real PID — won't be reclassified).
        import os
        state.issues["ABS-200"] = _make_issue(
            identifier="ABS-200", status="in_progress", pid=os.getpid(),
        )

        # All-dead is_pid_alive_fn for the two stale ones, real for the live one.
        def fake_alive(pid: int) -> bool:
            return pid == os.getpid()

        stale = nm_main._detect_stale_pids(state, fake_alive)

        assert set(stale) == {"ABS-121", "ABS-115"}
        assert state.issues["ABS-121"].status == "failed"
        assert state.issues["ABS-115"].status == "failed"
        assert "Orphaned" in (state.issues["ABS-121"].error or "")
        # Live PID untouched.
        assert state.issues["ABS-200"].status == "in_progress"

    def test_resume_clears_stale_pid_after_reset(self):
        """`reset_for_retry` keeps session_id; resume zeros the dead PID.

        Important because `claude --resume <session_id>` re-attaches to
        the durable session, but the recorded PID was from a dead
        process and must not leak through to the new lifecycle.
        """
        state = NMState(run_id="nm-test", started_at="2026-01-01T00:00:00+00:00")
        issue = _make_issue(status="failed", pid=99999)
        issue.session_id = "sess-durable"
        state.issues["ABS-121"] = issue

        # Simulate the bit of _run_resume that runs after target selection.
        state.issues["ABS-121"].reset_for_retry()
        state.issues["ABS-121"].pid = 0  # explicit clear (matches main.py)

        assert state.issues["ABS-121"].status == "queued"
        assert state.issues["ABS-121"].pid == 0
        # session_id is preserved — Claude Code keeps the session durable
        # across process restarts, so resume re-spawns into the same
        # conversation.
        assert state.issues["ABS-121"].session_id == "sess-durable"


# ---------------------------------------------------------------------------
# Plan rebuild — targets selected correctly with/without --resume-queued
# ---------------------------------------------------------------------------


class TestTargetSelection:
    """Verify the target-selection logic inside `_run_resume` (extracted)."""

    def _state_with_mixed_statuses(self) -> NMState:
        state = NMState(run_id="nm-test", started_at="2026-01-01T00:00:00+00:00")
        for ident, status in [
            ("ABS-1", "merged"),
            ("ABS-2", "failed"),
            ("ABS-3", "blocked"),
            ("ABS-4", "reviewing"),
            ("ABS-5", "queued"),
        ]:
            state.issues[ident] = _make_issue(identifier=ident, status=status)
        return state

    def test_default_targets_exclude_queued(self):
        state = self._state_with_mixed_statuses()
        targets = [
            ident for ident, iss in state.issues.items() if iss.is_resumable
        ]
        assert set(targets) == {"ABS-2", "ABS-3", "ABS-4"}

    def test_resume_queued_includes_queued(self):
        """Simulates the --resume-queued branch of target selection."""
        state = self._state_with_mixed_statuses()
        targets = [
            ident for ident, iss in state.issues.items() if iss.is_resumable
        ]
        queued = [
            ident for ident, iss in state.issues.items()
            if iss.status == "queued"
        ]
        targets.extend(queued)
        assert set(targets) == {"ABS-2", "ABS-3", "ABS-4", "ABS-5"}


# ---------------------------------------------------------------------------
# ABS-148: mark_completed / mark_merged scrub stale failure metadata
# ---------------------------------------------------------------------------


class TestMarkCompletedScrubsStaleFailureMetadata:
    """Regression for ABS-148: a later attempt succeeding must clear the
    `error` and `rate_limited` fields set by an earlier `mark_failed`,
    otherwise state.json reports a failed reason for an issue that
    actually completed (e.g., ABS-129 in run nm-20260526-004610)."""

    def test_mark_completed_clears_error_and_rate_limited(self):
        issue = _make_issue(status="in_progress")
        issue.mark_failed("Rate limited: <stale event>")
        issue.rate_limited = True
        assert issue.error is not None
        assert issue.rate_limited is True

        issue.mark_completed()

        assert issue.status == "reviewing"
        assert issue.completed_at is not None
        assert issue.error is None, (
            "mark_completed must scrub stale error from an earlier mark_failed"
        )
        assert issue.rate_limited is False, (
            "mark_completed must scrub stale rate_limited flag"
        )

    def test_mark_merged_clears_error_and_rate_limited(self):
        """Defense in depth — direct queued→merged transitions (out-of-band
        merge picked up by the reconciler) also need the fields scrubbed."""
        issue = _make_issue(status="failed")
        issue.error = "earlier failure"
        issue.rate_limited = True

        issue.mark_merged()

        assert issue.status == "merged"
        assert issue.merged_at is not None
        assert issue.error is None
        assert issue.rate_limited is False


# ---------------------------------------------------------------------------
# ABS-145: resume_agent picks the right path based on prior session state
# ---------------------------------------------------------------------------


class TestResumeAgentRouting:
    """Verifies the branch in `resume_agent` between `--resume <id>` and
    `_spawn_continuation` based on whether the prior session was consumed
    (`completed_at` set) — the actual root cause of nm-20260526-071803.log
    failing every resume with "Session ID already in use"."""

    @pytest.mark.asyncio
    async def test_consumed_session_routes_to_spawn_continuation(
        self, tmp_path, monkeypatch
    ):
        """When completed_at is set, resume_agent must NOT pass --resume —
        it must spawn a fresh session with a new UUID."""
        from scripts.night_manager import agent as agent_mod
        from scripts.night_manager.config import NMConfig

        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            class FakeProc:
                pid = 12345
                returncode = None
            return FakeProc()

        monkeypatch.setattr(
            agent_mod.asyncio, "create_subprocess_exec", fake_exec
        )

        issue = _make_issue(status="reviewing")
        issue.worktree = str(tmp_path)
        issue.log_file = str(tmp_path / "agent.jsonl")
        issue.session_id = "old-session-uuid"
        issue.completed_at = "2026-05-26T00:00:00+00:00"

        config = NMConfig(agent_model="sonnet", agent_effort="high")

        await agent_mod.resume_agent(issue, config, "feedback text")

        cmd = captured["cmd"]
        assert "--resume" not in cmd, (
            "resume_agent must not pass --resume when prior session is consumed"
        )
        assert "--session-id" in cmd, "_spawn_continuation must set a session id"
        new_session_id = cmd[cmd.index("--session-id") + 1]
        assert new_session_id != "old-session-uuid", (
            "_spawn_continuation must allocate a fresh UUID"
        )
        assert issue.session_id == new_session_id
        assert issue.completed_at is None, (
            "_spawn_continuation must reset completed_at so subsequent resumes "
            "see this as mid-flight, not another consumed-session case"
        )
        # ABS-149: continuation must label the session for the mobile app
        assert "--remote-control" in cmd, (
            "_spawn_continuation must enable --remote-control"
        )
        rc_idx = cmd.index("--remote-control")
        assert cmd[rc_idx + 1] == f"nm-{issue.identifier}", (
            "--remote-control name must be nm-<identifier>"
        )

    @pytest.mark.asyncio
    async def test_midflight_session_uses_resume_flag(
        self, tmp_path, monkeypatch
    ):
        """When completed_at is None (killed mid-stream), resume_agent
        keeps using --resume <session_id> for cache reuse."""
        from scripts.night_manager import agent as agent_mod
        from scripts.night_manager.config import NMConfig

        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            class FakeProc:
                pid = 12345
                returncode = None
            return FakeProc()

        monkeypatch.setattr(
            agent_mod.asyncio, "create_subprocess_exec", fake_exec
        )

        issue = _make_issue(status="failed")
        issue.worktree = str(tmp_path)
        issue.log_file = str(tmp_path / "agent.jsonl")
        issue.session_id = "live-session-uuid"
        issue.completed_at = None  # mid-flight

        config = NMConfig(agent_model="sonnet", agent_effort="high")

        await agent_mod.resume_agent(issue, config, "feedback text")

        cmd = captured["cmd"]
        assert "--resume" in cmd, (
            "Mid-flight resume must still use --resume for cache reuse"
        )
        resume_idx = cmd.index("--resume")
        assert cmd[resume_idx + 1] == "live-session-uuid", (
            "Mid-flight resume must pass the original session_id"
        )
        assert issue.session_id == "live-session-uuid"
        # ABS-149: --remote-control must also be passed on the resume path
        assert "--remote-control" in cmd, (
            "Mid-flight resume must enable --remote-control"
        )
        rc_idx = cmd.index("--remote-control")
        assert cmd[rc_idx + 1] == f"nm-{issue.identifier}"


# ---------------------------------------------------------------------------
# ABS-150: spawn_agent rotates consumed session_id on re-spawn
# ---------------------------------------------------------------------------


class TestSpawnAgentSessionIdRotation:
    """Regression for ABS-150: every re-spawn (attempts > 0) must drop the
    prior session_id and allocate a fresh UUID. Consumed sessions are
    rejected by Claude Code with `Session ID already in use`.

    ABS-145's resume_agent fix only handled the `completed_at is not None`
    case; a kernel panic between agent completion and state.save() leaves
    completed_at=None while the session_id is still consumed (the actual
    ABS-129 / ABS-135 situation in run nm-20260526-004610)."""

    @pytest.mark.asyncio
    async def test_respawn_rotates_session_id(self, tmp_path, monkeypatch):
        """attempts>0 with stale session_id → fresh UUID, both in the
        issue state and in the --session-id arg."""
        from scripts.night_manager import agent as agent_mod
        from scripts.night_manager.config import NMConfig

        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            class FakeProc:
                pid = 12345
                returncode = None
            return FakeProc()

        monkeypatch.setattr(
            agent_mod.asyncio, "create_subprocess_exec", fake_exec
        )

        issue = _make_issue(identifier="ABS-129", status="failed")
        issue.worktree = str(tmp_path)
        issue.log_file = str(tmp_path / "agent.jsonl")
        # Kernel-panic case: prior session was consumed by Claude Code
        # (terminal_reason=completed) but NM never observed it, so
        # completed_at stayed None. The gap ABS-145 didn't cover.
        issue.session_id = "8087a195-fd62-44b7-bc92-3c3883ab7e35"
        issue.attempts = 5
        issue.completed_at = None

        config = NMConfig(agent_model="sonnet", agent_effort="high")

        await agent_mod.spawn_agent(issue, config, "desc")

        cmd = captured["cmd"]
        assert "--session-id" in cmd
        new_session_id = cmd[cmd.index("--session-id") + 1]
        assert new_session_id != "8087a195-fd62-44b7-bc92-3c3883ab7e35", (
            "spawn_agent must rotate the consumed session_id on re-spawn"
        )
        assert issue.session_id == new_session_id, (
            "Rotated session_id must persist onto the issue so state.json "
            "reflects the new session"
        )

    @pytest.mark.asyncio
    async def test_initial_spawn_does_not_rotate(self, tmp_path, monkeypatch):
        """attempts=0 (first spawn) must NOT rotate — there's nothing to
        rotate and the existing flow (use existing or uuid4) is correct."""
        from scripts.night_manager import agent as agent_mod
        from scripts.night_manager.config import NMConfig

        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            class FakeProc:
                pid = 12345
                returncode = None
            return FakeProc()

        monkeypatch.setattr(
            agent_mod.asyncio, "create_subprocess_exec", fake_exec
        )

        issue = _make_issue(identifier="ABS-999", status="queued")
        issue.worktree = str(tmp_path)
        issue.log_file = str(tmp_path / "agent.jsonl")
        issue.session_id = "pre-allocated-uuid"
        issue.attempts = 0  # first spawn

        config = NMConfig(agent_model="sonnet", agent_effort="high")

        await agent_mod.spawn_agent(issue, config, "desc")

        cmd = captured["cmd"]
        assert "--session-id" in cmd
        used_session_id = cmd[cmd.index("--session-id") + 1]
        assert used_session_id == "pre-allocated-uuid", (
            "Initial spawn must honor a pre-allocated session_id"
        )


# ---------------------------------------------------------------------------
# ABS-149: spawn_agent enables --remote-control with nm-<identifier> label
# ---------------------------------------------------------------------------


class TestRemoteControlOnInitialSpawn:
    """Regression for ABS-149: every NM-spawned agent surfaces in the Claude
    mobile app's session list, labeled by issue."""

    @pytest.mark.asyncio
    async def test_spawn_agent_enables_remote_control(
        self, tmp_path, monkeypatch
    ):
        from scripts.night_manager import agent as agent_mod
        from scripts.night_manager.config import NMConfig

        captured: dict = {}

        async def fake_exec(*cmd, **kwargs):
            captured["cmd"] = list(cmd)
            class FakeProc:
                pid = 12345
                returncode = None
            return FakeProc()

        monkeypatch.setattr(
            agent_mod.asyncio, "create_subprocess_exec", fake_exec
        )

        issue = _make_issue(identifier="ABS-123", status="queued")
        issue.worktree = str(tmp_path)
        issue.log_file = str(tmp_path / "agent.jsonl")

        config = NMConfig(agent_model="sonnet", agent_effort="high")

        await agent_mod.spawn_agent(issue, config, "desc")

        cmd = captured["cmd"]
        assert "--remote-control" in cmd, (
            "spawn_agent must enable --remote-control on initial spawn"
        )
        rc_idx = cmd.index("--remote-control")
        assert cmd[rc_idx + 1] == "nm-ABS-123", (
            "--remote-control name must be nm-<identifier>"
        )


# ---------------------------------------------------------------------------
# ABS-146: error_detail fallback bounded to current attempt only
# ---------------------------------------------------------------------------


class TestAttemptLogOffsetIsolatesAttempts:
    """Regression for ABS-146: NM's `_is_rate_limited` was matching
    historical 'session limit' text from prior attempts in the same
    append-only per-issue log file. The fix snapshots a byte offset
    BEFORE each spawn/resume; the fallback reads only from that
    offset to EOF."""

    @pytest.mark.asyncio
    async def test_spawn_agent_snapshots_offset_before_writing(
        self, tmp_path, monkeypatch
    ):
        from scripts.night_manager import agent as agent_mod
        from scripts.night_manager.config import NMConfig

        log_path = tmp_path / "agent.jsonl"
        # Simulate an existing log with yesterday's rate-limit text
        prior_content = (
            '{"type":"rate_limit_event",'
            '"rate_limit_info":{"status":"rejected"}}\n'
            '{"type":"result","is_error":true,'
            '"result":"You\'ve hit your session limit · resets 4:10pm"}\n'
        )
        log_path.write_text(prior_content)
        prior_size = log_path.stat().st_size
        assert prior_size > 0

        async def fake_exec(*cmd, **kwargs):
            class FakeProc:
                pid = 12345
                returncode = None
            return FakeProc()

        monkeypatch.setattr(
            agent_mod.asyncio, "create_subprocess_exec", fake_exec
        )

        issue = _make_issue(status="queued")
        issue.worktree = str(tmp_path)
        issue.log_file = str(log_path)

        config = NMConfig(agent_model="sonnet", agent_effort="high")

        await agent_mod.spawn_agent(issue, config, "desc")

        assert issue.attempt_log_offset == prior_size, (
            "spawn_agent must snapshot the existing log size BEFORE writing "
            "anything new, so the error_detail fallback reads only this "
            "attempt's slice."
        )

    def test_attempt_log_offset_round_trips_through_state_json(self, tmp_path):
        """The offset is needed across saves because _run_agent_lifecycle
        runs after monitor_agent; state.save() in between must not
        clobber the value."""
        from scripts.night_manager.state import NMState

        state = NMState(
            run_id="nm-test", started_at="2026-01-01T00:00:00+00:00",
        )
        issue = _make_issue(status="in_progress")
        issue.attempt_log_offset = 12345
        state.issues["ABS-999"] = issue

        path = tmp_path / "state.json"
        state.save(path)

        reloaded = NMState.load(path)
        assert reloaded.issues["ABS-999"].attempt_log_offset == 12345
