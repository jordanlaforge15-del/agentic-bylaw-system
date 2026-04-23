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
