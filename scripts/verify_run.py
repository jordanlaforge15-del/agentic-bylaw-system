#!/usr/bin/env python3
"""
Grade an eval run. One command, two tiers, the gating one first (ABS-516).

Why this exists
---------------
There were two graders for one suite and they disagreed. On the same eight
transcripts, ``verify_test_prompts.py`` reported 5 PASS / 3 PARTIAL / 0 FAIL and
``verify_golden_cases.py`` reported 3 PASS / 1 PARTIAL / 4 FAIL. Nothing forced
a caller to run both and neither output mentioned the other, so running one and
reporting "the tests pass" was the default outcome rather than an edge case — it
is what happened in the ``zone-typology-all8`` run on the
``docs/zone-typology-test-questions`` branch, where the advisory grader
ran first, reported no failures at all, and five cases were failing the human
standard.

The split itself is right (``evals/golden/README.md``): model-authored
expectations and a professional's answer are different kinds of evidence and
must never be summed, averaged, or reported as one pass rate. The defect was two
entry points. This is one entry point with two labelled sections.

What it guarantees
------------------
* The golden tier is printed **first** and labelled as the one that gates.
* The generated tier is printed second and labelled advisory.
* The two are never added together — no combined count, no combined verdict, and
  ``RUN_SUMMARY.json`` keeps them under separate keys with no total.
* **Exit status comes from the golden tier alone.** A perfect advisory sweep
  cannot open the gate and a failing one cannot close it.
* Unattested golden entries are called out in their own block. "Nobody has
  recorded the right answer yet" is not "all passing".

Exit codes: 0 the deploy gate is open, 1 it is closed, 2 the command could not
grade the run (bad path, malformed golden file).

Usage:
  python scripts/verify_run.py evals/runs/<ts>
  python scripts/verify_run.py evals/runs/<ts> --corpus-json evals/fixtures/abs462_corpus_snapshot.json
  python scripts/verify_run.py evals/runs/<ts> --spec-source transcript
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import verify_golden_cases as golden_tier
from scripts import verify_test_prompts as generated_tier

# Each tier's verdicts, bucketed for display. The vocabularies are disjoint by
# construction (ABS-468) and stay that way here: a bucket is only ever filled
# from one tier's verdicts, so no arithmetic can cross the line.
GOLDEN_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("PASS", ("GOLDEN_PASS",)),
    ("PARTIAL", ("GOLDEN_PARTIAL",)),
    ("FAIL", ("GOLDEN_FAIL",)),
    ("UNATTESTED", ("UNATTESTED",)),
    ("NO_TRANSCRIPT", ("NO_TRANSCRIPT",)),
]
GENERATED_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("PASS", ("PASS",)),
    ("PARTIAL", ("PARTIAL",)),
    ("FAIL", ("FAIL", "FAIL_HALLUCINATION", "FAIL_APPLICABILITY")),
    ("NO_DATA", ("NO_DATA",)),
]
# Buckets that are always shown even at zero — a report that silently omits
# "0 FAIL" reads differently from one that states it.
ALWAYS_SHOWN = {"PASS", "PARTIAL", "FAIL"}

RULE = "=" * 78

TIER_NOTE = (
    "The two tiers are never summed, averaged, or reported as one pass rate. "
    "Golden is human-attested and gates the deploy; generated is model-authored "
    "and gates nothing."
)


def _bucket(
    verdicts: list[str], buckets: list[tuple[str, tuple[str, ...]]]
) -> dict[str, int]:
    counts = {label: 0 for label, _ in buckets}
    counts["OTHER"] = 0
    for verdict in verdicts:
        for label, members in buckets:
            if verdict in members:
                counts[label] += 1
                break
        else:
            counts["OTHER"] += 1
    return counts


def _counts_line(counts: dict[str, int]) -> str:
    parts = [
        f"{counts[label]} {label}"
        for label in counts
        if label in ALWAYS_SHOWN or counts[label]
    ]
    return "  ".join(parts)


def _header(label: str, counts: dict[str, int], suffix: str) -> str:
    return f"{label:<41} {_counts_line(counts):<34} {suffix}"


def report(
    golden_summary: dict[str, Any] | None,
    generated_rows: list[dict[str, Any]] | None,
    generated_error: str | None,
    run_dir: Path,
    out: Any = sys.stdout,
) -> dict[str, Any]:
    """Print the two-tier report and return the machine-readable summary."""
    lines: list[str] = [RULE, f"GRADE  {run_dir}", RULE]

    # ── Tier 1: the one that gates ───────────────────────────────────────────
    golden_cases = (golden_summary or {}).get("cases") or []
    golden_counts = _bucket([c["verdict"] for c in golden_cases], GOLDEN_BUCKETS)
    gate = (golden_summary or {}).get("gate") or {
        "open": False,
        "blockers": ["the golden tier could not be graded"],
    }
    gate_label = "[GATE: OPEN]" if gate["open"] else "[GATE: CLOSED]"
    lines.append(_header("GOLDEN (human-attested, gates deploy)", golden_counts, gate_label))

    # ── Tier 2: advisory, printed second and never added to the above ────────
    generated_counts = _bucket(
        [r.get("verdict") or "" for r in (generated_rows or [])], GENERATED_BUCKETS
    )
    if generated_error:
        lines.append(
            f"{'GENERATED (model-authored, advisory)':<41} "
            f"{'not graded: ' + generated_error:<34} [gates nothing]"
        )
    else:
        lines.append(
            _header("GENERATED (model-authored, advisory)", generated_counts, "[gates nothing]")
        )
    lines.append("")

    # ── Unattested is not "passing" ──────────────────────────────────────────
    unattested = [c["case_id"] for c in golden_cases if c["verdict"] == "UNATTESTED"]
    if unattested:
        lines += [
            f"!! {len(unattested)} of {len(golden_cases)} golden entries are UNATTESTED: "
            + ", ".join(unattested),
            "   No qualified human has recorded the correct answer for these cases, so",
            "   they cannot pass and they hold the deploy gate closed. Whatever the",
            "   advisory numbers say, this run has demonstrated nothing about",
            "   correctness. See evals/golden/README.md to fill in an attestation.",
            "",
        ]

    # ── Detail, golden first ─────────────────────────────────────────────────
    lines.append("GOLDEN — human-attested, gates the deploy")
    if not golden_cases:
        lines.append("  (no golden cases)")
    for case in golden_cases:
        lines.append(f"  {case['case_id']:<10} {case['verdict']}")
        for reason in case.get("reasons") or []:
            lines.append(f"             - {reason}")
    lines.append("")

    lines.append("GENERATED — model-authored, advisory only, gates nothing")
    if generated_error:
        lines.append(f"  (not graded: {generated_error})")
    elif not generated_rows:
        lines.append("  (no transcripts)")
    else:
        for row in generated_rows:
            lines.append(f"  {row.get('id'):<10} {row.get('verdict')}")
            for reason in row.get("reasons") or []:
                lines.append(f"             - {reason}")
    lines.append("")

    # ── The verdict, and what drove it ───────────────────────────────────────
    lines.append(RULE)
    if gate["open"]:
        lines.append("DEPLOY GATE: OPEN — every golden entry is attested and passes.")
    else:
        lines.append("DEPLOY GATE: CLOSED")
        for blocker in gate.get("blockers") or []:
            lines.append(f"  - {blocker}")
    lines.append(
        "Exit status is set by the golden tier alone; the advisory tier cannot "
        "open or close this gate."
    )
    lines.append(RULE)
    print("\n".join(lines), file=out)

    return {
        "schema_version": 1,
        "entry_point": "scripts/verify_run.py",
        "run_dir": str(run_dir),
        "note": TIER_NOTE,
        "gating": {
            "evidence_tier": "human_validated",
            "counts": golden_counts,
            "gate": gate,
            "cases": golden_cases,
        },
        "advisory": {
            "evidence_tier": "generated",
            "gates": None,
            "counts": generated_counts,
            "error": generated_error,
            "cases": [
                {
                    "id": r.get("id"),
                    "verdict": r.get("verdict"),
                    "reasons": r.get("reasons") or [],
                }
                for r in (generated_rows or [])
            ],
        },
        "gate_open": bool(gate["open"]),
        "exit_driven_by": "human_validated",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grade an eval run: golden (gating) then generated (advisory).",
    )
    parser.add_argument("run_dir", help="Path to evals/runs/<ts>/")
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL_PLAIN", generated_tier.DEFAULT_DB_URL),
    )
    parser.add_argument(
        "--corpus-json",
        help="Grade against a corpus snapshot instead of the layer1 DB (no database).",
    )
    parser.add_argument("--golden", default=str(golden_tier.DEFAULT_GOLDEN_FILE))
    parser.add_argument(
        "--prompts",
        default=str(generated_tier.DEFAULT_PROMPTS_FILE),
        help="Generated eval file: the expectations the advisory tier grades "
             "against, and the case list golden case_ids must exist in.",
    )
    parser.add_argument(
        "--spec-source",
        choices=("prompts", "transcript"),
        default="prompts",
        help="Where the advisory tier's expectations come from. 'transcript' "
             "uses the copy frozen into the run, which may be stale.",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"Run dir not found: {run_dir}", file=sys.stderr)
        return 2

    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"Golden file not found: {golden_path}", file=sys.stderr)
        return 2
    try:
        payload = golden_tier.load_and_validate(golden_path, Path(args.prompts))
    except ValueError as exc:
        # A malformed golden file is a refusal, not a closed gate: half-grading
        # the gating tier and printing a number would be worse than stopping.
        print(str(exc), file=sys.stderr)
        return 2

    corpus, conn = generated_tier.open_corpus(args.corpus_json, args.db_url)
    generated_error: str | None = None
    generated_rows: list[dict[str, Any]] = []
    try:
        print("\n--- golden tier (gating) ---", file=sys.stderr)
        golden_summary = golden_tier.grade_run(
            run_dir, corpus, payload, golden_path=golden_path
        )
        print("\n--- generated tier (advisory) ---", file=sys.stderr)
        try:
            generated_rows = generated_tier.grade_run(
                run_dir,
                corpus,
                prompts_file=Path(args.prompts) if args.prompts else None,
                spec_source=args.spec_source,
            )
        except FileNotFoundError as exc:
            # An advisory tier that cannot run is a note in the report, never a
            # reason to withhold the gating verdict.
            generated_error = str(exc)
            print(generated_error, file=sys.stderr)
    finally:
        if conn is not None:
            conn.close()

    summary = report(golden_summary, generated_rows, generated_error, run_dir)
    summary_path = run_dir / "verification" / "RUN_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nRun summary written to {summary_path}", file=sys.stderr)

    return 0 if summary["gate_open"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
