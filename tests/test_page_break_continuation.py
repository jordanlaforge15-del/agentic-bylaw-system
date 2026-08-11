"""Regression guard for ABS-461: a page break inside a hyphenated zone code.

Pages 171/172 of the Regional Centre LUB break clause 198(1)(a) mid-token:

    (a) ... any portion of which, is zoned ER-3, ER-      <- end of page 171
    2, ER-1, CH-2, CH-1, PCF, or RPK zone: ...            <- start of page 172

The tail starts with a bare number, so the numeric section regex read it as
section "2" and every following clause -- 198(1)(b) through (f), plus (b)'s
sub-clauses -- reparented under a phantom ``Part V > 2``. Retrieval then served
those clauses detached from the subsection that scopes them, which produced a
wrong side-setback answer (0.0 m instead of 2.5 m) in eval case TC-001.

The block texts below are verbatim ``page_block.raw_text`` values from
``document_id=4`` (blocks 8458-8472) on the dev corpus.
"""
from layer1.models.enums import BlockType, FragmentType
from layer1.models.schemas import PageBlockData
from layer1.pipeline.hierarchy import reconstruct_hierarchy

SIDE_SETBACK_BLOCKS: list[tuple[int, BlockType, str]] = [
    (170, BlockType.HEADING, "Part V Land Use"),
    (171, BlockType.HEADING, "Side Setback Requirements"),
    (
        171,
        BlockType.LIST_ITEM,
        (
            "198 (1) Subject to Subsections 198(2) and 198(3), the minimum required "
            "side setback for any main building shall be:"
        ),
    ),
    (
        171,
        BlockType.LIST_ITEM,
        (
            "(a) subject to Clauses 198(1)(b) and 198(1)(c), where a lot line abuts a "
            "lot, any portion of which, is zoned ER-3, ER-"
        ),
    ),
    (172, BlockType.LIST_ITEM, "2, ER-1, CH-2, CH-1, PCF, or RPK zone: (RCCC-Sep 4/24;E-Apr 17/25)"),
    (
        172,
        BlockType.LIST_ITEM,
        (
            "(i) 3.0 metres from the side lot line abutting the lot for any low-rise "
            "building, or (RCCC-Sep 4/24;E-Apr 17/25)"
        ),
    ),
    (
        172,
        BlockType.LIST_ITEM,
        (
            "(ii) 6.0 metres from the side lot line abutting the lot for any mid-rise, "
            "tall mid-rise, or high-rise building; (RCCC-Sep 4/24;E-Apr 17/25)"
        ),
    ),
    (172, BlockType.LIST_ITEM, "(b) for a townhouse dwelling use:"),
    (172, BlockType.LIST_ITEM, "(i) 0.0 metre along a common wall between each unit, or"),
    (172, BlockType.LIST_ITEM, "(ii) 3.0 metres elsewhere;"),
    (
        172,
        BlockType.LIST_ITEM,
        "(c) for a semi-detached dwelling use or duplex apartment use: (RCCC-Sep 4/24;E-Apr 17/25)",
    ),
    (172, BlockType.LIST_ITEM, "(i) 0.0 metre along a common wall between each unit, or"),
    (172, BlockType.LIST_ITEM, "(ii) 3.0 metres elsewhere;"),
    (
        172,
        BlockType.LIST_ITEM,
        (
            "(d) where a lot line abuts a lot, any portion of which, is zoned DD, DH, "
            "CEN-2, CEN-1, or COR zone, 0.0 metre, except as provided in Clause "
            "198(1)(a); (RCCC-Sep 4/24;E-Apr 17/25)"
        ),
    ),
    (
        172,
        BlockType.LIST_ITEM,
        (
            "(e) where a lot line abuts lands governed by the Downtown Halifax "
            "Secondary Municipal Planning Strategy and the Downtown Halifax Land Use "
            "By-law, 0.0 metre; or"
        ),
    ),
    (172, BlockType.LIST_ITEM, "(f) 2.5 metres elsewhere."),
]


def _blocks(rows: list[tuple[int, BlockType, str]]) -> list[PageBlockData]:
    return [
        PageBlockData(
            page_number=page,
            block_type=block_type,
            reading_order=1750 + order,
            raw_text=text,
            normalized_text=text,
            parser_source="docling",
        )
        for order, (page, block_type, text) in enumerate(rows)
    ]


def _side_setback_fragments():
    return reconstruct_hierarchy(_blocks(SIDE_SETBACK_BLOCKS))


def _by_label(fragments, label: str, *, fragment_type: FragmentType | None = None):
    return [
        fragment
        for fragment in fragments
        if fragment.citation_label == label
        and (fragment_type is None or fragment.fragment_type == fragment_type)
    ]


def test_page_break_does_not_create_a_phantom_section():
    """DoD 1: no fragment may hang off a ``Part V > 2`` that does not exist."""
    fragments = _side_setback_fragments()
    assert not [f for f in fragments if f.citation_label == "2"]
    assert not [f for f in fragments if (f.citation_path or "").startswith("Part V > 2 ")]
    assert not [f for f in fragments if f.citation_path == "Part V > 2"]


def test_truncated_clause_carries_the_complete_zone_list():
    """DoD 3: 198(1)(a)'s text no longer terminates at ``ER-``."""
    (clause_a,) = _by_label(_side_setback_fragments(), "(a)")
    assert not clause_a.text.rstrip().endswith("ER-")
    # The hyphen closes over the break the way the page rendered it: ER-2, not "ER- 2".
    assert "ER-3, ER-2, ER-1, CH-2, CH-1, PCF, or RPK zone:" in clause_a.text
    # Provenance and page range span both halves of the break.
    assert len(clause_a.source_block_indices) == 2
    assert (clause_a.page_start, clause_a.page_end) == (171, 172)


def test_following_clauses_are_siblings_of_clause_a():
    """DoD 2: (b)-(f) and (b)'s sub-clauses sit under the same subsection as (a)."""
    fragments = _side_setback_fragments()
    (clause_a,) = _by_label(fragments, "(a)")
    parent_path = clause_a.citation_path.rsplit(" > ", 1)[0]

    for label in ("(b)", "(c)", "(d)", "(e)", "(f)"):
        (clause,) = _by_label(fragments, label)
        assert clause.citation_path == f"{parent_path} > {label}", label
        assert clause.parent_index == clause_a.parent_index, label

    sub_clause_paths = [
        f.citation_path
        for f in fragments
        if f.fragment_type == FragmentType.SUBCLAUSE
        and (f.citation_path or "").startswith(f"{parent_path} > (b) > ")
    ]
    assert len(sub_clause_paths) == 2
    assert [path.rsplit(" > ", 1)[-1] for path in sub_clause_paths] == ["(i)", "(ii)"]


def test_clause_f_is_the_catch_all_setback():
    """DoD 4's precondition: (f) is reachable under the 198 subtree.

    This is the clause the advisor needed in TC-001 -- the catch-all
    "2.5 metres elsewhere" that governs when no other clause's condition is met.
    """
    (clause_f,) = _by_label(_side_setback_fragments(), "(f)")
    assert clause_f.text == "(f) 2.5 metres elsewhere."
    assert "198" in clause_f.citation_path


def test_hyphen_break_join_is_scoped_to_mid_token_breaks():
    """A paragraph that merely ends in a dash is not swallowed by the next one."""
    fragments = reconstruct_hierarchy(
        _blocks(
            [
                (1, BlockType.HEADING, "Part V Land Use"),
                (1, BlockType.LIST_ITEM, "10 A section that ends in an em dash —"),
                (2, BlockType.LIST_ITEM, "11 A genuinely new section."),
            ]
        )
    )
    assert [f.citation_label for f in fragments] == ["Part V", "10", "11"]


def test_numbered_heading_after_a_hyphen_ending_block_still_parses():
    """The join must not fire when the continuation is a heading."""
    fragments = reconstruct_hierarchy(
        _blocks(
            [
                (1, BlockType.HEADING, "Part V Land Use"),
                (1, BlockType.LIST_ITEM, "10 Lands zoned ER-"),
                (2, BlockType.HEADING, "11 Rear Setback Requirements"),
            ]
        )
    )
    assert [f.citation_label for f in fragments] == ["Part V", "10", "11"]


def test_wrapped_line_inside_one_block_is_not_a_section_start():
    """The same defect, but with the break landing inside a single block.

    ``_split_block_for_hierarchy`` splits multi-line blocks on citation-start
    lines; a wrapped zone list must not qualify as one.
    """
    fragments = reconstruct_hierarchy(
        _blocks(
            [
                (1, BlockType.HEADING, "Part V Land Use"),
                (
                    1,
                    BlockType.LIST_ITEM,
                    (
                        "(a) where a lot line abuts a lot, any portion of which, is "
                        "zoned ER-3, ER-\n2, ER-1, CH-2, CH-1, PCF, or RPK zone;"
                    ),
                ),
                (1, BlockType.LIST_ITEM, "(b) 2.5 metres elsewhere."),
            ]
        )
    )
    assert not [f for f in fragments if f.citation_label == "2"]
    assert [f.citation_label for f in fragments] == ["Part V", "(a)", "(b)"]
