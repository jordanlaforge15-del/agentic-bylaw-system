"""Tests for the ABS-206 empty-diff reviewer guard.

When a night-manager agent exits 0 without committing anything (auth
failure, sandbox crash, OOM, ...), the worktree is left on the dev
tip. Before ABS-206 the pipeline silently advanced through code review
(empty diff auto-passed), e2e (testing dev as-is, green by definition),
and merge (no-op) — flipping the Linear ticket to Done. See ABS-203
for the witnessed false-Done.

These tests lock in three things:

1. `_scan_log_for_auth_error` matches the three markers the issue calls
   out (`401`, `Invalid authentication credentials`, `authentication
   failed`) and respects `start_offset` so a marker left in the log
   file by an earlier attempt cannot leak forward.
2. `code_review`'s historic empty-diff auto-pass is now fail-closed —
   defensive coverage for any future direct caller.
3. `review_and_gate` blocks an empty-branch run BEFORE invoking the LLM
   reviewer or e2e, and surfaces the auth root cause in the reason when
   the agent log shows one. This is the gate that protects against the
   ABS-203 silent-pass shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.night_manager.reviewer import (
    _scan_log_for_auth_error,
    code_review,
    review_and_gate,
)
from scripts.night_manager.config import NMConfig
from scripts.night_manager.state import IssuePorts, IssueState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _linear_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`NMConfig.__post_init__` raises without LINEAR_API_KEY. Stub it so
    these tests are independent of whether a `.env` exists at REPO_ROOT.
    """
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _init_repo_on_dev(path: Path) -> None:
    """Initialise a tiny repo on `dev` with one base commit."""
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "-q", "-b", "dev")
    _run(path, "git", "config", "user.email", "nm-test@example.com")
    _run(path, "git", "config", "user.name", "NM Test")
    (path / "README.md").write_text("base\n")
    _run(path, "git", "add", "README.md")
    _run(path, "git", "commit", "-q", "-m", "base")


def _make_issue(
    *,
    identifier: str = "ABS-206-test",
    worktree: str = "",
    log_file: str = "",
    attempt_log_offset: int = 0,
) -> IssueState:
    return IssueState(
        identifier=identifier,
        title=f"{identifier} title",
        branch=f"agent/{identifier.lower()}",
        worktree=worktree,
        ports=IssuePorts(pg=5499, api=8099, web=3099),
        log_file=log_file,
        attempt_log_offset=attempt_log_offset,
    )


# ---------------------------------------------------------------------------
# Pure helper: _scan_log_for_auth_error
# ---------------------------------------------------------------------------


class TestScanLogForAuthError:
    """Each marker fires, and the offset is honoured."""

    def test_401_marker_returns_excerpt(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            "starting agent\n"
            "HTTP/1.1 401 Unauthorized\n"
            "body: {\"error\":\"oops\"}\n",
        )
        excerpt = _scan_log_for_auth_error(str(log_file), 0)
        assert excerpt is not None
        assert "401" in excerpt

    def test_invalid_authentication_credentials_marker(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            "claude exited: Invalid authentication credentials\n",
        )
        excerpt = _scan_log_for_auth_error(str(log_file), 0)
        assert excerpt is not None
        assert "Invalid authentication credentials" in excerpt

    def test_authentication_failed_marker_case_insensitive(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        log_file.write_text("ERROR: Authentication FAILED at token refresh\n")
        excerpt = _scan_log_for_auth_error(str(log_file), 0)
        assert excerpt is not None
        assert "Authentication FAILED" in excerpt

    def test_no_marker_returns_none(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        log_file.write_text("agent ran fine, exited 0\n")
        assert _scan_log_for_auth_error(str(log_file), 0) is None

    def test_offset_skips_earlier_marker(self, tmp_path: Path):
        """A marker before `start_offset` (i.e. from a prior attempt on
        the same append-only log file) must NOT trigger a hit on a later
        attempt's slice — mirrors the ABS-146 fix for rate-limit."""
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            "prior attempt: HTTP 401 Unauthorized\n"
            "===== NEW ATTEMPT =====\n"
            "agent ran fine this time\n",
        )
        # Offset starts AFTER the prior 401 line.
        offset = log_file.read_text().index("===== NEW ATTEMPT")
        assert _scan_log_for_auth_error(str(log_file), offset) is None

    def test_offset_finds_current_attempt_marker(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            "prior attempt: ok\n"
            "===== NEW ATTEMPT =====\n"
            "HTTP/1.1 401 Unauthorized\n",
        )
        offset = log_file.read_text().index("===== NEW ATTEMPT")
        excerpt = _scan_log_for_auth_error(str(log_file), offset)
        assert excerpt is not None
        assert "401" in excerpt

    def test_missing_log_file_returns_none(self):
        """Defensive: the agent crashed before opening its log."""
        assert _scan_log_for_auth_error("", 0) is None
        assert _scan_log_for_auth_error("/no/such/path.log", 0) is None

    def test_line_number_401_in_read_tool_output_does_not_match(self, tmp_path: Path):
        """ABS-210 false positive: docs/TERMS_AND_CONDITIONS.md is >400 lines,
        so a `Read` tool result includes a line-numbered prefix
        `401\\t<content>`. The previous substring-match marker treated that
        as an HTTP 401 and reported a bogus auth failure. The tightened
        patterns require an HTTP / status / "Unauthorized" / "invalid"
        context — bare `401` alone must NOT match.
        """
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            '{"type":"user","message":{"content":[{"type":"tool_result",'
            '"content":"399\\tprovision is...\\n400\\t3. **No waiver.**...\\n'
            '401\\t   right under these Terms operates as a waiver of that right.\\n'
            '402\\t4. **Assignment.**...\\n"}]}}\n',
        )
        assert _scan_log_for_auth_error(str(log_file), 0) is None

    def test_bare_401_in_other_contexts_does_not_match(self, tmp_path: Path):
        """Page numbers, counts, dates — anything containing the digits 401
        without an HTTP / status / Unauthorized / invalid neighbour must
        not trip the auth guard.
        """
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            "processed 401 rows\n"
            "error code 4015 thrown\n"
            "version 1401.2 of foo released in 2401\n",
        )
        assert _scan_log_for_auth_error(str(log_file), 0) is None

    def test_json_status_401_matches(self, tmp_path: Path):
        """The Anthropic SDK surfaces auth failures as JSON with
        `"status": 401` — keep that path matching after the tightening."""
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            'API error: {"type":"error","status":401,'
            '"message":"invalid x-api-key"}\n',
        )
        excerpt = _scan_log_for_auth_error(str(log_file), 0)
        assert excerpt is not None
        assert "401" in excerpt

    def test_401_unauthorized_matches(self, tmp_path: Path):
        """`401 Unauthorized` (no `HTTP/` prefix — e.g. a raw error line)
        is the canonical auth-failure phrase. Keep it matching."""
        log_file = tmp_path / "agent.log"
        log_file.write_text("response: 401 Unauthorized\n")
        excerpt = _scan_log_for_auth_error(str(log_file), 0)
        assert excerpt is not None
        assert "401" in excerpt


# ---------------------------------------------------------------------------
# code_review: defensive fail-closed on empty diff
# ---------------------------------------------------------------------------


class TestCodeReviewEmptyDiffFailsClosed:
    """If a direct caller of `code_review` reaches the empty-diff branch
    (e.g. `review_and_gate`'s guard was bypassed), we must NOT auto-pass.
    Before ABS-206 this branch returned (True, "No changes to review.").
    """

    @pytest.mark.asyncio
    async def test_empty_diff_returns_false(self, tmp_path: Path):
        repo = tmp_path / "empty-branch"
        _init_repo_on_dev(repo)
        _run(repo, "git", "checkout", "-q", "-b", "agent/ABS-206-test")
        # No commits added — branch is identical to dev.

        issue = _make_issue(worktree=str(repo))
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        # The empty diff is caught either by the new `review_and_gate`
        # guard or by the in-function empty-diff branch. Confirm
        # `code_review` no longer returns True for an empty diff.
        passed, feedback = await code_review(issue, config)
        assert passed is False
        assert "no commits" in feedback.lower() or "empty diff" in feedback.lower()


# ---------------------------------------------------------------------------
# review_and_gate: end-to-end empty-diff guard
# ---------------------------------------------------------------------------


class TestReviewAndGateEmptyDiffGuard:
    """Drive `review_and_gate` against a real empty-branch git fixture.

    The agent has exited successfully (status=reviewing) but committed
    nothing. The guard must:

    1. Mark the issue Blocked (not Failed; not silently pass).
    2. Set `issue.error` to a reason an operator can read in Linear.
    3. Return False so the caller posts the Gate FAILED comment.
    4. Never invoke `claude -p` (the LLM reviewer is expensive — the
       gate runs before it).
    5. Never invoke `make e2e` (e2e on an unmodified dev would pass,
       which is the failure mode that masked the bug).
    """

    @pytest.fixture
    def empty_branch_worktree(self, tmp_path: Path) -> Path:
        repo = tmp_path / "empty-branch-worktree"
        _init_repo_on_dev(repo)
        _run(repo, "git", "checkout", "-q", "-b", "agent/ABS-206-test")
        return repo

    @pytest.mark.asyncio
    async def test_empty_branch_marks_blocked(
        self,
        empty_branch_worktree: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from scripts.night_manager import reviewer as rev_mod

        commands_invoked: list[list[str]] = []
        real_exec = rev_mod.asyncio.create_subprocess_exec

        async def recording_exec(*cmd, **kwargs):
            commands_invoked.append(list(cmd))
            return await real_exec(*cmd, **kwargs)

        monkeypatch.setattr(
            rev_mod.asyncio, "create_subprocess_exec", recording_exec,
        )

        issue = _make_issue(worktree=str(empty_branch_worktree))
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        gate_passed = await review_and_gate(issue, config)

        assert gate_passed is False
        assert issue.status == "blocked"
        assert issue.error is not None
        assert "no commits" in issue.error.lower()
        # No LLM reviewer call (gate must fire before paying for claude).
        claude_calls = [c for c in commands_invoked if c and c[0] == "claude"]
        assert claude_calls == [], (
            "review_and_gate must short-circuit before invoking the LLM "
            "reviewer when the empty-diff guard trips"
        )
        # No e2e invocation either — an unmodified dev would pass e2e,
        # which is what masked the bug pre-fix.
        make_e2e_calls = [
            c for c in commands_invoked
            if len(c) >= 2 and c[0] == "make" and c[1] == "e2e"
        ]
        assert make_e2e_calls == []

    @pytest.mark.asyncio
    async def test_empty_branch_with_auth_log_names_auth(
        self,
        empty_branch_worktree: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """When the agent log shows an auth-error marker, the Blocked
        reason names auth as the root cause and includes the excerpt
        so the operator sees the failure mode in the Linear comment.
        """
        from scripts.night_manager import reviewer as rev_mod

        log_file = tmp_path / "agent-ABS-206.log"
        log_file.write_text(
            "[agent] starting work\n"
            "[claude] error: 401 Invalid authentication credentials\n"
            "[claude] exit code 1\n",
        )

        real_exec = rev_mod.asyncio.create_subprocess_exec

        async def passthrough_exec(*cmd, **kwargs):
            return await real_exec(*cmd, **kwargs)

        monkeypatch.setattr(
            rev_mod.asyncio, "create_subprocess_exec", passthrough_exec,
        )

        issue = _make_issue(
            worktree=str(empty_branch_worktree),
            log_file=str(log_file),
            attempt_log_offset=0,
        )
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        gate_passed = await review_and_gate(issue, config)

        assert gate_passed is False
        assert issue.status == "blocked"
        assert issue.error is not None
        # Reason names the auth failure root cause...
        assert "authentication" in issue.error.lower()
        # ...and includes the log excerpt with the marker.
        assert "401" in issue.error
        assert "Invalid authentication credentials" in issue.error

    @pytest.mark.asyncio
    async def test_non_empty_diff_falls_through_to_review(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A branch with a real commit must NOT trip the guard — the
        existing review/e2e path runs as normal. We stub `code_review`
        and `run_e2e` to assert the guard yielded control rather than
        end-to-end exercising the full pipeline."""
        from scripts.night_manager import reviewer as rev_mod

        repo = tmp_path / "real-branch-worktree"
        _init_repo_on_dev(repo)
        _run(repo, "git", "checkout", "-q", "-b", "agent/ABS-206-test")
        (repo / "added.py").write_text("# real work\n")
        _run(repo, "git", "add", "added.py")
        _run(repo, "git", "commit", "-q", "-m", "[ABS-206] real change")

        code_review_calls: list[str] = []
        e2e_calls: list[str] = []

        async def fake_code_review(issue, config):
            code_review_calls.append(issue.identifier)
            return True, "ok"

        async def fake_run_e2e(issue):
            e2e_calls.append(issue.identifier)
            return True, "ok"

        monkeypatch.setattr(rev_mod, "code_review", fake_code_review)
        monkeypatch.setattr(rev_mod, "run_e2e", fake_run_e2e)

        issue = _make_issue(worktree=str(repo))
        config = NMConfig(reviewer_model="sonnet", reviewer_token_limit=1.0)

        gate_passed = await review_and_gate(issue, config)

        assert gate_passed is True
        assert issue.status != "blocked"
        assert code_review_calls == [issue.identifier]
        assert e2e_calls == [issue.identifier]


# ---------------------------------------------------------------------------
# ABS-216 regression: source-code 401 boilerplate must not trip the auth guard
# ---------------------------------------------------------------------------


class TestAuthScannerABS216Regression:
    """A Next.js route handler that returns `{ status: 401 }` is the most
    common shape of "401 mentioned in source code without being an auth
    failure". Before ABS-216 the regex `(?:"?status"?)\\s*[:=]\\s*401\\b`
    made the surrounding quotes optional, so the bare TS/JS object literal
    matched as if it were an Anthropic JSON error envelope. The agent had
    actually crashed on a thinking-block round-trip (ABS-215, ABS-217),
    but the operator got pointed at credentials.

    These tests lock in:

    1. The `{ status: 401 }` TS/JS shape (unquoted key) does NOT match.
    2. The `"status": 401` JSON shape (quoted key) STILL matches — this
       is the shape Anthropic actually returns and the original
       `test_json_status_401_matches` already covers it; the test below
       cross-checks the post-tightening regex against the exact bytes
       that lit up the ABS-215 false positive.
    """

    def test_nextjs_route_handler_401_does_not_match(self, tmp_path: Path):
        """The ABS-215 agent log contained this snippet (from the route
        handler the agent was drafting):

            return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

        The fix requires `status` to be quoted (JSON), so the unquoted
        TS object-literal does not match. The accompanying `error:
        "Unauthorized"` substring also must NOT match — `Unauthorized`
        sits before `401`, not after, so the `\\b401\\s+(?:unauthorized
        |invalid)\\b` pattern requires the opposite order.
        """
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            "[agent] writing web/app/api/general-feedback/route.ts\n"
            'return NextResponse.json({ error: "Unauthorized" }, '
            "{ status: 401 });\n"
            "[agent] done editing\n",
        )
        assert _scan_log_for_auth_error(str(log_file), 0) is None

    def test_bare_status_401_in_python_dict_does_not_match(
        self, tmp_path: Path,
    ):
        """Same shape in a Python literal (e.g. a FastAPI route the agent
        is authoring). The unquoted key form must not match here either.
        """
        log_file = tmp_path / "agent.log"
        log_file.write_text(
            "return JSONResponse({'detail': 'nope'}, status=401)\n"
            "raise HTTPException(status=401)\n",
        )
        assert _scan_log_for_auth_error(str(log_file), 0) is None

    def test_quoted_status_401_still_matches(self, tmp_path: Path):
        """Real Anthropic API errors keep their JSON shape — the
        previous `test_json_status_401_matches` covers the canonical
        envelope; this guard checks that variants with extra whitespace,
        `=` (legacy ad-hoc loggers), and case differences all still
        match after the tightening.
        """
        for body in (
            '{"type":"error","status":401,"message":"invalid x-api-key"}',
            '{"status" : 401 , "error": "Unauthorized"}',
            '"status"=401  // raw error log line',
            '"STATUS": 401  // case insensitivity preserved',
        ):
            log_file = tmp_path / "agent.log"
            log_file.write_text(body + "\n")
            excerpt = _scan_log_for_auth_error(str(log_file), 0)
            assert excerpt is not None, f"Should match: {body!r}"
            assert "401" in excerpt
