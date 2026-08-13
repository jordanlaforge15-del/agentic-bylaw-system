"""ABS-488: clause paths carry their container, Part paths carry their chapter."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from layer1.models.enums import FragmentType
from layer1.pipeline.citation_repath import (
    MAX_CONTEXT_SEGMENT_CHARS,
    context_segment,
    part_label_with_chapter,
    repath_low_level_fragments,
)


@dataclass
class Node:
    """A stand-in for the fields ``RepathNode`` reads off a fragment."""

    fragment_type: FragmentType
    citation_label: str | None = None
    citation_path: str | None = None
    text: str = ""


def clause(label: str, text: str | None = None) -> Node:
    return Node(FragmentType.CLAUSE, label, None, text or f"{label} some requirement.")


def subclause(label: str, text: str | None = None) -> Node:
    return Node(FragmentType.SUBCLAUSE, label, None, text or f"{label} some requirement.")


def section(label: str, path: str, text: str = "") -> Node:
    return Node(FragmentType.SECTION, label, path, text or f"{label} A section.")


def stem(text: str) -> Node:
    return Node(FragmentType.LIST_ITEM, None, None, text)


def heading(text: str) -> Node:
    return Node(FragmentType.HEADING, None, None, text)


# --- Part chapters ---------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "title", "expected"),
    [
        ("Part I", ", Chapter 2: Development Permit", "Part I, Chapter 2"),
        ("Part V", ", Chapter 19: Accessory Structures", "Part V, Chapter 19"),
        ("Part X", "Chapter 3: Waterfront View Corridors", "Part X, Chapter 3"),
        ("Part I", "Administration", "Part I"),
        ("Part I", "", "Part I"),
        # A title that merely mentions a chapter is not one.
        ("Part V", "Requirements as set out in Chapter 4", "Part V"),
    ],
)
def test_part_label_folds_in_its_chapter(label: str, title: str, expected: str) -> None:
    assert part_label_with_chapter(label, title) == expected


# --- Context segments ------------------------------------------------------


def test_short_container_text_is_carried_whole() -> None:
    assert context_segment("Development Permit Exemptions") == "[Development Permit Exemptions]"


def test_container_segment_drops_trailing_punctuation() -> None:
    assert context_segment("the following shall apply:") == "[the following shall apply]"


def test_long_container_keeps_a_tail_so_shared_openings_stay_distinct() -> None:
    """Two by-law stems routinely share 200 characters and differ only at the end."""
    shared = (
        "Where a non-conforming use in a structure exists, the structure may be rebuilt, "
        "replaced, or repaired if destroyed or damaged by fire or otherwise, up to one "
        "hundred percent of the market value of the building including its foundation, "
    )
    first = context_segment(shared + "providing:")
    second = context_segment(shared + "providing: (RCCC-Oct 26/22;E-Nov 11/22)")
    assert first is not None and second is not None
    assert first != second
    assert len(first) <= MAX_CONTEXT_SEGMENT_CHARS + len("[] ... ")


def test_blank_container_contributes_nothing() -> None:
    assert context_segment("   ") is None


# --- The walk --------------------------------------------------------------


def test_clause_directly_under_a_section_needs_no_decoration() -> None:
    nodes = [section("9", "Part I > 9"), clause("(a)"), clause("(b)")]
    assert repath_low_level_fragments(nodes) == ["Part I > 9", "Part I > 9 > (a)", "Part I > 9 > (b)"]


def test_a_restarting_clause_group_adopts_the_stem_that_scopes_it() -> None:
    """The ABS-488 headline case: 9(1)(a) and 9(2)(a) must not compute one path."""
    nodes = [
        heading("Development Permit Exemptions"),
        section("9", "Part I > 9"),
        clause("(a)", "(a) accessory structures of 20.0 square metres or less;"),
        clause("(b)", "(b) kiosks of 20.0 square metres or less."),
        stem("On a registered heritage property, a development permit is required for:"),
        clause("(a)", "(a) uncovered structures less than 0.6 metre in height;"),
        clause("(b)", "(b) fences."),
    ]
    paths = repath_low_level_fragments(nodes)
    assert paths[2] == "Part I > 9 > (a)"
    assert paths[5] == (
        "Part I > 9 > [On a registered heritage property, a development permit is required for] > (a)"
    )
    assert len(set(paths[2:4]) & set(paths[5:])) == 0


def test_prose_wrapping_mid_list_does_not_split_the_group() -> None:
    """``(b)`` continues ``(a)``; the interrupting paragraph is not a new scope."""
    nodes = [
        section("12", "Part I > 12"),
        clause("(a)"),
        Node(FragmentType.PROSE, None, None, "and, for greater certainty,"),
        clause("(b)"),
    ]
    paths = repath_low_level_fragments(nodes)
    assert paths[1] == "Part I > 12 > (a)"
    assert paths[3] == "Part I > 12 > (b)"


def test_two_headings_still_disambiguate_repeated_clause_labels() -> None:
    nodes = [
        Node(FragmentType.SUBSECTION, "94(1)", "94(1)"),
        heading("5515/17/19 and 5523 Inglis Street"),
        clause("(p)"),
        heading("Cathedral Church of All Saints"),
        clause("(p)"),
    ]
    paths = repath_low_level_fragments(nodes)
    assert paths[2] == "94(1) > [5515/17/19 and 5523 Inglis Street] > (p)"
    assert paths[4] == "94(1) > [Cathedral Church of All Saints] > (p)"


def test_subclauses_hang_off_the_clause_above_them() -> None:
    nodes = [section("10", "Part I > 10"), clause("(b)"), subclause("(i)"), subclause("(ii)")]
    paths = repath_low_level_fragments(nodes)
    assert paths[2] == "Part I > 10 > (b) > (i)"
    assert paths[3] == "Part I > 10 > (b) > (ii)"


def test_roman_i_after_a_distant_clause_opens_a_nested_list() -> None:
    """``198 > (a) > (i)``, not a second ``198 > (i)``. Both groups must survive."""
    nodes = [
        section("198", "Part V > 198"),
        clause("(a)", "(a) where a lot line abuts a lot:"),
        clause("(i)", "(i) 3.0 metres for any low-rise building, or"),
        subclause("(ii)", "(ii) 6.0 metres for any high-rise building;"),
        clause("(c)", "(c) for a semi-detached dwelling use:"),
        clause("(i)", "(i) 0.0 metre along a common wall, or"),
        subclause("(ii)", "(ii) 3.0 metres elsewhere;"),
    ]
    paths = repath_low_level_fragments(nodes)
    assert paths[2] == "Part V > 198 > (a) > (i)"
    assert paths[3] == "Part V > 198 > (a) > (ii)"
    assert paths[5] == "Part V > 198 > (c) > (i)"
    assert paths[6] == "Part V > 198 > (c) > (ii)"


def test_roman_i_that_simply_follows_h_stays_a_clause() -> None:
    """The other side of the same coin: ``(h)`` then ``(i)`` is the ninth letter."""
    nodes = [section("9", "Part I > 9"), clause("(h)"), clause("(i)"), clause("(j)")]
    assert repath_low_level_fragments(nodes)[1:] == [
        "Part I > 9 > (h)",
        "Part I > 9 > (i)",
        "Part I > 9 > (j)",
    ]


def test_uppercase_enumerators_are_a_level_below_the_lowercase_list() -> None:
    """``(A)`` case-folds onto ``(a)``; only the rendered text can tell them apart."""
    nodes = [
        section("420", "Part X > 420"),
        clause("(b)", "(b) if the required landscaped area is at least 3.0 metres:"),
        subclause("(ii)", "(ii) at least one of the following materials for groundcover:"),
        clause("(a)", "(A) vegetation,"),
        clause("(b)", "(B) brick pavers, or"),
    ]
    paths = repath_low_level_fragments(nodes)
    assert paths[3] == "Part X > 420 > (b) > (ii) > (a)"
    assert paths[4] == "Part X > 420 > (b) > (ii) > (b)"
    assert len(set(p for p in paths if p)) == len([p for p in paths if p])


def test_a_dropped_numeral_does_not_start_a_new_list() -> None:
    """The renderer loses ``(iii)`` and ``(iv)``; ``(v)`` still belongs to the group."""
    nodes = [section("499", "Part X > 499"), clause("(b)"), subclause("(ii)"), subclause("(v)")]
    assert repath_low_level_fragments(nodes)[3] == "Part X > 499 > (b) > (v)"


def test_a_clause_with_no_addressable_ancestor_claims_no_path() -> None:
    nodes = [stem("Some floating text"), clause("(a)")]
    assert repath_low_level_fragments(nodes) == [None, None]


def test_non_low_level_fragments_keep_the_path_they_arrived_with() -> None:
    nodes = [section("9", "Part I > 9"), heading("Exemptions"), Node(FragmentType.PART, "Part I", "Part I")]
    assert repath_low_level_fragments(nodes)[::2] == ["Part I > 9", "Part I"]


def test_compound_clause_labels_are_left_alone() -> None:
    """``499(94)(f)`` already spells out its address; re-anchoring would repeat it."""
    nodes = [
        section("499", "Part X > 499"),
        Node(FragmentType.CLAUSE, "499(94)(f)", "Part X > 499 > 499(94)(f)", "499(94)(f) a corner lot."),
    ]
    assert repath_low_level_fragments(nodes)[1] == "Part X > 499 > 499(94)(f)"
