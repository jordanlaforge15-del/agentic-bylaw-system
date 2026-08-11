#!/usr/bin/env python3
"""
Independently verify the advisor's answers against the source bylaw.

Reads transcripts from ``evals/runs/<ts>/TC-NNN.json`` produced by
``scripts/run_test_prompts.py`` and grades each case on four axes.

Per turn (``verify_turn``):

1. Extracts citation references from the assistant text via regex
   (Section/Part/Table/Schedule/§ patterns) and probes each one against the
   layer1 ``source_fragment`` corpus. A reference with no matching fragment
   is a hallucinated citation and hard-fails the case.
2. Extracts *clause-level* citations ("198(1)(d)", "9(1)(c)") and runs the
   applicability check described below.
3. Scores hedging: on ``liability=high`` cases the answer must point the
   reader at professional / municipal review.

Per case (``grade_case``):

4. ``expected_answer_keywords`` — hit rate over the **union of every turn's
   text**, computed once for the case. Earlier turns satisfy keywords for the
   case as a whole; a keyword answered in turn 1 is not re-demanded of turn 2
   (ABS-462 defect 1).
5. ``expected_bylaw_references`` — every entry is resolved against the corpus
   (using the ABS-463 reference grammar) *and* compared against what the
   answer actually cited. Coverage counts expected references the answer
   reached; citations the answer made that no expected reference asked for are
   reported as ``unexpected`` (a real-but-different provision is not a
   hallucination, but it is not the provision the case is about either).
6. ``expected_topics`` — snake_case taxonomy labels, matched on **normalised,
   stemmed token sets within a proximity window**, never as substrings. No
   prose answer contains the literal string "development_permit_exemption";
   "…exempts from a development permit…" is the correct answer and scores
   full marks.

Applicability check (ABS-462 defect 3)
--------------------------------------
The old scorer asked "did it cite something that exists?". A confidently wrong
answer citing a real provision passed 7/7. The question that matters is "did it
cite something that *applies*?".

Many operative clauses in the Regional Centre LUB are conditional — they open
with a ``where a lot line abuts a lot … zoned DD, DH, CEN-2, CEN-1, or COR
zone`` style antecedent. When an answer leans on such a clause, the check pulls
that clause's text out of the corpus, extracts the zone codes that trigger it,
and asks whether *the answer itself* ever establishes any of them. If the
answer never mentions a single triggering zone, it has applied a conditional
provision without engaging its condition, and the case fails with
``FAIL_APPLICABILITY``.

That is exactly the ABS-462 regression: ``evals/runs/20260811T113204Z``
TC-001 turn 2 told a homeowner their side setback was 0.0 m under clause
198(1)(d), a clause that only bites where a lot line abuts DD/DH/CEN-2/CEN-1/
COR land, having itself established the neighbours are HR-1. ``(f) 2.5 metres
elsewhere`` governs. The clause is real, so the hallucination check passed it;
the applicability check flags it.

Known limits of a rule-based scorer
-----------------------------------
The applicability check catches *zone-conditional* misapplication, which is the
dominant failure mode in a setback/permission corpus. It cannot catch:

* **Use-conditional clauses** — "for a townhouse dwelling use" gates on the
  dwelling type, which lives in the user's facts rather than in any token the
  grader can resolve against the corpus.
* **Paraphrase drift** — the same TC-001 answer glossed 198(1)(d) as "all other
  uses", which is not what the clause says. Detecting that requires reading the
  clause and the gloss for entailment.
* **Arithmetic and composition** — stacking an encroachment allowance onto a
  setback, or picking the governing rule when two provisions both apply.
* **Whether the *right* provision was omitted entirely** — reference coverage
  approximates this, but only for the provisions the eval author foresaw.

Closing those requires an LLM-judge stage: feed the answer, the retrieved
clause texts, and the expected references to a model prompted to rule on
entailment and applicability, and treat its verdict as a fifth axis alongside
the four here. That stage is deliberately not implemented — it costs API spend
per grade and needs its own calibration set — but its absence is a known gap,
not an assumption that the rule-based score is sufficient.

What a pass here does and does not mean (ABS-468)
-------------------------------------------------
Every expectation this script grades against — the question, the persona, the
expected keywords, the expected references, the expected topics — was authored
by ``claude -p`` in ``scripts/generate_regional_centre_test_prompts.py``. The
system under test is a Claude model. So a passing case establishes that the
advisor agrees with what a model guessed the answer was. It does not establish
that the answer is correct under the by-law.

Results from this script are therefore **advisory** and every row it writes is
stamped ``evidence_tier: "generated"``. The human-validated tier lives in
``evals/golden/golden_cases.json``, is graded by
``scripts/verify_golden_cases.py``, and writes ``GOLDEN_SUMMARY.json`` beside
this script's ``SUMMARY.json``. The two are never summed, averaged, or reported
as one pass rate; the verdict vocabularies are disjoint so they cannot be
merged by accident.

Writes:
  evals/runs/<ts>/verification/TC-NNN.verify.json
  evals/runs/<ts>/verification/SUMMARY.json

Usage:
  python scripts/verify_test_prompts.py evals/runs/20260602T120000Z
  python scripts/verify_test_prompts.py evals/runs/latest  # symlink
  # Grade offline against a corpus snapshot instead of the dev DB:
  python scripts/verify_test_prompts.py <run_dir> --corpus-json corpus.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Run as a script, sys.path[0] is scripts/ — the reference-grammar import
    # below needs the repo root.
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_DB_URL = "postgresql://layer1:layer1@localhost:5432/layer1"
DEFAULT_PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"
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

# Clause-level references the answer leans on: "198(1)(d)", "Section 9(1)(c)",
# "340(b)". The section number must lead, so "(a) 6.0 m" on its own — a quoted
# clause list rather than a citation — is not picked up.
CLAUSE_CITATION_RE = re.compile(r"\b(\d{1,3}[A-Z]?)((?:\([0-9a-zA-Z]{1,3}\)){1,3})")
CLAUSE_GROUP_RE = re.compile(r"\(([0-9a-zA-Z]{1,3})\)")

# Zone codes as they appear in the by-law and in prose — the set enumerated by
# Tables 1A/1B/1C. Matched case-sensitively: lowercase "cor" / "dd" / "li" are
# ordinary words, uppercase are zones. The single-letter "H" zone is left out
# deliberately: it collides with too much ordinary text to be a usable token.
ZONE_TOKEN_RE = re.compile(
    r"\b(?:ER-\d|HR-\d|CH-\d|CEN-\d|UC-\d|COR|DD|DH|PCF|RPK|INS|DND|HRI|CLI|LI|WA)\b"
)

# A conditional antecedent: the clause only bites in a stated circumstance.
CONDITION_RE = re.compile(
    r"\bwhere\b[^;.]*?(?:\babuts\b|\bis zoned\b|\bzoned\b|\bgoverned by\b)[^;.]*",
    re.IGNORECASE,
)

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

# Grading bars. Keyword bars are the ABS-260 thresholds, unchanged; the
# reference and topic bars are ABS-462 additions applied at the same tier.
KEYWORD_PASS_BAR = {"simple": 0.80}
KEYWORD_PASS_BAR_DEFAULT = 0.60
KEYWORD_PARTIAL_FLOOR = {"simple": 0.50}
KEYWORD_PARTIAL_FLOOR_DEFAULT = 0.35
REFERENCE_PASS_BAR = 0.60
TOPIC_PASS_BAR = 0.60

# Topic tokens must co-occur inside a window this wide (in normalised tokens)
# so "rear" on page 1 and "setback" on page 3 don't credit "rear_setback".
TOPIC_WINDOW_TOKENS = 40


# ---------------------------------------------------------------------------
# Corpus access
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fragment:
    """One row of the layer1 ``source_fragment`` table."""

    citation_label: str | None
    citation_path: str | None
    page: int | None
    text: str

    def as_example(self, excerpt_chars: int = 200) -> dict[str, Any]:
        return {
            "citation_path": self.citation_path,
            "page": self.page,
            "text_excerpt": self.text[:excerpt_chars],
        }


class Corpus(Protocol):
    """What the grader needs from the source by-law."""

    def citation_exists(self, kind: str, value: str) -> dict[str, Any]:
        """Probe a free-text citation lifted from an answer."""

    def clause_fragment(self, section: str, clause: str) -> Fragment | None:
        """Fetch the fragment for ``<section>…(<clause>)``, or None."""

    def resolve_reference(self, reference: str) -> dict[str, Any]:
        """Resolve an ``expected_bylaw_references`` entry (ABS-463 grammar)."""


def _fragment_matches(kind: str, value: str, frag: Fragment) -> bool:
    """Python mirror of the SQL probes in :class:`PostgresCorpus`.

    Kept beside them deliberately: the JSON corpus is what the tests and the
    e2e spec grade against, so the two implementations have to agree on what
    "this citation exists" means.
    """
    path = frag.citation_path or ""
    label = frag.citation_label or ""
    text = frag.text or ""
    if kind in {"schedule", "table", "appendix"}:
        needle = f"{kind.capitalize()} {value}".lower()
        return needle in path.lower() or needle in label.lower() or needle in text.lower()
    if kind == "part":
        if path.lower().startswith(f"part {value}".lower()):
            return True
        return bool(re.match(rf"^Part {re.escape(value)}([^A-Za-z0-9]|$)", text))
    # section / rclub_ref
    if label == value or path.endswith(f"> {value}"):
        return True
    return bool(re.match(rf"^\s*{re.escape(value)}\s+", text))


def _reference_plan(reference: str) -> dict[str, Any]:
    """Parse an expected reference with the ABS-463 grammar.

    Delegates to ``scripts/build_bylaw_reference_index.py`` so the eval file,
    its snapshot index, and this grader can never disagree about what
    "Section 9(1)(c)" means. Only the parsed shape is reused — the SQL in the
    plan is SQLAlchemy-flavoured and each corpus runs its own query.
    """
    from scripts.build_bylaw_reference_index import resolution_plan

    return resolution_plan(reference)


class PostgresCorpus:
    """The live layer1 ingest."""

    def __init__(self, conn: Any, document_id: int) -> None:
        self.conn = conn
        self.doc_id = document_id

    def citation_exists(self, kind: str, value: str) -> dict[str, Any]:
        with self.conn.cursor() as cur:
            if kind in {"schedule", "table", "appendix"}:
                label_pat = f"{kind.capitalize()} {value}"
                cur.execute(
                    "SELECT citation_path, page_start, left(text, 200) FROM source_fragment "
                    "WHERE document_id = %s AND ("
                    "  citation_path ILIKE %s OR citation_label ILIKE %s "
                    "  OR (page_start <= 30 OR text ILIKE %s)"
                    ") LIMIT 5",
                    (self.doc_id, f"%{label_pat}%", f"%{label_pat}%", f"%{label_pat}%"),
                )
            elif kind == "part":
                # The ingest's hierarchy parser only captures a subset of Part
                # numbers in citation_path (Parts I / V / X). The rest live
                # only in fragment text (e.g. "Part IV: Lot Requirements" on
                # page 94). Fall back to a text-prefix probe so we don't
                # false-positive on real Parts the parser missed.
                cur.execute(
                    "SELECT citation_path, page_start, left(text, 200) FROM source_fragment "
                    "WHERE document_id = %s AND ("
                    "  citation_path ILIKE %s "
                    "  OR text ~ %s"
                    ") LIMIT 5",
                    (
                        self.doc_id,
                        f"Part {value}%",
                        # Postgres POSIX regex: no \b — use explicit boundary char.
                        rf"^Part {re.escape(value)}([^A-Za-z0-9]|$)",
                    ),
                )
            else:
                # section / rclub_ref
                cur.execute(
                    "SELECT citation_path, page_start, left(text, 200) FROM source_fragment "
                    "WHERE document_id = %s AND ("
                    "  citation_label = %s "
                    "  OR citation_path LIKE %s "
                    "  OR text ~ %s"
                    ") LIMIT 5",
                    (
                        self.doc_id,
                        value,
                        f"%> {value}",
                        rf"^\s*{re.escape(value)}\s+",
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

    def clause_fragment(self, section: str, clause: str) -> Fragment | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT citation_label, citation_path, page_start, text FROM source_fragment "
                "WHERE document_id = %s AND citation_label = %s "
                "AND citation_path LIKE %s AND citation_path NOT LIKE 'Appendix %%' "
                "ORDER BY id LIMIT 1",
                (self.doc_id, f"({clause})", f"%> {section} >%"),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Fragment(citation_label=row[0], citation_path=row[1], page=row[2], text=row[3] or "")

    def resolve_reference(self, reference: str) -> dict[str, Any]:
        plan = _reference_plan(reference)
        kind = plan["kind"]
        with self.conn.cursor() as cur:
            if kind == "section":
                cur.execute(
                    "SELECT citation_label, citation_path, page_start, text FROM source_fragment "
                    "WHERE document_id = %s AND fragment_type = 'SECTION' AND citation_label = %s "
                    "AND citation_path IS NOT NULL AND citation_path NOT LIKE 'Appendix %%' "
                    "ORDER BY id LIMIT 3",
                    (self.doc_id, plan["params"]["label"]),
                )
            elif kind == "section_clause":
                cur.execute(
                    "SELECT citation_label, citation_path, page_start, text FROM source_fragment "
                    "WHERE document_id = %s AND citation_label = %s AND citation_path IS NOT NULL "
                    "AND citation_path LIKE %s AND citation_path NOT LIKE 'Appendix %%' "
                    "ORDER BY id LIMIT 3",
                    (self.doc_id, plan["params"]["label"], plan["params"]["path_like"]),
                )
            else:
                cur.execute(
                    "SELECT citation_label, citation_path, page_start, text FROM source_fragment "
                    "WHERE document_id = %s AND citation_label = %s AND citation_path IS NOT NULL "
                    "ORDER BY id LIMIT 3",
                    (self.doc_id, plan["params"]["label"]),
                )
            rows = cur.fetchall()
        frags = [Fragment(r[0], r[1], r[2], r[3] or "") for r in rows]
        return _reference_resolution(reference, plan, frags)


class JsonCorpus:
    """A corpus snapshot on disk — grade a run with no database.

    Accepts ``{"fragments": [{citation_label, citation_path, page, text}, …]}``.
    Used by ``--corpus-json``, by ``tests/scripts/test_verify_test_prompts.py``
    and by the e2e spec, all of which run where the 4,341-fragment Halifax
    ingest does not exist.
    """

    def __init__(self, fragments: Iterable[dict[str, Any]]) -> None:
        self.fragments = [
            Fragment(
                citation_label=f.get("citation_label"),
                citation_path=f.get("citation_path"),
                page=f.get("page", f.get("page_start")),
                text=f.get("text") or "",
            )
            for f in fragments
        ]

    @classmethod
    def from_file(cls, path: Path) -> JsonCorpus:
        payload = json.loads(Path(path).read_text())
        if isinstance(payload, list):
            return cls(payload)
        return cls(payload.get("fragments") or [])

    def citation_exists(self, kind: str, value: str) -> dict[str, Any]:
        hits = [f for f in self.fragments if _fragment_matches(kind, value, f)]
        return {
            "found": bool(hits),
            "examples": [f.as_example() for f in hits[:3]],
        }

    def clause_fragment(self, section: str, clause: str) -> Fragment | None:
        for frag in self.fragments:
            path = frag.citation_path or ""
            if frag.citation_label == f"({clause})" and f"> {section} >" in path:
                return frag
        return None

    def resolve_reference(self, reference: str) -> dict[str, Any]:
        plan = _reference_plan(reference)
        label = plan["params"]["label"]
        frags: list[Fragment] = []
        for frag in self.fragments:
            if frag.citation_label != label:
                continue
            if plan["kind"] == "section_clause" and (
                f"> {plan['section']} >" not in (frag.citation_path or "")
            ):
                continue
            if (frag.citation_path or "").startswith("Appendix ") and plan["kind"] != "appendix":
                continue
            frags.append(frag)
        return _reference_resolution(reference, plan, frags)


def _reference_resolution(
    reference: str, plan: dict[str, Any], frags: list[Fragment]
) -> dict[str, Any]:
    return {
        "reference": reference,
        "kind": plan["kind"],
        "resolved": bool(frags),
        "matches": [f.as_example(240) for f in frags[:3]],
    }


def db_connect(url: str):
    import psycopg

    return psycopg.connect(url, autocommit=True)


def resolve_document_id(conn) -> int:
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


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


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


def extract_clause_citations(text: str) -> list[dict[str, Any]]:
    """Pull clause-level citations ("198(1)(d)") out of an assistant message.

    Returns deduplicated ``[{section, groups, clause, raw}]`` where ``clause``
    is the innermost group — the one that actually addresses a fragment, per
    the ABS-463 grammar.
    """
    seen: dict[str, dict[str, Any]] = {}
    for m in CLAUSE_CITATION_RE.finditer(text):
        section = m.group(1)
        groups = CLAUSE_GROUP_RE.findall(m.group(2))
        if not groups:
            continue
        key = f"{section}:{'.'.join(groups)}"
        if key in seen:
            continue
        seen[key] = {
            "section": section,
            "groups": groups,
            "clause": groups[-1],
            "raw": m.group(0),
        }
    return list(seen.values())


# ---------------------------------------------------------------------------
# Keywords, topics, hedging
# ---------------------------------------------------------------------------


def keyword_hit_rate(text: str, expected: list[str] | None) -> dict[str, Any]:
    """Fraction of expected_answer_keywords that appear in the text.

    Case-insensitive substring match. Numeric tokens like "6.0 m"
    are normalized to handle "6.0 metres" / "6 m" variants only
    minimally — exact phrasing matters in bylaw text, so we don't
    over-broaden.

    ABS-462: callers pass the **union of every turn's text** for a case. A
    multi-turn conversation answers a case's keywords across turns; scoring
    each turn against the whole case list penalised turn 2 for not repeating
    turn 1.
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


_STEM_SUFFIXES = (
    "ations", "ation", "ements", "ement", "ances", "ance",
    "ings", "ing", "ions", "ion", "ers", "er", "ed", "es", "s",
)


def stem(word: str) -> str:
    """Crude suffix stripper — enough to tie "exemption" to "exempts".

    Not a linguistic stemmer. It exists so a snake_case taxonomy label and the
    prose that satisfies it land on the same token, and it is applied to both
    sides so it can only ever be symmetric.
    """
    w = word.lower()
    for suffix in _STEM_SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            w = w[: len(w) - len(suffix)]
            break
    if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]
    return w


def normalise_tokens(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, stem. Order preserved."""
    return [stem(tok) for tok in re.split(r"[^0-9a-zA-Z]+", text) if tok]


def _topic_terms(topic: str) -> list[tuple[str, bool]]:
    """Split a topic label into ``(term, case_sensitive)`` requirements.

    All-caps parts are acronyms ("FAR", "FAR_overlay") and are matched
    case-sensitively: an answer saying the deck is "far from the lot line" has
    not discussed floor area ratio.
    """
    terms: list[tuple[str, bool]] = []
    for part in re.split(r"[^0-9a-zA-Z]+", topic):
        if not part:
            continue
        acronym = part.isupper() and len(part) >= 2 and part.isalpha()
        terms.append((part if acronym else stem(part), acronym))
    return terms


def topic_hit_rate(
    text: str, topics: list[str] | None, window: int = TOPIC_WINDOW_TOKENS
) -> dict[str, Any]:
    """Fraction of ``expected_topics`` the answer covers.

    Topics are snake_case taxonomy labels ("development_permit_exemption").
    A topic is covered when every one of its stemmed tokens occurs inside a
    single ``window``-token stretch of the answer — so "…exempts from a
    development permit…" covers ``development_permit_exemption`` while
    "development" in turn 1 and "exempt" three pages later does not.

    Substring matching is explicitly wrong here: no answer will ever contain
    the literal string "development_permit_exemption", so substrings would
    score every correct prose answer at zero (ABS-462 defect 2).
    """
    if not topics:
        return {"expected": 0, "hit": 0, "rate": None, "misses": []}
    raw_tokens = [tok for tok in re.split(r"[^0-9a-zA-Z]+", text) if tok]
    stemmed: dict[str, list[int]] = {}
    verbatim: dict[str, list[int]] = {}
    for idx, tok in enumerate(raw_tokens):
        stemmed.setdefault(stem(tok), []).append(idx)
        verbatim.setdefault(tok, []).append(idx)

    hit: list[str] = []
    miss: list[str] = []
    for topic in topics:
        required = _topic_terms(topic)
        found = {
            term: (verbatim if cased else stemmed).get(term, [])
            for term, cased in required
        }
        if (
            required
            and all(found[term] for term, _ in required)
            and _co_occurs_within(set(found), found, window)
        ):
            hit.append(topic)
            continue
        miss.append(topic)
    return {
        "expected": len(topics),
        "hit": len(hit),
        "rate": round(len(hit) / len(topics), 3),
        "misses": miss,
    }


def _co_occurs_within(
    required: set[str], positions: dict[str, list[int]], window: int
) -> bool:
    """Do all ``required`` stems appear inside one ``window``-token stretch?"""
    anchor = min(required, key=lambda r: len(positions[r]))
    for start in positions[anchor]:
        lo, hi = start - window, start + window
        if all(any(lo <= p <= hi for p in positions[r]) for r in required):
            return True
    return False


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


# ---------------------------------------------------------------------------
# Applicability
# ---------------------------------------------------------------------------


def clause_condition(clause_text: str) -> dict[str, Any]:
    """Does this clause only apply in a stated circumstance, and which one?

    Returns ``{"conditional", "trigger_phrase", "trigger_zones"}``. A clause is
    conditional when it carries a ``where …`` antecedent; the zone codes inside
    that antecedent are the terms an answer must engage before relying on it.
    """
    match = CONDITION_RE.search(clause_text or "")
    if not match:
        return {"conditional": False, "trigger_phrase": None, "trigger_zones": []}
    phrase = " ".join(match.group(0).split())
    zones = sorted(set(ZONE_TOKEN_RE.findall(phrase)))
    return {"conditional": True, "trigger_phrase": phrase, "trigger_zones": zones}


def check_applicability(
    clause_citations: list[dict[str, Any]],
    corpus: Corpus,
    context_text: str,
) -> list[dict[str, Any]]:
    """Flag conditional clauses the answer relied on without meeting them.

    ``context_text`` is the whole case — every turn's assistant text — because
    a condition established in turn 1 ("all neighbours are HR-1") still governs
    the clause picked in turn 2.

    A clause is flagged only when the corpus resolves it, its text carries a
    zone-conditional antecedent, and *not one* of that antecedent's zone codes
    appears anywhere in the answer. That is a deliberately conservative bar: an
    answer that quotes the clause in full trivially clears it.
    """
    findings: list[dict[str, Any]] = []
    for cite in clause_citations:
        frag = corpus.clause_fragment(cite["section"], cite["clause"])
        if frag is None:
            continue
        cond = clause_condition(frag.text)
        if not cond["conditional"] or not cond["trigger_zones"]:
            continue
        present = set(ZONE_TOKEN_RE.findall(context_text))
        if present & set(cond["trigger_zones"]):
            continue
        findings.append({
            "citation": cite["raw"],
            "section": cite["section"],
            "clause": cite["clause"],
            "citation_path": frag.citation_path,
            "page": frag.page,
            "clause_text": " ".join((frag.text or "").split())[:300],
            "trigger_phrase": cond["trigger_phrase"],
            "trigger_zones": cond["trigger_zones"],
            "zones_in_answer": sorted(present),
            "reason": (
                f"{cite['raw']} applies only where a lot line abuts "
                f"{', '.join(cond['trigger_zones'])} land; the answer never "
                "establishes any of those zones"
            ),
        })
    return findings


# ---------------------------------------------------------------------------
# Expected reference grading
# ---------------------------------------------------------------------------


def grade_references(
    expected: list[str] | None,
    citations: list[dict[str, str]],
    clause_citations: list[dict[str, Any]],
    corpus: Corpus,
) -> dict[str, Any]:
    """Grade ``expected_bylaw_references`` against the corpus and the answer.

    Two independent questions, both previously unasked (ABS-462 defect 2):

    * **Does the expected reference exist?** An eval file that points at a
      provision the ingest does not contain grades every answer unfairly, so
      an unresolved expectation is surfaced rather than silently counted.
    * **Did the answer reach it?** Section-level match is the hit; when the
      expectation names a clause, an exact clause match is recorded separately
      so "cited Section 198, but not 198(1)(f)" is visible.

    Citations the answer made that no expectation asked for are reported as
    ``unexpected``. They do not fail the case — a real-but-different provision
    is often a legitimate aside — but they are the trail left by an answer that
    wandered off the provision the case is about.
    """
    if not expected:
        return {
            "expected": 0, "hit": 0, "rate": None,
            "entries": [], "misses": [], "unexpected": [], "unresolved": [],
        }

    cited_sections = {
        c["value"].lower() for c in citations if c["kind"] in {"section", "rclub_ref"}
    }
    cited_clauses = {
        (c["section"].lower(), tuple(g.lower() for g in c["groups"]))
        for c in clause_citations
    }
    cited_clause_sections = {c["section"].lower() for c in clause_citations}
    cited_other = {
        (c["kind"], c["value"].lower())
        for c in citations
        if c["kind"] in {"table", "schedule", "appendix"}
    }

    entries: list[dict[str, Any]] = []
    misses: list[str] = []
    unresolved: list[str] = []
    hit = 0
    expected_sections: set[str] = set()
    expected_other: set[tuple[str, str]] = set()

    for ref in expected:
        try:
            plan = _reference_plan(ref)
        except ValueError as exc:  # unparseable reference in the eval file
            entries.append({"reference": ref, "error": str(exc), "cited": False})
            misses.append(ref)
            continue
        resolution = corpus.resolve_reference(ref)
        if not resolution["resolved"]:
            unresolved.append(ref)

        kind = plan["kind"]
        exact = None
        if kind == "section":
            section = plan["section"].lower()
            expected_sections.add(section)
            cited = section in cited_sections or section in cited_clause_sections
        elif kind == "section_clause":
            section = plan["section"].lower()
            expected_sections.add(section)
            groups = tuple(g.lower() for g in CLAUSE_GROUP_RE.findall(ref))
            exact = any(c[0] == section and c[1] == groups for c in cited_clauses)
            cited = section in cited_sections or section in cited_clause_sections
        else:
            value = plan["params"]["label"].split(" ", 1)[1].lower()
            expected_other.add((kind, value))
            cited = (kind, value) in cited_other

        if cited:
            hit += 1
        else:
            misses.append(ref)
        entries.append({
            "reference": ref,
            "kind": kind,
            "resolved_in_corpus": resolution["resolved"],
            "cited": cited,
            "exact_clause_match": exact,
            "corpus_matches": resolution["matches"],
        })

    unexpected: list[dict[str, Any]] = []
    for value in sorted(cited_sections | cited_clause_sections):
        if value not in expected_sections:
            unexpected.append({"kind": "section", "value": value})
    for kind, value in sorted(cited_other):
        if (kind, value) not in expected_other:
            unexpected.append({"kind": kind, "value": value})

    return {
        "expected": len(expected),
        "hit": hit,
        "rate": round(hit / len(expected), 3),
        "entries": entries,
        "misses": misses,
        "unexpected": unexpected,
        "unresolved": unresolved,
    }


# ---------------------------------------------------------------------------
# Per-turn and per-case grading
# ---------------------------------------------------------------------------


def verify_turn(
    corpus: Corpus,
    turn: dict[str, Any],
    liability: str | None,
) -> dict[str, Any]:
    """Grade one turn on citations and hedging.

    Keywords, topics and expected references are deliberately absent: they are
    properties of the case, not of a turn, and are graded in :func:`grade_case`.
    """
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
        res = corpus.citation_exists(c["kind"], c["value"])
        cite_results.append({
            "kind": c["kind"],
            "value": c["value"],
            "raw": c["raw"],
            **res,
        })
        if res["found"]:
            found += 1
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
        "clause_citations": extract_clause_citations(text),
        "hedging": hedge,
        "hedging_required": hedging_required,
        "hedging_ok": hedging_ok,
    }


def verify_case(
    corpus: Corpus,
    transcript: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Grade one transcript end to end."""
    liability = spec.get("liability")
    complexity = spec.get("complexity")
    turns = transcript.get("turns") or []
    turns_v = [verify_turn(corpus, turn, liability) for turn in turns]

    # The union of every turn's text: the case is what the conversation said,
    # not what any single turn repeated.
    case_text = "\n\n".join((t.get("assistant_text") or "") for t in turns).strip()

    keywords = keyword_hit_rate(case_text, spec.get("expected_answer_keywords") or [])
    topics = topic_hit_rate(case_text, spec.get("expected_topics") or [])
    all_citations = [
        {"kind": c["kind"], "value": c["value"], "raw": c["raw"]}
        for t in turns_v for c in (t.get("citations") or [])
    ]
    all_clause_citations = [
        c for t in turns_v for c in (t.get("clause_citations") or [])
    ]
    references = grade_references(
        spec.get("expected_bylaw_references") or [],
        all_citations,
        all_clause_citations,
        corpus,
    )
    applicability = check_applicability(all_clause_citations, corpus, case_text)

    grade = grade_case(
        turns_v,
        complexity,
        keywords=keywords,
        references=references,
        topics=topics,
        applicability=applicability,
    )
    return {
        "id": transcript.get("id"),
        "title": transcript.get("title"),
        "zone": transcript.get("zone"),
        "complexity": complexity,
        "liability": liability,
        "model": transcript.get("model"),
        "grade": grade,
        "turns": turns_v,
    }


def grade_case(
    turns_v: list[dict[str, Any]],
    complexity: str | None,
    *,
    keywords: dict[str, Any] | None = None,
    references: dict[str, Any] | None = None,
    topics: dict[str, Any] | None = None,
    applicability: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Roll up per-turn verifications plus the case-level scores into a verdict.

    Rules:
      - Any hallucinated citation in ANY turn → FAIL_HALLUCINATION.
      - Any conditional clause applied without its condition → FAIL_APPLICABILITY.
        A provision that exists but does not govern is a wrong answer, and it is
        the failure a citation-existence check cannot see (ABS-462).
      - Otherwise PASS needs the complexity's keyword bar (80% simple, 60%
        elsewhere — the ABS-260 thresholds), 60% expected-reference coverage,
        60% topic coverage, and hedging on high-liability prompts.
      - Below the bar but above the partial floor → PARTIAL; below it → FAIL.
    """
    if not turns_v:
        return {"verdict": "NO_DATA"}
    keywords = keywords or {"expected": 0, "hit": 0, "rate": None, "misses": []}
    topics = topics or {"expected": 0, "hit": 0, "rate": None, "misses": []}
    references = references or {"expected": 0, "hit": 0, "rate": None}
    applicability = applicability or []

    hallucinations = sum(
        t.get("citation_hallucinated", 0) for t in turns_v if "citation_hallucinated" in t
    )
    hedging_failed = any(not t.get("hedging_ok", True) for t in turns_v)
    citation_total = sum(t.get("citation_total", 0) for t in turns_v)
    citation_found = sum(t.get("citation_found", 0) for t in turns_v)

    kw_rate = keywords.get("rate")
    ref_rate = references.get("rate")
    topic_rate = topics.get("rate")
    kw_bar = KEYWORD_PASS_BAR.get(complexity or "", KEYWORD_PASS_BAR_DEFAULT)
    kw_floor = KEYWORD_PARTIAL_FLOOR.get(complexity or "", KEYWORD_PARTIAL_FLOOR_DEFAULT)

    reasons: list[str] = []
    if hallucinations > 0:
        reasons.append(f"{hallucinations} hallucinated citation(s)")
    for finding in applicability:
        reasons.append(f"inapplicable citation: {finding['reason']}")
    if hedging_failed:
        reasons.append("missing hedging on high-liability turn")
    if ref_rate is not None and ref_rate < REFERENCE_PASS_BAR:
        reasons.append(
            f"expected-reference coverage {ref_rate:.0%} below {REFERENCE_PASS_BAR:.0%} "
            f"(missed {', '.join(references.get('misses') or []) or 'none'})"
        )
    if references.get("unresolved"):
        reasons.append(
            "expected reference(s) not in the corpus: "
            + ", ".join(references["unresolved"])
        )
    if topic_rate is not None and topic_rate < TOPIC_PASS_BAR:
        reasons.append(
            f"topic coverage {topic_rate:.0%} below {TOPIC_PASS_BAR:.0%} "
            f"(missed {', '.join(topics.get('misses') or []) or 'none'})"
        )

    refs_ok = ref_rate is None or ref_rate >= REFERENCE_PASS_BAR
    topics_ok = topic_rate is None or topic_rate >= TOPIC_PASS_BAR

    if hallucinations > 0:
        verdict = "FAIL_HALLUCINATION"
    elif applicability:
        verdict = "FAIL_APPLICABILITY"
    elif kw_rate is not None and kw_rate >= kw_bar and refs_ok and topics_ok and not hedging_failed:
        verdict = "PASS"
    elif kw_rate is not None and kw_rate >= kw_floor:
        verdict = "PARTIAL"
        if kw_rate < kw_bar:
            reasons.append(
                f"keyword rate {kw_rate:.0%} below {kw_bar:.0%} bar for "
                f"{complexity or 'unknown'} cases"
            )
    else:
        verdict = "FAIL"
        reasons.append(f"keyword rate {kw_rate}")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "citation_total": citation_total,
        "citation_found": citation_found,
        "citation_hallucinated": hallucinations,
        "keyword_expected": keywords.get("expected", 0),
        "keyword_hit": keywords.get("hit", 0),
        "keyword_rate": kw_rate,
        "keyword_misses": keywords.get("misses") or [],
        "reference_expected": references.get("expected", 0),
        "reference_hit": references.get("hit", 0),
        "reference_rate": ref_rate,
        "references": references,
        "topic_expected": topics.get("expected", 0),
        "topic_hit": topics.get("hit", 0),
        "topic_rate": topic_rate,
        "topic_misses": topics.get("misses") or [],
        "applicability_findings": applicability,
        "hedging_failed": hedging_failed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_case_specs(prompts_file: Path | None) -> dict[str, dict[str, Any]]:
    """Index the live eval file by case id, for the spec-source override."""
    if prompts_file is None or not prompts_file.exists():
        return {}
    cases = json.loads(prompts_file.read_text())
    return {c["id"]: c for c in cases if c.get("id")}


def resolve_spec(
    transcript: dict[str, Any],
    live_specs: dict[str, dict[str, Any]],
    prefer: str,
) -> tuple[dict[str, Any], str]:
    """Pick the expectations to grade against.

    Transcripts embed a copy of the case spec as it stood when the run
    executed. That copy goes stale: the ``20260811T113204Z`` run carries
    TC-001's pre-ABS-463 references ("Table 3", "Section 9(a)"), which point at
    the wrong provisions. Default to the live eval file so a re-grade measures
    the answer against the corrected expectations, and fall back to the
    embedded copy for cases the eval file no longer carries.
    """
    embedded = transcript.get("spec") or {}
    live = live_specs.get(transcript.get("id") or "")
    if prefer == "prompts" and live:
        # complexity/liability are run-level facts; keep the live values but
        # fall back to the transcript when the eval file omits them.
        merged = dict(live)
        for key in ("complexity", "liability"):
            merged.setdefault(key, embedded.get(key) or transcript.get(key))
        return merged, "prompts"
    return embedded, "transcript"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", help="Path to evals/runs/<ts>/")
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL_PLAIN", DEFAULT_DB_URL))
    parser.add_argument(
        "--corpus-json",
        help="Grade against a JSON corpus snapshot instead of the layer1 DB "
             "(no database required).",
    )
    parser.add_argument(
        "--prompts",
        default=str(DEFAULT_PROMPTS_FILE),
        help="Eval file whose expectations to grade against (default: the "
             "committed regional_centre_test_prompts.json).",
    )
    parser.add_argument(
        "--spec-source",
        choices=("prompts", "transcript"),
        default="prompts",
        help="Where a case's expectations come from. 'transcript' uses the "
             "copy frozen into the run, which may be stale.",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        parser.error(f"Run dir not found: {run_dir}")
    transcripts = sorted(run_dir.glob("TC-*.json"))
    if not transcripts:
        parser.error(f"No TC-*.json transcripts in {run_dir}")

    verify_dir = run_dir / "verification"
    verify_dir.mkdir(exist_ok=True)

    live_specs = load_case_specs(Path(args.prompts) if args.prompts else None)

    conn = None
    if args.corpus_json:
        corpus: Corpus = JsonCorpus.from_file(Path(args.corpus_json))
        print(f"Verifying against corpus snapshot {args.corpus_json}", file=sys.stderr)
    else:
        conn = db_connect(args.db_url)
        doc_id = resolve_document_id(conn)
        corpus = PostgresCorpus(conn, doc_id)
        print(f"Verifying against document_id={doc_id} ({BYLAW_NAME})", file=sys.stderr)

    try:
        summary: list[dict[str, Any]] = []
        for tp in transcripts:
            transcript = json.loads(tp.read_text())
            spec, spec_source = resolve_spec(transcript, live_specs, args.spec_source)
            print(
                f"==> {transcript['id']} ({spec.get('complexity')}, "
                f"liability={spec.get('liability')}, spec={spec_source})",
                file=sys.stderr,
            )
            out = verify_case(corpus, transcript, spec)
            out["spec_source"] = spec_source
            grade = out["grade"]
            (verify_dir / f"{transcript['id']}.verify.json").write_text(
                json.dumps(out, indent=2)
            )
            summary.append({
                "id": transcript["id"],
                # ABS-468: the expectations behind this verdict were authored by
                # a model of the family under test. Stamped on every row so a
                # downstream reader cannot mistake it for a correctness measure
                # or add it to the golden tier.
                "evidence_tier": "generated",
                "title": transcript.get("title"),
                "zone": transcript.get("zone"),
                "complexity": out["complexity"],
                "liability": out["liability"],
                "verdict": grade.get("verdict"),
                "kw_rate": grade.get("keyword_rate"),
                "reference_rate": grade.get("reference_rate"),
                "topic_rate": grade.get("topic_rate"),
                "citation_found": grade.get("citation_found"),
                "citation_total": grade.get("citation_total"),
                "hallucinated": grade.get("citation_hallucinated"),
                "inapplicable": len(grade.get("applicability_findings") or []),
                "reasons": grade.get("reasons"),
            })
            print(
                f"    {grade.get('verdict')}  kw={grade.get('keyword_rate')}  "
                f"refs={grade.get('reference_rate')}  topics={grade.get('topic_rate')}  "
                f"cites {grade.get('citation_found')}/{grade.get('citation_total')}  "
                f"hallu={grade.get('citation_hallucinated')}  "
                f"inapplicable={len(grade.get('applicability_findings') or [])}",
                file=sys.stderr,
            )
    finally:
        if conn is not None:
            conn.close()

    summary_path = verify_dir / "SUMMARY.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nVerification summary written to {summary_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
