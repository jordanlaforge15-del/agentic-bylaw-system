from __future__ import annotations

from pathlib import Path

from layer1.models.schemas import TableData
from layer1.parsers.base import ParseResult
from layer1.parsers.pdf import CamelotTableFallback, DoclingParser, PdfParser, _extract_docling_tables
from layer1.parsers.text import TextParser
from layer1.profiles import ParsingProfile, profile_for_path
from layer1.utils.files import detect_mime_type


def parse_source(
    path: Path,
    *,
    ocr: bool = False,
    debug: bool = False,
    camelot: bool = False,
    profile: ParsingProfile | None = None,
) -> ParseResult:
    mime_type = detect_mime_type(path)
    warnings: list[str] = []
    profile = profile_for_path(path, profile)
    if mime_type == "application/pdf":
        if getattr(profile, "use_full_docling", True):
            try:
                result = DoclingParser().parse(path, ocr=ocr, debug=debug, profile=profile)
            except Exception as exc:
                warnings.append(f"Docling parse unavailable or failed: {exc}")
                result = PdfParser().parse(path, ocr=ocr, debug=debug, profile=profile)
        else:
            warnings.append(f"Full-document Docling parse skipped by profile: {profile.name}")
            result = PdfParser().parse(path, ocr=ocr, debug=debug, profile=profile)
        result.warnings = warnings + result.warnings
        if camelot:
            result.tables.extend(CamelotTableFallback().parse_tables(path))
        if getattr(profile, "use_docling_table_structure", True):
            try:
                structured_tables = _extract_docling_tables(path, ocr=ocr)
                if structured_tables:
                    result.tables = _merge_tables(result.tables, structured_tables)
            except Exception as exc:
                result.warnings.append(f"Structured table extraction failed: {exc}")
        return profile.postprocess_parse_result(result)

    result = TextParser().parse(path, ocr=ocr, debug=debug, profile=profile)
    result.warnings = warnings + result.warnings
    return profile.postprocess_parse_result(result)


def _merge_tables(basic_tables: list[TableData], structured_tables: list[TableData]) -> list[TableData]:
    structured_pages: set[int] = set()
    for st in structured_tables:
        for p in range(st.page_start, (st.page_end or st.page_start) + 1):
            structured_pages.add(p)

    block_indices_by_page: dict[int, list[int]] = {}
    for bt in basic_tables:
        if bt.page_start in structured_pages:
            idx = bt.metadata.get("source_block_index")
            if idx is not None:
                block_indices_by_page.setdefault(bt.page_start, []).append(idx)

    for st in structured_tables:
        indices: list[int] = []
        for p in range(st.page_start, (st.page_end or st.page_start) + 1):
            indices.extend(block_indices_by_page.get(p, []))
        if indices:
            st.metadata["source_block_indices"] = indices

    kept = [t for t in basic_tables if t.page_start not in structured_pages]
    return kept + structured_tables
