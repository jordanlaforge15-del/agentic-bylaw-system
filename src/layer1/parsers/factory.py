from __future__ import annotations

from pathlib import Path

from layer1.parsers.base import ParseResult
from layer1.parsers.pdf import CamelotTableFallback, DoclingParser, PdfParser
from layer1.parsers.text import TextParser
from layer1.profiles import profile_for_path
from layer1.utils.files import detect_mime_type


def parse_source(path: Path, *, ocr: bool = False, debug: bool = False, camelot: bool = False) -> ParseResult:
    mime_type = detect_mime_type(path)
    warnings: list[str] = []
    profile = profile_for_path(path)
    if mime_type == "application/pdf":
        if getattr(profile, "use_full_docling", True):
            try:
                result = DoclingParser().parse(path, ocr=ocr, debug=debug)
            except Exception as exc:
                warnings.append(f"Docling parse unavailable or failed: {exc}")
                result = PdfParser().parse(path, ocr=ocr, debug=debug)
        else:
            warnings.append(f"Full-document Docling parse skipped by profile: {profile.name}")
            result = PdfParser().parse(path, ocr=ocr, debug=debug)
        result.warnings = warnings + result.warnings
        if camelot:
            result.tables.extend(CamelotTableFallback().parse_tables(path))
        return profile.postprocess_parse_result(result)

    result = TextParser().parse(path, ocr=ocr, debug=debug)
    result.warnings = warnings + result.warnings
    return profile.postprocess_parse_result(result)
