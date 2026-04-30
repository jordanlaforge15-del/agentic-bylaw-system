from layer1.models.enums import BlockType, ParseStatus
from layer1.models.schemas import PageBlockData
from layer1.pipeline.hierarchy import reconstruct_hierarchy


def block(text: str, order: int, block_type: BlockType = BlockType.PARAGRAPH) -> PageBlockData:
    return PageBlockData(
        page_number=1,
        block_type=block_type,
        reading_order=order,
        raw_text=text,
        normalized_text=text,
        parser_source="test",
    )


def test_reconstructs_numbered_hierarchy():
    fragments = reconstruct_hierarchy(
        [
            block("Part 1 Administration", 0, BlockType.HEADING),
            block("1.1 Purpose", 1, BlockType.HEADING),
            block("The purpose text.", 2),
            block("(a) A clause.", 3, BlockType.LIST_ITEM),
        ]
    )
    assert [f.citation_label for f in fragments[:3]] == ["Part 1", "1.1", None]
    assert fragments[1].parent_index == 0
    assert fragments[2].parent_index == 1
    assert fragments[3].citation_label == "(a)"


def test_uncertain_fragment_is_preserved():
    fragments = reconstruct_hierarchy([block("General unparented statement.", 0)])
    assert len(fragments) == 1
    assert fragments[0].parse_status == ParseStatus.UNCERTAIN


def test_repeated_orphan_clauses_do_not_get_bare_duplicate_paths():
    fragments = reconstruct_hierarchy(
        [
            block("(a) First orphan clause.", 0, BlockType.LIST_ITEM),
            block("(a) Second orphan clause.", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[0].citation_label == "(a)"
    assert fragments[0].citation_path is None
    assert fragments[0].parse_status == ParseStatus.UNCERTAIN
    assert fragments[1].citation_label == "(a)"
    assert fragments[1].citation_path is None
    assert fragments[1].parse_status == ParseStatus.UNCERTAIN


def test_numbered_footnote_is_not_promoted_to_section():
    fragments = reconstruct_hierarchy(
        [
            block("1 space per 500m² GFA 50% Class A/ 50% Class B Minimum 2 Class B spaces", 0, BlockType.FOOTNOTE),
        ]
    )
    assert len(fragments) == 1
    assert fragments[0].fragment_type.value == "footnote"
    assert fragments[0].citation_label is None
    assert fragments[0].citation_path is None


def test_composite_numeric_sections_keep_distinct_citation_paths():
    fragments = reconstruct_hierarchy(
        [
            block("10(1) First rule.", 0, BlockType.PARAGRAPH),
            block("10(2) Second rule.", 1, BlockType.PARAGRAPH),
            block("10(3) Third rule.", 2, BlockType.PARAGRAPH),
        ]
    )
    assert [fragment.citation_label for fragment in fragments] == ["10(1)", "10(2)", "10(3)"]
    assert [fragment.citation_path for fragment in fragments] == ["10(1)", "10(2)", "10(3)"]


def test_duplicate_citation_paths_are_disambiguated_for_persistence():
    fragments = reconstruct_hierarchy(
        [
            block("10(3) First occurrence.", 0, BlockType.PARAGRAPH),
            block("(a) First child.", 1, BlockType.LIST_ITEM),
            block("10(3) Repeated occurrence.", 2, BlockType.PARAGRAPH),
            block("(a) Repeated child.", 3, BlockType.LIST_ITEM),
        ]
    )
    assert [fragment.citation_path for fragment in fragments] == [
        "10(3)",
        "10(3) > (a)",
        "10(3) [2]",
        "10(3) [2] > (a)",
    ]


def test_numbered_section_sentence_is_not_classified_as_footnote():
    fragments = reconstruct_hierarchy(
        [
            block("1 This By-law enables:", 0, BlockType.PARAGRAPH),
            block("(a) as-of-right development;", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[0].citation_label == "1"
    assert fragments[0].citation_path == "1"
    assert fragments[1].parent_index == 0


def test_parenthesized_numeric_subsections_are_addressable_under_section():
    fragments = reconstruct_hierarchy(
        [
            block("178 Minimum front setback.", 0, BlockType.PARAGRAPH),
            block("(2) If no setback is specified, the minimum setback is 1.5 metres.", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[1].citation_label == "(2)"
    assert fragments[1].citation_path == "178 > (2)"
    assert fragments[1].parent_index == 0
