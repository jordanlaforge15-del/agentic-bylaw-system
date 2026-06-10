"""Tests for ABS-302's scripts/measure_wi_token_savings.py.

The script has two measurement modes. The tests here cover:

* Analytical mode — the cost math (``_cost_actual``, ``_cost_projected_no_wi1``)
  exactly matches Anthropic's published Opus rates against hand-computed
  expectations. This is the load-bearing claim: real WI-1 savings = projected
  minus actual.

* MockGateway mode — the WI-1 monkey-patch and WI-4 env-var toggle actually
  do what they claim. ``baseline`` produces zero cache-flagged bytes;
  ``wi1`` produces non-zero; ``wi1+4`` produces non-zero AND a strictly
  smaller uncached growing region than ``wi1`` alone.

If these tests pass, the script's numbers are trustworthy.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

# Load the script as a module without polluting sys.path globally.
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_wi_token_savings.py"
spec = importlib.util.spec_from_file_location("measure_wi", _SCRIPT)
measure_wi = importlib.util.module_from_spec(spec)
sys.modules["measure_wi"] = measure_wi
sys.path.insert(0, str(_SCRIPT.parent.parent / "src"))
spec.loader.exec_module(measure_wi)


# -- ANALYTICAL MODE ----------------------------------------------------------


def test_cost_actual_matches_published_opus_rates():
    """Opus rates: $15 input / $75 output / $18.75 cache_write / $1.50 cache_read
    per MTok. A request usage with each field populated should price out
    exactly. Anchor test against the rates so a future rate change in the
    constant fails here loudly.
    """
    # 1 MTok in each bucket
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
    }
    cost = measure_wi._cost_actual(usage)
    expected = 15.00 + 75.00 + 18.75 + 1.50
    assert cost == pytest.approx(expected, rel=1e-9)


def test_cost_projected_no_wi1_rebills_cached_tokens_as_input():
    """Without WI-1, cache_create + cache_read tokens would all ship as
    uncached input. The projection sums them into the input bucket and
    bills at $15/MTok. Output is unaffected.
    """
    usage = {
        "input_tokens": 100_000,
        "output_tokens": 50_000,
        "cache_creation_input_tokens": 200_000,
        "cache_read_input_tokens": 400_000,
    }
    cost = measure_wi._cost_projected_no_wi1(usage)
    # All input-side tokens collapse to uncached: 100k + 200k + 400k = 700k @ $15/MTok
    # Output: 50k @ $75/MTok
    expected = (700_000 * 15.00 + 50_000 * 75.00) / 1_000_000
    assert cost == pytest.approx(expected, rel=1e-9)


def test_wi1_break_even_ratio_is_around_0_278():
    """Pin the WI-1 economics: cache_write costs $18.75/MTok (1.25× input),
    cache_read saves $13.50/MTok (input − cache_read). Break-even is when

        cache_read / cache_write >= (cache_write_rate - input_rate) / (input_rate - cache_read_rate)
                                  = (18.75 - 15.00) / (15.00 - 1.50)
                                  = 3.75 / 13.50
                                  ≈ 0.278

    Below the ratio, WI-1 costs MORE than no-caching (you're paying the
    write premium with insufficient reads to recoup it). Above the ratio,
    WI-1 saves money. This is a real failure mode for short loops where
    a tool_result enters the cache but the conversation ends before it
    gets read back.

    The ABS-293 production transcripts show ratios well above 0.278 (TC-005
    averages ~1.05 reads-per-write), so WI-1 is net positive in practice
    on these cases. But code that assumed WI-1 is unconditionally
    profitable is wrong — this test pins the actual math.
    """
    output_tokens = 50  # neutralised below; it's the same on both sides

    # Below break-even (writes >> reads) — WI-1 should cost more.
    below = {"input_tokens": 100, "output_tokens": output_tokens,
             "cache_creation_input_tokens": 1_000, "cache_read_input_tokens": 100}
    assert measure_wi._cost_actual(below) > measure_wi._cost_projected_no_wi1(below)

    # Above break-even (reads >> writes) — WI-1 should save money.
    above = {"input_tokens": 100, "output_tokens": output_tokens,
             "cache_creation_input_tokens": 1_000, "cache_read_input_tokens": 1_000}
    assert measure_wi._cost_actual(above) < measure_wi._cost_projected_no_wi1(above)

    # No cache activity — should be identical.
    none = {"input_tokens": 100, "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    assert measure_wi._cost_actual(none) == pytest.approx(
        measure_wi._cost_projected_no_wi1(none), rel=1e-12
    )


def test_analyse_transcript_handles_missing_tool_loop_metrics():
    """A turn with no tool_loop_metrics (older transcript shape, or an error
    turn) is skipped without crashing. The case totals only count turns
    that have the per-iteration usage.
    """
    transcript = {
        "id": "TC-FAKE",
        "title": "synthetic",
        "complexity": "simple",
        "model": "claude-opus-4-5",
        "turns": [
            {"turn": 1, "tool_loop_metrics": {
                "iterations": 5,
                "total_usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_creation_input_tokens": 500,
                    "cache_read_input_tokens": 2000,
                },
            }},
            {"turn": 2, "error": "transport: timeout"},  # no metrics
            {"turn": 3},  # no metrics, no error
        ],
    }
    import json as _json
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(_json.dumps(transcript))
        path = Path(f.name)
    try:
        result = measure_wi.analyse_transcript(path)
    finally:
        path.unlink()

    assert result["id"] == "TC-FAKE"
    assert result["turns_with_metrics"] == 1
    assert result["total_iterations"] == 5
    assert result["wi1_savings_usd"] > 0
    # 2000 cache_read tokens cost 2000 * (15 - 1.50) / 1M = $0.027 saved (read)
    # 500 cache_create tokens cost 500 * (18.75 - 15) / 1M = $0.00188 lost (write)
    # Net savings ≈ $0.025
    assert result["wi1_savings_usd"] == pytest.approx(0.02513, abs=1e-4)


# -- MOCKGATEWAY MODE ---------------------------------------------------------


def _totals(result):
    return result["totals"]


def test_mock_baseline_produces_zero_cached_bytes():
    """With WI-1 OFF (the baseline config), the gateway must see ZERO
    cache-flagged blocks. If anything other than 0 shows up, either the
    monkey-patch didn't apply or there's another path placing cache flags.
    """
    result = asyncio.run(measure_wi.run_mock_config("baseline", num_rounds=5))
    assert _totals(result)["msgs_cached_chars"] == 0


def test_mock_wi1_marks_a_rolling_breakpoint_each_iteration_after_first():
    """WI-1 ON: every gateway call AFTER the first must carry a cache
    breakpoint. The first call (initial user message, no tool_result yet)
    has nothing to cache.
    """
    result = asyncio.run(measure_wi.run_mock_config("wi1", num_rounds=5))
    per_iter = result["per_iteration"]
    assert per_iter[0]["msgs_cached_chars"] == 0, "first call should have no cached blocks"
    # Every subsequent call should have a non-zero cached partition.
    for m in per_iter[1:]:
        assert m["msgs_cached_chars"] > 0, (
            f"iter {m['iteration']}: WI-1 should have placed a cache marker; got 0 cached chars"
        )


def test_mock_wi4_shrinks_uncached_region_vs_wi1_alone():
    """WI-4 (in-loop compaction) on top of WI-1 must produce a strictly
    smaller uncached growing region. If wi1+4 doesn't shrink uncached
    bytes vs wi1, the compaction is silently a no-op — which would
    invalidate ABS-290's claimed savings.

    Use enough rounds (>3) that compaction has older tool_results to
    summarise.
    """
    wi1 = asyncio.run(measure_wi.run_mock_config("wi1", num_rounds=6))
    wi14 = asyncio.run(measure_wi.run_mock_config("wi1+4", num_rounds=6))

    assert (
        _totals(wi14)["msgs_uncached_chars"] < _totals(wi1)["msgs_uncached_chars"]
    ), (
        f"WI-4 did not shrink the uncached region: "
        f"wi1={_totals(wi1)['msgs_uncached_chars']}, "
        f"wi1+4={_totals(wi14)['msgs_uncached_chars']}"
    )


def test_mock_configs_are_independent_clean_up_after_themselves():
    """Running multiple configs back-to-back must not bleed state across
    them. After a baseline run (which monkey-patches the marker), a
    follow-up wi1 run must see WI-1 working correctly.
    """
    # Run baseline first to apply (and clean up) the monkey-patch.
    asyncio.run(measure_wi.run_mock_config("baseline", num_rounds=4))
    # Then wi1 must still produce cached bytes.
    wi1 = asyncio.run(measure_wi.run_mock_config("wi1", num_rounds=4))
    assert _totals(wi1)["msgs_cached_chars"] > 0, (
        "baseline run leaked its monkey-patch — WI-1 produces zero cached bytes"
    )


def test_mock_keep_recent_env_var_restored_after_wi4_off_run():
    """The WI-4 toggle uses ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT. Make sure a
    baseline run (which sets the var to 0 to disable compaction) restores
    the prior value when it finishes, so subsequent runs aren't
    contaminated.
    """
    import os
    prior = os.environ.get("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT")
    try:
        # Force a known prior value
        os.environ["ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT"] = "7"
        asyncio.run(measure_wi.run_mock_config("baseline", num_rounds=3))
        assert os.environ.get("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT") == "7"
    finally:
        if prior is None:
            os.environ.pop("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT", None)
        else:
            os.environ["ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT"] = prior
