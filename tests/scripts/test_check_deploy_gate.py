"""Unit tests for scripts/check_deploy_gate.py — the enforced deploy gate.

ABS-485. The gate rule was already written down in two places
(``evals/golden/golden_cases.json``'s ``gate`` block and
``evals/golden/README.md``) and enforced by nothing: no CI job referenced the
golden subset and no deploy skill ran a grader. These tests pin the properties
that make the difference between a rule and a gate:

  1. **The exit code is the contract.** A pipeline branches on it, so 0 must
     mean "attested and passing", non-zero must mean "do not promote", and
     "I could not evaluate this" must not be able to masquerade as either.
  2. **Held ≠ failed.** An unattested subset and a failing graded run both stop
     a promotion, but the operator's next move is completely different — one is
     "a qualified human has work to do", the other is "the advisor is wrong".
     The output has to say which.
  3. **A green grade cannot be inherited by an edited golden file.** Otherwise
     attest → grade → green → edit an attestation → promote reads as gated.
  4. **The committed subset holds the gate today**, and the message points at
     the attestation procedure rather than inviting anyone to write one.

Everything here is offline: the attestation condition is a file check by
construction, which is what makes it usable before a run rather than after one.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_deploy_gate import (
    EXIT_HELD,
    EXIT_OPEN,
    EXIT_USAGE,
    GateError,
    attestation_status,
    evaluate_gate,
    format_decision,
)
from scripts.verify_golden_cases import DEFAULT_GOLDEN_FILE, golden_file_digest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_deploy_gate.py"


# ---------------------------------------------------------------------------
# Fixtures — a minimal golden subset and a minimal graded run
# ---------------------------------------------------------------------------


def _attested_case(case_id: str = "TC-001") -> dict:
    return {
        "case_id": case_id,
        "zone": "HR-1",
        "liability": "low",
        "answer_shape": "determinate",
        "selection_rationale": "synthetic fixture",
        "question_for_reviewer": "What side setback governs?",
        "attestation": {
            "status": "attested",
            "attested_by": {"name": "A. Reviewer", "credential": "MCIP, LPP"},
            "attested_on": "2026-08-20",
            "method": "read the by-law",
            "correct_answer": "2.5 m side setback applies.",
            "governing_provisions": [
                {"reference": "Section 198", "holding": "side setback for HR-1"}
            ],
            "must_state": [
                {
                    "id": "side-setback",
                    "description": "gives 2.5 m as the side setback",
                    "any_of": ["2.5 m"],
                }
            ],
            "must_not_state": [],
        },
    }


def _unattested_case(case_id: str = "TC-002") -> dict:
    return {
        "case_id": case_id,
        "zone": "ER-2",
        "liability": "high",
        "answer_shape": "depends",
        "selection_rationale": "synthetic fixture",
        "question_for_reviewer": "Is a townhouse permitted?",
        "attestation": {
            "status": "unattested",
            "attested_by": None,
            "attested_on": None,
            "correct_answer": None,
            "governing_provisions": [],
            "must_state": [],
            "must_not_state": [],
        },
    }


def _write_golden(tmp_path: Path, cases: list[dict]) -> Path:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": cases}, indent=2))
    return path


def _write_graded_run(
    runs_root: Path,
    stamp: str,
    *,
    digest: str | None,
    verdicts: dict[str, str],
) -> Path:
    """A run directory carrying only what the gate reads: GOLDEN_SUMMARY.json."""
    run_dir = runs_root / stamp
    verification = run_dir / "verification"
    verification.mkdir(parents=True, exist_ok=True)
    failing = [cid for cid, v in verdicts.items() if v != "GOLDEN_PASS"]
    summary = {
        "evidence_tier": "human_validated",
        "golden_file": "golden.json",
        "run_dir": str(run_dir),
        "gate": {
            "gates": "production_deploy",
            "open": not failing,
            "blockers": ["not passing: " + ", ".join(failing)] if failing else [],
            "counts": {v: list(verdicts.values()).count(v) for v in set(verdicts.values())},
        },
        "cases": [
            {
                "case_id": cid,
                "verdict": verdict,
                "reasons": [] if verdict == "GOLDEN_PASS" else ["did not cite Section 198"],
            }
            for cid, verdict in verdicts.items()
        ],
    }
    if digest is not None:
        summary["golden_file_sha256"] = digest
    (verification / "GOLDEN_SUMMARY.json").write_text(json.dumps(summary, indent=2))
    return run_dir


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# 1. The committed subset holds the gate today — the whole point of ABS-485
# ---------------------------------------------------------------------------


def test_committed_golden_subset_holds_the_gate_today(runs_root: Path) -> None:
    decision = evaluate_gate(DEFAULT_GOLDEN_FILE, runs_root=runs_root)
    assert decision["open"] is False
    assert decision["reason_code"] == "unattested"
    assert decision["attestation"]["attested"] == 0
    assert decision["attestation"]["total"] == 6


def test_the_hold_points_at_the_procedure_and_forbids_authoring_one(
    runs_root: Path,
) -> None:
    # The failure output is the only thing standing between an agent reading
    # "gate held" and an agent helpfully filling in the blanks.
    text = format_decision(evaluate_gate(DEFAULT_GOLDEN_FILE, runs_root=runs_root))
    assert "evals/golden/README.md" in text
    assert "NOT a model" in text
    assert "DO NOT PROMOTE" in text


def test_a_hold_is_named_differently_from_a_failure(
    tmp_path: Path, runs_root: Path
) -> None:
    # Same effect on the pipeline, opposite next move: nobody has graded these
    # vs. someone graded them and the advisor was wrong. An operator who cannot
    # tell the two apart either waits on a human who has nothing to do, or ships
    # a wrong answer waiting for one.
    held = evaluate_gate(
        _write_golden(tmp_path, [_unattested_case()]), runs_root=runs_root
    )
    assert held["reason_code"] == "unattested"
    assert "HOLD, not a FAILURE" in held["explanation"]

    failing_dir = tmp_path / "failing"
    failing_dir.mkdir()
    failing_golden = _write_golden(failing_dir, [_attested_case()])
    _write_graded_run(
        runs_root, "20260828T000000Z",
        digest=golden_file_digest(failing_golden),
        verdicts={"TC-001": "GOLDEN_FAIL"},
    )
    failed = evaluate_gate(failing_golden, runs_root=runs_root)
    assert failed["reason_code"] == "graded_failing"
    assert "FAILURE, not a hold" in failed["explanation"]
    # …and the failure explicitly refuses the tempting fix.
    assert "do not edit the attestation to match" in failed["explanation"].lower()


# ---------------------------------------------------------------------------
# 2. Exit codes — the contract a pipeline branches on
# ---------------------------------------------------------------------------


def _run_cli(golden: Path, runs_root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "--golden",
            str(golden),
            "--runs-root",
            str(runs_root),
            *extra,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_attested_and_passing_exits_zero(tmp_path: Path, runs_root: Path) -> None:
    golden = _write_golden(tmp_path, [_attested_case()])
    _write_graded_run(
        runs_root, "20260828T000000Z",
        digest=golden_file_digest(golden), verdicts={"TC-001": "GOLDEN_PASS"},
    )
    proc = _run_cli(golden, runs_root)
    assert proc.returncode == EXIT_OPEN, proc.stdout + proc.stderr
    assert "GATE: OPEN" in proc.stdout


def test_unattested_exits_nonzero(tmp_path: Path, runs_root: Path) -> None:
    golden = _write_golden(tmp_path, [_attested_case(), _unattested_case()])
    proc = _run_cli(golden, runs_root)
    assert proc.returncode == EXIT_HELD
    assert "GATE: HELD (unattested)" in proc.stdout
    assert "TC-002" in proc.stdout


def test_graded_failure_exits_nonzero(tmp_path: Path, runs_root: Path) -> None:
    golden = _write_golden(tmp_path, [_attested_case()])
    _write_graded_run(
        runs_root, "20260828T000000Z",
        digest=golden_file_digest(golden), verdicts={"TC-001": "GOLDEN_FAIL"},
    )
    proc = _run_cli(golden, runs_root)
    assert proc.returncode == EXIT_HELD
    assert "GATE: HELD (graded_failing)" in proc.stdout
    assert "did not cite Section 198" in proc.stdout


def test_could_not_evaluate_exits_two_not_one(tmp_path: Path, runs_root: Path) -> None:
    # 2 is deliberately not 1: a pipeline that treats "the file is missing" as
    # "the gate is closed" is fine, but one that treats it as "the gate is open"
    # is catastrophic, and conflating the two invites exactly that refactor.
    proc = _run_cli(tmp_path / "nope.json", runs_root)
    assert proc.returncode == EXIT_USAGE
    assert "not a passing gate" in proc.stderr


def test_malformed_golden_file_is_a_refusal_not_a_verdict(
    tmp_path: Path, runs_root: Path
) -> None:
    broken = copy.deepcopy(_attested_case())
    broken["answer_shape"] = "not-a-shape"
    golden = _write_golden(tmp_path, [broken])
    proc = _run_cli(golden, runs_root)
    assert proc.returncode == EXIT_USAGE
    assert "answer_shape" in proc.stderr
    assert "GATE: OPEN" not in proc.stdout


def test_nonexistent_explicit_run_dir_exits_two(tmp_path: Path, runs_root: Path) -> None:
    golden = _write_golden(tmp_path, [_attested_case()])
    proc = _run_cli(golden, runs_root, "--run-dir", str(tmp_path / "no-such-run"))
    assert proc.returncode == EXIT_USAGE
    assert "Run dir not found" in proc.stderr


def test_json_mode_carries_the_reason_code(tmp_path: Path, runs_root: Path) -> None:
    golden = _write_golden(tmp_path, [_unattested_case()])
    proc = _run_cli(golden, runs_root, "--json")
    assert proc.returncode == EXIT_HELD
    payload = json.loads(proc.stdout)
    assert payload["open"] is False
    assert payload["reason_code"] == "unattested"
    assert payload["attestation"]["unattested"] == ["TC-002"]


# ---------------------------------------------------------------------------
# 3. An attestation is not evidence until something has graded it
# ---------------------------------------------------------------------------


def test_attested_but_never_graded_holds_the_gate(
    tmp_path: Path, runs_root: Path
) -> None:
    decision = evaluate_gate(
        _write_golden(tmp_path, [_attested_case()]), runs_root=runs_root
    )
    assert decision["open"] is False
    assert decision["reason_code"] == "no_graded_run"
    assert "scripts/verify_run.py" in decision["next_step"]


def test_a_grade_of_a_different_golden_file_cannot_open_this_one(
    tmp_path: Path, runs_root: Path
) -> None:
    # attest → grade → green → edit an attestation → promote. The stale summary
    # still says the gate was open; it opened for a file that no longer exists.
    golden = _write_golden(tmp_path, [_attested_case()])
    _write_graded_run(
        runs_root, "20260828T000000Z",
        digest=golden_file_digest(golden), verdicts={"TC-001": "GOLDEN_PASS"},
    )
    edited = json.loads(golden.read_text())
    edited["cases"][0]["attestation"]["correct_answer"] = "Actually 3.0 m applies."
    golden.write_text(json.dumps(edited, indent=2))

    decision = evaluate_gate(golden, runs_root=runs_root)
    assert decision["open"] is False
    assert decision["reason_code"] == "no_graded_run"
    assert "different golden file" in decision["explanation"]


def test_a_summary_predating_the_digest_is_not_trusted(
    tmp_path: Path, runs_root: Path
) -> None:
    golden = _write_golden(tmp_path, [_attested_case()])
    _write_graded_run(
        runs_root, "20260601T000000Z", digest=None, verdicts={"TC-001": "GOLDEN_PASS"}
    )
    decision = evaluate_gate(golden, runs_root=runs_root)
    assert decision["open"] is False
    assert decision["reason_code"] == "no_graded_run"
    assert "predate the digest" in decision["explanation"]


def test_the_newest_matching_grade_is_the_one_that_counts(
    tmp_path: Path, runs_root: Path
) -> None:
    golden = _write_golden(tmp_path, [_attested_case()])
    digest = golden_file_digest(golden)
    _write_graded_run(runs_root, "20260601T000000Z", digest=digest,
                      verdicts={"TC-001": "GOLDEN_FAIL"})
    _write_graded_run(runs_root, "20260828T000000Z", digest=digest,
                      verdicts={"TC-001": "GOLDEN_PASS"})
    decision = evaluate_gate(golden, runs_root=runs_root)
    assert decision["open"] is True
    assert "20260828T000000Z" in decision["graded_run"]["run_dir"]


def test_an_explicit_ungraded_run_dir_is_a_usage_error(
    tmp_path: Path, runs_root: Path
) -> None:
    golden = _write_golden(tmp_path, [_attested_case()])
    ungraded = runs_root / "20260828T000000Z"
    ungraded.mkdir()
    with pytest.raises(GateError, match="has not been graded"):
        evaluate_gate(golden, runs_root=runs_root, run_dir=ungraded)


# ---------------------------------------------------------------------------
# 4. Degenerate inputs
# ---------------------------------------------------------------------------


def test_an_empty_golden_subset_does_not_open_the_gate(
    tmp_path: Path, runs_root: Path
) -> None:
    # validate_golden already calls an empty subset a problem, so this arrives
    # as a refusal rather than an open gate — which is the property that matters.
    golden = _write_golden(tmp_path, [])
    with pytest.raises(GateError, match="no cases"):
        evaluate_gate(golden, runs_root=runs_root)


def test_attestation_status_counts_what_it_says_it_counts() -> None:
    payload = {"cases": [_attested_case(), _unattested_case()]}
    status = attestation_status(payload)
    assert status == {"total": 2, "attested": 1, "unattested": ["TC-002"]}


# ---------------------------------------------------------------------------
# 5. The grade records which golden file it graded
# ---------------------------------------------------------------------------


def test_golden_file_digest_changes_with_the_file(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text('{"cases": []}')
    before = golden_file_digest(path)
    path.write_text('{"cases": [] }')
    assert golden_file_digest(path) != before
