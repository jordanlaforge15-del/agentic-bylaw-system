from __future__ import annotations

import re
from collections import Counter

from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.pipeline.citations import parse_citation_label
from layer1.profiles import ParsingProfile, get_parsing_profile


FOOTNOTE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\s+)[A-Z]?[a-z].{8,}$")
LIST_RE = re.compile(r"^\s*(?:\([a-z]\)|\([ivxlcdm]+\)|[-*•])\s+", re.IGNORECASE)
TABLE_HINT_RE = re.compile(r"(?:[ \t]{2,}|\t|\|)")
NUMERIC_CELL_RE = re.compile(r"^(?:\d+(?:\.\d+)?%?|\d+\s*(?:m2|m²|ft2|ft²)|[ivxlcdm]+\)|[A-Za-z]-\d.*)$", re.IGNORECASE)
PAGE_PREFIX_RE = re.compile(r"^\s*PAGE\s+(?=[A-Z\"(])")
TOC_ENTRY_RE = re.compile(r"[.\u2026…]{2,}\s*\d+\s*$")
TOC_CODE_LINE_RE = re.compile(r"^[A-Z]{1,4}(?:-\d{1,2}[A-Z]?)?$")
PAGE_NUMBER_RE = re.compile(r"^(?:-?[ivxlcdm]+-?|\d+)$", re.IGNORECASE)


def normalize_text(text: str) -> str:
    return " ".join(text.replace("\x00", "").split())


def classify_text_block(
    text: str,
    y0: float | None = None,
    y1: float | None = None,
    page_height: float | None = None,
    profile: ParsingProfile | None = None,
) -> BlockType:
    profile = get_parsing_profile(profile)
    cleaned_text = strip_page_prefix(text)
    norm = normalize_text(cleaned_text)
    if not norm:
        return BlockType.UNKNOWN
    if _looks_like_toc_entry(cleaned_text):
        return BlockType.HEADING
    if page_height and PAGE_NUMBER_RE.fullmatch(norm):
        if y1 is not None and y1 < page_height * 0.10:
            return BlockType.HEADER
        if y0 is not None and y0 > page_height * 0.90:
            return BlockType.FOOTER
    if detect_table_like_text(cleaned_text, profile=profile):
        return BlockType.TABLE_REGION
    if FOOTNOTE_RE.match(norm) and len(norm) < 400:
        return BlockType.FOOTNOTE
    if LIST_RE.match(norm):
        return BlockType.LIST_ITEM
    if parse_citation_label(norm, profile=profile):
        return BlockType.HEADING if len(norm) <= 160 else BlockType.LIST_ITEM
    if looks_like_topic_heading(norm):
        return BlockType.HEADING
    if TABLE_HINT_RE.search(cleaned_text) and len(norm.split()) <= 12 and _looks_like_table_cell(norm):
        return BlockType.TABLE_REGION
    if norm.isupper() and 2 <= len(norm.split()) <= 12:
        return BlockType.HEADING
    if page_height:
        if y1 is not None and y1 < page_height * 0.10:
            return BlockType.HEADER
        if y0 is not None and y0 > page_height * 0.90:
            return BlockType.FOOTER
    return BlockType.PARAGRAPH


def detect_table_like_text(text: str, profile: ParsingProfile | None = None) -> bool:
    profile = get_parsing_profile(profile)
    text = strip_page_prefix(text)
    if "|" in text or "\t" in text:
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    numericish = sum(1 for line in lines if _looks_like_table_cell(line))
    citationish = sum(1 for line in lines if parse_citation_label(normalize_text(line), profile=profile) is not None)
    short_lines = sum(1 for line in lines if len(line.split()) <= 8)
    long_prose_lines = sum(1 for line in lines if len(line.split()) >= 9)
    if citationish >= 1 and long_prose_lines >= 1 and numericish == 0:
        return False
    if numericish >= 2 and short_lines >= 2 and citationish <= max(1, len(lines) // 3):
        return True
    if short_lines >= 3 and TABLE_HINT_RE.search(text):
        return True
    return False


def looks_like_topic_heading(text: str) -> bool:
    if any(punct in text for punct in ".;:"):
        return False
    words = text.split()
    if not 1 <= len(words) <= 8:
        return False
    if any(word[:1].islower() for word in words if word[:1].isalpha()):
        return False
    return sum(any(ch.isalpha() for ch in word) for word in words) >= 1


def strip_page_prefix(text: str) -> str:
    return PAGE_PREFIX_RE.sub("", text).strip()


def _looks_like_table_cell(line: str) -> bool:
    compact = normalize_text(line)
    if not compact:
        return False
    if NUMERIC_CELL_RE.match(compact):
        return True
    if any(token in compact for token in ["Class A", "Class B", "%", "minimum", "maximum"]):
        return True
    words = compact.split()
    return len(words) <= 6 and sum(any(ch.isdigit() for ch in word) for word in words) >= 1


def _looks_like_toc_entry(text: str) -> bool:
    return TOC_ENTRY_RE.search(normalize_text(text)) is not None


def _strip_toc_leaders(text: str) -> str:
    return TOC_ENTRY_RE.sub("", text).strip()


def split_toc_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    segments: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if _looks_like_toc_entry(line):
            segments.append(line)
            idx += 1
            continue
        if TOC_CODE_LINE_RE.fullmatch(normalize_text(line)) and idx + 1 < len(lines) and _looks_like_toc_entry(lines[idx + 1]):
            segments.append(f"{line}\n{lines[idx + 1]}")
            idx += 2
            continue
        return []
    return segments if len(segments) > 1 else []


def mark_boilerplate(
    blocks: list[PageBlockData],
    repetition_threshold: int = 2,
    profile: ParsingProfile | None = None,
) -> list[PageBlockData]:
    profile = get_parsing_profile(profile)
    counter = Counter(
        normalize_text(block.raw_text).lower()
        for block in blocks
        if block.raw_text and len(normalize_text(block.raw_text)) <= 120 and _eligible_for_boilerplate_marking(block, profile)
    )
    page_count = len({block.page_number for block in blocks})
    threshold = min(page_count, repetition_threshold)
    if threshold < 2:
        return blocks
    for block in blocks:
        if not _eligible_for_boilerplate_marking(block, profile):
            continue
        key = normalize_text(block.raw_text).lower()
        if counter[key] >= threshold and len(key) <= 120:
            block.is_boilerplate = True
            if block.block_type not in {BlockType.HEADER, BlockType.FOOTER}:
                block.block_type = BlockType.FOOTER
    return blocks


def _eligible_for_boilerplate_marking(block: PageBlockData, profile: ParsingProfile) -> bool:
    norm = normalize_text(block.raw_text)
    if not norm:
        return False
    if block.block_type == BlockType.LIST_ITEM:
        return False
    if parse_citation_label(norm, profile=profile):
        return False
    if LIST_RE.match(norm):
        return False
    if profile.protect_docling_headings_from_boilerplate and block.parser_source == "docling" and block.block_type == BlockType.HEADING:
        return False
    return True
