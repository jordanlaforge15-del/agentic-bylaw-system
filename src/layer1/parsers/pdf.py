from __future__ import annotations

import re
from pathlib import Path

from io import BytesIO

from layer1.models.enums import BlockType, ParseStatus
from layer1.models.schemas import BBox, ImageData, PageBlockData, TableCellData, TableData
from layer1.parsers.base import ParseResult, ParserAdapter
from layer1.parsers.table_fallback import extract_fallback_tables
from layer1.pipeline.block_classifier import classify_text_block, mark_boilerplate, normalize_text, split_toc_lines, strip_page_prefix
from layer1.profiles import ParsingProfile, get_parsing_profile


class PdfParser(ParserAdapter):
    name = "pymupdf-fallback"

    def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile: ParsingProfile | None = None) -> ParseResult:
        profile = get_parsing_profile(profile)
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
                    cleaned_text = strip_page_prefix(text)
                    if not cleaned_text:
                        continue
                    block_segments = _build_page_blocks_from_pdf_text(
                        page_number=page_idx,
                        text=cleaned_text,
                        x0=float(x0),
                        y0=float(y0),
                        x1=float(x1),
                        y1=float(y1),
                        page_height=page_height,
                        reading_order_start=order,
                        parser_source=self.name,
                        confidence=0.75,
                        profile=profile,
                    )
                    blocks.extend(block_segments)
                    order += len(block_segments)
            page_count = doc.page_count

        if not blocks and ocr:
            warnings.append("OCR was requested, but PaddleOCR adapter is optional and not configured in this fallback parser")
        mark_boilerplate(blocks, profile=profile)
        tables = extract_fallback_tables(blocks)
        return ParseResult(
            page_blocks=blocks,
            tables=tables,
            page_count=page_count,
            parser_version=self.name,
            warnings=warnings,
            raw={"fitz_blocks": len(blocks)} if debug else None,
        )


class DoclingParser(ParserAdapter):
    name = "docling"

    def parse(self, path: Path, *, ocr: bool = False, debug: bool = False, profile: ParsingProfile | None = None) -> ParseResult:
        profile = get_parsing_profile(profile)
        try:
            from docling_core.types.doc.base import CoordOrigin
            from docling_core.types.doc.document import (
                GroupItem,
                ListItem,
                PictureItem,
                SectionHeaderItem,
                TableItem,
                TextItem,
                TitleItem,
            )
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import OcrAutoOptions, PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:
            raise RuntimeError("Docling is not installed") from exc

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=PdfPipelineOptions(
                        do_ocr=ocr,
                        ocr_options=OcrAutoOptions(),
                    )
                )
            }
        )
        result = converter.convert(str(path))
        document = result.document
        blocks: list[PageBlockData] = []
        tables: list[TableData] = []
        images: list[ImageData] = []
        warnings: list[str] = []
        order = 0
        page_numbers = sorted(document.pages.keys())
        for page_number in page_numbers:
            page = document.pages[page_number]
            page_height = float(page.size.height)
            page_candidates = []
            for seq, (item, level) in enumerate(document.iterate_items(page_no=page_number, with_groups=True)):
                if isinstance(item, GroupItem):
                    continue
                bbox = _docling_item_bbox(item, page_height, CoordOrigin)
                raw_text = _docling_item_text(item)
                if isinstance(item, PictureItem):
                    images.append(
                        _docling_picture_to_image(
                            item,
                            page_number=page_number,
                            bbox=bbox,
                            document=document,
                        )
                    )
                    continue
                if bbox is None and not isinstance(item, TableItem):
                    if not raw_text:
                        continue
                    warnings.append(f"Docling item missing provenance on page {page_number}: {item.__class__.__name__}")
                    continue
                page_candidates.append((bbox.y0 if bbox else float("inf"), bbox.x0 if bbox else 0.0, seq, item, level, bbox, raw_text))

            for _, _, _, item, level, bbox, raw_text in sorted(page_candidates, key=lambda entry: (entry[0], entry[1], entry[2])):
                if isinstance(item, TableItem):
                    table_text = _docling_table_text(item)
                    table_block_index = len(blocks)
                    blocks.append(
                        PageBlockData(
                            page_number=page_number,
                            block_type=BlockType.TABLE_REGION,
                            bbox=bbox,
                            reading_order=order,
                            raw_text=table_text,
                            normalized_text=normalize_text(table_text),
                            parser_source=self.name,
                            confidence=0.9,
                            metadata={
                                "docling_ref": item.self_ref,
                                "docling_label": getattr(item.label, "value", str(item.label)),
                                "docling_level": level,
                            },
                        )
                    )
                    order += 1
                    tables.append(
                        _docling_table_data(
                            item,
                            page_number=page_number,
                            page_height=page_height,
                            coord_origin_cls=CoordOrigin,
                            source_block_index=table_block_index,
                        )
                    )
                    continue

                if not raw_text:
                    continue
                blocks.append(
                    PageBlockData(
                        page_number=page_number,
                        block_type=_docling_block_type(
                            item,
                            raw_text=raw_text,
                            bbox=bbox,
                            page_height=page_height,
                            text_item_cls=TextItem,
                            section_header_cls=SectionHeaderItem,
                            title_item_cls=TitleItem,
                            list_item_cls=ListItem,
                            profile=profile,
                        ),
                        bbox=bbox,
                        reading_order=order,
                        raw_text=raw_text,
                        normalized_text=normalize_text(raw_text),
                        parser_source=self.name,
                        confidence=0.9,
                        metadata={
                            "docling_ref": item.self_ref,
                            "docling_label": getattr(getattr(item, "label", None), "value", None),
                            "docling_level": level,
                            "docling_parent_ref": getattr(getattr(item, "parent", None), "cref", None),
                        },
                    )
                )
                order += 1

        mark_boilerplate(blocks, profile=profile)
        parsed = ParseResult(
            page_blocks=blocks,
            tables=tables,
            images=images,
            page_count=len(page_numbers) or None,
            parser_version=self.name,
            warnings=warnings,
            raw={
                "docling_pages": len(page_numbers),
                "docling_items": len(blocks) + len(tables),
                "docling_tables": len(tables),
                "docling_images": len(images),
            }
            if debug
            else None,
        )

        if path.suffix.lower() == ".pdf" and not parsed.page_blocks:
            warnings.append("Docling produced no page blocks; PyMuPDF fallback selected")
            pdf = PdfParser().parse(path, ocr=ocr, debug=debug, profile=profile)
            pdf.warnings = warnings + pdf.warnings
            return pdf
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


def _docling_item_bbox(item, page_height: float, coord_origin_cls) -> BBox | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    x0 = min(float(p.bbox.l) for p in prov)
    x1 = max(float(p.bbox.r) for p in prov)
    first_bbox = prov[0].bbox
    if first_bbox.coord_origin == coord_origin_cls.BOTTOMLEFT:
        y0 = min(page_height - float(p.bbox.t) for p in prov)
        y1 = max(page_height - float(p.bbox.b) for p in prov)
    else:
        y0 = min(float(p.bbox.t) for p in prov)
        y1 = max(float(p.bbox.b) for p in prov)
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _docling_block_type(
    item,
    *,
    raw_text: str,
    bbox: BBox | None,
    page_height: float,
    text_item_cls,
    section_header_cls,
    title_item_cls,
    list_item_cls,
    profile: ParsingProfile,
) -> BlockType:
    if profile.docling_definition_like_re.match(raw_text):
        return BlockType.PARAGRAPH
    if isinstance(item, (section_header_cls, title_item_cls)):
        return BlockType.HEADING
    if isinstance(item, list_item_cls):
        return BlockType.LIST_ITEM
    label = getattr(getattr(item, "label", None), "value", None)
    if label == "footnote":
        return BlockType.FOOTNOTE
    if label == "page_header":
        return BlockType.HEADER
    if label == "page_footer":
        return BlockType.FOOTER
    if isinstance(item, text_item_cls):
        if bbox is not None:
            return classify_text_block(raw_text, y0=bbox.y0, y1=bbox.y1, page_height=page_height, profile=profile)
        return classify_text_block(raw_text, profile=profile)
    return BlockType.UNKNOWN


def _docling_item_text(item) -> str:
    text = getattr(item, "text", None)
    if text:
        return text.strip()
    return ""


def _docling_table_text(item) -> str:
    rows: dict[int, dict[int, str]] = {}
    for cell in item.data.table_cells:
        row_index = int(cell.start_row_offset_idx)
        col_index = int(cell.start_col_offset_idx)
        rows.setdefault(row_index, {})[col_index] = cell.text.strip()
    rendered_rows = []
    for row_index in sorted(rows):
        cols = rows[row_index]
        rendered_rows.append(" | ".join(cols.get(col_index, "") for col_index in sorted(cols)))
    return "\n".join(row for row in rendered_rows if row.strip())


def _docling_table_data(item, *, page_number: int, page_height: float, coord_origin_cls, source_block_index: int) -> TableData:
    cells: list[TableCellData] = []
    for cell in item.data.table_cells:
        bbox = None
        if cell.bbox is not None:
            if cell.bbox.coord_origin == coord_origin_cls.BOTTOMLEFT:
                y0 = page_height - float(cell.bbox.t)
                y1 = page_height - float(cell.bbox.b)
            else:
                y0 = float(cell.bbox.t)
                y1 = float(cell.bbox.b)
            bbox = BBox(
                x0=float(cell.bbox.l),
                y0=min(y0, y1),
                x1=float(cell.bbox.r),
                y1=max(y0, y1),
            )
        cells.append(
            TableCellData(
                row_index=int(cell.start_row_offset_idx),
                col_index=int(cell.start_col_offset_idx),
                row_header_path=str(cell.start_row_offset_idx) if cell.row_header else None,
                col_header_path=str(cell.start_col_offset_idx) if cell.column_header else None,
                text=cell.text.strip(),
                bbox=bbox,
                metadata={
                    "row_span": int(cell.row_span),
                    "col_span": int(cell.col_span),
                    "row_header": bool(cell.row_header),
                    "column_header": bool(cell.column_header),
                    "row_section": bool(cell.row_section),
                },
            )
        )
    return TableData(
        page_start=page_number,
        page_end=page_number,
        parse_status=ParseStatus.PARSED,
        cells=cells,
        metadata={
            "source_block_index": source_block_index,
            "parser": "docling",
            "docling_ref": item.self_ref,
            "docling_label": getattr(item.label, "value", str(item.label)),
        },
    )


_PRECINCT_MAP_RE = re.compile(
    r"schedule\s+\d+.*(precinct|height|FAR|zone|district|map|view\s+plane)",
    re.IGNORECASE,
)


def _docling_picture_to_image(
    item, *, page_number: int, bbox: BBox | None, document
) -> ImageData:
    """Convert a Docling PictureItem into our ImageData record.

    Best-effort PNG bytes via ``item.get_image(document).save(buf)``. The
    pipeline writes those bytes to disk under a content-addressed path; we
    don't carry the bytes any further than necessary. ``figure_kind`` is a
    coarse classification from caption text; the precinct-map regex catches
    Schedule 15 / Schedule 17 / zone schedules without ML.
    """
    image_bytes: bytes | None = None
    try:
        pil_image = item.get_image(document)
        if pil_image is not None:
            buf = BytesIO()
            pil_image.save(buf, format="PNG")
            image_bytes = buf.getvalue()
    except Exception:
        # Docling may not have the image bytes for every PictureItem (e.g.
        # vector figures, or when image extraction wasn't enabled in
        # pipeline options). Persist the bbox/ref anyway so the figure is
        # at least addressable.
        image_bytes = None

    caption_text = ""
    try:
        caption = item.caption_text(document)
        if caption:
            caption_text = caption
    except Exception:
        caption_text = ""

    figure_kind = "precinct_map" if _PRECINCT_MAP_RE.search(caption_text) else "unknown"

    return ImageData(
        page_number=page_number,
        bbox=bbox,
        image_bytes=image_bytes,
        image_format="png" if image_bytes else None,
        docling_ref=getattr(item, "self_ref", None),
        figure_kind=figure_kind,
        parse_status=ParseStatus.PARSED,
        metadata={"caption_text": caption_text} if caption_text else {},
    )


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


def _segment_bbox_y(y0: float, y1: float, segment_count: int, segment_index: int) -> tuple[float, float]:
    if segment_count <= 1:
        return y0, y1
    height = max(y1 - y0, 1.0)
    step = height / segment_count
    segment_y0 = y0 + segment_index * step
    segment_y1 = y0 + (segment_index + 1) * step
    return segment_y0, segment_y1


def _build_page_blocks_from_pdf_text(
    *,
    page_number: int,
    text: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    page_height: float,
    reading_order_start: int,
    parser_source: str,
    confidence: float,
    profile: ParsingProfile | None = None,
) -> list[PageBlockData]:
    split_segments = split_toc_lines(text)
    if split_segments:
        blocks: list[PageBlockData] = []
        for segment_index, segment_text in enumerate(split_segments):
            segment_y0, segment_y1 = _segment_bbox_y(y0, y1, len(split_segments), segment_index)
            block_type = classify_text_block(segment_text, y0=segment_y0, y1=segment_y1, page_height=page_height, profile=profile)
            blocks.append(
                PageBlockData(
                    page_number=page_number,
                    block_type=block_type,
                    bbox=BBox(x0=x0, y0=segment_y0, x1=x1, y1=segment_y1),
                    reading_order=reading_order_start + segment_index,
                    raw_text=segment_text.strip(),
                    normalized_text=normalize_text(segment_text),
                    parser_source=parser_source,
                    confidence=confidence,
                    metadata={"split_pdf_toc_block": True},
                )
            )
        return blocks

    block_type = classify_text_block(text, y0=y0, y1=y1, page_height=page_height, profile=profile)
    return [
        PageBlockData(
            page_number=page_number,
            block_type=block_type,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
            reading_order=reading_order_start,
            raw_text=text.strip(),
            normalized_text=normalize_text(text),
            parser_source=parser_source,
            confidence=confidence,
        )
    ]
