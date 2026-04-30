from __future__ import annotations

from pathlib import Path

from layer1.models.schemas import BBox, PageBlockData, TableData
from layer1.parsers.base import ParseResult, ParserAdapter
from layer1.pipeline.block_classifier import classify_text_block, mark_boilerplate, normalize_text


class PdfParser(ParserAdapter):
    name = "pymupdf-fallback"

    def parse(self, path: Path, *, ocr: bool = False, debug: bool = False) -> ParseResult:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("PyMuPDF is required for PDF parsing") from exc

        warnings: list[str] = []
        blocks: list[PageBlockData] = []
        order = 0
        with fitz.open(path) as doc:
            for page_idx, page in enumerate(doc, start=1):
                page_height = float(page.rect.height)
                text_blocks = page.get_text("blocks")
                if not text_blocks:
                    warnings.append(f"Page {page_idx} has no text layer")
                    continue
                sorted_blocks = sorted(text_blocks, key=lambda b: (round(b[1], 1), round(b[0], 1)))
                for raw in sorted_blocks:
                    x0, y0, x1, y1, text, *_ = raw
                    if not text.strip():
                        continue
                    block_type = classify_text_block(text, y0=y0, y1=y1, page_height=page_height)
                    blocks.append(
                        PageBlockData(
                            page_number=page_idx,
                            block_type=block_type,
                            bbox=BBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1)),
                            reading_order=order,
                            raw_text=text.strip(),
                            normalized_text=normalize_text(text),
                            parser_source=self.name,
                            confidence=0.75,
                        )
                    )
                    order += 1
            page_count = doc.page_count

        if not blocks and ocr:
            warnings.append("OCR was requested, but PaddleOCR adapter is optional and not configured in this fallback parser")
        mark_boilerplate(blocks)
        return ParseResult(
            page_blocks=blocks,
            tables=[],
            page_count=page_count,
            parser_version=self.name,
            warnings=warnings,
            raw={"fitz_blocks": len(blocks)} if debug else None,
        )


class DoclingParser(ParserAdapter):
    name = "docling"

    def parse(self, path: Path, *, ocr: bool = False, debug: bool = False) -> ParseResult:
        try:
            from docling.document_converter import DocumentConverter
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                AcceleratorDevice,
                AcceleratorOptions,
                PdfPipelineOptions,
            )
            from docling.document_converter import PdfFormatOption
        except ImportError as exc:
            raise RuntimeError("Docling is not installed") from exc

        pdf_options = PdfPipelineOptions()
        pdf_options.do_ocr = ocr
        pdf_options.do_table_structure = False
        pdf_options.layout_batch_size = 1
        pdf_options.table_batch_size = 1
        pdf_options.ocr_batch_size = 1
        pdf_options.accelerator_options = AcceleratorOptions(num_threads=2, device=AcceleratorDevice.CPU)
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)},
        )
        result = converter.convert(str(path))
        document = result.document
        text = document.export_to_markdown()

        from layer1.parsers.text import TextParser

        temp_parser = TextParser()
        parsed = _parse_docling_markdown(text, temp_parser, path)
        parsed.parser_version = self.name
        parsed.warnings.append(
            "Docling markdown export used with lean PDF options; geometry supplied by PyMuPDF fallback where available"
        )

        if path.suffix.lower() == ".pdf":
            try:
                pdf = PdfParser().parse(path, ocr=ocr, debug=debug)
                if pdf.page_blocks:
                    parsed.page_blocks = pdf.page_blocks
                    parsed.page_count = pdf.page_count
                    parsed.raw = {"docling_markdown": text[:5000]} if debug else None
            except Exception as exc:  # pragma: no cover - depends on local parser installs
                parsed.warnings.append(f"PyMuPDF geometry fallback failed: {exc}")
        return parsed


def _parse_docling_markdown(text: str, parser: ParserAdapter, path: Path) -> ParseResult:
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile("w+", suffix=".txt", encoding="utf-8", delete=True) as tmp:
        tmp.write(text)
        tmp.flush()
        return parser.parse(Path(tmp.name))


class PdfPlumberInspector:
    def inspect(self, path: Path) -> dict:
        try:
            import pdfplumber
        except ImportError as exc:
            raise RuntimeError("pdfplumber is not installed") from exc
        pages = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                pages.append(
                    {
                        "page_number": page.page_number,
                        "width": page.width,
                        "height": page.height,
                        "chars": len(page.chars),
                        "tables": len(page.find_tables()),
                    }
                )
        return {"pages": pages}


class CamelotTableFallback:
    def parse_tables(self, path: Path) -> list[TableData]:
        try:
            import camelot
        except ImportError:
            return []
        tables: list[TableData] = []
        for table in camelot.read_pdf(str(path), pages="all"):
            cells = []
            for r_idx, row in enumerate(table.data):
                for c_idx, value in enumerate(row):
                    from layer1.models.schemas import TableCellData

                    cells.append(TableCellData(row_index=r_idx, col_index=c_idx, text=value))
            page = int(table.page)
            tables.append(TableData(page_start=page, page_end=page, cells=cells, metadata={"parser": "camelot"}))
        return tables
