from pathlib import Path

from layer1.parsers.base import ParseResult
from layer1.parsers.factory import parse_source
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

    result = parse_source(path, profile=get_parsing_profile("halifax"))

    assert calls == ["docling:halifax", "pymupdf:halifax"]
    assert result.parser_version == "pymupdf-fallback"
    assert result.warnings == ["Docling parse unavailable or failed: docling unavailable"]
