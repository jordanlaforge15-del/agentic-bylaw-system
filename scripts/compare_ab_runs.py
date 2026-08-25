#!/usr/bin/env python3
"""
Compare two run-directory outputs from run_test_prompts.py to evaluate
model A vs model B on cost and quality metrics.

Intended for ABS-286 WI-2: A/B Opus 4.5 vs Sonnet 4.x on the 20-case suite.

Usage:
  python scripts/compare_ab_runs.py \\
    --baseline  evals/runs/<opus-run-dir>  \\
    --candidate evals/runs/<sonnet-run-dir> \\
    --output-md evals/runs/<sonnet-run-dir>/AB_COMPARISON.md

Cost model ($/MTok):
  Model               Input   Output  Cache-write  Cache-read
  claude-opus-4-5     15.00   75.00   18.75        1.50
  claude-sonnet-4-5    3.00   15.00    3.75        0.30
  claude-sonnet-4-6    3.00   15.00    3.75        0.30
  claude-haiku-4-5     0.80    4.00    1.00        0.08

Costs are read from the PRICING table below; override per-model rates with
--baseline-input-price etc. if pricing changes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# $/MTok pricing — intentionally defined here so the file is self-contained
# and the comparison is reproducible.  Update when Anthropic changes rates.
PRICING: dict[str, dict[str, float]] = {
    # Opus 4.5 — production default as of ABS-286
    "claude-opus-4-5":            {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-opus-4-5-20251101":   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    # Sonnet 4.5
    "claude-sonnet-4-5":          {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    "claude-sonnet-4-5-20251219": {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    # Sonnet 4.6 (claude-sonnet-4-6 / claude-sonnet-4-6-20260601 - same tier)
    "claude-sonnet-4-6":          {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    # Haiku 4.5 — cheap-model regression baseline
    "claude-haiku-4-5":           {"input":  0.80, "output":  4.00, "cache_write":  1.00, "cache_read": 0.08},
    "claude-haiku-4-5-20251001":  {"input":  0.80, "output":  4.00, "cache_write":  1.00, "cache_read": 0.08},
}

# USD → CAD multiplier used in COST_REGRESSION.md (for display only)
USD_TO_CAD = 1.34


def tok_cost_usd(usage: dict[str, Any], rates: dict[str, float]) -> float:
    """Compute USD cost for a usage dict that may include cache fields."""
    inp    = usage.get("input_tokens", 0)
    out    = usage.get("output_tokens", 0)
    cw     = usage.get("cache_creation_input_tokens", 0)
    cr     = usage.get("cache_read_input_tokens", 0)
    # uncached input tokens = inp - cw - cr (though some SDKs report inp as the
    # uncached portion already). Use the explicit breakdown when available.
    if cw or cr:
        uncached_inp = inp
    else:
        uncached_inp = inp
    cost = (
        uncached_inp * rates["input"]
        + cw * rates["cache_write"]
        + cr * rates["cache_read"]
        + out * rates["output"]
    ) / 1_000_000
    return cost


def rates_for_model(model_id: str) -> dict[str, float] | None:
    """Look up pricing by exact model ID or by prefix match."""
    if model_id in PRICING:
        return PRICING[model_id]
    # Try prefix: "claude-opus-4-5" matches "claude-opus-4-5-20251101"
    for key, rates in PRICING.items():
        if model_id.startswith(key) or key.startswith(model_id):
            return rates
    return None


def summarise_case(transcript: dict[str, Any], rates: dict[str, float]) -> dict[str, Any]:
    """Roll up one TC-NNN.json transcript into per-case summary metrics."""
    turns = transcript.get("turns") or []
    total_input = total_output = total_cw = total_cr = 0
    total_iters = 0
    terminated_reasons: list[str] = []
    wall_s_total = 0.0
    errors = 0
    turns_with_metrics = 0

    for t in turns:
        if t.get("error"):
            errors += 1
            continue
        # Prefer tool_loop_metrics total_usage for full cost picture
        tlm = t.get("tool_loop_metrics") or {}
        if tlm:
            turns_with_metrics += 1
            u = tlm.get("total_usage") or {}
            total_input += u.get("input_tokens", 0)
            total_output += u.get("output_tokens", 0)
            total_cw += u.get("cache_creation_input_tokens", 0)
            total_cr += u.get("cache_read_input_tokens", 0)
            total_iters += tlm.get("iterations", 0)
            if tlm.get("terminated_reason"):
                terminated_reasons.append(tlm["terminated_reason"])
        else:
            # Fallback: SSE usage (only synthesis turn, under-counts true cost)
            u = t.get("usage") or {}
            total_input += u.get("input_tokens", 0)
            total_output += u.get("output_tokens", 0)
        wall_s_total += t.get("wall_time_s") or 0.0

    cost_usd = tok_cost_usd(
        {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_creation_input_tokens": total_cw,
            "cache_read_input_tokens": total_cr,
        },
        rates,
    )
    turns_completed = len([t for t in turns if not t.get("error")])
    reason_counts: dict[str, int] = {}
    for r in terminated_reasons:
        reason_counts[r] = reason_counts.get(r, 0) + 1

    # Cache effectiveness: what fraction of input was served from cache?
    total_billable_input = total_input + total_cw + total_cr
    cache_hit_rate = (total_cr / total_billable_input) if total_billable_input else 0.0

    return {
        "id": transcript.get("id"),
        "title": transcript.get("title"),
        "complexity": transcript.get("complexity"),
        "liability": transcript.get("liability"),
        "model": transcript.get("model"),
        "turns_completed": turns_completed,
        "turns_expected": len(turns),
        "total_iters": total_iters,
        "avg_iters_per_turn": round(total_iters / max(turns_with_metrics, 1), 1),
        "terminated_reasons": reason_counts,
        "total_input": total_input,
        "total_output": total_output,
        "total_cache_write": total_cw,
        "total_cache_read": total_cr,
        "cache_hit_rate": round(cache_hit_rate, 3),
        "cost_usd": round(cost_usd, 4),
        "wall_s": round(wall_s_total, 1),
        "has_tool_loop_metrics": turns_with_metrics > 0,
        "errors": errors,
    }


def load_run(run_dir: Path, default_rates: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Load all TC-*.json transcripts from a run directory."""
    transcripts = sorted(run_dir.glob("TC-*.json"))
    if not transcripts:
        raise SystemExit(f"No TC-*.json transcripts found in {run_dir}")
    results = []
    for tp in transcripts:
        transcript = json.loads(tp.read_text())
        model_id = transcript.get("model") or ""
        rates = rates_for_model(model_id) or default_rates or PRICING["claude-opus-4-5"]
        summary = summarise_case(transcript, rates)
        results.append(summary)
    return results


def load_quality(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Load verification/TC-*.verify.json quality scores if present."""
    verify_dir = run_dir / "verification"
    quality: dict[str, dict[str, Any]] = {}
    if not verify_dir.exists():
        return quality
    for vp in verify_dir.glob("TC-*.verify.json"):
        v = json.loads(vp.read_text())
        quality[v["id"]] = v.get("grade") or {}
    return quality


def fmt_reasons(d: dict[str, int]) -> str:
    if not d:
        return "—"
    return " ".join(f"{k}×{v}" for k, v in sorted(d.items()))


def compute_totals(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_input": sum(c["total_input"] for c in cases),
        "total_output": sum(c["total_output"] for c in cases),
        "total_cache_write": sum(c["total_cache_write"] for c in cases),
        "total_cache_read": sum(c["total_cache_read"] for c in cases),
        "cost_usd": round(sum(c["cost_usd"] for c in cases), 4),
        "wall_s": round(sum(c["wall_s"] for c in cases), 1),
        "total_iters": sum(c["total_iters"] for c in cases),
        "cases": len(cases),
    }


def build_report(
    baseline_dir: Path,
    candidate_dir: Path,
    baseline_label: str,
    candidate_label: str,
) -> str:
    base_cases = load_run(baseline_dir)
    cand_cases = load_run(candidate_dir)
    base_quality = load_quality(baseline_dir)
    cand_quality = load_quality(candidate_dir)

    base_by_id = {c["id"]: c for c in base_cases}
    cand_by_id = {c["id"]: c for c in cand_cases}
    all_ids = sorted(set(base_by_id) | set(cand_by_id))

    base_totals = compute_totals(base_cases)
    cand_totals = compute_totals(cand_cases)

    lines: list[str] = []

    def ln(s: str = "") -> None:
        lines.append(s)

    ln("# A/B Model Comparison Report — ABS-286")
    ln()
    ln(f"**Baseline:** `{baseline_label}` ({baseline_dir.name})")
    ln(f"**Candidate:** `{candidate_label}` ({candidate_dir.name})")
    ln()
    ln("## 1. Aggregate Cost Summary")
    ln()
    ln("| Metric | Baseline | Candidate | Δ |")
    ln("|--------|----------|-----------|---|")

    def pct_diff(a: float, b: float) -> str:
        if a == 0:
            return "n/a"
        d = (b - a) / a * 100
        sign = "+" if d > 0 else ""
        return f"{sign}{d:.0f}%"

    def tok_k(n: int) -> str:
        return f"{n/1000:.1f}k"

    bc = base_totals
    cc = cand_totals
    ln(f"| Cases run | {bc['cases']} | {cc['cases']} | — |")
    ln(f"| Total input tokens | {tok_k(bc['total_input'])} | {tok_k(cc['total_input'])} | {pct_diff(bc['total_input'], cc['total_input'])} |")
    ln(f"| Total output tokens | {tok_k(bc['total_output'])} | {tok_k(cc['total_output'])} | {pct_diff(bc['total_output'], cc['total_output'])} |")
    ln(f"| Cache write tokens | {tok_k(bc['total_cache_write'])} | {tok_k(cc['total_cache_write'])} | {pct_diff(bc['total_cache_write'], cc['total_cache_write'])} |")
    ln(f"| Cache read tokens | {tok_k(bc['total_cache_read'])} | {tok_k(cc['total_cache_read'])} | {pct_diff(bc['total_cache_read'], cc['total_cache_read'])} |")
    ln(f"| **Total cost (USD)** | **${bc['cost_usd']:.2f}** | **${cc['cost_usd']:.2f}** | **{pct_diff(bc['cost_usd'], cc['cost_usd'])}** |")
    cad_b = bc['cost_usd'] * USD_TO_CAD * 1.15  # +HST
    cad_c = cc['cost_usd'] * USD_TO_CAD * 1.15
    ln(f"| Total cost (CAD+HST) | ~${cad_b:.2f} | ~${cad_c:.2f} | {pct_diff(cad_b, cad_c)} |")
    ln(f"| Total iterations | {bc['total_iters']} | {cc['total_iters']} | {pct_diff(bc['total_iters'], cc['total_iters'])} |")
    ln(f"| Wall time (s) | {bc['wall_s']:.0f} | {cc['wall_s']:.0f} | {pct_diff(bc['wall_s'], cc['wall_s'])} |")
    ln()

    if bc['cases'] == cc['cases']:
        ln(f"**Cost per case (baseline):** ${bc['cost_usd']/bc['cases']:.2f} USD")
        ln(f"**Cost per case (candidate):** ${cc['cost_usd']/cc['cases']:.2f} USD")
        ratio = bc['cost_usd'] / cc['cost_usd'] if cc['cost_usd'] else float('inf')
        ln(f"**Cost ratio:** {ratio:.1f}× cheaper on candidate" if ratio > 1 else f"**Cost ratio:** {1/ratio:.1f}× more expensive on candidate")
    ln()

    # --- Terminated reason distribution ---
    ln("## 2. Tool Loop Metrics")
    ln()
    all_reason_keys: set[str] = set()
    for c in base_cases + cand_cases:
        all_reason_keys.update(c["terminated_reasons"].keys())
    if all_reason_keys:
        ln("### terminated_reason distribution")
        ln()
        ln("| Reason | Baseline count | Candidate count |")
        ln("|--------|---------------|-----------------|")
        reason_totals_b: dict[str, int] = {}
        reason_totals_c: dict[str, int] = {}
        for c in base_cases:
            for r, n in c["terminated_reasons"].items():
                reason_totals_b[r] = reason_totals_b.get(r, 0) + n
        for c in cand_cases:
            for r, n in c["terminated_reasons"].items():
                reason_totals_c[r] = reason_totals_c.get(r, 0) + n
        for r in sorted(all_reason_keys):
            ln(f"| `{r}` | {reason_totals_b.get(r, 0)} | {reason_totals_c.get(r, 0)} |")
        ln()
    ln()

    # --- Per-case comparison table ---
    ln("## 3. Per-Case Comparison")
    ln()
    ln("| TC | Complexity | Baseline cost | Candidate cost | Δ cost | Baseline iters | Candidate iters | Base terminated | Cand terminated |")
    ln("|-----|-----------|---------------|----------------|--------|---------------|-----------------|-----------------|-----------------|")

    for tc_id in all_ids:
        b = base_by_id.get(tc_id)
        c = cand_by_id.get(tc_id)
        complexity = (b or c or {}).get("complexity", "?")
        b_cost = f"${b['cost_usd']:.3f}" if b else "—"
        c_cost = f"${c['cost_usd']:.3f}" if c else "—"
        d_cost = pct_diff(b["cost_usd"], c["cost_usd"]) if (b and c) else "—"
        b_iters = str(b["total_iters"]) if b else "—"
        c_iters = str(c["total_iters"]) if c else "—"
        b_reason = fmt_reasons(b["terminated_reasons"]) if b else "—"
        c_reason = fmt_reasons(c["terminated_reasons"]) if c else "—"
        ln(f"| {tc_id} | {complexity} | {b_cost} | {c_cost} | {d_cost} | {b_iters} | {c_iters} | {b_reason} | {c_reason} |")
    ln()

    # --- Quality metrics if verification data is available ---
    if base_quality or cand_quality:
        ln("## 4. Quality Comparison")
        ln()
        ln("| TC | Complexity | Baseline verdict | Candidate verdict | Base kw% | Cand kw% | Base hallu | Cand hallu |")
        ln("|-----|-----------|-----------------|-------------------|----------|----------|------------|------------|")
        for tc_id in all_ids:
            bq = base_quality.get(tc_id) or {}
            cq = cand_quality.get(tc_id) or {}
            complexity = (base_by_id.get(tc_id) or cand_by_id.get(tc_id) or {}).get("complexity", "?")
            b_verdict = bq.get("verdict", "—")
            c_verdict = cq.get("verdict", "—")
            b_kw = f"{bq['keyword_rate']:.0%}" if bq.get("keyword_rate") is not None else "—"
            c_kw = f"{cq['keyword_rate']:.0%}" if cq.get("keyword_rate") is not None else "—"
            b_hallu = str(bq.get("citation_hallucinated", "—"))
            c_hallu = str(cq.get("citation_hallucinated", "—"))
            ln(f"| {tc_id} | {complexity} | {b_verdict} | {c_verdict} | {b_kw} | {c_kw} | {b_hallu} | {c_hallu} |")
        ln()

        # Quality summary
        ln("### Quality verdict summary")
        ln()
        b_pass = sum(1 for q in base_quality.values() if q.get("verdict") == "PASS")
        c_pass = sum(1 for q in cand_quality.values() if q.get("verdict") == "PASS")
        b_hallu_total = sum(q.get("citation_hallucinated", 0) for q in base_quality.values())
        c_hallu_total = sum(q.get("citation_hallucinated", 0) for q in cand_quality.values())
        b_q_total = len(base_quality)
        c_q_total = len(cand_quality)
        ln(f"| Metric | Baseline | Candidate |")
        ln(f"|--------|----------|-----------|")
        ln(f"| PASS | {b_pass}/{b_q_total} | {c_pass}/{c_q_total} |")
        ln(f"| Total hallucinated citations | {b_hallu_total} | {c_hallu_total} |")
        ln()
    else:
        ln("## 4. Quality Comparison")
        ln()
        ln("*No verification data found. Run `scripts/verify_run.py` on both")
        ln("run directories (requires the dev DB, or pass `--corpus-json`) to")
        ln("populate quality scores. The scores below are the advisory tier only;")
        ln("the golden tier is what gates a deploy.*")
        ln()

    # --- Verdict ---
    ln("## 5. Verdict")
    ln()
    if cand_totals["cases"] == 0:
        ln("*Candidate run not yet executed.*")
    elif base_totals["cases"] == 0:
        ln("*Baseline run not yet executed.*")
    else:
        ratio = bc['cost_usd'] / cc['cost_usd'] if cc['cost_usd'] else float('inf')
        ln(f"- Cost ratio: **{ratio:.1f}×** cheaper on candidate vs baseline (${cc['cost_usd']:.2f} vs ${bc['cost_usd']:.2f} USD)")
        if cand_quality and base_quality:
            b_pass = sum(1 for q in base_quality.values() if q.get("verdict") == "PASS")
            c_pass = sum(1 for q in cand_quality.values() if q.get("verdict") == "PASS")
            b_hallu_total = sum(q.get("citation_hallucinated", 0) for q in base_quality.values())
            c_hallu_total = sum(q.get("citation_hallucinated", 0) for q in cand_quality.values())
            ln(f"- PASS rate: baseline {b_pass}/{len(base_quality)}, candidate {c_pass}/{len(cand_quality)}")
            ln(f"- Hallucinations: baseline {b_hallu_total}, candidate {c_hallu_total}")
            if c_hallu_total > b_hallu_total:
                ln()
                ln("**Recommendation: KEEP OPUS** — candidate introduced hallucinations.")
            elif c_pass >= b_pass and ratio > 2.0:
                ln()
                ln("**Recommendation: SWITCH TO SONNET** — quality holds, cost materially lower.")
            elif ratio > 2.0:
                ln()
                ln("**Recommendation: REVIEW** — cost win is significant but quality degraded; "
                   "weigh acceptable tradeoffs per complexity tier.")
            else:
                ln()
                ln("**Recommendation: MARGINAL** — cost difference does not justify a model switch.")
        else:
            ln()
            ln("*Run `scripts/verify_run.py` on both dirs to get quality scores before "
               "making a recommendation.*")
    ln()
    ln("---")
    ln(f"*Generated by `scripts/compare_ab_runs.py`*")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two run_test_prompts.py output directories."
    )
    parser.add_argument("--baseline", required=True, help="Path to baseline run directory")
    parser.add_argument("--candidate", required=True, help="Path to candidate run directory")
    parser.add_argument(
        "--baseline-label",
        default=None,
        help="Human label for baseline (default: model from first transcript)",
    )
    parser.add_argument(
        "--candidate-label",
        default=None,
        help="Human label for candidate (default: model from first transcript)",
    )
    parser.add_argument("--output-md", help="Write Markdown report to this path")
    args = parser.parse_args()

    baseline_dir = Path(args.baseline).resolve()
    candidate_dir = Path(args.candidate).resolve()

    def infer_label(run_dir: Path, provided: str | None) -> str:
        if provided:
            return provided
        for tp in sorted(run_dir.glob("TC-*.json")):
            t = json.loads(tp.read_text())
            m = t.get("model")
            if m:
                return m
        return run_dir.name

    baseline_label = infer_label(baseline_dir, args.baseline_label)
    candidate_label = infer_label(candidate_dir, args.candidate_label)

    report = build_report(baseline_dir, candidate_dir, baseline_label, candidate_label)
    print(report)

    if args.output_md:
        out = Path(args.output_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report)
        print(f"\nReport written to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
