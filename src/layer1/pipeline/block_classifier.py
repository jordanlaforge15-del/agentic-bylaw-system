from __future__ import annotations

import re
from collections import Counter

from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.pipeline.citations import parse_citation_label


FOOTNOTE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\s+)[a-z].{8,}$")
LIST_RE = re.compile(r"^\s*(?:\([a-z]\)|\([ivxlcdm]+\)|[-*•])\s+", re.IGNORECASE)
TABLE_HINT_RE = re.compile(r"\s{2,}|\t|\|")


def normalize_text(text: str) -> str:
    return " ".join(text.replace("\x00", "").split())


def classify_text_block(text: str, y0: float | None = None, y1: float | None = None, page_height: float | None = None) -> BlockType:
    norm = normalize_text(text)
    if not norm:
        return BlockType.UNKNOWN
    if page_height:
        if y1 is not None and y1 < page_height * 0.10:
            return BlockType.HEADER
        if y0 is not None and y0 > page_height * 0.90:
            return BlockType.FOOTER
    if FOOTNOTE_RE.match(norm) and len(norm) < 400:
        return BlockType.FOOTNOTE
    if parse_citation_label(norm):
        return BlockType.HEADING if len(norm) <= 160 else BlockType.LIST_ITEM
    if LIST_RE.match(norm):
        return BlockType.LIST_ITEM
    if TABLE_HINT_RE.search(text) and len(norm.split()) <= 80:
        return BlockType.TABLE_REGION
    if norm.isupper() and 2 <= len(norm.split()) <= 12:
        return BlockType.HEADING
    return BlockType.PARAGRAPH


def mark_boilerplate(blocks: list[PageBlockData], repetition_threshold: int = 2) -> list[PageBlockData]:
    counter = Counter(
        normalize_text(block.raw_text).lower()
        for block in blocks
        if block.raw_text and len(normalize_text(block.raw_text)) <= 120
    )
    page_count = len({block.page_number for block in blocks})
    threshold = min(page_count, repetition_threshold)
    if threshold < 2:
        return blocks
    for block in blocks:
        key = normalize_text(block.raw_text).lower()
        if counter[key] >= threshold and len(key) <= 120:
            block.is_boilerplate = True
            if block.block_type not in {BlockType.HEADER, BlockType.FOOTER}:
                block.block_type = BlockType.FOOTER
    return blocks
