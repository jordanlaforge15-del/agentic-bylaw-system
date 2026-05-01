from __future__ import annotations

import re
from pathlib import Path

from layer1.models.schemas import BBox, PageBlockData, TableCellData, TableData
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

    def __init__(self, *, extract_tables: bool = True) -> None:
        self.extract_tables = extract_tables

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
        if self.extract_tables and path.suffix.lower() == ".pdf":
            try:
                parsed.tables.extend(_extract_docling_tables(path, ocr=ocr))
            except Exception as exc:  # pragma: no cover - depends on local parser installs and model memory
                parsed.warnings.append(f"Docling table extraction failed: {exc}")

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


def _docling_pdf_options(*, ocr: bool, table_structure: bool):
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
    )

    pdf_options = PdfPipelineOptions()
    pdf_options.do_ocr = ocr
    pdf_options.do_table_structure = table_structure
    pdf_options.layout_batch_size = 1
    pdf_options.table_batch_size = 1
    pdf_options.ocr_batch_size = 1
    pdf_options.accelerator_options = AcceleratorOptions(num_threads=2, device=AcceleratorDevice.CPU)
    return pdf_options


def _extract_docling_tables(path: Path, *, ocr: bool) -> list[TableData]:
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    table_ranges = _find_table_page_ranges(path)
    if not table_ranges:
        return []

    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_docling_pdf_options(ocr=ocr, table_structure=True))},
    )
    tables: list[TableData] = []
    seen: set[tuple[int, int, str | None]] = set()
    captions_by_page = _table_captions_by_page(path)
    headers_by_caption: dict[str, list[str]] = {}
    for page_range in table_ranges:
        range_caption = captions_by_page.get(page_range[0])
        result = converter.convert(str(path), page_range=page_range)
        document = result.document
        for table in document.tables:
            page_no = table.prov[0].page_no if table.prov else page_range[0]
            caption = _docling_caption_text(table, document) or captions_by_page.get(page_no) or range_caption
            dataframe = table.export_to_dataframe(doc=document)
            header_override = headers_by_caption.get(caption or "")
            if caption and not header_override:
                headers_by_caption[caption] = [_clean_table_text(str(column)) for column in dataframe.columns]
            cells = _dataframe_to_cells(dataframe, columns_override=header_override)
            if not cells:
                continue
            key = (page_no, len(cells), caption)
            if key in seen:
                continue
            seen.add(key)
            tables.append(
                TableData(
                    caption=caption,
                    page_start=page_no,
                    page_end=page_no,
                    cells=cells,
                    metadata={"parser": "docling", "page_range": list(page_range)},
                )
            )
    return tables


def _docling_caption_text(table, document) -> str | None:
    try:
        caption = table.caption_text(document)
    except Exception:
        return None
    caption = normalize_text(caption)
    return caption or None


def _dataframe_to_cells(dataframe, *, columns_override: list[str] | None = None) -> list[TableCellData]:
    cells: list[TableCellData] = []
    columns = columns_override or [_clean_table_text(str(column)) for column in dataframe.columns]
    for col_idx, column in enumerate(columns):
        cells.append(TableCellData(row_index=0, col_index=col_idx, text=column, metadata={"role": "column_header"}))
    for row_offset, (_, row) in enumerate(dataframe.fillna("").iterrows(), start=1):
        row_header = _clean_table_text(str(row.iloc[0])) if len(row) else None
        for col_idx, value in enumerate(row):
            text = _clean_table_text(str(value))
            cells.append(
                TableCellData(
                    row_index=row_offset,
                    col_index=col_idx,
                    row_header_path=row_header if col_idx > 0 else None,
                    col_header_path=columns[col_idx] if col_idx < len(columns) else None,
                    text=text,
                )
            )
    return [cell for cell in cells if cell.text]


def _clean_table_text(text: str) -> str:
    return normalize_text(text.replace("\uf020", " "))


def _find_table_page_ranges(path: Path) -> list[tuple[int, int]]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required to detect table pages") from exc

    ranges: list[tuple[int, int]] = []
    with fitz.open(path) as doc:
        page_lines = [_content_lines(page.get_text("text")) for page in doc]
        for idx, lines in enumerate(page_lines):
            table_heading = next((line for line in lines if re.match(r"^Table\s+\d+[A-Z]?\s*:", line, flags=re.I)), None)
            if table_heading is None:
                continue
            start = idx + 1
            end = start
            if re.match(r"^Table\s+1[A-D]\s*:", table_heading, flags=re.I):
                next_heading_idx = _next_table_heading_index(page_lines, start_idx=idx + 1)
                end = min(start + 2, next_heading_idx if next_heading_idx is not None else start + 2, len(page_lines))
            else:
                for next_idx in range(idx + 1, min(idx + 4, len(page_lines))):
                    next_lines = page_lines[next_idx]
                    if next_lines and _looks_like_table_continuation(next_lines):
                        end = next_idx + 1
                    else:
                        break
            ranges.append((start, end))
    return _merge_page_ranges(ranges)


def _table_captions_by_page(path: Path) -> dict[int, str]:
    try:
        import fitz
    except ImportError:
        return {}

    captions: dict[int, str] = {}
    with fitz.open(path) as doc:
        for page_idx, page in enumerate(doc, start=1):
            lines = _content_lines(page.get_text("text"))
            for line_idx, line in enumerate(lines):
                if re.match(r"^Table\s+\d+[A-Z]?\s*:", line, flags=re.I):
                    caption_lines = [line]
                    if line_idx + 1 < len(lines) and not _looks_like_table_header(lines[line_idx + 1]):
                        caption_lines.append(lines[line_idx + 1])
                    captions[page_idx] = normalize_text(" ".join(caption_lines))
                    break
    return captions


def _merge_page_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _next_table_heading_index(page_lines: list[list[str]], *, start_idx: int) -> int | None:
    for idx in range(start_idx, len(page_lines)):
        if any(re.match(r"^Table\s+\d+[A-Z]?\s*:", line, flags=re.I) for line in page_lines[idx]):
            return idx
    return None


def _content_lines(text: str) -> list[str]:
    return [
        normalize_text(line)
        for line in text.splitlines()
        if line.strip() and not normalize_text(line).startswith("Regional Centre Land Use By-law |")
    ]


def _looks_like_table_continuation(lines: list[str]) -> bool:
    first_lines = lines[:12]
    return _looks_like_table_header(first_lines[0]) or any(line in {"DD, DH,", "CEN-2,", "COR", "Design Requirements"} for line in first_lines)


def _looks_like_table_header(line: str) -> bool:
    return line in {"Residential", "Commercial", "Design Requirements"} or bool(re.fullmatch(r"[A-Z]{1,4}(?:-\d[A-Z]?)?,?", line))


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
