from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.parsers.pdf import _find_table_page_ranges, _table_captions_by_page
from layer1.parsers.base import ParseResult
from layer1.profiles import RegionalCentreLandUseBylawProfile


def test_regional_centre_profile_marks_toc_as_boilerplate_and_normalizes_zone_spacing():
    profile = RegionalCentreLandUseBylawProfile()
    assert profile.use_full_docling is True
    result = ParseResult(
        page_blocks=[
            PageBlockData(
                page_number=4,
                block_type=BlockType.HEADING,
                reading_order=1,
                raw_text="Part V, Chapter 9: Built Form within the ER- 3, ER-2, and ER-1 Zones",
                normalized_text="Part V, Chapter 9: Built Form within the ER- 3, ER-2, and ER-1 Zones",
                parser_source="test",
            ),
            PageBlockData(
                page_number=107,
                block_type=BlockType.PARAGRAPH,
                reading_order=2,
                raw_text="A building in the CEN- 2 zone.",
                normalized_text="A building in the CEN- 2 zone.",
                parser_source="test",
            ),
            PageBlockData(
                page_number=147,
                block_type=BlockType.FOOTER,
                reading_order=3,
                raw_text="not exceed the maximum required building height specified on Schedule 15.",
                normalized_text="not exceed the maximum required building height specified on Schedule 15.",
                parser_source="test",
                is_boilerplate=True,
            ),
            PageBlockData(
                page_number=160,
                block_type=BlockType.TABLE_REGION,
                reading_order=4,
                raw_text="(2) If a minimum required front or flanking setback has not been specified on Schedule 18, the minimum required front or flanking setback shall be 1.5 metres.",
                normalized_text="(2) If a minimum required front or flanking setback has not been specified on Schedule 18, the minimum required front or flanking setback shall be 1.5 metres.",
                parser_source="test",
            ),
        ],
        parser_version="test-parser",
    )

    processed = profile.postprocess_parse_result(result)

    assert processed.page_blocks[0].is_boilerplate is True
    assert processed.page_blocks[0].block_type == BlockType.FOOTER
    assert "ER-3" in processed.page_blocks[0].normalized_text
    assert processed.page_blocks[1].is_boilerplate is False
    assert "CEN-2" in processed.page_blocks[1].normalized_text
    assert processed.page_blocks[2].is_boilerplate is False
    assert processed.page_blocks[2].block_type == BlockType.PARAGRAPH
    assert processed.page_blocks[3].block_type == BlockType.LIST_ITEM
    assert processed.parser_version.endswith("+profile:halifax-regional-centre-lub")


def test_regional_centre_table_page_detection_finds_use_tables():
    from pathlib import Path

    pdf_path = Path("regionalcentrelub-eff-26april13-case24469toclinked.pdf")
    if not pdf_path.exists():
        return

    ranges = _find_table_page_ranges(pdf_path)
    captions = _table_captions_by_page(pdf_path)

    assert (45, 47) in ranges
    assert (48, 50) in ranges
    assert (332, 334) in ranges
    assert captions[45] == "Table 1A: Permitted uses by zone (DD, DH, CEN-2, CEN-1, COR, HR-2, and HR-1)"
