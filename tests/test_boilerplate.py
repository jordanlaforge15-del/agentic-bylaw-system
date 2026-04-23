from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.pipeline.block_classifier import mark_boilerplate


def test_repeated_short_blocks_marked_boilerplate():
    blocks = [
        PageBlockData(page_number=1, block_type=BlockType.PARAGRAPH, reading_order=0, raw_text="Town Header", parser_source="test"),
        PageBlockData(page_number=1, block_type=BlockType.PARAGRAPH, reading_order=1, raw_text="Unique text", parser_source="test"),
        PageBlockData(page_number=2, block_type=BlockType.PARAGRAPH, reading_order=2, raw_text="Town Header", parser_source="test"),
    ]
    mark_boilerplate(blocks)
    assert blocks[0].is_boilerplate
    assert not blocks[1].is_boilerplate
    assert blocks[2].block_type == BlockType.FOOTER
