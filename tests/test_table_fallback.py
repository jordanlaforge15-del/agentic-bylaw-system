from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.parsers.table_fallback import extract_fallback_tables
from layer1.pipeline.block_classifier import classify_text_block
from layer1.pipeline.hierarchy import reconstruct_hierarchy


def test_detects_table_like_multiline_block():
    text = "R-1 uses  40  4000\nDuplex  50  5000\nSemi-detached  60  6000"
    assert classify_text_block(text) == BlockType.TABLE_REGION


def test_extracts_fallback_table_from_table_region_block():
    block = PageBlockData(
        page_number=1,
        block_type=BlockType.TABLE_REGION,
        reading_order=0,
        raw_text="R-1 uses  40  4000\nDuplex  50  5000",
        normalized_text="R-1 uses 40 4000 Duplex 50 5000",
        parser_source="test",
    )
    tables = extract_fallback_tables([block])
    assert len(tables) == 1
    assert len(tables[0].cells) == 6
    assert tables[0].cells[0].text == "R-1 uses"
    assert tables[0].cells[1].text == "40"
    assert tables[0].metadata["parser"] == "multiline_table_fallback"


def test_groups_adjacent_table_blocks_into_one_table():
    blocks = [
        PageBlockData(
            page_number=1,
            block_type=BlockType.TABLE_REGION,
            reading_order=0,
            raw_text="R-1 uses  40  4000",
            normalized_text="R-1 uses 40 4000",
            parser_source="test",
        ),
        PageBlockData(
            page_number=1,
            block_type=BlockType.TABLE_REGION,
            reading_order=1,
            raw_text="Duplex  50  5000",
            normalized_text="Duplex 50 5000",
            parser_source="test",
        ),
    ]
    tables = extract_fallback_tables(blocks)
    assert len(tables) == 1
    assert tables[0].metadata["source_block_indices"] == [0, 1]


def test_table_region_does_not_emit_numeric_hierarchy_fragments():
    fragments = reconstruct_hierarchy(
        [
            PageBlockData(
                page_number=1,
                block_type=BlockType.TABLE_REGION,
                reading_order=0,
                raw_text="R-1 uses  40  4000\nDuplex  50  5000",
                normalized_text="R-1 uses 40 4000 Duplex 50 5000",
                parser_source="test",
            )
        ]
    )
    assert fragments == []
