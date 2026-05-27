"""Tests for the ABS-160 diff-vs-ticket-areas reviewer gate.

The gate fires inside `reviewer.code_review` before any LLM call: if
the planner's `touch_profile["areas"]` for the ticket is non-empty and
the worktree's diff is *entirely* under `web/e2e/` or `tests/` while
being *fully disjoint* from those declared areas, the reviewer
short-circuits with a clear "wrong ticket" error. This matches the
ABS-80 / ABS-84 failure mode from run nm-20260526-153728 where the
agent committed an unrelated test fix under the ticket prefix.

These tests exercise the pure helper (`check_diff_touches_ticket_areas`)
plus the end-to-end `code_review` path via a real git fixture, so the
behaviour is locked in against both unit-level regressions and the
caller-ordering bug "I added the helper but forgot to call it".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.night_manager.reviewer import (
    check_diff_touches_ticket_areas,
    code_review,
)
from scripts.night_manager.config import NMConfig
from scripts.night_manager.state import IssuePorts, IssueState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> None:
    """Repo on `dev` with one base commit and a feature branch checked out."""
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "-q", "-b", "dev")
    _run(path, "git", "config", "user.email", "nm-test@example.com")
    _run(path, "git", "config", "user.name", "NM Test")
    (path / "README.md").write_text("base\n")
    _run(path, "git", "add", "README.md")
    _run(path, "git", "commit", "-q", "-m", "base")


def _make_issue(
    *,
    identifier: str = "ABS-160-test",
    areas: list[str] | None,
    worktree: str = "",
) -> IssueState:
    """Minimal IssueState carrying just what the gate consumes."""
    touch_profile = None
    if areas is not None:
        touch_profile = {"areas": list(areas), "kind": "backend", "risk_tags": []}
    return IssueState(
        identifier=identifier,
        title=f"{identifier} title",
        branch=f"agent/{identifier.lower()}",
        worktree=worktree,
        ports=IssuePorts(pg=5499, api=8099, web=3099),
        touch_profile=touch_profile,
    )


# ---------------------------------------------------------------------------
# Pure helper: check_diff_touches_ticket_areas
# ---------------------------------------------------------------------------


class TestAreasDisjointFails:
    """Areas={pyproject.toml} but diff is only web/e2e/* → fail."""

    def test_pyproject_area_test_only_diff_fails(self):
        """The ABS-80 scenario: pyproject.toml ticket, diff is one spec.

        Without this gate the agent's commit would have read as a
        clean fix. With the gate it surfaces a clear blocker message.
        """
        issue = _make_issue(areas=["pyproject.toml"])
        ok, msg = check_diff_touches_ticket_areas(
            issue, ["web/e2e/functional/nm-access-log.spec.ts"],
        )

        assert ok is False
        assert "pyproject.toml" in msg
        assert "web/e2e/functional/nm-access-log.spec.ts" in msg
        # Operator-facing language must explain *why* it failed
        assert "test files" in msg.lower()
        assert "unchanged" in msg.lower()

    def test_dockerfile_area_test_only_diff_fails(self):
        """The ABS-84 scenario: Dockerfile.advisor ticket, spec-only diff."""
        issue = _make_issue(areas=["Dockerfile.advisor", ".dockerignore"])
        ok, msg = check_diff_touches_ticket_areas(
            issue, ["web/e2e/functional/night-manager.spec.ts"],
        )
        assert ok is False
        assert "Dockerfile.advisor" in msg


class TestAreasEmptySkips:
    """Empty `areas` (or missing touch_profile) must SKIP the gate.

    The planner doesn't always emit an areas list (older runs, fast
    paths). Firing on empty areas would false-positive every
    legitimate test-only change and break the resume flow.
    """

    def test_empty_areas_list_skips(self):
        issue = _make_issue(areas=[])
        ok, msg = check_diff_touches_ticket_areas(
            issue, ["web/e2e/functional/foo.spec.ts"],
        )
        assert ok is True
        assert msg == ""

    def test_missing_touch_profile_skips(self):
        issue = _make_issue(areas=None)
        assert issue.touch_profile is None
        ok, msg = check_diff_touches_ticket_areas(
            issue, ["web/e2e/functional/foo.spec.ts"],
        )
        assert ok is True
        assert msg == ""

    def test_whitespace_only_areas_skip(self):
        """Defensive: the planner could emit empty / whitespace strings."""
        issue = _make_issue(areas=["", "   "])
        ok, _ = check_diff_touches_ticket_areas(
            issue, ["web/e2e/functional/foo.spec.ts"],
        )
        assert ok is True


class TestAreasOverlapPasses:
    """Areas overlap with diff → gate passes (no false positive)."""

    def test_dockerfile_area_with_dockerfile_diff_passes(self):
        """Direct match on the area path."""
        issue = _make_issue(areas=["Dockerfile.advisor"])
        ok, msg = check_diff_touches_ticket_areas(
            issue, ["Dockerfile.advisor"],
        )
        assert ok is True
        assert msg == ""

    def test_directory_area_matches_file_within(self):
        """`scripts/night_manager` area matches files under it."""
        issue = _make_issue(areas=["scripts/night_manager"])
        ok, _ = check_diff_touches_ticket_areas(
            issue, ["scripts/night_manager/reviewer.py"],
        )
        assert ok is True

    def test_test_files_under_declared_area_pass(self):
        """If the area is `tests/advisor`, a diff under `tests/advisor/`
        is on-topic — the gate must not punish a test-only diff that
        actually lives inside one of the ticket's declared areas."""
        issue = _make_issue(areas=["tests/advisor"])
        ok, _ = check_diff_touches_ticket_areas(
            issue, ["tests/advisor/test_new_thing.py"],
        )
        assert ok is True


class TestMixedDiffFallsThrough:
    """If even one touched file is production code, defer to the LLM
    reviewer. The areas gate is narrow on purpose — it only fires on
    the test-only-disjoint shape."""

    def test_mixed_diff_passes_areas_gate(self):
        issue = _make_issue(areas=["Dockerfile.advisor"])
        ok, _ = check_diff_touches_ticket_areas(
            issue,
            ["web/e2e/functional/foo.spec.ts", "pyproject.toml"],
        )
        # pyproject.toml isn't a test path, so condition #3 fails → pass through.
        assert ok is True


class TestEmptyDiffSkips:
    """No changed files → gate is a no-op (empty-diff handled upstream)."""

    def test_empty_change_list_skips(self):
        issue = _make_issue(areas=["Dockerfile.advisor"])
        ok, msg = check_diff_touches_ticket_areas(issue, [])
        assert ok is True
        assert msg == ""


# ---------------------------------------------------------------------------
# End-to-end: code_review wires the gate in correctly (calls before LLM)
# ---------------------------------------------------------------------------


class TestCodeReviewWiresAreasGate:
    """Drive the full `code_review` async path against a real git
    fixture. Asserts that when the gate trips, `code_review` returns
    `passed=False` WITHOUT spawning the LLM reviewer subprocess.

    The previous test in this file exercises the helper directly; this
    test exercises caller-ordering — i.e. "the helper is plumbed in
    at the right point in `code_review`".
    """

    @pytest.fixture
    def stale_worktree(self, tmp_path: Path) -> Path:
        """Repo with a test-only commit ahead of `dev`. Mirrors the
        ABS-80 / ABS-84 shape: one or more spec edits, zero changes
        to the ticket's actual deliverable."""
        path = tmp_path / "stale-worktree"
        _init_repo(path)
        # Branch off dev, add a spec-only commit
        _run(path, "git", "checkout", "-q", "-b", "agent/ABS-80-test")
        spec = path / "web" / "e2e" / "functional"
        spec.mkdir(parents=True, exist_ok=True)
        (spec / "nm-access-log.spec.ts").write_text(
            "// off-topic correlation-id fix\n",
        )
        _run(path, "git", "add", "web/e2e/functional/nm-access-log.spec.ts")
        _run(path, "git", "commit", "-q", "-m", "[ABS-80] fix flaky access-log test")
        return path

    @pytest.mark.asyncio
    async def test_review_fails_fast_on_areas_disjoint(
        self, stale_worktree: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """code_review must short-circuit BEFORE invoking the LLM
        when the gate fires. We assert that by patching
        `asyncio.create_subprocess_exec` to record every call and
        confirming no `claude -p` invocation is made for the LLM
        review (git diff calls are fine and expected)."""
        from scripts.night_manager import reviewer as rev_mod

        commands_invoked: list[list[str]] = []
        real_exec = rev_mod.asyncio.create_subprocess_exec

        async def recording_exec(*cmd, **kwargs):
            commands_invoked.append(list(cmd))
            return await real_exec(*cmd, **kwargs)

        monkeypatch.setattr(
            rev_mod.asyncio, "create_subprocess_exec", recording_exec,
        )

        issue = _make_issue(
            identifier="ABS-80",
            areas=["pyproject.toml"],
            worktree=str(stale_worktree),
        )
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        passed, feedback = await code_review(issue, config)

        assert passed is False
        assert "pyproject.toml" in feedback
        assert "test files" in feedback.lower()
        # The LLM reviewer is invoked via `claude -p`. Confirm no such
        # subprocess was spawned — the gate must have fired first.
        claude_calls = [c for c in commands_invoked if c and c[0] == "claude"]
        assert claude_calls == [], (
            "code_review must short-circuit before the LLM when the "
            "diff-vs-areas gate trips"
        )

    @pytest.mark.asyncio
    async def test_review_continues_when_areas_overlap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """When the diff touches an area path, code_review proceeds
        to the LLM reviewer (we stub it out to avoid network)."""
        from scripts.night_manager import reviewer as rev_mod

        repo = tmp_path / "good-worktree"
        _init_repo(repo)
        _run(repo, "git", "checkout", "-q", "-b", "agent/ABS-84-test")
        (repo / "Dockerfile.advisor").write_text("FROM python:3.12\nCOPY scripts ./scripts\n")
        _run(repo, "git", "add", "Dockerfile.advisor")
        _run(repo, "git", "commit", "-q", "-m", "[ABS-84] add scripts copy")

        import json as _json

        real_exec = rev_mod.asyncio.create_subprocess_exec

        async def faking_exec(*cmd, **kwargs):
            # Let git calls pass through; intercept the LLM call.
            if cmd and cmd[0] == "claude":
                # Emulate stream-json output: one JSONL line with type=result.
                result_line = _json.dumps({
                    "type": "result",
                    "subtype": "success",
                    "result": '{"passed": true, "findings": [], "summary": "Looks good"}',
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

        monkeypatch.setattr(
            rev_mod.asyncio, "create_subprocess_exec", faking_exec,
        )

        issue = _make_issue(
            identifier="ABS-84",
            areas=["Dockerfile.advisor"],
            worktree=str(repo),
        )
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        passed, summary = await code_review(issue, config)
        assert passed is True
        # Surface the fake LLM's summary string — confirms the LLM path ran.
        assert "Looks good" in summary
