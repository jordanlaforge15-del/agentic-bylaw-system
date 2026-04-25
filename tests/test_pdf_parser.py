from layer1.models.enums import BlockType
from layer1.parsers.pdf import _build_page_blocks_from_pdf_text
from layer1.pipeline.block_classifier import classify_text_block, split_toc_lines


def test_split_toc_lines_splits_wrapped_toc_entries():
    text = (
        "ZM-21\nWater Access Areas – Northwest Arm ................................................................143\n"
        "ZM-22\nFront Yard Setbacks on Existing Streets (RC-Jun 25/14;E-Oct 18/14)  ......144\n"
        "ZM-23\nWind Energy Zoning (RC-Jun 25/14;E-Oct 18/14) .........................................145\n"
        "AMENDMENTS .........................................................................................................................146"
    )
    segments = split_toc_lines(text)
    assert segments == [
        "ZM-21\nWater Access Areas – Northwest Arm ................................................................143",
        "ZM-22\nFront Yard Setbacks on Existing Streets (RC-Jun 25/14;E-Oct 18/14)  ......144",
        "ZM-23\nWind Energy Zoning (RC-Jun 25/14;E-Oct 18/14) .........................................145",
        "AMENDMENTS .........................................................................................................................146",
    ]


def test_pdf_block_builder_splits_wrapped_toc_entries_into_multiple_blocks():
    text = (
        "ZM-21\nWater Access Areas – Northwest Arm ................................................................143\n"
        "ZM-22\nFront Yard Setbacks on Existing Streets (RC-Jun 25/14;E-Oct 18/14)  ......144\n"
        "ZM-23\nWind Energy Zoning (RC-Jun 25/14;E-Oct 18/14) .........................................145\n"
        "AMENDMENTS .........................................................................................................................146\n"
    )
    toc_blocks = _build_page_blocks_from_pdf_text(
        page_number=1,
        text=text,
        x0=72,
        y0=140,
        x1=540,
        y1=320,
        page_height=792,
        reading_order_start=10,
        parser_source="test",
        confidence=0.75,
    )
    assert [block.raw_text for block in toc_blocks] == [
        "ZM-21\nWater Access Areas – Northwest Arm ................................................................143",
        "ZM-22\nFront Yard Setbacks on Existing Streets (RC-Jun 25/14;E-Oct 18/14)  ......144",
        "ZM-23\nWind Energy Zoning (RC-Jun 25/14;E-Oct 18/14) .........................................145",
        "AMENDMENTS .........................................................................................................................146",
    ]
    assert all(block.block_type == BlockType.HEADING for block in toc_blocks)


def test_short_leader_wrapped_toc_entry_classifies_as_heading():
    text = "ZM-20\nAreas of Elevated Archaeological Potential (RC-Jun 25/14;E-Oct 18/14)  ..142"
    assert classify_text_block(text) == BlockType.HEADING
