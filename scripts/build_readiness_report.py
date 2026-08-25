#!/usr/bin/env python3
"""
Compose the production-readiness Markdown report for an ABS-260 run.

Reads:
  evals/runs/<ts>/SUMMARY.json
  evals/runs/<ts>/verification/SUMMARY.json
  evals/runs/<ts>/verification/TC-*.verify.json
  evals/runs/<ts>/verification/GOLDEN_SUMMARY.json  (optional, ABS-468)

Writes:
  evals/runs/<ts>/REPORT.md

Two tiers of evidence, reported separately and never summed (ABS-468). The
ABS-260 threshold below is computed over generated cases, whose expected
answers were authored by ``claude -p`` — a model of the family under test — so
it measures agreement with a model's guess, not correctness under the by-law.
It is advisory. The gating verdict comes from the golden subset, whose answers
a qualified human recorded; if a run has no GOLDEN_SUMMARY.json the deploy gate
is closed, because nothing in the run speaks to correctness at all.

The report contains:
  - Headline go/no-go vs the ABS-260 threshold (advisory).
  - The golden-subset gate state (blocking).
  - Per-case verdict table.
  - Hallucinated citation index (if any).
  - Top keyword-miss patterns (which expected facts the advisor
    most often failed to surface).
  - Notable failure modes (e.g. spatial-zone override of user-stated
    zone) called out narratively.

Usage:
  python scripts/build_readiness_report.py evals/runs/<ts>
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


VERDICT_GLYPH = {
    "PASS": "✅",
    "PARTIAL": "⚠️",
    "FAIL": "❌",
    "FAIL_HALLUCINATION": "🚨",
    "NO_DATA": "⛔",
}


def load_json(p: Path) -> Any:
    return json.loads(p.read_text())


def fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x*100:.0f}%"


def threshold_verdict(summary: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Apply the ABS-260 strict threshold:

    - Zero hallucinated citations across the sweep (hard fail).
    - 100% PASS on complexity=simple.
    - ≥85% PASS on complexity=medium AND complexity=complex.
    """
    notes: list[str] = []

    total_hallu = sum(s.get("hallucinated") or 0 for s in summary)
    if total_hallu > 0:
        notes.append(f"{total_hallu} hallucinated citation(s) across the sweep — hard fail.")
        return ("NO-GO", notes)

    by_complexity: dict[str, list[dict[str, Any]]] = {}
    for s in summary:
        by_complexity.setdefault(s.get("complexity") or "unknown", []).append(s)

    def pass_rate(rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0
        return sum(1 for r in rows if r["verdict"] == "PASS") / len(rows)

    simple_rate = pass_rate(by_complexity.get("simple", []))
    medium_rate = pass_rate(by_complexity.get("medium", []))
    complex_rate = pass_rate(by_complexity.get("complex", []))

    notes.append(f"simple PASS rate: {simple_rate*100:.0f}% (target 100%)")
    notes.append(f"medium PASS rate: {medium_rate*100:.0f}% (target ≥85%)")
    notes.append(f"complex PASS rate: {complex_rate*100:.0f}% (target ≥85%)")

    if simple_rate < 1.0:
        return ("NO-GO", notes + [f"simple cases below 100% bar"])
    if medium_rate < 0.85:
        return ("NO-GO", notes + ["medium cases below 85% bar"])
    if complex_rate < 0.85:
        return ("NO-GO", notes + ["complex cases below 85% bar"])
    return ("GO", notes)


GOLDEN_GLYPH = {
    "GOLDEN_PASS": "✅",
    "GOLDEN_PARTIAL": "⚠️",
    "GOLDEN_FAIL": "❌",
    "UNATTESTED": "⬜",
    "NO_TRANSCRIPT": "⛔",
}


def golden_section(run_dir: Path) -> list[str]:
    """Render the human-validated tier, or say plainly that there isn't one.

    Deliberately its own block with its own counts. An earlier version of this
    report had one headline number, and one number is exactly how a
    generated-case pass rate gets read as a correctness measure (ABS-468).
    """
    path = run_dir / "verification" / "GOLDEN_SUMMARY.json"
    lines = ["## Golden subset — human-validated (gating)", ""]
    if not path.exists():
        lines += [
            (
                "**Not graded for this run.** The deploy gate is CLOSED: nothing "
                "in this run was checked against an answer a qualified human "
                "recorded. Run `python scripts/verify_run.py <run_dir>`, which "
                "grades both tiers."
            ),
            "",
        ]
        return lines

    golden = load_json(path)
    gate = golden.get("gate") or {}
    cases = golden.get("cases") or []
    lines.append(f"**Gate ({gate.get('gates', 'production_deploy')}):** "
                 f"**{'OPEN' if gate.get('open') else 'CLOSED'}**")
    for blocker in gate.get("blockers") or []:
        lines.append(f"- {blocker}")
    lines.append("")
    lines.append("| Case | Verdict | Zone | Liability | Answer shape | Reasons |")
    lines.append("|---|---|---|---|---|---|")
    for c in cases:
        glyph = GOLDEN_GLYPH.get(c["verdict"], "?")
        lines.append(
            f"| {c['case_id']} | {glyph} {c['verdict']} | {c.get('zone') or ''} | "
            f"{c.get('liability') or ''} | {c.get('answer_shape') or ''} | "
            f"{'; '.join(c.get('reasons') or [])} |"
        )
    lines.append("")
    lines.append(
        "These counts are never added to the generated-case counts above. A "
        "golden case tests whether the advisor is right; a generated case tests "
        "whether it agrees with a model."
    )
    lines.append("")
    return lines


def collect_keyword_misses(verify_files: list[Path]) -> Counter[str]:
    c: Counter[str] = Counter()
    for vf in verify_files:
        v = load_json(vf)
        for t in v.get("turns") or []:
            for kw in (t.get("keyword_hits") or {}).get("misses") or []:
                c[kw] += 1
    return c


def collect_hallucinations(verify_files: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for vf in verify_files:
        v = load_json(vf)
        for t in v.get("turns") or []:
            for c in t.get("citations") or []:
                if not c.get("found"):
                    out.append({
                        "case_id": v["id"],
                        "turn": t.get("turn"),
                        "kind": c["kind"],
                        "value": c["value"],
                        "raw": c["raw"],
                    })
    return out


def collect_hedge_failures(verify_files: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for vf in verify_files:
        v = load_json(vf)
        if v.get("liability") != "high":
            continue
        for t in v.get("turns") or []:
            if t.get("hedging_required") and not t.get("hedging_ok"):
                out.append({
                    "case_id": v["id"],
                    "turn": t.get("turn"),
                })
    return out


def build_report(run_dir: Path) -> str:
    run_summary = load_json(run_dir / "SUMMARY.json")
    verify_summary = load_json(run_dir / "verification" / "SUMMARY.json")
    verify_files = sorted((run_dir / "verification").glob("TC-*.verify.json"))

    by_id = {s["id"]: s for s in run_summary}

    overall, notes = threshold_verdict(verify_summary)

    # Aggregates.
    n = len(verify_summary)
    n_pass = sum(1 for s in verify_summary if s["verdict"] == "PASS")
    n_partial = sum(1 for s in verify_summary if s["verdict"] == "PARTIAL")
    n_fail = sum(1 for s in verify_summary if s["verdict"] in ("FAIL", "FAIL_HALLUCINATION"))
    total_cites = sum(s.get("citation_total") or 0 for s in verify_summary)
    total_found = sum(s.get("citation_found") or 0 for s in verify_summary)
    total_hallu = sum(s.get("hallucinated") or 0 for s in verify_summary)

    lines: list[str] = []
    lines.append("# ABS-260 — Production-Readiness Sweep Report")
    lines.append("")
    lines.append(f"**Run directory:** `{run_dir}`")
    lines.append(f"**Generated-case verdict (advisory):** **{overall}**")
    lines.append("")
    lines.append(
        "> The verdict above is computed against `regional_centre_test_prompts.json`, "
        "whose expected answers were authored by `claude -p` — a model of the family "
        "under test. It measures agreement with a model's guess, not correctness "
        "under the by-law, and it gates nothing. The blocking verdict is the golden "
        "subset below (ABS-468)."
    )
    lines.append("")
    lines.extend(golden_section(run_dir))
    lines.append("## Threshold check (generated cases)")
    lines.append("")
    for note in notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## Headline metrics (generated cases — advisory)")
    lines.append("")
    lines.append(f"- **Cases run:** {n}")
    lines.append(f"- **PASS:** {n_pass}   **PARTIAL:** {n_partial}   **FAIL:** {n_fail}")
    lines.append(f"- **Citations cross-checked:** {total_found} / {total_cites} grounded in source ({fmt_pct(total_found/total_cites if total_cites else None)})")
    lines.append(f"- **Hallucinated citations:** {total_hallu}")
    lines.append("")

    # Per-case table.
    lines.append("## Per-case verdicts (generated cases — advisory)")
    lines.append("")
    lines.append("| ID | Verdict | Zone | Complexity | Liability | Kw rate | Cites (found/total) | Notes |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for s in verify_summary:
        glyph = VERDICT_GLYPH.get(s["verdict"], "?")
        reason = "; ".join(s.get("reasons") or []) or ""
        wall = (by_id.get(s["id"]) or {}).get("wall_s")
        kw = fmt_pct(s.get("kw_rate"))
        lines.append(
            f"| {s['id']} | {glyph} {s['verdict']} | {s.get('zone') or ''} | "
            f"{s.get('complexity') or ''} | {s.get('liability') or ''} | "
            f"{kw} | {s.get('citation_found')}/{s.get('citation_total')} | {reason} |"
        )
    lines.append("")

    # Hallucinated citations index.
    halls = collect_hallucinations(verify_files)
    lines.append("## Hallucinated citations")
    lines.append("")
    if halls:
        lines.append("Citations the advisor referenced that did NOT resolve in the source bylaw "
                     "(`document_id=4`, Halifax Regional Centre LUB):")
        lines.append("")
        lines.append("| Case | Turn | Kind | Value | Raw text |")
        lines.append("|---|---|---|---|---|")
        for h in halls:
            lines.append(f"| {h['case_id']} | T{h['turn']} | {h['kind']} | {h['value']} | `{h['raw']}` |")
    else:
        lines.append("**None.** Every citation the advisor produced resolved to a real fragment "
                     "in the source bylaw.")
    lines.append("")

    # Hedging failures.
    hedge = collect_hedge_failures(verify_files)
    lines.append("## Hedging failures (high-liability turns)")
    lines.append("")
    if hedge:
        lines.append("Turns on high-liability cases that did not flag the user to confirm with a "
                     "professional / HRM:")
        lines.append("")
        for h in hedge:
            lines.append(f"- {h['case_id']} T{h['turn']}")
    else:
        lines.append("**None.** Every high-liability turn included appropriate hedging language.")
    lines.append("")

    # Top keyword misses.
    misses = collect_keyword_misses(verify_files)
    lines.append("## Most-frequent expected-keyword misses")
    lines.append("")
    if misses:
        lines.append("These spec-expected keywords appeared in the test prompts' "
                     "`expected_answer_keywords` but NOT in the advisor's response. "
                     "High counts may indicate either advisor gaps OR overly-rigid "
                     "test expectations (e.g. spatial lookup overrode the test's "
                     "assumed zone).")
        lines.append("")
        lines.append("| Keyword | Miss count |")
        lines.append("|---|---|")
        for kw, c in misses.most_common(20):
            lines.append(f"| `{kw}` | {c} |")
    else:
        lines.append("All expected keywords were surfaced.")
    lines.append("")

    # Per-case detail links.
    lines.append("## Per-case transcripts and verifications")
    lines.append("")
    for s in verify_summary:
        lines.append(f"- **{s['id']}** — `{s['id']}.json` (transcript) · "
                     f"`verification/{s['id']}.verify.json` (graded)")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    report = build_report(run_dir)
    out_path = run_dir / "REPORT.md"
    out_path.write_text(report)
    print(f"Report written: {out_path}")


if __name__ == "__main__":
    main()
