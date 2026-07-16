"""Tests for ABS-302's scripts/measure_wi_token_savings.py (post-ABS-304).

Covers the analytical mode — the cost math (``_cost_actual``,
``_cost_projected_no_wi1``) exactly matches Anthropic's published Opus
rates against hand-computed expectations. This is the load-bearing claim:
real WI-1 savings = projected minus actual.

The MockGateway mode and its tests were removed in ABS-304 alongside the
reverted WI-1 / WI-4 production code.
"""
from __future__ import annotations

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


# ABS-304: MockGateway-mode tests removed alongside the reverted WI-1 / WI-4
# production code and the script's `run_mock_config` helper. The analytical
# mode tests above still cover the historical cost math used to read existing
# transcripts.
