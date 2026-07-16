"""ABS-302 / ABS-304: Historical WI-1 effect from existing transcripts.

``--from-transcripts <dir>`` reads the per-turn ``tool_loop_metrics.total_usage``
already captured by ``scripts/run_test_prompts.py`` and computes WI-1's
historical effect analytically:

    actual_cost     = (input * input_rate
                      + cache_create * cache_write_rate
                      + cache_read * cache_read_rate
                      + output * output_rate) / 1M
    projected_no_WI1 = ((input + cache_create + cache_read) * input_rate
                       + output * output_rate) / 1M
    WI-1 savings    = projected_no_WI1 - actual

This computes what WI-1 saved against Anthropic billing on a transcript
that was captured WITH WI-1 enabled. Useful for understanding historical
runs (e.g. ABS-293's TC-001/TC-005 Opus runs). No new model calls.

NOTE: ABS-304 reverted WI-1 and WI-4 in production. The earlier MockGateway
mode (which toggled both optimizations via monkey-patch + env var) was
removed alongside the reverted code. Only the analytical mode against
existing transcripts remains.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# Opus 4.5 published rates (USD per MTok).
RATES = {
    "input": 15.00,
    "output": 75.00,
    "cache_write": 18.75,
    "cache_read": 1.50,
}


# -- ANALYTICAL MODE (from transcripts) ---------------------------------------


def _cost_actual(usage: dict[str, int]) -> float:
    """Cost as Anthropic billed it: uncached input + cache_write + cache_read + output."""
    return (
        usage.get("input_tokens", 0) * RATES["input"]
        + usage.get("cache_creation_input_tokens", 0) * RATES["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * RATES["cache_read"]
        + usage.get("output_tokens", 0) * RATES["output"]
    ) / 1_000_000


def _cost_projected_no_wi1(usage: dict[str, int]) -> float:
    """Cost if WI-1 (rolling cache breakpoint) had not been in place.

    Without WI-1, the cache=True marker is never placed on tool_results,
    so the prefix is never cached. Every token Anthropic counted as
    cache_create + cache_read would instead have shipped as uncached input
    each iteration. Output tokens stay the same — they're per-iteration
    new content, unaffected by caching.
    """
    total_input = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    return (
        total_input * RATES["input"]
        + usage.get("output_tokens", 0) * RATES["output"]
    ) / 1_000_000


def analyse_transcript(path: Path) -> dict[str, Any]:
    """Per-case WI-1 effect from a TC-NNN.json transcript."""
    transcript = json.loads(path.read_text())
    case_actual = 0.0
    case_projected = 0.0
    turns_with_metrics = 0
    iters = 0
    for turn in transcript.get("turns", []):
        tlm = turn.get("tool_loop_metrics") or {}
        usage = tlm.get("total_usage")
        if not usage:
            continue
        case_actual += _cost_actual(usage)
        case_projected += _cost_projected_no_wi1(usage)
        iters += tlm.get("iterations", 0) or 0
        turns_with_metrics += 1
    saved = case_projected - case_actual
    pct = (saved / case_projected * 100.0) if case_projected else 0.0
    return {
        "id": transcript.get("id"),
        "title": transcript.get("title"),
        "complexity": transcript.get("complexity"),
        "model": transcript.get("model"),
        "turns_with_metrics": turns_with_metrics,
        "total_iterations": iters,
        "actual_usd": round(case_actual, 4),
        "projected_no_wi1_usd": round(case_projected, 4),
        "wi1_savings_usd": round(saved, 4),
        "wi1_savings_pct": round(pct, 2),
    }


def analyse_run_dir(run_dir: Path) -> list[dict[str, Any]]:
    files = sorted(run_dir.glob("TC-*.json"))
    if not files:
        raise SystemExit(f"No TC-*.json found in {run_dir}")
    return [analyse_transcript(p) for p in files]


# ABS-304: MockGateway mode removed — depended on `_mark_rolling_cache_breakpoint`
# (WI-1) and `compact_keep_recent` (WI-4), both reverted. The analytical mode
# above still works on existing transcripts that were captured with WI-1
# enabled.


# -- CLI ----------------------------------------------------------------------


def _print_analytical(rows: list[dict[str, Any]]) -> None:
    print("=" * 96)
    print("ANALYTICAL MODE — WI-1 effect from existing transcripts")
    print("=" * 96)
    print(
        f"  {'id':<8} {'iters':<7} {'actual $':<12} "
        f"{'projected no WI-1 $':<22} {'saved $':<12} {'saved %':<8}"
    )
    total_actual = total_projected = 0.0
    for r in rows:
        print(
            f"  {r['id']:<8} {r['total_iterations']:<7} "
            f"${r['actual_usd']:<11.4f} "
            f"${r['projected_no_wi1_usd']:<21.4f} "
            f"${r['wi1_savings_usd']:<11.4f} "
            f"{r['wi1_savings_pct']:<7.2f}%"
        )
        total_actual += r["actual_usd"]
        total_projected += r["projected_no_wi1_usd"]
    saved = total_projected - total_actual
    pct = (saved / total_projected * 100.0) if total_projected else 0.0
    print(
        f"\n  TOTAL across {len(rows)} cases: actual=${total_actual:.4f}  "
        f"projected_no_WI1=${total_projected:.4f}  "
        f"WI-1 saved=${saved:.4f} ({pct:.2f}%)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    p_t = sub.add_parser("from-transcripts", help="Analytical from existing run dir")
    p_t.add_argument("run_dir", type=Path, help="evals/runs/<dir> with TC-*.json")
    p_t.add_argument("--out", type=Path, help="Optional JSON output path")

    args = parser.parse_args()

    if args.mode == "from-transcripts":
        rows = analyse_run_dir(args.run_dir)
        _print_analytical(rows)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(rows, indent=2))
            print(f"\nWrote: {args.out}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
