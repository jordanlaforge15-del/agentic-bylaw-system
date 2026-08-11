#!/usr/bin/env python3
"""
Grade a run against the human-validated golden subset (ABS-468).

Why this is a separate script and not a flag on ``verify_test_prompts.py``
-------------------------------------------------------------------------
The two graders answer different questions and the answers must never be added
together.

``verify_test_prompts.py`` grades against ``regional_centre_test_prompts.json``,
every field of which — the question, the persona, the expected keywords, the
expected references — was authored by ``claude -p``. The system under test is a
Claude model. A generated case that passes establishes that the advisor agrees
with what a model guessed the answer was; it does not establish that the answer
is correct under the by-law. That is the failure mode an eval exists to catch,
and until ABS-468 it was inside the eval itself.

This script grades against ``evals/golden/golden_cases.json``, whose
expectations a qualified human recorded. Sharing an output file with the
generated grader would let "19/20 passing" be read as a correctness measure, so
the artifacts are separate by construction: ``GOLDEN_SUMMARY.json`` beside
``SUMMARY.json``, never merged.

What is checked
---------------
Per attested case, over the union of every turn's assistant text:

1. ``governing_provisions`` — each reference the human named must be cited by
   the answer, and is separately resolved against the ingested corpus. A
   provision the corpus does not contain is reported (``not_in_corpus``): it is
   a data gap in the ingest or a slip in the attestation, and either way the
   case cannot be graded honestly against it.
2. ``must_state`` — propositions the answer is wrong without. A group hits when
   any of its ``any_of`` phrases appears. All groups must hit.
3. ``must_not_state`` — propositions that make the answer wrong. Any hit fails
   the case. This is the check a generated eval cannot produce: a model does not
   know which wrong answer it is inclined to give.
4. ``answer_shape`` — a ``depends`` case must answer conditionally and a
   ``refusal`` case must say the by-law does not settle the question. Without
   this an eval of determinate cases only ever rewards confidence.

An entry whose ``attestation.status`` is not ``attested`` grades UNATTESTED. It
is never counted as a pass and it holds the deploy gate closed.

Usage:
  python scripts/verify_golden_cases.py --check                 # validate the file
  python scripts/verify_golden_cases.py evals/runs/<ts>
  python scripts/verify_golden_cases.py evals/runs/<ts> --corpus-json snapshot.json
  python scripts/verify_golden_cases.py evals/runs/<ts> --gate   # exit 1 unless all PASS
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_test_prompts import (
    BYLAW_NAME,
    DEFAULT_DB_URL,
    Corpus,
    JsonCorpus,
    PostgresCorpus,
    db_connect,
    detect_hedging,
    extract_citations,
    extract_clause_citations,
    resolve_document_id,
)

DEFAULT_GOLDEN_FILE = REPO_ROOT / "evals" / "golden" / "golden_cases.json"

ANSWER_SHAPES = {"determinate", "depends", "refusal"}
ATTESTATION_STATUSES = {"unattested", "attested"}

# A "depends" answer has to be conditional out loud. These are the ordinary ways
# an advisor says so; the bar is deliberately low, because the check exists to
# catch an answer that gives a flat number where the by-law gives a condition,
# not to police phrasing.
CONDITIONAL_MARKERS = (
    "depends", "depending", "conditional", "subject to", "if ", "unless",
    "would need to", "cannot be determined", "varies", "case-by-case",
    "case by case", "at the discretion", "discretionary", "negotiat",
)

# A "refusal" answer has to say the by-law does not settle the question. Saying
# it politely still counts; saying nothing does not.
REFUSAL_MARKERS = (
    "does not address", "doesn't address", "is silent", "not addressed",
    "outside the scope", "beyond the scope", "not governed by", "no provision",
    "does not appear in", "doesn't appear in", "not regulated by",
    "cannot answer", "can't answer", "does not answer", "not a matter for",
    "not something the by-law", "not something the bylaw",
)


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def load_golden(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or "cases" not in payload:
        raise ValueError(f"{path}: expected an object with a 'cases' array")
    return payload


def validate_golden(payload: dict[str, Any], known_case_ids: set[str] | None = None) -> list[str]:
    """Structural problems with the golden file, as a list of messages.

    Run by ``--check`` and by the Playwright spec, both of which have no
    database and no run to grade — the file's own coherence is checkable
    without either, and it is what a reviewer's edit is most likely to break.
    """
    problems: list[str] = []
    cases = payload.get("cases") or []
    if not cases:
        problems.append("no cases")

    seen: set[str] = set()
    for idx, case in enumerate(cases):
        cid = case.get("case_id") or f"<index {idx}>"
        if not case.get("case_id"):
            problems.append(f"{cid}: missing case_id")
        elif case["case_id"] in seen:
            problems.append(f"{cid}: duplicate case_id")
        else:
            seen.add(case["case_id"])
        if known_case_ids is not None and case.get("case_id") not in known_case_ids:
            problems.append(f"{cid}: no such case in the generated eval file")
        if case.get("answer_shape") not in ANSWER_SHAPES:
            problems.append(
                f"{cid}: answer_shape {case.get('answer_shape')!r} not one of "
                f"{sorted(ANSWER_SHAPES)}"
            )
        if not (case.get("selection_rationale") or "").strip():
            problems.append(f"{cid}: selection_rationale is empty")
        if not (case.get("question_for_reviewer") or "").strip():
            problems.append(f"{cid}: question_for_reviewer is empty")

        att = case.get("attestation")
        if not isinstance(att, dict):
            problems.append(f"{cid}: missing attestation block")
            continue
        status = att.get("status")
        if status not in ATTESTATION_STATUSES:
            problems.append(f"{cid}: attestation.status {status!r} not one of "
                            f"{sorted(ATTESTATION_STATUSES)}")
            continue
        if status == "unattested":
            # Half-filled entries are the dangerous state: they read as ground
            # truth and grade as nothing.
            for field in ("correct_answer", "governing_provisions", "must_state"):
                if att.get(field):
                    problems.append(
                        f"{cid}: attestation.status is 'unattested' but {field} is "
                        "populated — set status to 'attested' or clear the field"
                    )
            continue
        problems.extend(_validate_attested(cid, att))
    return problems


def _validate_attested(cid: str, att: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    who = att.get("attested_by")
    if not isinstance(who, dict) or not (who.get("name") or "").strip():
        problems.append(f"{cid}: attested entries need attested_by.name")
    if not (who or {}).get("credential"):
        problems.append(f"{cid}: attested entries need attested_by.credential")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(att.get("attested_on") or "")):
        problems.append(f"{cid}: attested_on must be an ISO date (YYYY-MM-DD)")
    if not (att.get("correct_answer") or "").strip():
        problems.append(f"{cid}: attested entries need a prose correct_answer")
    if not att.get("governing_provisions"):
        problems.append(f"{cid}: attested entries need at least one governing provision")
    for prov in att.get("governing_provisions") or []:
        if not (prov.get("reference") or "").strip():
            problems.append(f"{cid}: a governing provision has no reference")
        if not (prov.get("holding") or "").strip():
            problems.append(
                f"{cid}: governing provision {prov.get('reference')!r} has no holding — "
                "a bare citation is not an answer"
            )
    if not att.get("must_state"):
        problems.append(
            f"{cid}: attested entries need at least one must_state group, or the "
            "attestation is not machine-checkable"
        )
    for field in ("must_state", "must_not_state"):
        for group in att.get(field) or []:
            gid = group.get("id") or "<no id>"
            if not (group.get("id") or "").strip():
                problems.append(f"{cid}: a {field} group has no id")
            if not (group.get("description") or "").strip():
                problems.append(f"{cid}: {field} group {gid} has no description")
            if not group.get("any_of"):
                problems.append(f"{cid}: {field} group {gid} has no any_of phrases")
    return problems


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def match_groups(text: str, groups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Which ``any_of`` phrase, if any, satisfied each group."""
    low = text.lower()
    out: list[dict[str, Any]] = []
    for group in groups or []:
        matched = next((p for p in group.get("any_of") or [] if p.lower() in low), None)
        out.append({
            "id": group.get("id"),
            "description": group.get("description"),
            "hit": matched is not None,
            "matched_phrase": matched,
        })
    return out


def grade_provisions(
    provisions: list[dict[str, Any]], case_text: str, corpus: Corpus
) -> dict[str, Any]:
    """Did the answer cite what the human said governs, and does it exist?

    Citation extraction is shared with the generated grader deliberately: the
    two tiers disagree about what the right answer is, never about what counts
    as having cited Section 198.
    """
    citations = extract_citations(case_text)
    clause_citations = extract_clause_citations(case_text)
    cited_sections = {
        c["value"].lower() for c in citations if c["kind"] in {"section", "rclub_ref"}
    }
    cited_sections |= {c["section"].lower() for c in clause_citations}
    cited_other = {
        (c["kind"], c["value"].lower())
        for c in citations
        if c["kind"] in {"table", "schedule", "appendix"}
    }
    cited_clauses = {
        (c["section"].lower(), tuple(g.lower() for g in c["groups"]))
        for c in clause_citations
    }

    from scripts.build_bylaw_reference_index import resolution_plan

    entries: list[dict[str, Any]] = []
    not_in_corpus: list[str] = []
    misses: list[str] = []
    hit = 0
    for prov in provisions:
        ref = prov.get("reference") or ""
        entry: dict[str, Any] = {"reference": ref, "holding": prov.get("holding")}
        try:
            plan = resolution_plan(ref)
        except ValueError as exc:
            entry.update({"error": str(exc), "cited": False, "resolved_in_corpus": False})
            entries.append(entry)
            misses.append(ref)
            not_in_corpus.append(ref)
            continue
        resolution = corpus.resolve_reference(ref)
        if not resolution["resolved"]:
            not_in_corpus.append(ref)
        kind = plan["kind"]
        if kind in {"section", "section_clause"}:
            section = plan["section"].lower()
            cited = section in cited_sections
            if kind == "section_clause":
                groups = tuple(
                    g.lower() for g in re.findall(r"\(([0-9a-zA-Z]{1,3})\)", ref)
                )
                entry["exact_clause_match"] = any(
                    c[0] == section and c[1] == groups for c in cited_clauses
                )
        else:
            value = plan["params"]["label"].split(" ", 1)[1].lower()
            cited = (kind, value) in cited_other
        entry.update({
            "kind": kind,
            "cited": cited,
            "resolved_in_corpus": resolution["resolved"],
        })
        entries.append(entry)
        if cited:
            hit += 1
        else:
            misses.append(ref)

    return {
        "expected": len(provisions),
        "hit": hit,
        "rate": round(hit / len(provisions), 3) if provisions else None,
        "entries": entries,
        "misses": misses,
        "not_in_corpus": not_in_corpus,
    }


def grade_shape(answer_shape: str, case_text: str) -> dict[str, Any]:
    """Does the answer have the shape the human said the correct answer has?"""
    low = case_text.lower()
    if answer_shape == "depends":
        markers = [m for m in CONDITIONAL_MARKERS if m in low]
        return {"shape": answer_shape, "ok": bool(markers), "markers": markers}
    if answer_shape == "refusal":
        markers = [m for m in REFUSAL_MARKERS if m in low]
        return {"shape": answer_shape, "ok": bool(markers), "markers": markers}
    return {"shape": answer_shape, "ok": True, "markers": []}


def grade_golden_case(
    case: dict[str, Any], transcript: dict[str, Any] | None, corpus: Corpus
) -> dict[str, Any]:
    """Grade one golden entry. Verdicts are their own vocabulary on purpose.

    GOLDEN_PASS / GOLDEN_PARTIAL / GOLDEN_FAIL / UNATTESTED / NO_TRANSCRIPT do
    not collide with the generated grader's PASS / PARTIAL / FAIL, so a summary
    row can never be miscounted into the wrong tier by a script that reads both.
    """
    cid = case.get("case_id")
    att = case.get("attestation") or {}
    base = {
        "case_id": cid,
        "zone": case.get("zone"),
        "liability": case.get("liability"),
        "answer_shape": case.get("answer_shape"),
        "evidence_tier": "human_validated",
    }
    if att.get("status") != "attested":
        return {
            **base,
            "verdict": "UNATTESTED",
            "reasons": [
                (
                    "no qualified human has recorded the correct answer for this "
                    "case; it cannot pass and it holds the deploy gate closed"
                )
            ],
        }
    attested_by = att.get("attested_by") or {}
    base["attested_by"] = attested_by.get("name")
    base["attested_on"] = att.get("attested_on")

    if transcript is None:
        return {**base, "verdict": "NO_TRANSCRIPT",
                "reasons": [f"no {cid}.json transcript in the run directory"]}

    turns = transcript.get("turns") or []
    case_text = "\n\n".join((t.get("assistant_text") or "") for t in turns).strip()
    if not case_text:
        return {**base, "verdict": "NO_TRANSCRIPT",
                "reasons": [f"{cid} transcript has no assistant text"]}

    provisions = grade_provisions(att.get("governing_provisions") or [], case_text, corpus)
    must_state = match_groups(case_text, att.get("must_state"))
    must_not_state = match_groups(case_text, att.get("must_not_state"))
    shape = grade_shape(case.get("answer_shape") or "determinate", case_text)
    hedging = detect_hedging(case_text)

    stated_misses = [g for g in must_state if not g["hit"]]
    violations = [g for g in must_not_state if g["hit"]]

    reasons: list[str] = []
    for g in violations:
        reasons.append(
            f"asserted something the reviewer marked wrong: {g['description']} "
            f"(matched {g['matched_phrase']!r})"
        )
    for g in stated_misses:
        reasons.append(f"never states: {g['description']}")
    if not shape["ok"]:
        reasons.append(
            f"the reviewer recorded the correct answer as a {shape['shape']}; the "
            "answer is unconditional"
        )
    if provisions["misses"]:
        reasons.append(
            "did not cite the governing provision(s): " + ", ".join(provisions["misses"])
        )
    if provisions["not_in_corpus"]:
        reasons.append(
            "reviewer-cited provision(s) absent from the ingested corpus: "
            + ", ".join(provisions["not_in_corpus"])
            + " — an ingest gap or an attestation slip; resolve before trusting this grade"
        )

    if violations or stated_misses or not shape["ok"]:
        verdict = "GOLDEN_FAIL"
    elif provisions["misses"]:
        # Right answer, wrong (or missing) authority. Distinct from a wrong
        # answer, and a deploy still does not go out on it.
        verdict = "GOLDEN_PARTIAL"
    else:
        verdict = "GOLDEN_PASS"

    return {
        **base,
        "verdict": verdict,
        "reasons": reasons,
        "provisions": provisions,
        "must_state": must_state,
        "must_not_state": must_not_state,
        "shape_check": shape,
        "hedging": hedging,
    }


def gate_status(results: list[dict[str, Any]]) -> dict[str, Any]:
    """The deploy gate: open only when every entry is attested and passes."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    unattested = [r["case_id"] for r in results if r["verdict"] == "UNATTESTED"]
    failing = [
        r["case_id"] for r in results
        if r["verdict"] in {"GOLDEN_FAIL", "GOLDEN_PARTIAL", "NO_TRANSCRIPT"}
    ]
    blockers: list[str] = []
    if not results:
        blockers.append("the golden subset is empty")
    if unattested:
        blockers.append("unattested: " + ", ".join(unattested))
    if failing:
        blockers.append("not passing: " + ", ".join(failing))
    return {
        "gates": "production_deploy",
        "open": not blockers,
        "blockers": blockers,
        "counts": counts,
        "note": (
            "Golden results are never summed with generated-case results. A "
            "generated pass rate is advisory and gates nothing."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_transcripts(run_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tp in sorted(run_dir.glob("TC-*.json")):
        payload = json.loads(tp.read_text())
        if payload.get("id"):
            out[payload["id"]] = payload
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("run_dir", nargs="?", help="Path to evals/runs/<ts>/")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN_FILE))
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL_PLAIN", DEFAULT_DB_URL))
    parser.add_argument("--corpus-json", help="Grade against a corpus snapshot, no database.")
    parser.add_argument(
        "--check", action="store_true",
        help="Validate the golden file and exit; no run or database needed.",
    )
    parser.add_argument(
        "--gate", action="store_true",
        help="Exit non-zero unless every golden case is attested and passes.",
    )
    parser.add_argument(
        "--prompts", default=str(REPO_ROOT / "evals" / "regional_centre_test_prompts.json"),
        help="Generated eval file, used only to confirm each golden case_id exists.",
    )
    args = parser.parse_args()

    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"Golden file not found: {golden_path}", file=sys.stderr)
        return 2
    payload = load_golden(golden_path)

    known: set[str] | None = None
    prompts_path = Path(args.prompts)
    if prompts_path.exists():
        known = {c["id"] for c in json.loads(prompts_path.read_text()) if c.get("id")}

    problems = validate_golden(payload, known)
    if problems:
        print(f"{golden_path} has {len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    if args.check:
        cases = payload["cases"]
        attested = sum(
            1 for c in cases if (c.get("attestation") or {}).get("status") == "attested"
        )
        print(
            f"{golden_path}: {len(cases)} case(s), {attested} attested, "
            f"{len(cases) - attested} awaiting a qualified human.",
            file=sys.stderr,
        )
        return 0

    if not args.run_dir:
        parser.error("run_dir is required unless --check is given")
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        parser.error(f"Run dir not found: {run_dir}")

    conn = None
    if args.corpus_json:
        corpus: Corpus = JsonCorpus.from_file(Path(args.corpus_json))
        print(f"Grading against corpus snapshot {args.corpus_json}", file=sys.stderr)
    else:
        conn = db_connect(args.db_url)
        doc_id = resolve_document_id(conn)
        corpus = PostgresCorpus(conn, doc_id)
        print(f"Grading against document_id={doc_id} ({BYLAW_NAME})", file=sys.stderr)

    try:
        transcripts = _load_transcripts(run_dir)
        verify_dir = run_dir / "verification"
        verify_dir.mkdir(exist_ok=True)
        results = [
            grade_golden_case(case, transcripts.get(case["case_id"]), corpus)
            for case in payload["cases"]
        ]
    finally:
        if conn is not None:
            conn.close()

    for result in results:
        (verify_dir / f"{result['case_id']}.golden.json").write_text(
            json.dumps(result, indent=2)
        )
        print(
            f"==> {result['case_id']}  {result['verdict']}"
            + ("  " + "; ".join(result.get("reasons") or []) if result.get("reasons") else ""),
            file=sys.stderr,
        )

    gate = gate_status(results)
    summary = {
        "evidence_tier": "human_validated",
        "golden_file": str(golden_path.relative_to(REPO_ROOT))
        if golden_path.is_absolute() and str(golden_path).startswith(str(REPO_ROOT))
        else str(golden_path),
        "run_dir": str(run_dir),
        "gate": gate,
        "cases": [
            {
                "case_id": r["case_id"],
                "zone": r.get("zone"),
                "liability": r.get("liability"),
                "answer_shape": r.get("answer_shape"),
                "verdict": r["verdict"],
                "reasons": r.get("reasons") or [],
            }
            for r in results
        ],
    }
    summary_path = verify_dir / "GOLDEN_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nGolden summary written to {summary_path}", file=sys.stderr)
    print(
        "Gate: " + ("OPEN" if gate["open"] else "CLOSED — " + "; ".join(gate["blockers"])),
        file=sys.stderr,
    )

    if args.gate and not gate["open"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
