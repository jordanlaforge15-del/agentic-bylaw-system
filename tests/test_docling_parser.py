from pathlib import Path

from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.document import (
    DocItemLabel,
    DoclingDocument,
    GroupItem,
    ListItem,
    PageItem,
    ProvenanceItem,
    SectionHeaderItem,
    TableCell,
    TableData as DoclingTableData,
    TableItem,
    TextItem,
)

from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.parsers.base import ParseResult
from layer1.parsers.pdf import DoclingParser


def _prov(page_no: int, left: float, top: float, right: float, bottom: float) -> list[ProvenanceItem]:
    return [
        ProvenanceItem(
            page_no=page_no,
            bbox=BoundingBox(l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin.BOTTOMLEFT),
            charspan=(0, 1),
        )
    ]


def test_docling_pdf_uses_native_docling_items_for_blocks_and_tables(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    doc = DoclingDocument(name="sample", pages={1: PageItem(size=Size(width=612, height=792), page_no=1)})
    heading = SectionHeaderItem(
        self_ref="#/texts/0",
        parent=None,
        children=[],
        label=DocItemLabel.SECTION_HEADER,
        prov=_prov(1, 72, 700, 200, 680),
        orig="GENERAL PROVISIONS",
        text="GENERAL PROVISIONS",
        source=[],
        comments=[],
    )
    list_item = ListItem(
        self_ref="#/texts/1",
        parent=None,
        children=[],
        label=DocItemLabel.LIST_ITEM,
        prov=_prov(1, 72, 650, 500, 630),
        orig="1. This is a rule.",
        text="1. This is a rule.",
        source=[],
        comments=[],
        marker="1.",
        enumerated=True,
    )
    table = TableItem(
        self_ref="#/tables/0",
        parent=None,
        children=[],
        prov=_prov(1, 72, 500, 500, 350),
        data=DoclingTableData(
            num_rows=2,
            num_cols=2,
            table_cells=[
                TableCell(start_row_offset_idx=0, end_row_offset_idx=1, start_col_offset_idx=0, end_col_offset_idx=1, text="Use", column_header=True),
                TableCell(start_row_offset_idx=0, end_row_offset_idx=1, start_col_offset_idx=1, end_col_offset_idx=2, text="Min", column_header=True),
                TableCell(start_row_offset_idx=1, end_row_offset_idx=2, start_col_offset_idx=0, end_col_offset_idx=1, text="Dwelling"),
                TableCell(start_row_offset_idx=1, end_row_offset_idx=2, start_col_offset_idx=1, end_col_offset_idx=2, text="40"),
            ],
        ),
        source=[],
        comments=[],
    )
    items = [(GroupItem(self_ref="#/body", parent=None, children=[]), 0), (heading, 1), (list_item, 1), (table, 1)]

    class FakeDocument:
        pages = doc.pages

        def iterate_items(self, page_no=None, with_groups=False, **kwargs):
            assert page_no == 1
            return iter(items)

    class FakeConversionResult:
        document = FakeDocument()

    class FakeConverter:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, path_str: str) -> FakeConversionResult:
            return FakeConversionResult()

    monkeypatch.setattr("docling.document_converter.DocumentConverter", FakeConverter)

    parsed = DoclingParser().parse(pdf_path, debug=True)

    assert parsed.parser_version == "docling"
    assert parsed.page_count == 1
    assert [block.block_type for block in parsed.page_blocks] == [
        BlockType.HEADING,
        BlockType.LIST_ITEM,
        BlockType.TABLE_REGION,
    ]
    assert all(block.parser_source == "docling" for block in parsed.page_blocks)
    assert parsed.tables[0].metadata["parser"] == "docling"
    assert parsed.tables[0].metadata["source_block_index"] == 2
    assert parsed.tables[0].cells[0].text == "Use"
    assert parsed.tables[0].cells[-1].text == "40"
    assert parsed.raw["docling_tables"] == 1


def test_docling_definition_like_items_become_paragraphs(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    doc = DoclingDocument(name="sample", pages={1: PageItem(size=Size(width=612, height=792), page_no=1)})
    definition = SectionHeaderItem(
        self_ref="#/texts/0",
        parent=None,
        children=[],
        label=DocItemLabel.SECTION_HEADER,
        prov=_prov(1, 72, 700, 300, 680),
        orig="'Accessory Building' means a building that is:",
        text="'Accessory Building' means a building that is:",
        source=[],
        comments=[],
    )
    listish_definition = ListItem(
        self_ref="#/texts/1",
        parent=None,
        children=[],
        label=DocItemLabel.LIST_ITEM,
        prov=_prov(1, 72, 650, 300, 630),
        orig='"Alter" means to make any change.',
        text='"Alter" means to make any change.',
        source=[],
        comments=[],
        marker="-",
        enumerated=False,
    )

    class FakeDocument:
        pages = doc.pages

        def iterate_items(self, page_no=None, with_groups=False, **kwargs):
            return iter([(definition, 1), (listish_definition, 1)])

    class FakeConversionResult:
        document = FakeDocument()

    class FakeConverter:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, path_str: str) -> FakeConversionResult:
            return FakeConversionResult()

    monkeypatch.setattr("docling.document_converter.DocumentConverter", FakeConverter)

    parsed = DoclingParser().parse(pdf_path)

    assert [block.block_type for block in parsed.page_blocks] == [BlockType.PARAGRAPH, BlockType.PARAGRAPH]


def test_docling_deleted_schedule_item_becomes_paragraph(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    doc = DoclingDocument(name="sample", pages={1: PageItem(size=Size(width=612, height=792), page_no=1)})
    deleted_schedule = ListItem(
        self_ref="#/texts/0",
        parent=None,
        children=[],
        label=DocItemLabel.LIST_ITEM,
        prov=_prov(1, 72, 700, 300, 680),
        orig='"Schedule F" (Deleted - RC-Jun 16/09;E-Oct 24/09)',
        text='"Schedule F" (Deleted - RC-Jun 16/09;E-Oct 24/09)',
        source=[],
        comments=[],
        marker="-",
        enumerated=False,
    )

    class FakeDocument:
        pages = doc.pages

        def iterate_items(self, page_no=None, with_groups=False, **kwargs):
            return iter([(deleted_schedule, 1)])

    class FakeConversionResult:
        document = FakeDocument()

    class FakeConverter:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, path_str: str) -> FakeConversionResult:
            return FakeConversionResult()

    monkeypatch.setattr("docling.document_converter.DocumentConverter", FakeConverter)

    parsed = DoclingParser().parse(pdf_path)

    assert parsed.page_blocks[0].block_type == BlockType.PARAGRAPH


def test_docling_pdf_sorts_items_by_geometry_before_persisting(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    doc = DoclingDocument(name="sample", pages={1: PageItem(size=Size(width=612, height=792), page_no=1)})
    late_heading = SectionHeaderItem(
        self_ref="#/texts/1",
        parent=None,
        children=[],
        label=DocItemLabel.SECTION_HEADER,
        prov=_prov(1, 72, 700, 200, 680),
        orig="HEADING",
        text="HEADING",
        source=[],
        comments=[],
    )
    early_clause = ListItem(
        self_ref="#/texts/2",
        parent=None,
        children=[],
        label=DocItemLabel.LIST_ITEM,
        prov=_prov(1, 72, 500, 500, 480),
        orig="(a) lower on page",
        text="(a) lower on page",
        source=[],
        comments=[],
        marker="(a)",
        enumerated=True,
    )
    items = [(early_clause, 2), (late_heading, 1)]

    class FakeDocument:
        pages = doc.pages

        def iterate_items(self, page_no=None, with_groups=False, **kwargs):
            return iter(items)

    class FakeConversionResult:
        document = FakeDocument()

    class FakeConverter:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, path_str: str) -> FakeConversionResult:
            return FakeConversionResult()

    monkeypatch.setattr("docling.document_converter.DocumentConverter", FakeConverter)

    parsed = DoclingParser().parse(pdf_path)

    assert [block.raw_text for block in parsed.page_blocks] == ["HEADING", "(a) lower on page"]


def test_docling_pdf_falls_back_only_when_docling_produces_no_blocks(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    class FakeDocument:
        pages = {1: PageItem(size=Size(width=612, height=792), page_no=1)}

        def iterate_items(self, page_no=None, with_groups=False, **kwargs):
            return iter([])

    class FakeConversionResult:
        document = FakeDocument()

    class FakeConverter:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, path_str: str) -> FakeConversionResult:
            return FakeConversionResult()

    class FakePdfParser:
        def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile=None) -> ParseResult:
            return ParseResult(
                page_blocks=[
                    PageBlockData(
                        page_number=1,
                        block_type=BlockType.HEADING,
                        reading_order=0,
                        raw_text="Fallback heading",
                        normalized_text="Fallback heading",
                        parser_source="pymupdf-fallback",
                    )
                ],
                page_count=1,
                parser_version="pymupdf-fallback",
            )

    monkeypatch.setattr("docling.document_converter.DocumentConverter", FakeConverter)
    monkeypatch.setattr("layer1.parsers.pdf.PdfParser", FakePdfParser)

    parsed = DoclingParser().parse(pdf_path)

    assert parsed.parser_version == "pymupdf-fallback"
    assert parsed.page_blocks[0].parser_source == "pymupdf-fallback"
    assert any("Docling produced no page blocks" in warning for warning in parsed.warnings)


def test_docling_pdf_disables_ocr_by_default(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    captured = {}

    class FakeDocument:
        pages = {1: PageItem(size=Size(width=612, height=792), page_no=1)}

        def iterate_items(self, page_no=None, with_groups=False, **kwargs):
            text = TextItem(
                self_ref="#/texts/0",
                parent=None,
                children=[],
                label=DocItemLabel.TEXT,
                prov=_prov(1, 72, 700, 200, 680),
                orig="Body text",
                text="Body text",
                source=[],
                comments=[],
            )
            return iter([(text, 0)])

    class FakeConversionResult:
        document = FakeDocument()

    class FakeConverter:
        def __init__(self, *, format_options=None, **kwargs):
            captured["format_options"] = format_options

        def convert(self, path_str: str) -> FakeConversionResult:
            return FakeConversionResult()

    monkeypatch.setattr("docling.document_converter.DocumentConverter", FakeConverter)

    DoclingParser().parse(pdf_path, ocr=False)

    pdf_option = next(iter(captured["format_options"].values()))
    assert pdf_option.pipeline_options.do_ocr is False


def test_docling_pdf_honors_explicit_ocr_flag(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    captured = {}

    class FakeDocument:
        pages = {1: PageItem(size=Size(width=612, height=792), page_no=1)}

        def iterate_items(self, page_no=None, with_groups=False, **kwargs):
            text = TextItem(
                self_ref="#/texts/0",
                parent=None,
                children=[],
                label=DocItemLabel.TEXT,
                prov=_prov(1, 72, 700, 200, 680),
                orig="Body text",
                text="Body text",
                source=[],
                comments=[],
            )
            return iter([(text, 0)])

    class FakeConversionResult:
        document = FakeDocument()

    class FakeConverter:
        def __init__(self, *, format_options=None, **kwargs):
            captured["format_options"] = format_options

        def convert(self, path_str: str) -> FakeConversionResult:
            return FakeConversionResult()

    monkeypatch.setattr("docling.document_converter.DocumentConverter", FakeConverter)

    DoclingParser().parse(pdf_path, ocr=True)

    pdf_option = next(iter(captured["format_options"].values()))
    assert pdf_option.pipeline_options.do_ocr is True
