"""Unit coverage for the permitted-use near-miss matcher (ABS-351).

The matcher is what lets a budget-bounded paid answer run resolve a human-style
use term ("Multiple-unit dwelling", "multi unit dwelling") to its canonical
matrix row ("Multi-unit dwelling use") without guessing spellings, and — for a
genuinely ambiguous term ("Dwelling unit") — return ranked suggestions instead
of silently picking a row.
"""
from __future__ import annotations

import pytest

from layer1.semantic.use_matching import UseMatch, match_use, use_match_key

# The residential slice of a Regional-Centre-style permission matrix, as the
# rows bind after enrichment (canonical, lowercased forms).
CANDIDATES = [
    "multi-unit dwelling use",
    "single-unit dwelling use",
    "two-unit dwelling use",
    "office use",
    "restaurant use",
]


# --------------------------------------------------------------------------
# use_match_key — deterministic equivalence
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term",
    [
        "Multi-unit dwelling use",
        "Multiple-unit dwelling",
        "multi unit dwelling",
        "MULTI-UNIT DWELLING USE",
        "multi-unit dwellings",
    ],
)
def test_key_collapses_multi_unit_variants(term):
    """Case, hyphen/space, multiple→multi, and trailing 'use' all key alike."""
    assert use_match_key(term) == "multi unit dwelling"


def test_key_keeps_distinct_terms_apart():
    # "Dwelling unit" only overlaps the multi-unit row — it must NOT collapse
    # onto it (that ambiguity is what forces the suggestion tier).
    assert use_match_key("Dwelling unit") != use_match_key("Multi-unit dwelling use")


# --------------------------------------------------------------------------
# match_use — resolve tier (deterministic key equivalence)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["Multiple-unit dwelling", "multi unit dwelling", "Multi-Unit Dwelling"],
)
def test_near_miss_multi_unit_resolves(query):
    match = match_use(query, CANDIDATES)
    assert match.resolved == "multi-unit dwelling use"
    assert match.suggestions == []


def test_exact_canonical_resolves():
    match = match_use("multi-unit dwelling use", CANDIDATES)
    assert match.resolved == "multi-unit dwelling use"


# --------------------------------------------------------------------------
# match_use — suggest tier (advisory, never silently picks)
# --------------------------------------------------------------------------


def test_ambiguous_dwelling_unit_suggests_not_resolves():
    match = match_use("Dwelling unit", CANDIDATES)
    assert match.resolved is None
    # The intended row is present in the ranked advisory list.
    assert "multi-unit dwelling use" in match.suggestions


def test_shared_key_across_two_rows_is_ambiguous():
    """When two rows share the query's key, resolve must abstain (suggest)."""
    dupes = ["multi-unit dwelling use", "multiple unit dwelling"]
    match = match_use("multi unit dwelling", dupes)
    assert match.resolved is None
    assert set(match.suggestions) == set(dupes)


def test_no_close_match_returns_empty():
    match = match_use("Brewery use", CANDIDATES)
    assert match.resolved is None
    assert match.suggestions == []


def test_token_poor_coincidence_is_filtered():
    # "Residential use" shares no dwelling tokens with any row; the cutoff keeps
    # a misleading "Restaurant use" suggestion out.
    match = match_use("Residential use", CANDIDATES)
    assert match.resolved is None
    assert "restaurant use" not in match.suggestions


def test_empty_candidates_is_empty_match():
    assert match_use("Multi-unit dwelling use", []) == UseMatch()


def test_suggestions_are_capped():
    many = [f"use number {n}" for n in range(20)]
    match = match_use("use number 3", many)
    assert len(match.suggestions) <= 5
