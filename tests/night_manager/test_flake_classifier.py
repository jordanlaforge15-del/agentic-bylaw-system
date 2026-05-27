"""Tests for ABS-164: e2e flake classification in reviewer.py.

Covers:
- _extract_failing_tests_structured parses results.json correctly.
- _classify_e2e_flake returns is_flake=True when solo re-run passes.
- _classify_e2e_flake returns is_flake=False when solo re-run still fails.
- run_e2e skips continuation when flake is detected (no continuation spawned).
- run_e2e fires continuation when real failure confirmed.
- Flake history accumulates and surfaces flake_candidate=true after ≥3 flakes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from scripts.night_manager.reviewer import (
    _extract_failing_tests_structured,
    _classify_e2e_flake,
    _FailingTest,
    _flake_history,
    _FLAKE_CANDIDATE_THRESHOLD,
    run_e2e,
)
from scripts.night_manager.state import IssuePorts, IssueState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issue(
    *,
    identifier: str = "ABS-164-test",
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


def _write_results_json(worktree: Path, failing_specs: list[dict]) -> None:
    """Write a minimal Playwright results.json with the given failing specs."""
    results_dir = worktree / "web" / "test-results"
    results_dir.mkdir(parents=True, exist_ok=True)

    suites = []
    for spec in failing_specs:
        suites.append({
            "file": spec["file"],
            "title": spec["file"],
            "suites": [],
            "specs": [
                {
                    "title": spec["title"],
                    "tests": [
                        {
                            "results": [
                                {"status": "failed", "duration": 100}
                            ]
                        }
                    ],
                }
            ],
        })

    (results_dir / "results.json").write_text(
        json.dumps({"suites": suites}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# _extract_failing_tests_structured
# ---------------------------------------------------------------------------


class TestExtractFailingTestsStructured:
    def test_extracts_single_failing_test(self, tmp_path: Path):
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "loads the page"},
        ])
        result = _extract_failing_tests_structured(str(tmp_path))
        assert len(result) == 1
        assert result[0].spec_file == "e2e/functional/foo.spec.ts"
        assert result[0].test_title == "loads the page"

    def test_extracts_multiple_failing_tests(self, tmp_path: Path):
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "test A"},
            {"file": "e2e/smoke/bar.spec.ts", "title": "test B"},
        ])
        result = _extract_failing_tests_structured(str(tmp_path))
        assert len(result) == 2
        spec_files = {t.spec_file for t in result}
        assert spec_files == {"e2e/functional/foo.spec.ts", "e2e/smoke/bar.spec.ts"}

    def test_returns_empty_when_no_results_file(self, tmp_path: Path):
        result = _extract_failing_tests_structured(str(tmp_path))
        assert result == []

    def test_returns_empty_when_results_file_malformed(self, tmp_path: Path):
        results_dir = tmp_path / "web" / "test-results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "results.json").write_text("not json", encoding="utf-8")
        result = _extract_failing_tests_structured(str(tmp_path))
        assert result == []

    def test_ignores_passing_tests(self, tmp_path: Path):
        results_dir = tmp_path / "web" / "test-results"
        results_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "suites": [
                {
                    "file": "e2e/functional/foo.spec.ts",
                    "title": "e2e/functional/foo.spec.ts",
                    "suites": [],
                    "specs": [
                        {
                            "title": "passing test",
                            "tests": [{"results": [{"status": "passed", "duration": 50}]}],
                        },
                        {
                            "title": "failing test",
                            "tests": [{"results": [{"status": "failed", "duration": 100}]}],
                        },
                    ],
                }
            ]
        }
        (results_dir / "results.json").write_text(json.dumps(data), encoding="utf-8")
        result = _extract_failing_tests_structured(str(tmp_path))
        assert len(result) == 1
        assert result[0].test_title == "failing test"

    def test_treats_timedout_as_failing(self, tmp_path: Path):
        results_dir = tmp_path / "web" / "test-results"
        results_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "suites": [
                {
                    "file": "e2e/functional/timeout.spec.ts",
                    "title": "e2e/functional/timeout.spec.ts",
                    "suites": [],
                    "specs": [
                        {
                            "title": "timed out test",
                            "tests": [{"results": [{"status": "timedOut", "duration": 30000}]}],
                        }
                    ],
                }
            ]
        }
        (results_dir / "results.json").write_text(json.dumps(data), encoding="utf-8")
        result = _extract_failing_tests_structured(str(tmp_path))
        assert len(result) == 1
        assert result[0].test_title == "timed out test"

    def test_key_format(self, tmp_path: Path):
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "my test"},
        ])
        result = _extract_failing_tests_structured(str(tmp_path))
        assert result[0].key == "e2e/functional/foo.spec.ts::my test"


# ---------------------------------------------------------------------------
# _classify_e2e_flake: flake path (solo re-run passes)
# ---------------------------------------------------------------------------


class TestClassifyE2EFlakeRecovered:
    """When all failing tests pass on solo re-run, classify as flake."""

    @pytest.mark.asyncio
    async def test_all_pass_solo_returns_is_flake_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "test that flakes"},
        ])

        from scripts.night_manager import reviewer as rev_mod

        async def _mock_exec(*cmd, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return b"1 passed", b""
            return FakeProc()

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        is_flake, n_recovered = await _classify_e2e_flake(
            str(tmp_path), "ABS-164", {}
        )
        assert is_flake is True
        assert n_recovered == 1

    @pytest.mark.asyncio
    async def test_multiple_all_pass_returns_is_flake_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "test A"},
            {"file": "e2e/functional/foo.spec.ts", "title": "test B"},
        ])

        from scripts.night_manager import reviewer as rev_mod

        async def _mock_exec(*cmd, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return b"1 passed", b""
            return FakeProc()

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        is_flake, n_recovered = await _classify_e2e_flake(
            str(tmp_path), "ABS-164", {}
        )
        assert is_flake is True
        assert n_recovered == 2

    @pytest.mark.asyncio
    async def test_flake_log_line_emitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ):
        """The log line matching the acceptance criteria is emitted on flake recovery."""
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "flaky test"},
        ])

        from scripts.night_manager import reviewer as rev_mod

        async def _mock_exec(*cmd, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return b"1 passed", b""
            return FakeProc()

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        with caplog.at_level(logging.INFO, logger="night_manager.reviewer"):
            is_flake, n = await _classify_e2e_flake(str(tmp_path), "ABS-999", {})

        assert is_flake is True
        assert any(
            "classify_flake" in r.message and "1 failing test" in r.message
            for r in caplog.records
        ), f"Expected classify_flake log, got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# _classify_e2e_flake: real failure path (solo re-run still fails)
# ---------------------------------------------------------------------------


class TestClassifyE2EFlakeRealFailure:
    """When a test still fails on solo re-run, classify as real failure."""

    @pytest.mark.asyncio
    async def test_solo_fail_returns_is_flake_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "real broken test"},
        ])

        from scripts.night_manager import reviewer as rev_mod

        async def _mock_exec(*cmd, **kwargs):
            class FakeProc:
                returncode = 1
                async def communicate(self):
                    return b"1 failed", b""
            return FakeProc()

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        is_flake, n_recovered = await _classify_e2e_flake(
            str(tmp_path), "ABS-164", {}
        )
        assert is_flake is False
        assert n_recovered == 0

    @pytest.mark.asyncio
    async def test_partial_recovery_is_not_flake(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """If only some tests recover, it's not a flake — real failure path must fire."""
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "test passes solo"},
            {"file": "e2e/functional/bar.spec.ts", "title": "test still fails"},
        ])

        from scripts.night_manager import reviewer as rev_mod
        call_count = {"n": 0}

        async def _mock_exec(*cmd, **kwargs):
            call_count["n"] += 1
            # First call (test passes solo) → pass; second → fail.
            rc = 0 if call_count["n"] == 1 else 1

            class FakeProc:
                returncode = rc
                async def communicate(self_):
                    return b"", b""
            return FakeProc()

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        is_flake, n_recovered = await _classify_e2e_flake(
            str(tmp_path), "ABS-164", {}
        )
        assert is_flake is False
        assert n_recovered == 1  # one recovered, one did not

    @pytest.mark.asyncio
    async def test_no_results_json_returns_real_failure(
        self, tmp_path: Path,
    ):
        """No results.json → cannot determine flake → treat as real failure."""
        is_flake, n_recovered = await _classify_e2e_flake(
            str(tmp_path), "ABS-164", {}
        )
        assert is_flake is False
        assert n_recovered == 0


# ---------------------------------------------------------------------------
# run_e2e integration: continuation skipped on flake, fired on real failure
# ---------------------------------------------------------------------------


class FakeProc:
    """Reusable fake subprocess for make e2e and e2e-down calls."""
    def __init__(self, returncode: int, output: bytes = b""):
        self.returncode = returncode
        self._output = output
        self.stdout = None

    async def communicate(self):
        return self._output, b""


class TestRunE2EFlakeIntegration:
    """Drive run_e2e via mocked subprocess calls.

    We mock asyncio.create_subprocess_exec so:
    - e2e-down.sh calls → succeed (returncode 0)
    - make e2e call → fails (returncode 1, triggers flake check)
    - npx playwright solo re-run → pass (flake) or fail (real)

    Then assert the return value of run_e2e reflects the correct path.
    """

    def _make_worktree_with_results(
        self, tmp_path: Path, failing_specs: list[dict],
    ) -> tuple[Path, IssueState]:
        """Create minimal worktree structure with results.json."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / "web").mkdir()
        _write_results_json(wt, failing_specs)
        issue = _make_issue(worktree=str(wt))
        return wt, issue

    @pytest.mark.asyncio
    async def test_run_e2e_returns_passed_true_on_flake(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """run_e2e must return (True, ...) when all solo re-runs pass."""
        wt, issue = self._make_worktree_with_results(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "flaky test"},
        ])

        from scripts.night_manager import reviewer as rev_mod
        calls: list[list[str]] = []

        async def _mock_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            # e2e-down.sh → always succeed
            if cmd[0] == "./scripts/e2e-down.sh" or (len(cmd) > 1 and cmd[1] == "e2e-down.sh"):
                return FakeProc(0)
            # make e2e → fail to trigger flake check
            if cmd[0] == "make" and "e2e" in cmd:
                return FakeProc(1, b"test failed\n")
            # npx playwright solo re-run → pass (flake!)
            if cmd[0] == "npx" and "playwright" in cmd:
                return FakeProc(0, b"1 passed\n")
            return FakeProc(0)

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        passed, output = await run_e2e(issue)

        assert passed is True
        assert "flake_recovered=true" in output

    @pytest.mark.asyncio
    async def test_run_e2e_returns_passed_false_on_real_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """run_e2e must return (False, ...) when solo re-run also fails."""
        wt, issue = self._make_worktree_with_results(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "real broken test"},
        ])

        from scripts.night_manager import reviewer as rev_mod

        async def _mock_exec(*cmd, **kwargs):
            if cmd[0] == "./scripts/e2e-down.sh":
                return FakeProc(0)
            # make e2e → fail
            if cmd[0] == "make" and "e2e" in cmd:
                return FakeProc(1, b"test failed\n")
            # npx playwright solo re-run → also fail (real failure)
            if cmd[0] == "npx" and "playwright" in cmd:
                return FakeProc(1, b"1 failed\n")
            return FakeProc(0)

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        passed, output = await run_e2e(issue)

        assert passed is False
        assert "flake_recovered" not in output

    @pytest.mark.asyncio
    async def test_run_e2e_passes_through_unchanged_on_clean_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """When make e2e passes, flake check must not run at all."""
        wt, issue = self._make_worktree_with_results(tmp_path, [])

        from scripts.night_manager import reviewer as rev_mod
        playwright_calls: list[list[str]] = []

        async def _mock_exec(*cmd, **kwargs):
            if cmd[0] == "npx" and "playwright" in cmd:
                playwright_calls.append(list(cmd))
            if cmd[0] == "./scripts/e2e-down.sh":
                return FakeProc(0)
            # make e2e → pass
            return FakeProc(0, b"all passed\n")

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        passed, _ = await run_e2e(issue)

        assert passed is True
        assert playwright_calls == [], "Playwright solo re-run must not fire when make e2e passes"


# ---------------------------------------------------------------------------
# Flake history accumulation and flake_candidate warning
# ---------------------------------------------------------------------------


class TestFlakeHistory:
    """Verify that repeated flakes accumulate in _flake_history and
    trigger the flake_candidate=true warning after ≥ threshold occurrences."""

    @pytest.fixture(autouse=True)
    def _reset_history(self):
        """Clear module-level flake history before each test."""
        _flake_history.clear()
        yield
        _flake_history.clear()

    @pytest.mark.asyncio
    async def test_flake_history_accumulates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Each flake recovery increments the history entry for that test."""
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "recurring flake"},
        ])

        from scripts.night_manager import reviewer as rev_mod

        async def _mock_exec(*cmd, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return b"1 passed", b""
            return FakeProc()

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        key = "e2e/functional/foo.spec.ts::recurring flake"
        assert key not in _flake_history or len(_flake_history[key]) == 0

        await _classify_e2e_flake(str(tmp_path), "ABS-164", {})
        assert len(_flake_history[key]) == 1
        assert _flake_history[key][0] is True

        await _classify_e2e_flake(str(tmp_path), "ABS-164", {})
        assert len(_flake_history[key]) == 2

    @pytest.mark.asyncio
    async def test_flake_candidate_warning_after_threshold(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """After ≥ _FLAKE_CANDIDATE_THRESHOLD recoveries, a WARNING is emitted."""
        _write_results_json(tmp_path, [
            {"file": "e2e/functional/foo.spec.ts", "title": "chronic flake"},
        ])

        from scripts.night_manager import reviewer as rev_mod

        async def _mock_exec(*cmd, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return b"1 passed", b""
            return FakeProc()

        monkeypatch.setattr(rev_mod.asyncio, "create_subprocess_exec", _mock_exec)

        with caplog.at_level(logging.WARNING, logger="night_manager.reviewer"):
            for _ in range(_FLAKE_CANDIDATE_THRESHOLD):
                await _classify_e2e_flake(str(tmp_path), "ABS-164", {})

        candidate_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "flake_candidate=true" in r.message
        ]
        assert len(candidate_warnings) >= 1, (
            f"Expected flake_candidate=true warning after {_FLAKE_CANDIDATE_THRESHOLD} flakes, "
            f"got records: {[r.message for r in caplog.records]}"
        )
