"""Unit tests for scripts/verify_test_prompts.py — the eval grader.

ABS-462. The grader used to answer one question ("did the answer cite something
that exists?") and answer it badly:

  1. Every turn was scored against the *case's whole* keyword list, so a 2-turn
     case was graded out of 2× its keywords and each turn was penalised for not
     repeating the other. TC-001 hit all 6 distinct keywords and scored 67%.
  2. ``expected_bylaw_references`` and ``expected_topics`` were never read. Both
     fields were decoration.
  3. A confidently wrong answer citing a *real* provision passed 7/7. The
     20260811T113204Z run told a homeowner their side setback was 0.0 m under
     clause 198(1)(d) — a clause that only bites where a lot line abuts
     DD/DH/CEN-2/CEN-1/COR land, having itself established the neighbours are
     HR-1 (so ``(f) 2.5 metres elsewhere`` governs).

Everything here runs offline against ``evals/fixtures/abs462_corpus_snapshot.json``,
a verbatim slice of the real Regional Centre ingest — no database, no stack.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verify_test_prompts import (
    JsonCorpus,
    check_applicability,
    clause_condition,
    extract_citations,
    extract_clause_citations,
    grade_case,
    grade_references,
    keyword_hit_rate,
    resolve_spec,
    topic_hit_rate,
    verify_case,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_FILE = REPO_ROOT / "evals" / "fixtures" / "abs462_corpus_snapshot.json"
COMMITTED_RUN = REPO_ROOT / "evals" / "runs" / "20260811T113204Z" / "TC-001.json"
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"


@pytest.fixture(scope="module")
def corpus() -> JsonCorpus:
    return JsonCorpus.from_file(CORPUS_FILE)


def _transcript(*texts: str, **spec) -> dict:
    """A minimal transcript: one assistant turn per text."""
    return {
        "id": spec.pop("id", "TC-999"),
        "title": "synthetic case",
        "turns": [
            {"turn": i + 1, "assistant_text": t} for i, t in enumerate(texts)
        ],
        "spec": {"complexity": "simple", "liability": "low", **spec},
    }


# ─── DoD 1: keyword coverage is a case-level score ───────────────────────────


def test_keywords_split_across_turns_score_full_marks(corpus):
    """A 2-turn case whose keywords are split across turns scores 100%.

    Before ABS-462 the same conversation scored 50%: each turn was graded
    against all six keywords, so turn 1 missed the three turn 2 answered and
    vice versa.
    """
    keywords = ["rear setback", "3.0 m", "development permit",
                "side setback", "2.5 m", "HR-1"]
    transcript = _transcript(
        "In HR-1 the rear setback is 3.0 m and you need a development permit.",
        "The side setback is 2.5 m.",
    )
    result = verify_case(corpus, transcript, {
        "complexity": "simple",
        "liability": "low",
        "expected_answer_keywords": keywords,
    })
    grade = result["grade"]
    assert grade["keyword_expected"] == 6
    assert grade["keyword_hit"] == 6
    assert grade["keyword_rate"] == 1.0
    assert grade["keyword_misses"] == []

    # The old per-turn scoring, reconstructed: 12 chances, 6 hits.
    per_turn = [keyword_hit_rate(t["assistant_text"], keywords)
                for t in transcript["turns"]]
    assert sum(k["expected"] for k in per_turn) == 12
    assert sum(k["hit"] for k in per_turn) == 6


def test_keyword_rate_still_penalises_genuinely_missing_keywords(corpus):
    result = verify_case(corpus, _transcript("The rear setback is 3.0 m."), {
        "complexity": "simple",
        "expected_answer_keywords": ["rear setback", "3.0 m", "side setback", "HR-1"],
    })
    assert result["grade"]["keyword_rate"] == 0.5
    assert sorted(result["grade"]["keyword_misses"]) == ["HR-1", "side setback"]


# ─── DoD 2: expected_bylaw_references are graded ─────────────────────────────


def test_expected_references_resolve_against_the_corpus(corpus):
    graded = grade_references(
        ["Section 198", "Section 9(1)(c)"],
        extract_citations("Section 198 and Section 9(1)(c) apply."),
        extract_clause_citations("Section 198 and Section 9(1)(c) apply."),
        corpus,
    )
    assert graded["rate"] == 1.0
    assert graded["unresolved"] == []
    by_ref = {e["reference"]: e for e in graded["entries"]}
    assert by_ref["Section 198"]["resolved_in_corpus"] is True
    assert by_ref["Section 9(1)(c)"]["resolved_in_corpus"] is True
    # The clause-level expectation was met at clause level, not just section.
    assert by_ref["Section 9(1)(c)"]["exact_clause_match"] is True


def test_a_real_but_different_provision_does_not_count_as_coverage(corpus):
    """Citing Section 200 when Section 199 was expected is a miss, not a pass.

    Section 200 exists (maximum streetwall heights), so the hallucination check
    is silent — the reference comparison is what notices the answer never
    reached the provision the case is about, and records the substitute.
    """
    answer = "Your rear setback is governed by Section 200(1)(a)."
    graded = grade_references(
        ["Section 199"],
        extract_citations(answer),
        extract_clause_citations(answer),
        corpus,
    )
    assert graded["hit"] == 0
    assert graded["rate"] == 0.0
    assert graded["misses"] == ["Section 199"]
    assert {"kind": "section", "value": "200"} in graded["unexpected"]
    # And the substitute really does exist in the corpus — this is not a
    # hallucination, which is exactly why the old scorer waved it through.
    assert corpus.citation_exists("section", "200")["found"] is True


def test_a_clause_expectation_met_only_at_section_level_is_visible(corpus):
    answer = "See Section 198 generally."
    graded = grade_references(
        ["Section 198(1)(f)"],
        extract_citations(answer),
        extract_clause_citations(answer),
        corpus,
    )
    entry = graded["entries"][0]
    assert entry["cited"] is True          # the section was reached
    assert entry["exact_clause_match"] is False  # the clause was not
    assert graded["rate"] == 1.0


def test_expected_reference_missing_from_the_corpus_is_surfaced(corpus):
    graded = grade_references(["Section 9999"], [], [], corpus)
    assert graded["unresolved"] == ["Section 9999"]
    grade = grade_case(
        [{"citation_hallucinated": 0, "citation_total": 0, "citation_found": 0}],
        "simple",
        keywords={"expected": 1, "hit": 1, "rate": 1.0, "misses": []},
        references=graded,
    )
    assert any("not in the corpus" in r for r in grade["reasons"])


def test_reference_coverage_below_the_bar_blocks_a_pass():
    grade = grade_case(
        [{"citation_hallucinated": 0, "citation_total": 1, "citation_found": 1}],
        "simple",
        keywords={"expected": 5, "hit": 5, "rate": 1.0, "misses": []},
        references={"expected": 2, "hit": 0, "rate": 0.0, "misses": ["Section 199"],
                    "unexpected": [], "unresolved": []},
        topics={"expected": 1, "hit": 1, "rate": 1.0, "misses": []},
    )
    assert grade["verdict"] == "PARTIAL"
    assert any("expected-reference coverage" in r for r in grade["reasons"])


# ─── DoD 3: expected_topics match on normalised tokens, never substrings ─────


def test_correct_prose_answer_scores_full_topic_marks():
    """Prose never contains "development_permit_exemption" — and shouldn't have to."""
    topics = ["rear_setback", "side_setback", "development_permit_exemption"]
    answer = (
        "Section 9(1)(c) exempts uncovered structures under 0.6 m from needing a "
        "development permit. Your rear setback must be 3.0 metres, and the side "
        "setbacks are 2.5 metres."
    )
    scored = topic_hit_rate(answer, topics)
    assert scored["rate"] == 1.0, scored["misses"]

    # A substring scorer — the approach the ticket rules out — scores this
    # perfectly correct answer at zero.
    low = answer.lower()
    assert not any(t in low for t in topics)


def test_topic_tokens_must_co_occur_not_merely_both_appear():
    """"rear" here and "setback" three pages later is not the rear setback."""
    far_apart = "The rear of the lot faces north. " + ("filler word " * 60) + "A setback applies."
    assert topic_hit_rate(far_apart, ["rear_setback"])["rate"] == 0.0
    assert topic_hit_rate("The rear setback is 3.0 m.", ["rear_setback"])["rate"] == 1.0


def test_topic_stemming_is_symmetric_and_conservative():
    assert topic_hit_rate("permitted encroachments", ["permit_encroachment"])["rate"] == 1.0
    # Unrelated prose does not accidentally satisfy a topic.
    assert topic_hit_rate("The lot is 400 square metres.", ["rear_setback"])["rate"] == 0.0


def test_acronym_topics_match_case_sensitively():
    """"far from the lot line" is not a discussion of floor area ratio."""
    assert topic_hit_rate("The deck sits far from the rear lot line.", ["FAR"])["rate"] == 0.0
    assert topic_hit_rate("The FAR limit is 2.0 under the overlay.",
                          ["FAR_overlay"])["rate"] == 1.0


def test_every_topic_in_the_eval_corpus_is_an_underscore_token_label():
    """Guards the assumption the token matcher is built on.

    Case is not constrained — FAR and FAR_overlay are acronyms, handled by
    :func:`test_acronym_topics_match_case_sensitively` — but a topic has to be
    alphanumeric words joined by underscores for tokenisation to mean anything.
    """
    cases = json.loads(PROMPTS_FILE.read_text())
    bad = [
        f"{c['id']}:{t}"
        for c in cases
        for t in c.get("expected_topics", [])
        if not t or not all(part.isalnum() for part in t.split("_"))
    ]
    assert bad == []


# ─── DoD 4: the applicability check ──────────────────────────────────────────


def test_conditional_clause_is_parsed_out_of_the_real_clause_text(corpus):
    frag = corpus.clause_fragment("198", "d")
    assert frag is not None
    cond = clause_condition(frag.text)
    assert cond["conditional"] is True
    assert cond["trigger_zones"] == ["CEN-1", "CEN-2", "COR", "DD", "DH"]

    unconditional = corpus.clause_fragment("198", "f")   # "2.5 metres elsewhere."
    assert clause_condition(unconditional.text)["conditional"] is False


def test_the_198_1_d_side_setback_error_is_flagged(corpus):
    """The exact ABS-462 regression, in miniature.

    The answer establishes HR-1 neighbours and then applies 198(1)(d), whose
    condition is DD/DH/CEN-2/CEN-1/COR abutment. Nothing about the citation is
    fabricated; it simply does not govern.
    """
    answer = (
        "All neighbouring lots are zoned HR-1. Clause 198(1)(d) applies: the "
        "minimum side setback is 0.0 metres."
    )
    findings = check_applicability(extract_clause_citations(answer), corpus, answer)
    assert [f["clause"] for f in findings] == ["d"]
    assert findings[0]["section"] == "198"
    assert set(findings[0]["trigger_zones"]) == {"DD", "DH", "CEN-2", "CEN-1", "COR"}
    assert "HR-1" in findings[0]["zones_in_answer"]


def test_an_answer_that_engages_the_condition_is_not_flagged(corpus):
    """199(1)(a) is equally conditional; quoting its trigger zones clears it.

    This is the false-positive guard: the check only fires when the answer
    never engages the condition at all.
    """
    answer = (
        "Section 199(1)(a) requires 6.0 m where the rear lot line abuts an "
        "ER-3, ER-2, ER-1, CH-2, CH-1, PCF, or RPK zone. Your neighbours are "
        "HR-1, so 199(1)(b) — 3.0 metres elsewhere — governs."
    )
    assert check_applicability(extract_clause_citations(answer), corpus, answer) == []


def test_unconditional_clauses_are_never_flagged(corpus):
    answer = "Section 9(1)(c) exempts uncovered structures under 0.6 m. See also 340(b)."
    assert check_applicability(extract_clause_citations(answer), corpus, answer) == []


def test_the_condition_may_be_established_in_an_earlier_turn(corpus):
    """Context is the whole conversation: turn 1's facts still bind turn 2."""
    turn1 = "Your lot abuts a COR zone to the east."
    turn2 = "Clause 198(1)(d) applies: the side setback is 0.0 metres."
    assert check_applicability(extract_clause_citations(turn2), corpus, turn2) != []
    assert check_applicability(
        extract_clause_citations(turn2), corpus, f"{turn1}\n\n{turn2}"
    ) == []


def test_regrading_the_committed_run_flags_the_side_setback_error(corpus):
    """DoD 4, end to end, on the committed transcript itself.

    Graded against the corpus snapshot: 7/7 citations resolve, no
    hallucinations, references and topics at 100%, keywords above the bar — and
    the case still fails, because clause 198(1)(d) does not apply to this lot.

    Keyword coverage was 100% when this test landed. ABS-470 corrected TC-001's
    expectations -- s.198(1)(f) puts this lot's side yard at 2.5 m, and the
    3.0 m the corpus used to expect is the townhouse branch -- so the one
    keyword this answer never says now scores as the miss it is. It changes
    nothing about the point: `simple` cases are graded against a 0.80 keyword
    bar (KEYWORD_PASS_BAR), 0.833 clears it, and every scalar the grader
    reports would still pass this answer. Only applicability fails it.
    """
    transcript = json.loads(COMMITTED_RUN.read_text())
    spec = next(
        c for c in json.loads(PROMPTS_FILE.read_text()) if c["id"] == "TC-001"
    )
    result = verify_case(corpus, transcript, {**spec, "complexity": "simple",
                                              "liability": "low"})
    grade = result["grade"]

    assert grade["citation_hallucinated"] == 0
    assert grade["citation_found"] == grade["citation_total"] == 7
    assert grade["keyword_rate"] == 0.833
    assert grade["keyword_misses"] == ["2.5 m"]
    assert grade["reference_rate"] == 1.0
    assert grade["topic_rate"] == 1.0
    assert grade["verdict"] == "FAIL_APPLICABILITY"

    findings = grade["applicability_findings"]
    assert len(findings) == 1
    assert findings[0]["citation"] == "198(1)(d)"
    assert findings[0]["page"] == 172
    assert "0.0 metre" in findings[0]["clause_text"]


# ─── Wiring: verdicts, output shape, spec source ─────────────────────────────


def test_hallucination_still_outranks_everything():
    grade = grade_case(
        [{"citation_hallucinated": 1, "citation_total": 2, "citation_found": 1}],
        "simple",
        keywords={"expected": 3, "hit": 3, "rate": 1.0, "misses": []},
        applicability=[{"reason": "x", "clause": "d", "citation": "198(1)(d)"}],
    )
    assert grade["verdict"] == "FAIL_HALLUCINATION"


def test_grade_shape_stays_backwards_compatible():
    """compare_ab_runs.py and the ABS-458 specs read these keys."""
    grade = grade_case(
        [{"citation_hallucinated": 0, "citation_total": 1, "citation_found": 1,
          "hedging_ok": True}],
        "simple",
        keywords={"expected": 2, "hit": 2, "rate": 1.0, "misses": []},
    )
    for key in ("verdict", "reasons", "citation_total", "citation_found",
                "citation_hallucinated", "keyword_rate", "hedging_failed"):
        assert key in grade
    assert grade["verdict"] == "PASS"


def test_empty_transcript_still_grades_fail_with_its_turns_recorded(corpus):
    transcript = {"id": "TC-777", "turns": [{"turn": 1, "assistant_text": "",
                                             "error": "credit exhausted"}]}
    result = verify_case(corpus, transcript, {"complexity": "simple",
                                              "expected_answer_keywords": ["HR-1"]})
    assert result["grade"]["verdict"] == "FAIL"
    assert result["grade"]["keyword_rate"] == 0.0
    assert result["turns"][0]["skipped"] == "empty assistant text"


def test_high_liability_case_without_hedging_cannot_pass(corpus):
    transcript = _transcript("The rear setback is 3.0 m in HR-1.")
    result = verify_case(corpus, transcript, {
        "complexity": "simple", "liability": "high",
        "expected_answer_keywords": ["rear setback", "3.0 m"],
    })
    assert result["grade"]["hedging_failed"] is True
    assert result["grade"]["verdict"] != "PASS"


def test_spec_defaults_to_the_live_eval_file_not_the_frozen_copy():
    """The 20260811 transcript froze TC-001's pre-ABS-463 references.

    Grading against that copy would measure the answer against provisions
    ABS-463 established were the wrong ones ("Table 3" is minimum lot area,
    not setbacks).
    """
    transcript = json.loads(COMMITTED_RUN.read_text())
    assert transcript["spec"]["expected_bylaw_references"] == [
        "Table 3", "Section 9(a)", "Section 9(d)",
    ]
    live = {c["id"]: c for c in json.loads(PROMPTS_FILE.read_text())}
    spec, source = resolve_spec(transcript, live, "prompts")
    assert source == "prompts"
    assert spec["expected_bylaw_references"] == [
        "Section 9(1)(c)", "Section 198", "Section 199",
    ]
    spec, source = resolve_spec(transcript, live, "transcript")
    assert source == "transcript"
    assert spec["expected_bylaw_references"] == ["Table 3", "Section 9(a)", "Section 9(d)"]


def test_clause_citation_extraction_ignores_prose_parentheticals():
    found = {c["raw"] for c in extract_clause_citations(
        "Section 198(1)(d) and 340(b) apply. (See the note.) A 0.6 m (two foot) deck."
    )}
    assert found == {"198(1)(d)", "340(b)"}
