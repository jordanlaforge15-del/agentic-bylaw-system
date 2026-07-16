"""Tests for ABS-169: NM regression gate runs pytest when diff touches scripts/night_manager/.

Acceptance criteria:
- run_dev_e2e runs pytest tests/night_manager/ when _diff_touches returns True.
- run_dev_e2e skips pytest when _diff_touches returns False (non-NM-touching merge).
- A failing pytest returns (False, <message containing "NM unit-test regression failed">).
- A passing pytest returns (True, ...) without the failure message.
- _diff_touches correctly identifies files under scripts/night_manager/.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from scripts.night_manager import reviewer
from scripts.night_manager.reviewer import _diff_touches, run_dev_e2e
from scripts.night_manager.state import IssuePorts, IssueState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_issue() -> IssueState:
    return IssueState(
        identifier="ABS-169",
        title="Regression gate test",
        branch="agent/ABS-169-test",
        worktree="/fake/worktree",
        ports=IssuePorts(pg=5499, api=8099, web=3099),
    )


def _fake_process(returncode: int = 0, stdout: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.stdout = None
    return proc


# ---------------------------------------------------------------------------
# _diff_touches unit tests
# ---------------------------------------------------------------------------


class TestDiffTouches:
    async def test_returns_true_when_nm_file_touched(self):
        """Files under scripts/night_manager/ are detected."""
        with patch.object(reviewer, "_git_changed_files", return_value=[
            "scripts/night_manager/reviewer.py",
            "tests/night_manager/test_foo.py",
        ]):
            result = await _diff_touches("/fake/worktree", "scripts/night_manager/")
        assert result is True

    async def test_returns_false_when_no_nm_file_touched(self):
        """Non-NM-only diffs do not trigger the pytest gate."""
        with patch.object(reviewer, "_git_changed_files", return_value=[
            "web/e2e/functional/auth.spec.ts",
            "advisor/layer1/models.py",
        ]):
            result = await _diff_touches("/fake/worktree", "scripts/night_manager/")
        assert result is False

    async def test_returns_false_on_empty_diff(self):
        with patch.object(reviewer, "_git_changed_files", return_value=[]):
            result = await _diff_touches("/fake/worktree", "scripts/night_manager/")
        assert result is False

    async def test_trailing_slash_normalised(self):
        """Path prefix with or without trailing slash behaves identically."""
        with patch.object(reviewer, "_git_changed_files", return_value=[
            "scripts/night_manager/main.py",
        ]):
            with_slash = await _diff_touches("/fake/worktree", "scripts/night_manager/")
            without_slash = await _diff_touches("/fake/worktree", "scripts/night_manager")
        assert with_slash is True
        assert without_slash is True

    async def test_does_not_match_sibling_prefix(self):
        """scripts/night_manager_v2/ must not match scripts/night_manager/."""
        with patch.object(reviewer, "_git_changed_files", return_value=[
            "scripts/night_manager_v2/foo.py",
        ]):
            result = await _diff_touches("/fake/worktree", "scripts/night_manager/")
        assert result is False


# ---------------------------------------------------------------------------
# run_dev_e2e — pytest gate invocation tests
# ---------------------------------------------------------------------------


def _patch_run_dev_e2e_subprocess(
    monkeypatch,
    *,
    e2e_rc: int = 0,
    pytest_rc: int = 0,
    pytest_stdout: bytes = b"1 passed",
):
    """Patch asyncio.create_subprocess_exec inside reviewer to avoid real subprocesses.

    Returns a list that accumulates (cmd, kwargs) calls so tests can assert
    which commands were invoked.
    """
    invocations: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        invocations.append(args)
        cmd = list(args)
        # pytest invocation
        if ".venv/bin/pytest" in cmd:
            return _fake_process(returncode=pytest_rc, stdout=pytest_stdout)
        # e2e / make invocation
        if "make" in cmd and "e2e" in cmd:
            return _fake_process(returncode=e2e_rc, stdout=b"e2e output")
        # everything else (git checkout, pull, pip, e2e-down) — succeed silently
        return _fake_process(returncode=0, stdout=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return invocations


class TestRunDevE2EPytestGate:
    async def test_pytest_invoked_when_nm_touched(self, monkeypatch):
        """When diff touches scripts/night_manager/, pytest must be called."""
        monkeypatch.setattr(reviewer, "_diff_touches", AsyncMock(return_value=True))
        invocations = _patch_run_dev_e2e_subprocess(monkeypatch)

        issue = _mk_issue()
        passed, _ = await run_dev_e2e(issue)

        assert passed is True
        pytest_calls = [inv for inv in invocations if ".venv/bin/pytest" in inv]
        assert len(pytest_calls) == 1, f"Expected 1 pytest call, got {len(pytest_calls)}: {pytest_calls}"
        assert "tests/night_manager/" in pytest_calls[0]
        assert "-x" in pytest_calls[0]
        assert "--tb=short" in pytest_calls[0]

    async def test_pytest_not_invoked_when_nm_not_touched(self, monkeypatch):
        """When diff does not touch scripts/night_manager/, pytest must be skipped."""
        monkeypatch.setattr(reviewer, "_diff_touches", AsyncMock(return_value=False))
        invocations = _patch_run_dev_e2e_subprocess(monkeypatch)

        issue = _mk_issue()
        passed, _ = await run_dev_e2e(issue)

        assert passed is True
        pytest_calls = [inv for inv in invocations if ".venv/bin/pytest" in inv]
        assert len(pytest_calls) == 0, f"Expected no pytest call, got: {pytest_calls}"

    async def test_failing_pytest_returns_false_with_revert_message(self, monkeypatch):
        """Failing pytest produces (False, message containing revert notice)."""
        monkeypatch.setattr(reviewer, "_diff_touches", AsyncMock(return_value=True))
        _patch_run_dev_e2e_subprocess(
            monkeypatch,
            pytest_rc=1,
            pytest_stdout=b"FAILED tests/night_manager/test_foo.py::test_bar - AssertionError",
        )

        issue = _mk_issue()
        passed, message = await run_dev_e2e(issue)

        assert passed is False
        assert "NM unit-test regression failed" in message

    async def test_passing_pytest_returns_true(self, monkeypatch):
        """Passing pytest (exit 0) produces (True, ...)."""
        monkeypatch.setattr(reviewer, "_diff_touches", AsyncMock(return_value=True))
        _patch_run_dev_e2e_subprocess(monkeypatch, pytest_rc=0)

        issue = _mk_issue()
        passed, _ = await run_dev_e2e(issue)

        assert passed is True

    async def test_e2e_failure_skips_pytest(self, monkeypatch):
        """When make e2e fails, pytest must NOT be invoked (short-circuit on e2e failure)."""
        monkeypatch.setattr(reviewer, "_diff_touches", AsyncMock(return_value=True))
        invocations = _patch_run_dev_e2e_subprocess(monkeypatch, e2e_rc=1)

        issue = _mk_issue()
        passed, _ = await run_dev_e2e(issue)

        assert passed is False
        pytest_calls = [inv for inv in invocations if ".venv/bin/pytest" in inv]
        assert len(pytest_calls) == 0, "pytest must not run when e2e already failed"

    async def test_log_line_emitted_on_nm_pass(self, monkeypatch, caplog):
        """Log contains 'NM unit-test regression for ABS-169: PASSED' on success."""
        import logging
        monkeypatch.setattr(reviewer, "_diff_touches", AsyncMock(return_value=True))
        _patch_run_dev_e2e_subprocess(monkeypatch, pytest_rc=0)

        issue = _mk_issue()
        with caplog.at_level(logging.INFO, logger="night_manager.reviewer"):
            await run_dev_e2e(issue)

        messages = " ".join(caplog.messages)
        assert "NM unit-test regression for ABS-169" in messages
        assert "PASSED" in messages

    async def test_log_line_emitted_on_nm_fail(self, monkeypatch, caplog):
        """Log contains 'NM unit-test regression for ABS-169: FAILED' on failure."""
        import logging
        monkeypatch.setattr(reviewer, "_diff_touches", AsyncMock(return_value=True))
        _patch_run_dev_e2e_subprocess(monkeypatch, pytest_rc=1)

        issue = _mk_issue()
        with caplog.at_level(logging.INFO, logger="night_manager.reviewer"):
            await run_dev_e2e(issue)

        messages = " ".join(caplog.messages)
        assert "NM unit-test regression for ABS-169" in messages
        assert "FAILED" in messages
