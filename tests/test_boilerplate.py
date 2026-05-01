from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.pipeline.block_classifier import classify_text_block, detect_table_like_text, mark_boilerplate, strip_page_prefix


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


def test_repeated_clause_blocks_are_not_marked_boilerplate():
    blocks = [
        PageBlockData(page_number=1, block_type=BlockType.LIST_ITEM, reading_order=0, raw_text="(iv) the distance between buildings shall be 10 feet;", parser_source="docling"),
        PageBlockData(page_number=2, block_type=BlockType.LIST_ITEM, reading_order=1, raw_text="(iv) the distance between buildings shall be 10 feet;", parser_source="docling"),
    ]
    mark_boilerplate(blocks)
    assert not blocks[0].is_boilerplate
    assert blocks[0].block_type == BlockType.LIST_ITEM
    assert not blocks[1].is_boilerplate


def test_repeated_docling_headings_are_not_marked_boilerplate():
    blocks = [
        PageBlockData(page_number=1, block_type=BlockType.HEADING, reading_order=0, raw_text="OPEN SPACE", parser_source="docling"),
        PageBlockData(page_number=2, block_type=BlockType.HEADING, reading_order=1, raw_text="OPEN SPACE", parser_source="docling"),
    ]
    mark_boilerplate(blocks)
    assert blocks[0].block_type == BlockType.HEADING
    assert not blocks[0].is_boilerplate


def test_page_prefix_is_removed_before_classification():
    assert strip_page_prefix("PAGE General Prohibition ......16") == "General Prohibition ......16"
    assert classify_text_block("PAGE General Prohibition ......16") == BlockType.HEADING


def test_short_title_case_line_is_heading():
    assert classify_text_block("Reconstruction") == BlockType.HEADING


def test_bottom_of_page_toc_entry_is_not_forced_to_footer():
    text = "BCDD ..........................................................................................................................................116"
    assert classify_text_block(text, y0=760, y1=775, page_height=792) == BlockType.HEADING


def test_bottom_of_page_clause_is_not_forced_to_footer():
    text = "(iv) the distance between each of the buildings shall not be less than 10 feet; and"
    assert classify_text_block(text, y0=760, y1=775, page_height=792) == BlockType.LIST_ITEM


def test_bottom_of_page_page_number_is_footer():
    assert classify_text_block("43", y0=760, y1=775, page_height=792) == BlockType.FOOTER


def test_vertical_signature_phrase_is_not_table_region():
    text = "the \nseal \nof \nHalifax \nRegional \nMunicipality \nthis \n____ \nday \nof"
    assert not detect_table_like_text(text)
    assert classify_text_block(text) == BlockType.PARAGRAPH
