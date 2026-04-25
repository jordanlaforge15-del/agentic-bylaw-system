from layer1.db.base import PageBlock
from layer1.models.enums import BlockType
from layer1.validators.structural import validate_document_objects


def _block(block_id: int, block_type: BlockType, is_boilerplate: bool = False) -> PageBlock:
    return PageBlock(
        id=block_id,
        document_id=1,
        page_number=1,
        block_type=block_type,
        bbox_json=None,
        reading_order=block_id,
        raw_text="sample",
        normalized_text="sample",
        is_boilerplate=is_boilerplate,
        parser_source="test",
        confidence=1.0,
        metadata_json={},
    )


def test_header_footer_blocks_do_not_require_fragment_coverage():
    report = validate_document_objects(
        page_count=1,
        blocks=[
            _block(1, BlockType.HEADER),
            _block(2, BlockType.FOOTER),
            _block(3, BlockType.TABLE_REGION),
        ],
        fragments=[],
        tables=[],
        table_cells=[],
        cross_references=[],
    )
    assert report.errors == []
