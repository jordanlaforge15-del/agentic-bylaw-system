#!/usr/bin/env python3
"""
Independently verify the advisor's answers against the source bylaw.

Reads transcripts from ``evals/runs/<ts>/TC-NNN.json`` produced by
``scripts/run_test_prompts.py`` and, for each assistant turn:

1. Extracts citation references from the assistant text via regex
   (Section/Part/Table/Schedule/§ patterns).
2. For each cited reference, queries the layer1 ``source_fragment``
   table directly to confirm the citation exists in the Halifax
   Regional Centre LUB (document_id resolved by bylaw_name).
3. Scans for dimensional / use-permission claims (e.g. "7.5 m",
   "permitted in ER-1") and looks for corroborating source fragments.
4. Scores each turn on:
     - citation_exists_count / citation_total
     - citation_hallucinated_count  (citation referenced but no
       matching fragment in the source bylaw)
     - keyword_hit_rate              (% of expected_answer_keywords
       from the test spec that appear in the assistant text)
     - liability_hedging             (does the response acknowledge
       uncertainty / suggest professional review on liability=high?)

Writes:
  evals/runs/<ts>/verification/TC-NNN.verify.json
  evals/runs/<ts>/verification/SUMMARY.json

Usage:
  python scripts/verify_test_prompts.py evals/runs/20260602T120000Z
  python scripts/verify_test_prompts.py evals/runs/latest  # symlink
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_URL = "postgresql://layer1:layer1@localhost:5432/layer1"
BYLAW_NAME = "Regional Centre Land Use By-Law"

# Citation patterns we lift from the assistant text. Each yields a
# canonical "claimed citation" string we then probe in the source DB.
# Patterns are ordered most-specific → least; the first match wins.
CITATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("schedule",   re.compile(r"\bSchedule\s+(\d+[A-Z]?)\b", re.IGNORECASE)),
    ("table",      re.compile(r"\bTable\s+(\d+[A-Z]?)\b", re.IGNORECASE)),
    ("appendix",   re.compile(r"\bAppendix\s+(\d+[A-Z]?)\b", re.IGNORECASE)),
    ("part",       re.compile(r"\bPart\s+([IVX]+|\d+)\b")),
    # Numeric section refs: "Section 49", "section 9(a)", "§ 230", "§15.4"
    ("section",    re.compile(r"(?:§|\bSection\s+|\bsection\s+)(\d+(?:\.\d+)?[a-z]?)")),
    # Common "RC-LUB §X" shorthand.
    ("rclub_ref",  re.compile(r"\bRC-LUB\s*§\s*(\d+(?:\.\d+)?[a-z]?)", re.IGNORECASE)),
]

DIMENSIONAL_VALUE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:m\b|metres?\b|meters?\b|%|percent\b|storeys?\b|stories?\b)",
    re.IGNORECASE,
)

# Ambiguous single-digit "Part 1/2/3" pattern matches things like
# "Part III" but also stray "1 m" sized numbers if we're not careful.
# We disallow ranges like "0-200" by anchoring on whitespace + keyword
# in CITATION_PATTERNS above; this list just helps us *exclude* obviously
# wrong "citations" we extract.
NOISE_CITATIONS = {"part:0", "part:00"}


def db_connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True)


def resolve_document_id(conn: psycopg.Connection) -> int:
    """Find the most recent Halifax Regional Centre LUB document."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM document WHERE bylaw_name = %s "
            "ORDER BY ingestion_timestamp DESC LIMIT 1",
            (BYLAW_NAME,),
        )
        row = cur.fetchone()
        if row is None:
            raise SystemExit(
                f"Could not find document with bylaw_name={BYLAW_NAME!r} in the dev DB. "
                "Is the ingest loaded?"
            )
        return row[0]


def citation_exists(conn: psycopg.Connection, doc_id: int, kind: str, value: str) -> dict[str, Any]:
    """Probe the source_fragment table for a citation reference.

    Returns ``{"found": bool, "examples": [<citation_path>, ...]}``.
    The search strategy depends on the citation kind:

    - schedule/table/appendix/part: look for ``kind`` token + value
      anywhere in citation_path OR in the fragment text near the
      start (these often appear as headings).
    - section / rclub_ref: prefer exact citation_label match; fall back
      to a citation_path containing "> <value>" or text starting
      with "<value> ".
    """
    with conn.cursor() as cur:
        if kind in {"schedule", "table", "appendix"}:
            label_pat = f"{kind.capitalize()} {value}"
            cur.execute(
                "SELECT citation_path, page_start, left(text, 200) FROM source_fragment "
                "WHERE document_id = %s AND ("
                "  citation_path ILIKE %s OR citation_label ILIKE %s "
                "  OR (page_start <= 30 OR text ILIKE %s)"
                ") LIMIT 5",
                (doc_id, f"%{label_pat}%", f"%{label_pat}%", f"%{label_pat}%"),
            )
        elif kind == "part":
            cur.execute(
                "SELECT citation_path, page_start, left(text, 200) FROM source_fragment "
                "WHERE document_id = %s AND citation_path ILIKE %s LIMIT 5",
                (doc_id, f"Part {value}%"),
            )
        else:
            # section / rclub_ref
            label_match = value
            cur.execute(
                "SELECT citation_path, page_start, left(text, 200) FROM source_fragment "
                "WHERE document_id = %s AND ("
                "  citation_label = %s "
                "  OR citation_path LIKE %s "
                "  OR text ~ %s"
                ") LIMIT 5",
                (
                    doc_id,
                    label_match,
                    f"%> {label_match}",
                    rf"^\s*{re.escape(label_match)}\s+",
                ),
            )
        rows = cur.fetchall()
        return {
            "found": bool(rows),
            "examples": [
                {"citation_path": r[0], "page": r[1], "text_excerpt": r[2]}
                for r in rows[:3]
            ],
        }


def extract_citations(text: str) -> list[dict[str, str]]:
    """Pull citation references out of an assistant message.

    Returns deduplicated ``[{kind, value, raw}]``. The same Section 9
    referenced twice yields one entry.
    """
    seen: dict[str, dict[str, str]] = {}
    for kind, pat in CITATION_PATTERNS:
        for m in pat.finditer(text):
            value = m.group(1)
            key = f"{kind}:{value.lower()}"
            if key in NOISE_CITATIONS:
                continue
            if key in seen:
                continue
            seen[key] = {"kind": kind, "value": value, "raw": m.group(0)}
    return list(seen.values())


def keyword_hit_rate(text: str, expected: list[str] | None) -> dict[str, Any]:
    """Fraction of expected_answer_keywords that appear in the text.

    Case-insensitive substring match. Numeric tokens like "6.0 m"
    are normalized to handle "6.0 metres" / "6 m" variants only
    minimally — exact phrasing matters in bylaw text, so we don't
    over-broaden.
    """
    if not expected:
        return {"expected": 0, "hit": 0, "rate": None, "misses": []}
    low = text.lower()
    hit: list[str] = []
    miss: list[str] = []
    for kw in expected:
        if kw.lower() in low:
            hit.append(kw)
        else:
            miss.append(kw)
    return {
        "expected": len(expected),
        "hit": len(hit),
        "rate": round(len(hit) / len(expected), 3),
        "misses": miss,
    }


def detect_hedging(text: str) -> dict[str, Any]:
    """Heuristic: does the response hedge / point to professional review?

    Production bylaw advice on high-liability questions should
    explicitly tell the user to consult a planner / lawyer or to
    confirm with HRM. We look for common hedging phrases.
    """
    low = text.lower()
    markers = [
        "confirm with",
        "consult",
        "professional",
        "planner",
        "lawyer",
        "hrm",
        "halifax regional municipality",
        "i recommend",
        "i'd recommend",
        "would recommend",
        "verify",
        "before proceeding",
        "may vary",
        "not legal advice",
        "this is general",
        "for a specific",
        "site-specific",
    ]
    present = [m for m in markers if m in low]
    return {
        "hedged": bool(present),
        "markers": present,
    }


def verify_turn(
    conn: psycopg.Connection,
    doc_id: int,
    turn: dict[str, Any],
    expected_keywords: list[str] | None,
    liability: str | None,
) -> dict[str, Any]:
    text = turn.get("assistant_text") or ""
    if not text:
        return {
            "turn": turn.get("turn"),
            "skipped": "empty assistant text",
            "error": turn.get("error"),
        }
    citations = extract_citations(text)
    cite_results: list[dict[str, Any]] = []
    found = 0
    for c in citations:
        res = citation_exists(conn, doc_id, c["kind"], c["value"])
        cite_results.append({
            "kind": c["kind"],
            "value": c["value"],
            "raw": c["raw"],
            **res,
        })
        if res["found"]:
            found += 1
    kw = keyword_hit_rate(text, expected_keywords)
    hedge = detect_hedging(text)
    hedging_required = (liability == "high")
    hedging_ok = (not hedging_required) or hedge["hedged"]
    return {
        "turn": turn.get("turn"),
        "wall_time_s": turn.get("wall_time_s"),
        "stop_reason": turn.get("stop_reason"),
        "citation_total": len(citations),
        "citation_found": found,
        "citation_hallucinated": len(citations) - found,
        "citations": cite_results,
        "keyword_hits": kw,
        "hedging": hedge,
        "hedging_required": hedging_required,
        "hedging_ok": hedging_ok,
    }


def grade_case(turns_v: list[dict[str, Any]], complexity: str | None) -> dict[str, Any]:
    """Roll up per-turn verifications into a case verdict.

    Rules (matching the threshold in ABS-260):
      - Any hallucinated citation in ANY turn → hard fail (verdict=FAIL_HALLUCINATION).
      - Complexity=simple cases: keyword hit rate >= 80% across all
        turns AND hedging_ok → PASS; else PARTIAL/FAIL.
      - Complexity=medium/complex: aggregate keyword hit rate >= 60%
        AND hedging_ok on high-liability prompts → PASS.
    """
    if not turns_v:
        return {"verdict": "NO_DATA"}
    hallucinations = sum(t.get("citation_hallucinated", 0) for t in turns_v if "citation_hallucinated" in t)
    kw_expected = sum((t["keyword_hits"]["expected"] for t in turns_v if t.get("keyword_hits")), 0)
    kw_hit = sum((t["keyword_hits"]["hit"] for t in turns_v if t.get("keyword_hits")), 0)
    kw_rate = (kw_hit / kw_expected) if kw_expected else None
    hedging_failed = any(not t.get("hedging_ok", True) for t in turns_v)
    citation_total = sum(t.get("citation_total", 0) for t in turns_v)
    citation_found = sum(t.get("citation_found", 0) for t in turns_v)

    reasons: list[str] = []
    if hallucinations > 0:
        reasons.append(f"{hallucinations} hallucinated citation(s)")
    if hedging_failed:
        reasons.append("missing hedging on high-liability turn")

    if hallucinations > 0:
        verdict = "FAIL_HALLUCINATION"
    elif complexity == "simple":
        if kw_rate is not None and kw_rate >= 0.80 and not hedging_failed:
            verdict = "PASS"
        elif kw_rate is not None and kw_rate >= 0.50:
            verdict = "PARTIAL"
            reasons.append(f"keyword rate {kw_rate:.0%} below 80% bar for simple cases")
        else:
            verdict = "FAIL"
            reasons.append(f"keyword rate {kw_rate}")
    else:
        if kw_rate is not None and kw_rate >= 0.60 and not hedging_failed:
            verdict = "PASS"
        elif kw_rate is not None and kw_rate >= 0.35:
            verdict = "PARTIAL"
            reasons.append(f"keyword rate {kw_rate:.0%} below 60% bar for {complexity}")
        else:
            verdict = "FAIL"
            reasons.append(f"keyword rate {kw_rate}")
    return {
        "verdict": verdict,
        "reasons": reasons,
        "citation_total": citation_total,
        "citation_found": citation_found,
        "citation_hallucinated": hallucinations,
        "keyword_expected": kw_expected,
        "keyword_hit": kw_hit,
        "keyword_rate": kw_rate,
        "hedging_failed": hedging_failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", help="Path to evals/runs/<ts>/")
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL_PLAIN", DEFAULT_DB_URL))
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        parser.error(f"Run dir not found: {run_dir}")
    transcripts = sorted(run_dir.glob("TC-*.json"))
    if not transcripts:
        parser.error(f"No TC-*.json transcripts in {run_dir}")

    verify_dir = run_dir / "verification"
    verify_dir.mkdir(exist_ok=True)

    conn = db_connect(args.db_url)
    try:
        doc_id = resolve_document_id(conn)
        print(f"Verifying against document_id={doc_id} ({BYLAW_NAME})", file=sys.stderr)
        summary: list[dict[str, Any]] = []
        for tp in transcripts:
            transcript = json.loads(tp.read_text())
            spec = transcript.get("spec") or {}
            expected = spec.get("expected_answer_keywords") or []
            liability = spec.get("liability")
            complexity = spec.get("complexity")
            print(f"==> {transcript['id']} ({complexity}, liability={liability})", file=sys.stderr)
            turns_v: list[dict[str, Any]] = []
            for turn in transcript.get("turns") or []:
                turns_v.append(verify_turn(conn, doc_id, turn, expected, liability))
            grade = grade_case(turns_v, complexity)
            out = {
                "id": transcript["id"],
                "title": transcript.get("title"),
                "zone": transcript.get("zone"),
                "complexity": complexity,
                "liability": liability,
                "model": transcript.get("model"),
                "grade": grade,
                "turns": turns_v,
            }
            (verify_dir / f"{transcript['id']}.verify.json").write_text(
                json.dumps(out, indent=2)
            )
            summary.append({
                "id": transcript["id"],
                "title": transcript.get("title"),
                "zone": transcript.get("zone"),
                "complexity": complexity,
                "liability": liability,
                "verdict": grade.get("verdict"),
                "kw_rate": grade.get("keyword_rate"),
                "citation_found": grade.get("citation_found"),
                "citation_total": grade.get("citation_total"),
                "hallucinated": grade.get("citation_hallucinated"),
                "reasons": grade.get("reasons"),
            })
            print(f"    {grade.get('verdict')}  kw={grade.get('keyword_rate')}  "
                  f"cites {grade.get('citation_found')}/{grade.get('citation_total')}  "
                  f"hallu={grade.get('citation_hallucinated')}", file=sys.stderr)
    finally:
        conn.close()

    summary_path = verify_dir / "SUMMARY.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nVerification summary written to {summary_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
