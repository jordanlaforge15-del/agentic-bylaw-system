from pathlib import Path

from layer1.models.schemas import TableCellData, TableData
from layer1.parsers.base import ParseResult
from layer1.parsers.factory import _merge_tables, parse_source
from layer1.profiles import get_parsing_profile


def test_pdf_parse_prefers_docling(monkeypatch, tmp_path: Path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4\n")

    calls: list[str] = []

    class FakeDoclingParser:
        def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile=None) -> ParseResult:
            calls.append(f"docling:{profile.name}")
            return ParseResult(page_blocks=[], parser_version="docling", warnings=["docling ok"])

    class FakePdfParser:
        def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile=None) -> ParseResult:
            calls.append(f"pymupdf:{profile.name}")
            return ParseResult(page_blocks=[], parser_version="pymupdf-fallback")

    monkeypatch.setattr("layer1.parsers.factory.DoclingParser", FakeDoclingParser)
    monkeypatch.setattr("layer1.parsers.factory.PdfParser", FakePdfParser)
    monkeypatch.setattr("layer1.parsers.factory._extract_docling_tables", lambda *a, **kw: [])

    result = parse_source(path, profile=get_parsing_profile("default"))

    assert calls == ["docling:default"]
    assert result.parser_version == "docling"
    assert result.warnings == ["docling ok"]


def test_pdf_parse_falls_back_to_pymupdf_when_docling_fails(monkeypatch, tmp_path: Path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4\n")

    calls: list[str] = []

    class FakeDoclingParser:
        def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile=None) -> ParseResult:
            calls.append(f"docling:{profile.name}")
            raise RuntimeError("docling unavailable")

    class FakePdfParser:
        def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile=None) -> ParseResult:
            calls.append(f"pymupdf:{profile.name}")
            return ParseResult(page_blocks=[], parser_version="pymupdf-fallback")

    monkeypatch.setattr("layer1.parsers.factory.DoclingParser", FakeDoclingParser)
    monkeypatch.setattr("layer1.parsers.factory.PdfParser", FakePdfParser)
    monkeypatch.setattr("layer1.parsers.factory._extract_docling_tables", lambda *a, **kw: [])

    result = parse_source(path, profile=get_parsing_profile("halifax"))

    assert calls == ["docling:halifax", "pymupdf:halifax"]
    assert result.parser_version == "pymupdf-fallback"
    assert result.warnings == ["Docling parse unavailable or failed: docling unavailable"]


def test_merge_tables_replaces_basic_on_overlapping_pages():
    basic = [
        TableData(page_start=42, page_end=42, cells=[TableCellData(row_index=0, col_index=0, text="old")], metadata={"source_block_index": 10}),
        TableData(page_start=43, page_end=43, cells=[TableCellData(row_index=0, col_index=0, text="old2")], metadata={"source_block_index": 11}),
        TableData(page_start=60, page_end=60, cells=[TableCellData(row_index=0, col_index=0, text="keep")], metadata={"source_block_index": 20}),
    ]
    structured = [
        TableData(
            caption="Table 1A: Permitted uses by zone",
            page_start=42,
            page_end=43,
            cells=[TableCellData(row_index=0, col_index=0, text="Dwelling")],
            metadata={"parser": "docling"},
        ),
    ]

    merged = _merge_tables(basic, structured)

    assert len(merged) == 2
    kept = [t for t in merged if t.caption is None]
    assert len(kept) == 1
    assert kept[0].page_start == 60

    structured_result = [t for t in merged if t.caption is not None]
    assert len(structured_result) == 1
    assert structured_result[0].caption == "Table 1A: Permitted uses by zone"
    assert structured_result[0].metadata["source_block_indices"] == [10, 11]


def test_merge_tables_keeps_all_when_no_overlap():
    basic = [
        TableData(page_start=10, page_end=10, cells=[], metadata={"source_block_index": 1}),
    ]
    structured = [
        TableData(caption="Table 2", page_start=50, page_end=50, cells=[], metadata={}),
    ]

    merged = _merge_tables(basic, structured)

    assert len(merged) == 2


def test_parse_source_calls_extract_docling_tables(monkeypatch, tmp_path: Path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4\n")

    class FakeDoclingParser:
        def parse(self, path, *, ocr=False, debug=False, profile=None) -> ParseResult:
            return ParseResult(
                page_blocks=[],
                tables=[TableData(page_start=42, page_end=42, cells=[], metadata={"source_block_index": 0})],
                parser_version="docling",
            )

    structured = [
        TableData(
            caption="Table 1A: Permitted uses by zone",
            page_start=42,
            page_end=43,
            cells=[TableCellData(row_index=0, col_index=0, text="Dwelling")],
            metadata={"parser": "docling"},
        ),
    ]

    monkeypatch.setattr("layer1.parsers.factory.DoclingParser", FakeDoclingParser)
    monkeypatch.setattr("layer1.parsers.factory._extract_docling_tables", lambda *a, **kw: structured)

    result = parse_source(path, profile=get_parsing_profile("default"))

    assert len(result.tables) == 1
    assert result.tables[0].caption == "Table 1A: Permitted uses by zone"
    assert result.tables[0].cells[0].text == "Dwelling"


def test_parse_source_structured_table_failure_adds_warning(monkeypatch, tmp_path: Path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4\n")

    class FakeDoclingParser:
        def parse(self, path, *, ocr=False, debug=False, profile=None) -> ParseResult:
            return ParseResult(page_blocks=[], tables=[], parser_version="docling")

    def failing_extract(*args, **kwargs):
        raise RuntimeError("table model unavailable")

    monkeypatch.setattr("layer1.parsers.factory.DoclingParser", FakeDoclingParser)
    monkeypatch.setattr("layer1.parsers.factory._extract_docling_tables", failing_extract)

    result = parse_source(path, profile=get_parsing_profile("default"))

    assert any("Structured table extraction failed" in w for w in result.warnings)
