"""ABS-471 (G2, G3): eval citations must govern the zone their case claims.

``expected_answer_keywords`` is the field the grader actually scores, and until
this module it had **no validation of any kind**. ``expected_bylaw_references``
had one guard — ABS-463's index, which proves a citation *resolves* — and every
reference defect ABS-470 fixed sailed through it, because each of them resolved
perfectly well. ``Section 200`` is a real provision; it just governs HR-2 and
HR-1, not the DD, COR, INS and CDD-2 cases that were citing it.

The rule both fields are now held to: **a section governs only the zones its
chapter names, and a permitted-use table covers only the zones in its caption.**
It lives in ``scripts/eval_zone_chapters.py``, over boundaries
``scripts/build_zone_chapter_map.py`` derives from the corpus. Nothing here
hardcodes a section range.

Two tiers:

* **Offline** — the rule against the committed corpus and against the specific
  regressions ABS-470 removed. No database; runs anywhere, mirrored in
  ``web/e2e/functional/abs471-eval-corpus-guards.spec.ts``.
* **Live** — every citation named in ``expected_answer_keywords`` is *resolved*
  against the real ingest, the half ABS-463 only ever did for
  ``expected_bylaw_references``. Skips cleanly where the corpus is absent.

**Out of scope, deliberately:** numeric keywords (``6.0 m``, ``80%``). Three
cases asserted lot-coverage percentages against sections reading "No maximum
required lot coverage applies", and no rule available here would have caught it
— see docs/ABS-471-EVAL-CORPUS-GUARDS.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from scripts.build_bylaw_reference_index import (
    BYLAW_NAME,
    DEFAULT_DB_URL,
    UnparseableReference,
    resolution_plan,
    resolve,
    zone_appropriateness_failures,
)
from scripts.build_zone_chapter_map import load_map
from scripts.eval_zone_chapters import (
    case_violations,
    citations_in,
    zone_violation,
    zones_governing_section,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"

GUARDED_FIELDS = ("expected_answer_keywords", "expected_bylaw_references")


@pytest.fixture(scope="module")
def chapter_map() -> dict[str, Any]:
    return load_map()


def load_cases() -> list[dict[str, Any]]:
    return json.loads(PROMPTS_FILE.read_text())


CASES = load_cases()
CASE_IDS = [case["id"] for case in CASES]


# ---------------------------------------------------------------------------
# The tokeniser
# ---------------------------------------------------------------------------


def test_every_citation_shape_the_eval_uses_is_recognised():
    found = citations_in(
        ["Section 254", "Section 9(1)(c)", "Table 1A", "Schedule 15", "3.0 m", "HR-1"]
    )
    assert [(c.kind, c.label) for c in found] == [
        ("section", "Section 254"),
        ("section", "Section 9"),
        ("table", "Table 1A"),
        ("schedule", "Schedule 15"),
    ]
    # A clause citation keeps its full text for the failure message but is
    # placed by its section number, which is what carries the chapter.
    assert found[1].text == "Section 9(1)(c)" and found[1].number == 9


def test_a_citation_embedded_in_a_longer_keyword_is_still_found():
    """Keywords are usually bare tokens, but nothing enforces that.

    A guard that only matched whole strings would silently skip
    "see Section 200 for stepbacks" — the exact kind of near-miss that let the
    original defects through.
    """
    found = citations_in(["the streetwall rule in Section 200 applies"])
    assert [c.label for c in found] == ["Section 200"]


def test_a_repeated_citation_is_reported_once():
    assert len(citations_in(["Section 200", "Section 200"])) == 1


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_general_provision_is_not_pinned_to_any_zone(chapter_map):
    """Part I administration and Part XIII parking govern every zone.

    Over-constraining here would be worse than under-constraining: it would
    fail correct cases and train the next person to delete the guard.
    """
    assert zones_governing_section(9, chapter_map) is None  # development permit
    assert zones_governing_section(433, chapter_map) is None  # motor vehicle parking
    assert zones_governing_section(331, chapter_map) is None  # accessory structures


def test_a_zone_chapter_pins_its_own_sections(chapter_map):
    assert zones_governing_section(200, chapter_map) == ["HR-2", "HR-1"]
    assert zones_governing_section(111, chapter_map) == ["DD"]
    assert zones_governing_section(344, chapter_map) == ["HCD-SV"]


@pytest.mark.parametrize(
    "zone,token,governs",
    [
        # Every regression named in ABS-471, all of them real, all of them from
        # the audit of the pre-ABS-470 file.
        ("INS", "Section 196", "HR-2, HR-1"),
        ("DD", "Section 200", "HR-2, HR-1"),
        ("COR", "Section 200", "HR-2, HR-1"),
        ("CDD-2", "Section 196", "HR-2, HR-1"),
        ("DH", "Section 111", "DD"),
        ("ER-3", "Section 344", "HCD-SV"),
    ],
)
def test_the_rule_rejects_a_provision_from_another_zone_s_chapter(
    zone, token, governs, chapter_map
):
    citation = citations_in([token])[0]
    reason = zone_violation(zone, citation, chapter_map)
    assert reason is not None, f"{token} on a {zone} case must not pass"
    assert token in reason and governs in reason and zone in reason, reason


def test_the_rule_rejects_the_wrong_permitted_use_table(chapter_map):
    reason = zone_violation("RPK", citations_in(["Table 1A"])[0], chapter_map)
    assert reason is not None
    assert "Table 1C" in reason, "name the table the zone IS in, not just the error"


def test_the_rule_accepts_a_provision_from_the_case_s_own_chapter(chapter_map):
    for zone, token in [
        ("HR-2", "Section 200"),
        ("HR-1", "Section 196"),
        ("DD", "Section 111"),
        ("INS", "Section 254"),
        ("RPK", "Table 1C"),
        ("ER-3", "Table 1B"),
    ]:
        citation = citations_in([token])[0]
        assert zone_violation(zone, citation, chapter_map) is None, f"{token} in {zone}"


def test_a_schedule_is_not_constrained_by_zone(chapter_map):
    """Schedules are Part I; a height precinct schedule serves every zone."""
    assert zone_violation("ER-3", citations_in(["Schedule 15"])[0], chapter_map) is None


# ---------------------------------------------------------------------------
# The rule, over the committed corpus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
@pytest.mark.parametrize("field", GUARDED_FIELDS)
def test_every_case_cites_only_its_own_zone_s_chapters(case, field, chapter_map):
    failures = case_violations(case, field, chapter_map)
    assert failures == [], "\n".join(failures)


def test_the_reference_index_builder_agrees(chapter_map):
    """G3 is the same rule reached through `--check`'s entry point.

    Asserting it here keeps the builder's guard from silently diverging from
    the one the eval suite is held to.
    """
    assert zone_appropriateness_failures() == []


def test_the_guard_actually_reaches_most_of_the_corpus():
    """How much of `expected_answer_keywords` G2 can see at all.

    Nine of the twenty cases keep their keywords purely descriptive ("rear
    setback", "3.0 m", "permitted") and name no provision, so G2 has nothing to
    check on them — a coverage limit worth stating rather than discovering. The
    floor is here so a rewrite that strips citations out of the keyword sets
    cannot quietly reduce this guard to a no-op.
    """
    with_citations = [c["id"] for c in CASES if citations_in(c["expected_answer_keywords"])]
    assert len(with_citations) >= 11, (
        "expected_answer_keywords name a Section/Table/Schedule in only "
        f"{len(with_citations)} of {len(CASES)} cases ({with_citations}); G2 "
        "cannot check a keyword set that cites nothing"
    )


# ---------------------------------------------------------------------------
# Live: keyword citations must resolve against the real corpus
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_corpus():
    """A connection to the real Regional Centre ingest, or a clean skip."""
    sa = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
    db_url = os.environ.get("BYLAW_REFERENCE_DB_URL", DEFAULT_DB_URL)
    try:
        engine = sa.create_engine(db_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            document_id = conn.execute(
                sa.text("SELECT id FROM document WHERE bylaw_name = :name ORDER BY id LIMIT 1"),
                {"name": BYLAW_NAME},
            ).scalar()
            if document_id is None:
                pytest.skip(f"no document row for bylaw_name={BYLAW_NAME!r} at {db_url}")
            yield conn, document_id
    except sa.exc.SQLAlchemyError as exc:
        pytest.skip(f"Regional Centre corpus not reachable at {db_url}: {type(exc).__name__}")


# Schedules the eval cites that the ingest does not carry as a fragment.
#
# The by-law publishes ~51 schedules; the Regional Centre ingest carries six
# (7, 15, 17, 22, 50, 51) because the rest are map plates the parser emits as
# pictures, not text fragments. So an unresolvable *schedule* keyword is a
# statement about the ingest's coverage, not about the eval — unlike an
# unresolvable section or table, which the ingest carries comprehensively
# (511 sections, every numbered table) and which is therefore a real defect.
#
# The set is explicit so a *new* unresolvable schedule fails and gets a human
# decision, instead of the whole class being waved through.
SCHEDULES_NOT_INGESTED = {"Schedule 18"}


def test_every_keyword_section_or_table_resolves_against_the_corpus(live_corpus):
    """The half ABS-463 only ever did for `expected_bylaw_references`.

    A keyword naming a section the by-law does not have cannot be scored, and
    the grader would mark a correct answer wrong for not containing it.
    """
    conn, document_id = live_corpus
    failures: list[str] = []
    for case in CASES:
        for citation in citations_in(case["expected_answer_keywords"]):
            if citation.kind == "schedule":
                continue  # see test_unresolvable_keyword_schedules_are_a_known_gap
            try:
                entry = resolve(conn, document_id, citation.label)
            except UnparseableReference as exc:
                failures.append(f"{case['id']}: {citation.text!r} — {exc}")
                continue
            if not entry["resolved"]:
                failures.append(
                    f"{case['id']} (expected_answer_keywords): {citation.label!r} "
                    f"does not resolve to any fragment in document_id={document_id}"
                )
    assert failures == [], "\n".join(failures)


def test_unresolvable_keyword_schedules_are_a_known_gap(live_corpus):
    """A schedule keyword either resolves or is on the ingest-coverage list.

    Keeps the exemption above from becoming a blanket one: a case that starts
    citing Schedule 19 fails here until someone decides whether the schedule is
    missing from the ingest or missing from the by-law.
    """
    conn, document_id = live_corpus
    unexpected: list[str] = []
    for case in CASES:
        for citation in citations_in(case["expected_answer_keywords"]):
            if citation.kind != "schedule":
                continue
            if resolve(conn, document_id, citation.label)["resolved"]:
                continue
            if citation.label not in SCHEDULES_NOT_INGESTED:
                unexpected.append(
                    f"{case['id']} (expected_answer_keywords): {citation.label!r} "
                    f"does not resolve in document_id={document_id} and is not a "
                    "known ingest gap — check whether the schedule exists in the "
                    "by-law at all before adding it to SCHEDULES_NOT_INGESTED"
                )
    assert unexpected == [], "\n".join(unexpected)


def test_the_known_schedule_gap_is_still_a_gap(live_corpus):
    """Delete the exemption once the ingest carries the schedule.

    Without this the list would outlive the problem and start hiding real
    defects behind a stale excuse.
    """
    conn, document_id = live_corpus
    resolved_now = [
        label for label in SCHEDULES_NOT_INGESTED if resolve(conn, document_id, label)["resolved"]
    ]
    assert resolved_now == [], (
        f"{resolved_now} now resolve against the corpus — remove them from "
        "SCHEDULES_NOT_INGESTED so they are checked like every other citation"
    )


def test_a_fabricated_keyword_citation_would_be_caught(live_corpus):
    """Prove the resolution check bites rather than trusting that it would."""
    conn, document_id = live_corpus
    # The by-law's operative sections stop well short of 9999.
    assert resolution_plan("Section 9999")["kind"] == "section"
    assert not resolve(conn, document_id, "Section 9999")["resolved"]
