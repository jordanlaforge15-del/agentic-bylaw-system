"""Unit tests for scripts/verify_golden_cases.py — the human-validated tier.

ABS-468. Every expectation in ``evals/regional_centre_test_prompts.json`` was
written by ``claude -p`` and the system under test is a Claude model, so a
generated-case pass means the advisor agrees with a model's guess. The golden
subset is the only ground truth in the project that does not come from a model,
and these tests pin the three properties that make it worth having:

  1. An unattested entry can never pass and holds the deploy gate closed. The
     tempting failure is a placeholder that quietly grades green.
  2. A confidently wrong answer fails on ``must_not_state`` even when it cites
     the right sections — the case the generated grader cannot construct,
     because a model does not know which wrong answer it is inclined to give.
  3. Golden results carry their own verdict vocabulary and their own summary
     file, so nothing can add them to a generated pass rate.

Everything runs offline against ``evals/fixtures/abs462_corpus_snapshot.json``.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.verify_golden_cases import (
    DEFAULT_GOLDEN_FILE,
    gate_status,
    grade_golden_case,
    grade_shape,
    load_golden,
    match_groups,
    validate_golden,
)
from scripts.verify_test_prompts import JsonCorpus

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_FILE = REPO_ROOT / "evals" / "fixtures" / "abs462_corpus_snapshot.json"
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"


@pytest.fixture(scope="module")
def corpus() -> JsonCorpus:
    return JsonCorpus.from_file(CORPUS_FILE)


@pytest.fixture(scope="module")
def golden() -> dict:
    return load_golden(DEFAULT_GOLDEN_FILE)


def _transcript(*texts: str, case_id: str = "TC-001") -> dict:
    return {
        "id": case_id,
        "turns": [{"turn": i + 1, "assistant_text": t} for i, t in enumerate(texts)],
    }


def _attested_case(**overrides) -> dict:
    """A minimal attested entry grading TC-001's real side-setback question."""
    case = {
        "case_id": "TC-001",
        "zone": "HR-1",
        "liability": "low",
        "answer_shape": "determinate",
        "selection_rationale": "…",
        "question_for_reviewer": "…",
        "attestation": {
            "status": "attested",
            "attested_by": {"name": "A. Reviewer", "credential": "MCIP, LPP"},
            "attested_on": "2026-08-20",
            "method": "read the by-law",
            "correct_answer": "2.5 m side setback applies.",
            "governing_provisions": [
                {"reference": "Section 198", "holding": "side setback standards for HR-1"}
            ],
            "must_state": [
                {
                    "id": "side-setback",
                    "description": "gives 2.5 m as the side setback",
                    "any_of": ["2.5 m", "2.5 metres"],
                }
            ],
            "must_not_state": [
                {
                    "id": "no-zero-setback",
                    "description": "must not say the side setback is zero",
                    "any_of": ["0.0 m", "no side setback"],
                }
            ],
        },
    }
    case.update(overrides)
    return case


# ---------------------------------------------------------------------------
# The committed golden file
# ---------------------------------------------------------------------------


def test_committed_golden_file_validates(golden: dict) -> None:
    known = {c["id"] for c in json.loads(PROMPTS_FILE.read_text())}
    assert validate_golden(golden, known) == []


def test_selection_spans_zones_liability_and_answer_shapes(golden: dict) -> None:
    """The DoD's selection constraints, pinned so a later edit cannot erode them.

    A golden subset of six determinate low-liability lookups would be cheap to
    attest and would measure nothing: the answers most likely to be wrong are
    the ones where the by-law does not give a number.
    """
    cases = golden["cases"]
    assert 5 <= len(cases) <= 6
    assert len({c["zone"] for c in cases}) == len(cases), "zones must not repeat"
    liabilities = {c["liability"] for c in cases}
    assert liabilities == {"low", "medium", "high"}
    shapes = {c["answer_shape"] for c in cases}
    assert "refusal" in shapes or "depends" in shapes
    assert all(c["case_id"] for c in cases)


def test_committed_golden_file_is_unattested_and_says_so(golden: dict) -> None:
    """No engineer- or model-authored answer has been slipped into the artifact.

    If this test starts failing, someone attested the subset — which is the
    point — but check *who*: the file is worthless the moment a model fills it.
    """
    statuses = {(c["attestation"] or {}).get("status") for c in golden["cases"]}
    assert statuses == {"unattested"}


def test_golden_case_ids_exist_in_the_generated_eval(golden: dict) -> None:
    known = {c["id"] for c in json.loads(PROMPTS_FILE.read_text())}
    assert {c["case_id"] for c in golden["cases"]} <= known


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_half_filled_attestation_is_rejected(golden: dict) -> None:
    """The dangerous state: reads like ground truth, grades as nothing."""
    payload = copy.deepcopy(golden)
    payload["cases"][0]["attestation"]["correct_answer"] = "2.5 m."
    problems = validate_golden(payload)
    assert any("unattested" in p and "correct_answer" in p for p in problems)


def test_attested_entry_needs_a_named_credentialled_reviewer() -> None:
    payload = {"cases": [_attested_case()]}
    payload["cases"][0]["attestation"]["attested_by"] = {"name": "", "credential": ""}
    problems = validate_golden(payload)
    assert any("attested_by.name" in p for p in problems)
    assert any("credential" in p for p in problems)


def test_a_bare_citation_is_not_an_attestation() -> None:
    payload = {"cases": [_attested_case()]}
    payload["cases"][0]["attestation"]["governing_provisions"] = [
        {"reference": "Section 198"}
    ]
    assert any("holding" in p for p in validate_golden(payload))


def test_attested_entry_without_must_state_is_not_machine_checkable() -> None:
    payload = {"cases": [_attested_case()]}
    payload["cases"][0]["attestation"]["must_state"] = []
    assert any("must_state" in p for p in validate_golden(payload))


def test_unknown_case_id_is_reported() -> None:
    payload = {"cases": [_attested_case(case_id="TC-999")]}
    assert any("no such case" in p for p in validate_golden(payload, {"TC-001"}))


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def test_unattested_case_never_passes(golden: dict, corpus: JsonCorpus) -> None:
    result = grade_golden_case(
        golden["cases"][0], _transcript("Anything at all. Section 198. 2.5 m."), corpus
    )
    assert result["verdict"] == "UNATTESTED"
    assert result["verdict"] not in {"GOLDEN_PASS", "GOLDEN_PARTIAL"}


def test_correct_answer_passes(corpus: JsonCorpus) -> None:
    result = grade_golden_case(
        _attested_case(),
        _transcript("Under Section 198 the side setback is 2.5 m for your lot."),
        corpus,
    )
    assert result["verdict"] == "GOLDEN_PASS", result["reasons"]
    assert result["provisions"]["rate"] == 1.0


def test_confidently_wrong_answer_fails_on_must_not_state(corpus: JsonCorpus) -> None:
    """The ABS-462 regression, graded against a human's answer instead of a model's.

    This answer cites a real, correctly-formatted provision and reads fluently.
    Citation-existence passes it; reference coverage passes it. Only a human
    saying "0.0 m is the wrong answer for this lot" catches it.
    """
    result = grade_golden_case(
        _attested_case(),
        _transcript("Section 198(1)(d) gives you a 0.0 m side setback — build to the line."),
        corpus,
    )
    assert result["verdict"] == "GOLDEN_FAIL"
    assert any("marked wrong" in r for r in result["reasons"])


def test_right_answer_without_the_authority_is_partial_not_pass(corpus: JsonCorpus) -> None:
    result = grade_golden_case(
        _attested_case(),
        _transcript("Your side setback is 2.5 m."),
        corpus,
    )
    assert result["verdict"] == "GOLDEN_PARTIAL"
    assert result["provisions"]["misses"] == ["Section 198"]


def test_depends_case_rejects_an_unconditional_answer(corpus: JsonCorpus) -> None:
    case = _attested_case(answer_shape="depends")
    case["attestation"]["must_state"] = [
        {"id": "precinct", "description": "names the height precinct map",
         "any_of": ["Schedule 15"]}
    ]
    flat = grade_golden_case(
        case, _transcript("Section 198. The maximum height is 90 m under Schedule 15."), corpus
    )
    assert flat["verdict"] == "GOLDEN_FAIL"
    assert any("unconditional" in r for r in flat["reasons"])

    conditional = grade_golden_case(
        case,
        _transcript(
            "Section 198. Height depends on the precinct: Schedule 15 sets the "
            "applicable maximum for this site."
        ),
        corpus,
    )
    assert conditional["verdict"] == "GOLDEN_PASS", conditional["reasons"]


def test_refusal_case_requires_saying_the_bylaw_does_not_settle_it(corpus: JsonCorpus) -> None:
    case = _attested_case(answer_shape="refusal")
    case["attestation"]["must_state"] = [
        {"id": "rpk-absent", "description": "says RPK is not in the use tables",
         "any_of": ["RPK"]}
    ]
    answered_anyway = grade_golden_case(
        case, _transcript("Section 198. RPK permits accessory storage structures."), corpus
    )
    assert answered_anyway["verdict"] == "GOLDEN_FAIL"

    refused = grade_golden_case(
        case,
        _transcript(
            "Section 198. RPK does not appear in Table 1A or 1B, and a boundary "
            "encroachment is outside the scope of the land use by-law."
        ),
        corpus,
    )
    assert refused["verdict"] == "GOLDEN_PASS", refused["reasons"]


def test_missing_transcript_is_not_a_pass(corpus: JsonCorpus) -> None:
    result = grade_golden_case(_attested_case(), None, corpus)
    assert result["verdict"] == "NO_TRANSCRIPT"


def test_provision_absent_from_the_corpus_is_surfaced(corpus: JsonCorpus) -> None:
    """An ingest gap must not be laundered into a grade either way."""
    case = _attested_case()
    case["attestation"]["governing_provisions"] = [
        {"reference": "Section 9999", "holding": "a provision the ingest lacks"}
    ]
    result = grade_golden_case(
        case, _transcript("Under Section 9999 the side setback is 2.5 m."), corpus
    )
    assert result["provisions"]["not_in_corpus"] == ["Section 9999"]
    assert any("absent from the ingested corpus" in r for r in result["reasons"])


def test_a_table_counts_as_cited_in_either_form_it_is_written(
    corpus: JsonCorpus,
) -> None:
    """ABS-524: a table-kind governing provision reaches the answer two ways.

    The model may write the label the way the by-law prints it ("Table 1B") or
    quote the citation_path ``get_zone_profile`` handed it
    ("Part I > [Table 1B]"). Both are the same provision and both must grade as
    cited — otherwise the grader manufactures a miss on a correctly attributed
    answer, which is the opposite of the defect it exists to catch.
    """
    case = _attested_case()
    case["attestation"]["governing_provisions"] = [
        {"reference": "Table 1B", "holding": "grants townhouse dwelling use in ER-3"}
    ]
    for form in ("Table 1B", "Part I > [Table 1B]", "table 1b"):
        result = grade_golden_case(
            case,
            _transcript(f"The use is permitted under {form}; the side setback is 2.5 m."),
            corpus,
        )
        assert result["provisions"]["misses"] == [], f"{form!r} should count as cited"


def test_a_permission_stated_without_its_table_is_partial(corpus: JsonCorpus) -> None:
    """The TC-022 shape: right holding, no attribution. A right answer whose
    authority is missing is never a pass, table-kind provisions included."""
    case = _attested_case()
    case["attestation"]["governing_provisions"] = [
        {"reference": "Table 1B", "holding": "grants townhouse dwelling use in ER-3"}
    ]
    result = grade_golden_case(
        case,
        _transcript(
            "Townhouse dwelling use is permitted in ER-3. Under Section 233 you "
            "may build up to four units; the side setback is 2.5 m."
        ),
        corpus,
    )
    assert result["verdict"] == "GOLDEN_PARTIAL"
    assert result["provisions"]["misses"] == ["Table 1B"]


def test_match_groups_reports_which_phrase_hit() -> None:
    groups = [{"id": "g", "description": "d", "any_of": ["2.5 metres", "2.5 m"]}]
    hit = match_groups("the setback is 2.5 metres", groups)[0]
    assert hit["hit"] and hit["matched_phrase"] == "2.5 metres"
    assert match_groups("the setback is 6 m", groups)[0]["hit"] is False


def test_determinate_shape_imposes_nothing() -> None:
    assert grade_shape("determinate", "6.0 m. Full stop.")["ok"] is True


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_gate_is_closed_while_anything_is_unattested() -> None:
    gate = gate_status([
        {"case_id": "TC-001", "verdict": "GOLDEN_PASS"},
        {"case_id": "TC-002", "verdict": "UNATTESTED"},
    ])
    assert gate["open"] is False
    assert any("unattested: TC-002" in b for b in gate["blockers"])


def test_gate_is_closed_on_partial() -> None:
    gate = gate_status([
        {"case_id": "TC-001", "verdict": "GOLDEN_PASS"},
        {"case_id": "TC-002", "verdict": "GOLDEN_PARTIAL"},
    ])
    assert gate["open"] is False


def test_gate_opens_only_when_every_case_passes() -> None:
    gate = gate_status([
        {"case_id": "TC-001", "verdict": "GOLDEN_PASS"},
        {"case_id": "TC-002", "verdict": "GOLDEN_PASS"},
    ])
    assert gate["open"] is True
    assert gate["gates"] == "production_deploy"


def test_empty_golden_subset_does_not_open_the_gate() -> None:
    assert gate_status([])["open"] is False


def test_golden_verdicts_cannot_collide_with_generated_verdicts() -> None:
    """Nothing can sum the two tiers by accident: the vocabularies are disjoint."""
    generated = {"PASS", "PARTIAL", "FAIL", "FAIL_HALLUCINATION", "FAIL_APPLICABILITY",
                 "NO_DATA"}
    golden = {"GOLDEN_PASS", "GOLDEN_PARTIAL", "GOLDEN_FAIL", "UNATTESTED",
              "NO_TRANSCRIPT"}
    assert generated & golden == set()


# ---------------------------------------------------------------------------
# Heading consistency (ABS-519)
# ---------------------------------------------------------------------------


def test_contradicting_heading_fails_a_case_the_substring_rules_pass(
    corpus: JsonCorpus,
) -> None:
    """The TC-026 defect, in the shape no ``must_not_state`` phrase can catch.

    The body is right, the number the reviewer asked for is present, the
    provision is cited — so ``must_state``, ``must_not_state`` and the
    provision check all pass. Only the structural heading check sees that the
    most scannable line on the page says the opposite of the paragraph under
    it.
    """
    result = grade_golden_case(
        _attested_case(),
        _transcript(
            "Under Section 198 the side setback is 2.5 m.\n\n"
            "### Townhouse Dwelling Use — Permitted in ER-2 (with conditions)\n\n"
            "Table 1B confirms townhouse dwelling use is permitted in the ER-3 "
            "zone, but not in ER-2.\n"
        ),
        corpus,
    )
    assert result["verdict"] == "GOLDEN_FAIL", result["reasons"]
    assert any("contradicts its own section" in r for r in result["reasons"])
    contradiction = result["heading_consistency"]["contradictions"][0]
    assert contradiction["zone"] == "ER-2"
    assert contradiction["suggested_heading"].endswith("Not Permitted in ER-2")


def test_agreeing_heading_still_passes(corpus: JsonCorpus) -> None:
    result = grade_golden_case(
        _attested_case(),
        _transcript(
            "Under Section 198 the side setback is 2.5 m.\n\n"
            "### Townhouse Dwelling Use — Not Permitted in ER-2\n\n"
            "Table 1B confirms townhouse dwelling use is permitted in the ER-3 "
            "zone, but not in ER-2.\n"
        ),
        corpus,
    )
    assert result["verdict"] == "GOLDEN_PASS", result["reasons"]
    assert result["heading_consistency"]["ok"] is True


def test_heading_check_does_not_bleed_across_turns(corpus: JsonCorpus) -> None:
    """A heading in turn 1 does not introduce turn 2's prose.

    Graded over the concatenation, the turn-1 heading would swallow turn 2's
    denial and fail a perfectly coherent conversation.
    """
    result = grade_golden_case(
        _attested_case(),
        _transcript(
            "### Townhouse Dwelling Use — Permitted in ER-3\n\n"
            "Table 1B is the use table for the ER zones. Under Section 198 "
            "the side setback is 2.5 m.",
            "A different question: townhouse dwelling use is not permitted "
            "in ER-3 where the lot has no public-street frontage.",
        ),
        corpus,
    )
    assert result["heading_consistency"]["ok"] is True
