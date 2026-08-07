"""Unit tests for scripts/compare_ab_runs.py.

Covers: cost calculation, case summarisation, report generation, and
the PRICING table coverage.  Does NOT require a running advisor stack
or database.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.compare_ab_runs import (
    PRICING,
    build_report,
    compute_totals,
    fmt_reasons,
    rates_for_model,
    summarise_case,
    tok_cost_usd,
)

# ---------------------------------------------------------------------------
# tok_cost_usd
# ---------------------------------------------------------------------------

def test_tok_cost_usd_opus_no_cache() -> None:
    rates = PRICING["claude-opus-4-5"]
    cost = tok_cost_usd({"input_tokens": 1_000_000, "output_tokens": 0}, rates)
    assert cost == pytest.approx(15.00)


def test_tok_cost_usd_sonnet_output() -> None:
    rates = PRICING["claude-sonnet-4-6"]
    cost = tok_cost_usd({"input_tokens": 0, "output_tokens": 1_000_000}, rates)
    assert cost == pytest.approx(15.00)


def test_tok_cost_usd_with_cache() -> None:
    rates = PRICING["claude-opus-4-5"]
    usage = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_creation_input_tokens": 2000,
        "cache_read_input_tokens": 50000,
    }
    expected = (
        1000 * 15.00 / 1_000_000
        + 2000 * 18.75 / 1_000_000
        + 50000 * 1.50 / 1_000_000
        + 500 * 75.00 / 1_000_000
    )
    assert tok_cost_usd(usage, rates) == pytest.approx(expected)


def test_sonnet_5x_cheaper_than_opus() -> None:
    usage = {"input_tokens": 10_000, "output_tokens": 2_000}
    opus_cost = tok_cost_usd(usage, PRICING["claude-opus-4-5"])
    sonnet_cost = tok_cost_usd(usage, PRICING["claude-sonnet-4-6"])
    ratio = opus_cost / sonnet_cost
    assert ratio == pytest.approx(5.0), f"Expected 5× cost ratio, got {ratio:.2f}"


# ---------------------------------------------------------------------------
# rates_for_model
# ---------------------------------------------------------------------------

def test_rates_for_exact_match() -> None:
    r = rates_for_model("claude-opus-4-5")
    assert r is not None
    assert r["input"] == 15.00


def test_rates_for_dated_suffix() -> None:
    r = rates_for_model("claude-opus-4-5-20251101")
    assert r is not None
    assert r["input"] == 15.00


def test_rates_for_sonnet_46() -> None:
    r = rates_for_model("claude-sonnet-4-6")
    assert r is not None
    assert r["input"] == 3.00


def test_rates_unknown_model() -> None:
    r = rates_for_model("some-unknown-model-99")
    assert r is None


# ---------------------------------------------------------------------------
# summarise_case — with tool_loop_metrics
# ---------------------------------------------------------------------------

def _make_transcript(
    tc_id: str = "TC-001",
    model: str = "claude-opus-4-5",
    complexity: str = "simple",
    turns: list[dict] | None = None,
) -> dict:
    return {
        "id": tc_id,
        "title": "Test case",
        "zone": "ER-1",
        "complexity": complexity,
        "liability": "low",
        "model": model,
        "turns": turns or [],
    }


def _make_turn_with_metrics(
    turn_n: int = 1,
    iterations: int = 3,
    terminated_reason: str = "end_turn",
    input_toks: int = 5000,
    output_toks: int = 300,
    cache_write: int = 1000,
    cache_read: int = 20000,
) -> dict:
    return {
        "turn": turn_n,
        "user_message": "test",
        "assistant_text": "answer",
        "tool_calls": [],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": input_toks,
            "output_tokens": output_toks,
        },
        "wall_time_s": 10.0,
        "error": None,
        "tool_loop_metrics": {
            "type": "tool_loop_metrics",
            "iterations": iterations,
            "terminated_reason": terminated_reason,
            "total_usage": {
                "input_tokens": input_toks,
                "output_tokens": output_toks,
                "cache_creation_input_tokens": cache_write,
                "cache_read_input_tokens": cache_read,
            },
            "per_iteration": [],
        },
    }


def test_summarise_case_basic_opus() -> None:
    turn = _make_turn_with_metrics(iterations=5, terminated_reason="end_turn")
    transcript = _make_transcript(model="claude-opus-4-5", turns=[turn])
    rates = rates_for_model("claude-opus-4-5")
    result = summarise_case(transcript, rates)

    assert result["id"] == "TC-001"
    assert result["total_iters"] == 5
    assert result["terminated_reasons"] == {"end_turn": 1}
    assert result["has_tool_loop_metrics"] is True
    assert result["cost_usd"] > 0


def test_summarise_case_multiple_turns() -> None:
    turns = [
        _make_turn_with_metrics(turn_n=1, iterations=4, terminated_reason="end_turn"),
        _make_turn_with_metrics(turn_n=2, iterations=10, terminated_reason="iteration_cap"),
    ]
    transcript = _make_transcript(model="claude-opus-4-5", turns=turns)
    rates = rates_for_model("claude-opus-4-5")
    result = summarise_case(transcript, rates)

    assert result["total_iters"] == 14
    assert result["terminated_reasons"] == {"end_turn": 1, "iteration_cap": 1}
    assert result["turns_completed"] == 2


def test_summarise_case_no_tool_loop_metrics() -> None:
    turns = [{
        "turn": 1,
        "user_message": "test",
        "assistant_text": "answer",
        "tool_calls": [],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10000, "output_tokens": 500},
        "wall_time_s": 20.0,
        "error": None,
        "tool_loop_metrics": None,
    }]
    transcript = _make_transcript(model="claude-opus-4-5", turns=turns)
    rates = rates_for_model("claude-opus-4-5")
    result = summarise_case(transcript, rates)

    assert result["has_tool_loop_metrics"] is False
    assert result["cost_usd"] > 0


def test_summarise_case_cache_hit_rate() -> None:
    turn = _make_turn_with_metrics(
        input_toks=1000, cache_write=2000, cache_read=50000
    )
    transcript = _make_transcript(turns=[turn])
    rates = rates_for_model("claude-opus-4-5")
    result = summarise_case(transcript, rates)
    # cache_hit_rate = cache_read / (input + cw + cr) = 50000 / 53000
    expected_rate = round(50000 / (1000 + 2000 + 50000), 3)
    assert result["cache_hit_rate"] == pytest.approx(expected_rate)


# ---------------------------------------------------------------------------
# compute_totals
# ---------------------------------------------------------------------------

def test_compute_totals() -> None:
    cases = [
        {"total_input": 1000, "total_output": 100, "total_cache_write": 0,
         "total_cache_read": 0, "cost_usd": 0.015, "wall_s": 10.0,
         "total_iters": 3},
        {"total_input": 2000, "total_output": 200, "total_cache_write": 500,
         "total_cache_read": 0, "cost_usd": 0.045, "wall_s": 20.0,
         "total_iters": 7},
    ]
    totals = compute_totals(cases)
    assert totals["total_input"] == 3000
    assert totals["cost_usd"] == pytest.approx(0.060)
    assert totals["cases"] == 2
    assert totals["total_iters"] == 10


# ---------------------------------------------------------------------------
# fmt_reasons
# ---------------------------------------------------------------------------

def test_fmt_reasons_empty() -> None:
    assert fmt_reasons({}) == "—"


def test_fmt_reasons_single() -> None:
    assert fmt_reasons({"end_turn": 3}) == "end_turn×3"


def test_fmt_reasons_sorted() -> None:
    result = fmt_reasons({"iteration_cap": 2, "end_turn": 5})
    # alphabetical: end_turn before iteration_cap
    assert result == "end_turn×5 iteration_cap×2"


# ---------------------------------------------------------------------------
# build_report — smoke test (doesn't require disk/DB)
# ---------------------------------------------------------------------------

def test_build_report_unequal_runs(tmp_path: Path) -> None:
    """build_report should not crash when dirs have different cases."""
    base_dir = tmp_path / "baseline"
    cand_dir = tmp_path / "candidate"
    base_dir.mkdir()
    cand_dir.mkdir()

    # Write 2 Opus transcripts to baseline
    for i, tc in enumerate(["TC-001", "TC-002"]):
        transcript = _make_transcript(
            tc_id=tc,
            model="claude-opus-4-5",
            turns=[_make_turn_with_metrics(iterations=5)],
        )
        (base_dir / f"{tc}.json").write_text(json.dumps(transcript))

    # Write 1 Sonnet transcript to candidate (TC-001 only)
    transcript = _make_transcript(
        tc_id="TC-001",
        model="claude-sonnet-4-6",
        turns=[_make_turn_with_metrics(iterations=4)],
    )
    (cand_dir / "TC-001.json").write_text(json.dumps(transcript))

    report = build_report(base_dir, cand_dir, "opus-4-5", "sonnet-4-6")
    assert "A/B Model Comparison Report" in report
    assert "TC-001" in report
    assert "TC-002" in report
    # TC-002 only in baseline → candidate col should show —
    lines = [l for l in report.splitlines() if "TC-002" in l]
    assert lines, "TC-002 row should appear in per-case table"
    assert "—" in lines[0]


def test_build_report_cost_ratio_shows_sonnet_cheaper(tmp_path: Path) -> None:
    """Report verdict line should appear and include a cost ratio."""
    base_dir = tmp_path / "base"
    cand_dir = tmp_path / "cand"
    base_dir.mkdir()
    cand_dir.mkdir()

    for run_dir, model, iters in [
        (base_dir, "claude-opus-4-5", 5),
        (cand_dir, "claude-sonnet-4-6", 6),
    ]:
        t = _make_transcript(
            tc_id="TC-001",
            model=model,
            turns=[_make_turn_with_metrics(
                iterations=iters,
                input_toks=10000,
                output_toks=1000,
                cache_write=0,
                cache_read=0,
            )],
        )
        (run_dir / "TC-001.json").write_text(json.dumps(t))

    report = build_report(base_dir, cand_dir, "opus", "sonnet")
    # Opus should cost 5× more than Sonnet for same token count
    assert "5.0×" in report, f"Expected 5× ratio in report:\n{report}"
