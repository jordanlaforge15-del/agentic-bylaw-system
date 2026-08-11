"""ABS-463: guard `expected_bylaw_references` in the Regional Centre eval suite.

Before this, no case in `evals/regional_centre_test_prompts.json` had a
validated reference set: TC-001..010 carried labels that resolved but pointed at
the wrong provisions (`Table 3` is minimum lot area, not setbacks), and
TC-011..020 carried `[]`. ABS-265 had rewritten the keywords against the real
ingest and left everything else at its synthetic-fixture values.

These tests are what stops the file rotting again after the next re-ingest. They
check the references against
`evals/regional_centre_bylaw_reference_index.json`, the snapshot written by
`scripts/build_bylaw_reference_index.py` from document_id=4. The snapshot exists
because the full 4,341-fragment Halifax ingest is not present in CI or in a
worktree's e2e database — only on a box that has run the real ingest. Whoever
re-ingests re-runs the script; these tests then fail loudly if the eval file and
the corpus have drifted apart.

Semantic correctness — "is this the provision that actually *governs* the
question this case asks?" — cannot be asserted mechanically. That judgement, and
the evidence behind it, is recorded in
`docs/ABS-463-BYLAW-REFERENCE-VERIFICATION.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"
INDEX_FILE = REPO_ROOT / "evals" / "regional_centre_bylaw_reference_index.json"

# Kept in lockstep with scripts/build_bylaw_reference_index.py.
ALLOWED_KINDS = {"section", "section_clause", "table", "schedule", "appendix"}


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return json.loads(PROMPTS_FILE.read_text())


@pytest.fixture(scope="module")
def index() -> dict:
    return json.loads(INDEX_FILE.read_text())


def _refs(case: dict) -> list[str]:
    return case.get("expected_bylaw_references", [])


def test_index_snapshot_exists_and_targets_the_regional_centre_bylaw(index):
    assert index["bylaw_name"] == "Regional Centre Land Use By-Law"
    assert index["document_id"] == 4
    assert index["references"], "snapshot has no references"


def test_every_case_has_at_least_one_bylaw_reference(cases):
    empty = [c["id"] for c in cases if not _refs(c)]
    assert empty == [], (
        f"cases with no expected_bylaw_references: {empty}. TC-011..TC-020 were "
        "empty before ABS-463; do not let a case regress to []."
    )


def test_every_reference_resolves_against_the_corpus(cases, index):
    """The core guard: a reference nobody can look up is worse than none."""
    entries = index["references"]
    failures: list[str] = []
    for case in cases:
        for ref in _refs(case):
            entry = entries.get(ref)
            if entry is None:
                failures.append(
                    f"{case['id']}: {ref!r} is not in the reference index — "
                    "re-run scripts/build_bylaw_reference_index.py"
                )
            elif not entry["resolved"]:
                failures.append(
                    f"{case['id']}: {ref!r} does not resolve to any fragment "
                    f"in document_id={index['document_id']}"
                )
    assert failures == [], "\n".join(failures)


def test_every_reference_has_a_citation_path(index):
    """`resolves` means a real citation_path, not just a matching label.

    The ingest leaves some fragments with a NULL citation_path (Section 9(1)(a),
    120(a), 120(b) at the time of writing). Those provisions are unreachable and
    must not be used as expected references until the ingest is fixed.
    """
    bad: list[str] = []
    for ref, entry in index["references"].items():
        for match in entry["matches"]:
            if not match.get("citation_path"):
                bad.append(f"{ref} -> fragment {match['fragment_id']} has no citation_path")
    assert bad == [], "\n".join(bad)


def test_every_reference_is_unambiguous(index):
    """One reference, one provision.

    The by-law's operative sections share a numbering namespace with the
    internal sections of its appendices ("Section 9" is both a development-permit
    exemption and a shadow-diagram rule). The resolver disambiguates; this test
    fails if a future corpus reintroduces a collision the resolver can't split.
    """
    ambiguous = {
        ref: [m["citation_path"] for m in entry["matches"]]
        for ref, entry in index["references"].items()
        if entry["ambiguous"]
    }
    assert ambiguous == {}, f"references matching more than one fragment: {ambiguous}"


def test_index_covers_exactly_the_references_in_the_eval_file(cases, index):
    """Catches a reference edited into the eval file without regenerating."""
    used = {ref for case in cases for ref in _refs(case)}
    indexed = set(index["references"])
    assert used - indexed == set(), (
        f"references in the eval file but not in the index: {sorted(used - indexed)}"
    )
    assert indexed - used == set(), (
        f"stale index entries no longer referenced by any case: {sorted(indexed - used)}"
    )


def test_reference_kinds_are_within_the_supported_grammar(index):
    unsupported = {
        ref: entry["kind"]
        for ref, entry in index["references"].items()
        if entry["kind"] not in ALLOWED_KINDS
    }
    assert unsupported == {}, f"unsupported reference kinds: {unsupported}"


def test_every_index_entry_records_how_it_was_verified(index):
    """DoD #5: the next person must be able to re-check without redoing research."""
    missing = [
        ref
        for ref, entry in index["references"].items()
        if not entry.get("verified_by", {}).get("sql")
    ]
    assert missing == [], f"index entries with no recorded verification query: {missing}"


def test_discredited_references_are_gone(cases):
    """The specific wrong values ABS-463 was filed to remove.

    Each of these resolves as a label, which is exactly why resolution alone was
    never the bar. `Table 3` is minimum lot area for Established Residential
    Special Areas; the eval used it as a setback table for five cases.
    """
    banned = {
        "Table 3": "minimum lot area (Established Residential Special Areas), not setbacks",
        "Table 4": "minimum lot area (Schmidtville HCD), not setbacks or lot coverage",
        "Table 5": "minimum lot frontage, not height",
        "Section 9(a)": "no citation_path in the ingest; the deck exemption is 9(1)(c)",
        "Section 9(d)": "home office uses, not a deck permit exemption",
        "Section 120(a)": "no citation_path; and Section 120 is a DD streetwall rule",
        "Section 120(b)": "no citation_path; the parking rule is Section 433 + Table 15",
    }
    found: list[str] = []
    for case in cases:
        for ref in _refs(case):
            if ref in banned:
                found.append(f"{case['id']}: {ref} — {banned[ref]}")
    assert found == [], "\n".join(found)


def test_zone_specific_provisions_are_not_cited_for_the_wrong_zone(cases):
    """Part V is chaptered by zone; a section only governs its own chapter.

    The original data cited the DD chapter's Sections 110/111/112/120 for CEN-1,
    CEN-2, DH and COR. This pins the chapter boundaries so that mistake cannot
    come back silently.
    """
    # (first_section, last_section) -> zones the Part V chapter governs
    chapters = [
        ((107, 128), {"DD"}),
        ((129, 148), {"DH"}),
        ((156, 175), {"CEN-2", "CEN-1"}),
        ((176, 194), {"COR"}),
        ((195, 211), {"HR-2", "HR-1"}),
        ((226, 235), {"ER-3", "ER-2", "ER-1"}),
        ((253, 267), {"INS"}),
        ((305, 312), {"PCF", "RPK"}),
    ]
    failures: list[str] = []
    for case in cases:
        for ref in _refs(case):
            if not ref.startswith("Section ") or "(" in ref:
                continue
            number = int(ref.split()[1])
            for (low, high), zones in chapters:
                if low <= number <= high and case["zone"] not in zones:
                    failures.append(
                        f"{case['id']} (zone {case['zone']}): {ref} belongs to the "
                        f"Part V chapter governing {sorted(zones)}"
                    )
    assert failures == [], "\n".join(failures)


def test_permitted_use_table_matches_the_zone(cases):
    """Table 1A/1B/1C each cover a disjoint set of zones (nine cases had 1A/1B swapped)."""
    table_zones = {
        "Table 1A": {"DD", "DH", "CEN-2", "CEN-1", "COR", "HR-2", "HR-1"},
        "Table 1B": {"ER-3", "ER-2", "ER-1", "CH-2", "CH-1"},
        "Table 1C": {
            "CLI", "LI", "HRI", "INS", "UC-2", "UC-1", "DND", "H", "PCF", "RPK", "WA",
        },
    }
    failures: list[str] = []
    for case in cases:
        for ref in _refs(case):
            zones = table_zones.get(ref)
            if zones is not None and case["zone"] not in zones:
                failures.append(
                    f"{case['id']} (zone {case['zone']}) cites {ref}, which covers "
                    f"{sorted(zones)}"
                )
    assert failures == [], "\n".join(failures)


def test_tc001_zone_matches_the_geocoded_zone(cases):
    """1234 Oxford Street sits in the HR-1 polygon, not ER-1.

    Source of truth: the `halifax_zoning_boundaries` external dataset. The
    geocoded point (-63.5957318, 44.6344483) intersects GlobalID
    5f05d7eb-5062-473c-b463-465c7443cd8d, ZONE=HR-1 ("Higher-Order Residential
    1", bylaw area 23, effective 2024-06-13), on parcel PID 00078147. The case's
    own expected_answer_keywords already said HR-1; only the `zone` field was
    stale.
    """
    tc001 = next(c for c in cases if c["id"] == "TC-001")
    assert tc001["zone"] == "HR-1"
    assert "HR-1" in tc001["expected_answer_keywords"]
    # The turn text deliberately misstates the zone — that is the test, not a bug.
    assert "ER-1" in tc001["turns"][0]["message"]
