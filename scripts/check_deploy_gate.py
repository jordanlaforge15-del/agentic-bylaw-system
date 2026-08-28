#!/usr/bin/env python3
"""
The golden-case deploy gate, as something a pipeline can actually run (ABS-485).

Why this exists
---------------
The gate rule was written down twice — in ``evals/golden/golden_cases.json``'s
``gate`` block ("A production deploy requires every entry here to be attested
and to grade GOLDEN_PASS") and in ``evals/golden/README.md`` — and enforced by
nothing. No CI job referenced the golden subset, and neither the
``deploy-bylaw`` nor the ``test-and-deploy-bylaw`` skill ran a grader. A rule
that only exists in prose is a rule the next release ignores, which is how a
known-wrong answer ended up live (``docs/data-gaps/abs461-production-impact.md``).

``scripts/verify_run.py`` already answers "did *this run* pass?" — but it needs
a run, and a run costs metered API spend. A promotion pipeline needs a different
question answered cheaply and in one exit code:

    May this release be promoted?

That is what this script answers. It is the step the skills call.

The two conditions
------------------
1. **Attested.** Every entry in the golden subset must carry a qualified human's
   attestation. This is a file check: no run, no database, no API spend. Today
   every entry is ``unattested``, so the gate is HELD and this script exits 1 —
   which is the designed behaviour, not a bug to route around. Nothing in this
   repo may author an attestation; see ``evals/golden/README.md``.

2. **Graded and passing.** Once attested, an attestation that has never been
   graded against the advisor proves nothing, so the gate additionally requires
   a ``verification/GOLDEN_SUMMARY.json`` from a real run whose gate is open.
   The summary records the SHA-256 of the golden file it graded, so a grade
   produced against a *different* golden file — anything edited after the run —
   cannot open the gate for the edited one.

The two conditions fail differently and the output says which, because the
operator's next move is completely different: condition 1 is "a human has work
to do", condition 2 is "the advisor is wrong and the code needs a fix".

Exit codes
----------
0   gate OPEN — every entry attested, and a matching run graded them all PASS.
1   gate HELD — promotion must not proceed. The reason is printed.
2   usage error — the gate could not be evaluated at all (missing or malformed
    golden file, run directory that does not exist). Deliberately distinct from
    1: "I could not check" is not "I checked and it failed", and a pipeline that
    conflates them will eventually treat a typo as a passing gate.

Usage
-----
  python scripts/check_deploy_gate.py                    # the pre-promotion gate
  python scripts/check_deploy_gate.py --run-dir evals/runs/<ts>
  python scripts/check_deploy_gate.py --json             # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

# These four are file-and-hash helpers, but the module they live in is shared
# with the grader. It must therefore stay importable under a base
# ``pip install -e "."`` — no ``[advisor]`` extra, no FastAPI, no database. That
# is a live constraint, not a preference: this gate runs on the leanest runner
# in CI, and if this import raises the script exits non-zero *without having
# evaluated anything*, which is indistinguishable from a held gate. See the
# ABS-485 note at the top of scripts/verify_golden_cases.py.
from scripts.verify_golden_cases import (
    DEFAULT_GOLDEN_FILE,
    golden_file_digest,
    load_golden,
    validate_golden,
)

DEFAULT_RUNS_ROOT = REPO_ROOT / "evals" / "runs"

EXIT_OPEN = 0
EXIT_HELD = 1
EXIT_USAGE = 2

ATTESTATION_PROCEDURE = (
    "evals/golden/README.md § 'Filling in an attestation' is the procedure. The "
    "attestation must come from someone qualified to give the answer "
    "professionally — a planner, a municipal reviewer, the pilot's architect. "
    "Not an engineer on this project, and NOT a model: an attestation a model "
    "drafted is not an attestation, and backfilling one to open this gate "
    "destroys the only non-model ground truth the project has."
)

RULE = "=" * 78


class GateError(Exception):
    """The gate could not be evaluated. Exits 2, never 1."""


# ---------------------------------------------------------------------------
# The two conditions
# ---------------------------------------------------------------------------


def attestation_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Which entries a qualified human has recorded an answer for.

    Condition 1, and the only one that can be checked with no run and no
    database — which is what makes it usable as a pre-promotion step rather than
    a post-hoc report.
    """
    cases = payload.get("cases") or []
    unattested = [
        c.get("case_id")
        for c in cases
        if (c.get("attestation") or {}).get("status") != "attested"
    ]
    return {
        "total": len(cases),
        "attested": len(cases) - len(unattested),
        "unattested": unattested,
    }


def find_graded_run(
    digest: str, runs_root: Path, explicit: Path | None = None
) -> dict[str, Any]:
    """Locate a GOLDEN_SUMMARY.json that graded *this* golden file.

    The digest match is the load-bearing part. Without it the sequence
    "attest → grade → green → edit an attestation → promote" reads as gated,
    because the stale summary on disk still says the gate was open. It graded a
    different file.

    Summaries written before ABS-485 carry no digest; they are reported as
    unverifiable rather than trusted, for the same reason.
    """
    if explicit is not None:
        summary_path = explicit / "verification" / "GOLDEN_SUMMARY.json"
        if not summary_path.exists():
            raise GateError(
                f"{explicit} has no verification/GOLDEN_SUMMARY.json — it has not "
                "been graded. Grade it with `python scripts/verify_run.py "
                f"{explicit}`."
            )
        candidates = [summary_path]
    else:
        candidates = sorted(
            runs_root.glob("*/verification/GOLDEN_SUMMARY.json"), reverse=True
        )

    mismatched = 0
    undigested = 0
    for path in candidates:
        try:
            summary = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        recorded = summary.get("golden_file_sha256")
        if recorded is None:
            undigested += 1
            continue
        if recorded != digest:
            mismatched += 1
            continue
        return {"found": True, "summary": summary, "path": path}
    return {
        "found": False,
        "summary": None,
        "path": None,
        "mismatched": mismatched,
        "undigested": undigested,
        "searched": len(candidates),
    }


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def evaluate_gate(
    golden_path: Path,
    *,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Decide whether promotion may proceed. Raises :class:`GateError` (exit 2)
    when the question cannot be answered at all.

    ``reason_code`` is the field a caller should branch on; ``explanation`` is
    the field a human should read. They are separate because the two audiences
    need different things and collapsing them produced the status quo: a rule
    stated in prose that no pipeline could act on.
    """
    if not golden_path.exists():
        raise GateError(f"Golden file not found: {golden_path}")
    try:
        payload = load_golden(golden_path)
    except (json.JSONDecodeError, ValueError) as exc:
        raise GateError(f"{golden_path} could not be read: {exc}") from exc

    problems = validate_golden(payload)
    if problems:
        # A malformed golden file is a refusal, not a held gate: the file that
        # defines the standard is itself broken, and reporting "gate closed"
        # would send the operator hunting for a failing case that does not exist.
        raise GateError(
            f"{golden_path} has {len(problems)} structural problem(s); the gate "
            "cannot be evaluated against it:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    digest = golden_file_digest(golden_path)
    att = attestation_status(payload)
    base = {
        "golden_file": _display_path(golden_path),
        "golden_file_sha256": digest,
        "attestation": att,
    }

    if att["total"] == 0:
        return {
            **base,
            "open": False,
            "reason_code": "empty_golden_subset",
            "explanation": (
                "The golden subset is empty, so there is no human-validated "
                "evidence that anything is correct. An empty gate is an open "
                "gate, which is why this is held."
            ),
            "next_step": ATTESTATION_PROCEDURE,
        }

    if att["unattested"]:
        return {
            **base,
            "open": False,
            "reason_code": "unattested",
            "explanation": (
                f"{len(att['unattested'])} of {att['total']} golden entries are "
                "UNATTESTED: " + ", ".join(att["unattested"]) + ". No qualified "
                "human has recorded the correct answer for these cases, so no "
                "run can pass them and nothing has been demonstrated about "
                "correctness. This is a HOLD, not a FAILURE — the advisor has "
                "not been graded wrong; it has not been graded at all."
            ),
            "next_step": ATTESTATION_PROCEDURE,
        }

    # find_graded_run raises GateError for an explicit run dir that was never
    # graded; that propagates to exit 2 rather than being reported as a closed
    # gate, because the operator named a run this script could not check.
    located = find_graded_run(digest, runs_root, run_dir)
    if not located["found"]:
        detail = ""
        if located.get("mismatched") or located.get("undigested"):
            detail = (
                f" ({located.get('mismatched', 0)} graded run(s) used a different "
                f"golden file and {located.get('undigested', 0)} predate the "
                "digest, so neither can vouch for this one.)"
            )
        return {
            **base,
            "open": False,
            "reason_code": "no_graded_run",
            "explanation": (
                "Every entry is attested, but no eval run has been graded against "
                "this exact golden file, so the attestations have never been "
                "checked against what the advisor actually says." + detail
            ),
            "next_step": (
                "Produce a run and grade it: `python scripts/verify_run.py "
                "evals/runs/<ts>` (see evals/golden/README.md § 'Grading a run'). "
                "Then re-run this gate."
            ),
        }

    summary = located["summary"]
    gate = summary.get("gate") or {}
    failing = [
        c for c in summary.get("cases") or [] if c.get("verdict") != "GOLDEN_PASS"
    ]
    graded = {
        "run_dir": summary.get("run_dir"),
        "summary_path": _display_path(located["path"]),
        "counts": gate.get("counts") or {},
    }

    if not gate.get("open"):
        lines = []
        for case in failing:
            reasons = "; ".join(case.get("reasons") or []) or "no reason recorded"
            lines.append(f"{case.get('case_id')} {case.get('verdict')}: {reasons}")
        return {
            **base,
            "open": False,
            "reason_code": "graded_failing",
            "graded_run": graded,
            "failing_cases": lines,
            "explanation": (
                "Every entry is attested and a run was graded against them, and "
                f"{len(failing)} case(s) did not pass. This is a FAILURE, not a "
                "hold: a qualified human recorded the correct answer and the "
                "advisor gave a different one. Fix the advisor — do not edit the "
                "attestation to match the output, which would convert the only "
                "ground truth in the project into a record of what the model "
                "already says."
            ),
            "next_step": (
                "Read the per-case reasons above and "
                f"{graded['summary_path']}. evals/golden/README.md § 'One run is "
                "not a verdict' applies before a single failing run drives a "
                "revert: N ≥ 5 per side, and diff the evidence, not the verdicts."
            ),
        }

    return {
        **base,
        "open": True,
        "reason_code": "attested_and_passing",
        "graded_run": graded,
        "explanation": (
            f"All {att['total']} golden entries are attested by a qualified human "
            f"and every one graded GOLDEN_PASS in {graded['run_dir']}."
        ),
        "next_step": "Promotion may proceed.",
    }


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_decision(decision: dict[str, Any]) -> str:
    att = decision["attestation"]
    lines = [
        RULE,
        "GOLDEN-CASE DEPLOY GATE  (evals/golden/golden_cases.json)",
        RULE,
        f"  golden file : {decision['golden_file']}",
        f"  attested    : {att['attested']}/{att['total']} entries",
    ]
    graded = decision.get("graded_run")
    if graded:
        counts = ", ".join(f"{v} {k}" for k, v in (graded.get("counts") or {}).items())
        lines.append(f"  graded run  : {graded['run_dir']}  [{counts}]")
    else:
        lines.append("  graded run  : none applicable")
    lines.append("")
    lines.append(
        "GATE: OPEN — promotion may proceed."
        if decision["open"]
        else f"GATE: HELD ({decision['reason_code']}) — DO NOT PROMOTE OR DEPLOY."
    )
    lines.append("")
    lines += ["  " + line for line in _wrap(decision["explanation"])]
    for failing in decision.get("failing_cases") or []:
        lines.append(f"    - {failing}")
    lines.append("")
    lines.append("  Next step:")
    lines += ["    " + line for line in _wrap(decision["next_step"])]
    lines.append(RULE)
    return "\n".join(lines)


def _wrap(text: str, width: int = 74) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width) or [""]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "The golden-case deploy gate: may this release be promoted? "
            "Exit 0 open, 1 held, 2 could not be evaluated."
        )
    )
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN_FILE))
    parser.add_argument(
        "--runs-root",
        default=str(DEFAULT_RUNS_ROOT),
        help="Where to look for a graded run. Default: evals/runs/",
    )
    parser.add_argument(
        "--run-dir",
        help="Require this specific run's grade rather than searching evals/runs/.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the decision as JSON on stdout."
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).resolve() if args.run_dir else None
    if run_dir is not None and not run_dir.exists():
        print(f"Run dir not found: {run_dir}", file=sys.stderr)
        return EXIT_USAGE

    try:
        decision = evaluate_gate(
            Path(args.golden), runs_root=Path(args.runs_root), run_dir=run_dir
        )
    except GateError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "The gate could not be evaluated (exit 2). This is not a passing "
            "gate — do not promote.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.json:
        print(json.dumps(decision, indent=2))
    else:
        print(format_decision(decision))
    return EXIT_OPEN if decision["open"] else EXIT_HELD


if __name__ == "__main__":
    raise SystemExit(main())
