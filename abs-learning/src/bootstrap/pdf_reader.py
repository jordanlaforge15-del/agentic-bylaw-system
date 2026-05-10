"""Bootstrap PDF reader for Phase 1.

Provides :class:`PdfBootstrapReader` which extracts page text from a PDF,
detects the regulatory content zone (skipping front and back matter), and
samples evenly spaced windows for downstream LLM-driven structure inference.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

import pdfplumber


# Patterns are anchored at the start of a line; use re.MULTILINE on page text.
# Priority order matters: a Part heading is a stronger signal than a numbered
# section, which is in turn stronger than a bare subsection like "(1)".
_PRIORITY_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"^Part [IVX]+", re.MULTILINE),
    re.compile(r"^\d{1,3}\s+[A-Z]", re.MULTILINE),
    re.compile(r"^\(\d+\)", re.MULTILINE),
)


class PdfBootstrapReader:
    """Read a PDF, detect its regulatory body, and sample windows from it."""

    def __init__(self, pdf_path: str) -> None:
        self.pdf_path = pdf_path

    def extract_pages(self) -> Dict[int, str]:
        """Return ``{page_number: text}`` for every page (1-indexed)."""
        pages: Dict[int, str] = {}
        with pdfplumber.open(self.pdf_path) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                pages[idx] = page.extract_text() or ""
        return pages

    def detect_content_zone(
        self, pages: Dict[int, str]
    ) -> Tuple[int, int]:
        """Return ``(start_page, end_page)`` of the regulatory content zone.

        Skips the first and last 5% of pages, then walks forward/backward
        through the remaining range looking for the highest-priority pattern
        match. Falls through to lower priority patterns only if no page in
        range matches the higher one. Falls back to the trimmed range if
        nothing matches.
        """
        total = len(pages)
        if total == 0:
            return (0, 0)

        # Trim ~5% of pages off each end as front/back matter, but cap the
        # absolute trim so that on long bylaws (e.g. 400+ pages) we don't
        # skip past the first few real provisions. Halifax's Regional
        # Centre LUB has its first numbered section on page 8 of 457 — a
        # blanket 5% skip would land at page 23 and miss Part I entirely.
        skip = max(1, min(int(total * 0.05), 5))
        first_scan = skip + 1
        last_scan = total - skip
        if first_scan > last_scan:
            # Degenerate tiny PDF — nothing to do, return a sane default.
            return (first_scan, last_scan)

        scan_range = range(first_scan, last_scan + 1)

        for pattern in _PRIORITY_PATTERNS:
            first_match = None
            last_match = None
            for p in scan_range:
                if pattern.search(pages.get(p, "")):
                    if first_match is None:
                        first_match = p
                    last_match = p
            if first_match is not None and last_match is not None:
                return (first_match, last_match)

        return (first_scan, last_scan)

    def sample_windows(
        self,
        pages: Dict[int, str],
        content_zone: Tuple[int, int],
        n_windows: int = 4,
        window_size: int = 25,
    ) -> List[Dict]:
        """Return ``n_windows`` non-overlapping windows from the content zone.

        Each window is a dict with keys ``window_index`` (0-based),
        ``start_page``, ``end_page``, and ``text`` (joined page text separated
        by newlines).
        """
        start, end = content_zone
        zone_len = end - start + 1
        if n_windows <= 0 or window_size <= 0 or zone_len <= 0:
            return []

        if n_windows * window_size >= zone_len:
            # Lay windows adjacently inside the zone, clamping to the end.
            starts: List[int] = []
            cursor = start
            for _ in range(n_windows):
                if cursor > end:
                    break
                starts.append(cursor)
                cursor += window_size
        elif n_windows == 1:
            starts = [start]
        else:
            stride = (zone_len - window_size) // (n_windows - 1)
            starts = [start + i * stride for i in range(n_windows)]

        windows: List[Dict] = []
        for i, s in enumerate(starts):
            e = s + window_size - 1
            if e > end:
                e = end
                s = max(start, e - window_size + 1)
            text = "\n".join(pages.get(p, "") for p in range(s, e + 1))
            windows.append(
                {
                    "window_index": i,
                    "start_page": s,
                    "end_page": e,
                    "text": text,
                }
            )
        return windows
