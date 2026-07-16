from layer1.db.base import PageBlock, SourceFragment
from layer1.models.enums import BlockType, FragmentType, ParseStatus
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


def _fragment(
    fragment_id: int,
    fragment_type: FragmentType,
    *,
    parent_id: int | None = None,
    citation_path: str | None = None,
    page: int = 1,
) -> SourceFragment:
    return SourceFragment(
        id=fragment_id,
        document_id=1,
        fragment_type=fragment_type,
        citation_label=None,
        citation_path=citation_path,
        parent_fragment_id=parent_id,
        page_start=page,
        page_end=page,
        reading_order_start=fragment_id,
        reading_order_end=fragment_id,
        text="sample",
        parse_status=ParseStatus.PARSED,
        confidence=0.9,
        source_block_ids_json=[],
        metadata_json={},
    )


def test_unusual_root_warning_includes_citation_path_and_page():
    frag = _fragment(9771, FragmentType.SUBSECTION, citation_path="28AO(1)", page=42)
    report = validate_document_objects(
        page_count=100,
        blocks=[],
        fragments=[frag],
        tables=[],
        table_cells=[],
        cross_references=[],
    )
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert "9771" in warning
    assert "subsection" in warning
    assert "28AO(1)" in warning
    assert "page=42" in warning
