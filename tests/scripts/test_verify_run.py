"""Unit tests for scripts/verify_run.py — the one entry point (ABS-516).

There were two graders for one suite and they disagreed: on the same eight
transcripts the generated grader said 5 PASS / 3 PARTIAL / 0 FAIL while the
golden grader said 3 PASS / 1 PARTIAL / 4 FAIL. Nothing forced a caller to run
both, so "I ran the grader and it passed" was the default outcome rather than an
edge case.

These tests pin the properties that make one entry point worth having:

  1. Both tiers are graded and both artifacts are written by a single call.
  2. The golden tier is printed first and labelled as the gating one.
  3. The exit status comes from the golden tier alone — a perfect advisory sweep
     cannot open the gate, and a failing advisory sweep cannot close it.
  4. Unattested golden entries are announced, not quietly folded into "0 FAIL".
  5. No number in the output or in RUN_SUMMARY.json sums the two tiers.

Everything runs offline against ``evals/fixtures/abs462_corpus_snapshot.json``.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scripts import verify_run
from scripts.verify_test_prompts import JsonCorpus

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_FILE = REPO_ROOT / "evals" / "fixtures" / "abs462_corpus_snapshot.json"

RIGHT_ANSWER = "Under Section 198 the side setback for your lot is 2.5 m."
# Fluent, real citation, right topic, wrong answer — the shape the generated
# grader is structurally unable to fail, because a model authoring the
# expectations does not know which wrong answer a model is inclined to give.
WRONG_ANSWER = "Under Section 198 your side setback is 0.0 m, so build to the line."


@pytest.fixture(scope="module")
def corpus() -> JsonCorpus:
    return JsonCorpus.from_file(CORPUS_FILE)


def _attested_case(**overrides) -> dict:
    case = {
        "case_id": "TC-001",
        "zone": "HR-1",
        "liability": "low",
        "answer_shape": "determinate",
        "selection_rationale": "…",
        "question_for_reviewer": "…",
        "attestation": {
            "status": "attested",
            "attested_by": {"name": "A. Reviewer", "credential": "MCIP, LPP"},
            "attested_on": "2026-08-20",
            "method": "read the by-law",
            "correct_answer": "2.5 m side setback applies.",
            "governing_provisions": [
                {"reference": "Section 198", "holding": "side setback standards for HR-1"}
            ],
            "must_state": [
                {
                    "id": "side-setback",
                    "description": "gives 2.5 m as the side setback",
                    "any_of": ["2.5 m", "2.5 metres"],
                }
            ],
            "must_not_state": [
                {
                    "id": "no-zero-setback",
                    "description": "must not say the side setback is zero",
                    "any_of": ["0.0 m", "no side setback"],
                }
            ],
        },
    }
    case.update(overrides)
    return case


def _unattested_case() -> dict:
    return _attested_case(
        attestation={
            "status": "unattested",
            "attested_by": None,
            "attested_on": None,
            "correct_answer": None,
            "governing_provisions": [],
            "must_state": [],
            "must_not_state": [],
        }
    )


def _write_run(tmp_path: Path, answer: str, *, keywords: list[str] | None = None) -> Path:
    """A one-case run whose generated spec the answer satisfies."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "TC-001.json").write_text(json.dumps({
        "id": "TC-001",
        "title": "side setback",
        "zone": "HR-1",
        "turns": [{"turn": 1, "assistant_text": answer}],
        "spec": {
            "complexity": "simple",
            "liability": "low",
            "expected_answer_keywords": keywords if keywords is not None else ["Section 198"],
            "expected_bylaw_references": ["Section 198"],
            "expected_topics": ["side_setback"],
        },
    }))
    return run_dir


def _grade(
    run_dir: Path, corpus: JsonCorpus, golden_cases: list[dict]
) -> tuple[dict, str]:
    """Drive both tiers the way ``main`` does, and render the report."""
    from scripts import verify_golden_cases, verify_test_prompts

    payload = {"schema_version": 1, "cases": golden_cases}
    golden_summary = verify_golden_cases.grade_run(
        run_dir, corpus, payload, log=io.StringIO()
    )
    generated_rows = verify_test_prompts.grade_run(
        run_dir, corpus, spec_source="transcript", log=io.StringIO()
    )
    out = io.StringIO()
    summary = verify_run.report(golden_summary, generated_rows, None, run_dir, out=out)
    return summary, out.getvalue()


# ---------------------------------------------------------------------------
# One call grades both tiers
# ---------------------------------------------------------------------------


def test_one_call_writes_both_tiers_artifacts(tmp_path: Path, corpus: JsonCorpus) -> None:
    run_dir = _write_run(tmp_path, RIGHT_ANSWER)
    _grade(run_dir, corpus, [_attested_case()])
    verification = run_dir / "verification"
    assert (verification / "GOLDEN_SUMMARY.json").exists()
    assert (verification / "SUMMARY.json").exists()
    assert (verification / "TC-001.golden.json").exists()
    assert (verification / "TC-001.verify.json").exists()


def test_golden_is_printed_first_and_labelled_as_the_gating_tier(
    tmp_path: Path, corpus: JsonCorpus
) -> None:
    run_dir = _write_run(tmp_path, RIGHT_ANSWER)
    _, text = _grade(run_dir, corpus, [_attested_case()])
    golden_at = text.index("GOLDEN (human-attested, gates deploy)")
    generated_at = text.index("GENERATED (model-authored, advisory)")
    assert golden_at < generated_at, "the tier that gates is read first"
    assert "gates nothing" in text


# ---------------------------------------------------------------------------
# The gate is the golden tier's, and only the golden tier's
# ---------------------------------------------------------------------------


def test_advisory_pass_cannot_open_the_gate(tmp_path: Path, corpus: JsonCorpus) -> None:
    """The ABS-516 defect itself: the advisory tier reporting no failures while
    the human standard is failing. Exactly the ``zone-typology-all8`` outcome."""
    run_dir = _write_run(tmp_path, WRONG_ANSWER, keywords=["Section 198"])
    summary, text = _grade(run_dir, corpus, [_attested_case()])

    assert summary["advisory"]["counts"]["FAIL"] == 0, "the advisory tier sees no problem"
    assert summary["gating"]["counts"]["FAIL"] == 1
    assert summary["gate_open"] is False
    assert "DEPLOY GATE: CLOSED" in text


def test_advisory_failure_cannot_close_an_open_gate(
    tmp_path: Path, corpus: JsonCorpus
) -> None:
    # Keywords the answer cannot possibly hit: the advisory tier fails while the
    # human-attested tier passes. The gate is the human's call, so it opens.
    run_dir = _write_run(tmp_path, RIGHT_ANSWER, keywords=["quonset hut", "aerodrome"])
    summary, _ = _grade(run_dir, corpus, [_attested_case()])
    assert summary["advisory"]["counts"]["PASS"] == 0
    assert summary["gating"]["counts"]["PASS"] == 1
    assert summary["gate_open"] is True


def test_exit_status_is_attributed_to_the_golden_tier(
    tmp_path: Path, corpus: JsonCorpus
) -> None:
    run_dir = _write_run(tmp_path, RIGHT_ANSWER)
    summary, text = _grade(run_dir, corpus, [_attested_case()])
    assert summary["exit_driven_by"] == "human_validated"
    assert summary["advisory"]["gates"] is None
    assert "Exit status is set by the golden tier alone" in text


# ---------------------------------------------------------------------------
# Unattested is loud
# ---------------------------------------------------------------------------


def test_unattested_entries_are_announced_not_folded_into_zero_fail(
    tmp_path: Path, corpus: JsonCorpus
) -> None:
    run_dir = _write_run(tmp_path, RIGHT_ANSWER)
    summary, text = _grade(run_dir, corpus, [_unattested_case()])
    assert summary["gating"]["counts"]["UNATTESTED"] == 1
    assert summary["gate_open"] is False
    assert "UNATTESTED" in text
    assert "1 of 1 golden entries are UNATTESTED" in text
    assert "demonstrated nothing about" in text
    # The advisory tier passed; that must not read as the run passing.
    assert summary["advisory"]["counts"]["PASS"] == 1


def test_a_run_with_no_transcript_for_a_golden_case_closes_the_gate(
    tmp_path: Path, corpus: JsonCorpus
) -> None:
    run_dir = _write_run(tmp_path, RIGHT_ANSWER)
    summary, _ = _grade(
        run_dir, corpus, [_attested_case(), _attested_case(case_id="TC-002", zone="ER-2")]
    )
    assert summary["gating"]["counts"]["NO_TRANSCRIPT"] == 1
    assert summary["gate_open"] is False


# ---------------------------------------------------------------------------
# The tiers are never summed
# ---------------------------------------------------------------------------


def test_summary_keeps_the_tiers_apart_with_no_total(
    tmp_path: Path, corpus: JsonCorpus
) -> None:
    run_dir = _write_run(tmp_path, RIGHT_ANSWER)
    summary, text = _grade(run_dir, corpus, [_attested_case()])

    assert summary["gating"]["evidence_tier"] == "human_validated"
    assert summary["advisory"]["evidence_tier"] == "generated"
    # No key anywhere holds a combined count or a combined verdict.
    for key in summary:
        assert key not in {"counts", "total", "totals", "verdict", "pass_rate"}
    assert "never summed" in summary["note"]
    # Two PASS rows, one per tier, and nothing in the report says "2 PASS".
    assert summary["gating"]["counts"]["PASS"] == 1
    assert summary["advisory"]["counts"]["PASS"] == 1
    assert "2 PASS" not in text


def test_verdict_vocabularies_stay_disjoint_in_the_report(
    tmp_path: Path, corpus: JsonCorpus
) -> None:
    run_dir = _write_run(tmp_path, RIGHT_ANSWER)
    summary, _ = _grade(run_dir, corpus, [_attested_case()])
    golden_verdicts = {c["verdict"] for c in summary["gating"]["cases"]}
    generated_verdicts = {c["verdict"] for c in summary["advisory"]["cases"]}
    assert golden_verdicts == {"GOLDEN_PASS"}
    assert generated_verdicts == {"PASS"}
    assert not (golden_verdicts & generated_verdicts)


# ---------------------------------------------------------------------------
# Degenerate inputs still produce a gating verdict
# ---------------------------------------------------------------------------


def test_an_ungradeable_advisory_tier_does_not_withhold_the_gating_verdict(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    (empty / "verification").mkdir(parents=True)
    summary, text = (
        verify_run.report(
            {
                "gate": {"open": False, "blockers": ["not passing: TC-001"], "counts": {}},
                "cases": [{"case_id": "TC-001", "verdict": "NO_TRANSCRIPT", "reasons": []}],
            },
            [],
            "No TC-*.json transcripts in the run",
            empty,
            out=(out := io.StringIO()),
        ),
        out.getvalue(),
    )
    assert summary["advisory"]["error"]
    assert summary["gate_open"] is False
    assert "DEPLOY GATE: CLOSED" in text


def test_the_advisory_only_banner_names_the_entry_point() -> None:
    """Running the advisory grader alone has to say what it is not (ABS-516)."""
    from scripts.verify_test_prompts import ADVISORY_ONLY_BANNER

    assert "ADVISORY TIER ONLY" in ADVISORY_ONLY_BANNER
    assert "scripts/verify_run.py" in ADVISORY_ONLY_BANNER
    assert "gate nothing" in ADVISORY_ONLY_BANNER
