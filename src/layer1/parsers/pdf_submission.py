"""PDF → Phase-1 attribute extraction stub (ABS-57).

Minimal PDF extractor that pulls text from the uploaded PDF and
returns attributes with low confidence scores. The real extraction
logic (OCR, table detection, spatial reasoning) is future work;
this stub ensures the submission pipeline can accept PDF uploads and
the confirmation UI has data to display.

Confidence policy mirrors the taxonomy's ``extraction_difficulty``
for ``pdf_vector``:

| level | meaning                                           |
| ----- | ------------------------------------------------- |
| 0.85  | Value found via keyword match in clean vector PDF |
| 0.5   | Value found but ambiguous context                 |
| 0.3   | Heuristic guess from nearby text                  |

Importing this module registers the PDF extractor for
``SubmissionSourceType.PDF``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionExtractionResult,
    SubmissionIngestConfig,
)
from layer1.parsers.submission_factory import register_extractor
from layer2.compliance.db.models import SubmissionSourceType

logger = logging.getLogger(__name__)

_HEIGHT_RE = re.compile(
    r"(?:building\s+height|max(?:imum)?\s+height)\s*[:\-–]?\s*(\d+(?:\.\d+)?)\s*(?:m(?:etres?)?|meters?)",
    re.IGNORECASE,
)
_STOREYS_RE = re.compile(
    r"(?:stor(?:ey|ie)s?|floors?)\s*[:\-–]?\s*(\d+)",
    re.IGNORECASE,
)
_GFA_RE = re.compile(
    r"(?:gross\s+floor\s+area|GFA|floor\s+area)\s*[:\-–]?\s*(\d+(?:\.\d+)?)\s*(?:m[²2]|sq\.?\s*m)",
    re.IGNORECASE,
)
_UNITS_RE = re.compile(
    r"(?:(?:dwelling\s+)?units?|residential\s+units?)\s*[:\-–]?\s*(\d+)",
    re.IGNORECASE,
)


def extract_pdf(
    source_path: Path, config: SubmissionIngestConfig
) -> SubmissionExtractionResult:
    text = _read_pdf_text(source_path)
    attributes: list[ExtractedAttribute] = []
    warnings: list[str] = []

    m = _HEIGHT_RE.search(text)
    if m:
        attributes.append(
            ExtractedAttribute(
                attribute_key="building_height_m",
                value=float(m.group(1)),
                unit="m",
                confidence=0.5,
                evidence={"pdf_snippet": text[max(0, m.start() - 40):m.end() + 40]},
            )
        )
    else:
        warnings.append("building_height_m not found in PDF text.")

    m = _STOREYS_RE.search(text)
    if m:
        attributes.append(
            ExtractedAttribute(
                attribute_key="building_height_storeys",
                value=int(m.group(1)),
                unit="storeys",
                confidence=0.85,
                evidence={"pdf_snippet": text[max(0, m.start() - 40):m.end() + 40]},
            )
        )

    m = _GFA_RE.search(text)
    if m:
        attributes.append(
            ExtractedAttribute(
                attribute_key="gross_floor_area_m2",
                value=float(m.group(1)),
                unit="m2",
                confidence=0.3,
                evidence={"pdf_snippet": text[max(0, m.start() - 40):m.end() + 40]},
            )
        )

    m = _UNITS_RE.search(text)
    if m:
        attributes.append(
            ExtractedAttribute(
                attribute_key="residential_unit_count",
                value=int(m.group(1)),
                unit="units",
                confidence=0.5,
                evidence={"pdf_snippet": text[max(0, m.start() - 40):m.end() + 40]},
            )
        )

    if not attributes:
        warnings.append(
            "PDF text extraction found no recognisable building attributes. "
            "All values must be entered manually on the confirmation screen."
        )

    return SubmissionExtractionResult(
        source_type=SubmissionSourceType.PDF,
        source_artifact_path=str(source_path),
        attributes=attributes,
        raw_metadata={"extractor": {"name": "pdf-submission-stub", "text_length": len(text)}},
        warnings=warnings,
    )


def _read_pdf_text(source_path: Path) -> str:
    try:
        import fitz

        doc = fitz.open(str(source_path))
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n".join(pages)
    except ImportError:
        pass
    try:
        from layer1.parsers.pdf import PdfParser

        result = PdfParser()(str(source_path))
        return "\n".join(b.text for b in result.blocks)
    except Exception:
        pass
    logger.warning("no PDF parser available; returning empty text for %s", source_path)
    return ""


register_extractor(SubmissionSourceType.PDF, extract_pdf)

__all__ = ["extract_pdf"]
