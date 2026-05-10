"""Tests for the bootstrap PDF reader (Phase 1, Task 1)."""
from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from bootstrap.pdf_reader import PdfBootstrapReader


HALIFAX_PDF = Path(
    "/Users/christopherrafuse/dev/agentic-bylaw-system"
    "/regionalcentrelub-eff-26april13-case24469toclinked.pdf"
)


def _build_synthetic_pages() -> dict[int, str]:
    """Synthetic 20-page bylaw with clear front/back matter and a body."""
    pages: dict[int, str] = {}
    pages[1] = "TITLE PAGE\nHalifax Regional Centre Land Use By-law"
    pages[2] = "TABLE OF CONTENTS\n  Section A ........... 4\n  Section B ........ 10"
    pages[3] = "DEFINITIONS\nThe following terms have the meanings assigned."

    # Body pages 4-17: each carries at least one priority-2 match.
    body_lines_a = (
        "1 This By-law applies to:\n"
        "(a) thing one\n"
        "(b) thing two\n"
    )
    body_lines_b = (
        "2 The following definitions:\n"
        "(a) widget\n"
        "(b) gadget\n"
    )
    for p in range(4, 18):
        pages[p] = body_lines_a if p % 2 == 0 else body_lines_b

    pages[18] = "REVISION HISTORY\nAdopted on date X."
    pages[19] = "INDEX OF TERMS\nabutting, accessory, ..."
    pages[20] = "AMENDMENTS LOG\nAmendment 1, Amendment 2."
    return pages


@pytest.mark.skipif(
    not HALIFAX_PDF.exists(), reason="Halifax PDF fixture not available"
)
def test_extract_pages_returns_all_pages() -> None:
    """T1-A: extract_pages returns one entry per page, mostly non-empty."""
    reader = PdfBootstrapReader(str(HALIFAX_PDF))
    pages = reader.extract_pages()

    with pdfplumber.open(str(HALIFAX_PDF)) as pdf:
        expected_count = len(pdf.pages)

    assert len(pages) == expected_count
    assert expected_count > 100, (
        f"sanity check: expected a real bylaw PDF, got {expected_count} pages"
    )
    assert all(isinstance(v, str) for v in pages.values())

    non_empty = sum(1 for v in pages.values() if v.strip())
    assert non_empty / len(pages) >= 0.9, (
        f"only {non_empty}/{len(pages)} pages had text"
    )


def test_detect_content_zone_excludes_front_and_back_matter() -> None:
    """T1-B: detect_content_zone homes in on the regulatory body."""
    pages = _build_synthetic_pages()
    reader = PdfBootstrapReader("ignored — operating on injected pages")

    start, end = reader.detect_content_zone(pages)

    assert abs(start - 4) <= 1, f"start_page {start} not within 1 of 4"
    assert abs(end - 17) <= 1, f"end_page {end} not within 1 of 17"


def test_sample_windows_coverage() -> None:
    """T1-C: sample_windows produces non-overlapping coverage of the zone."""
    pages = _build_synthetic_pages()
    reader = PdfBootstrapReader("ignored — operating on injected pages")

    windows = reader.sample_windows(
        pages, (4, 17), n_windows=3, window_size=3
    )

    assert len(windows) == 3
    for w in windows:
        assert 4 <= w["start_page"] <= 17
        assert 4 <= w["end_page"] <= 17
        assert w["start_page"] <= w["end_page"]

    ordered = sorted(windows, key=lambda w: w["start_page"])
    for prev, nxt in zip(ordered, ordered[1:]):
        assert prev["end_page"] < nxt["start_page"], (
            f"windows overlap: {prev} and {nxt}"
        )

    covered: set[int] = set()
    for w in windows:
        covered.update(range(w["start_page"], w["end_page"] + 1))

    zone_size = 17 - 4 + 1  # 14 pages
    assert len(covered) >= zone_size * 0.5, (
        f"only {len(covered)} unique pages covered out of {zone_size}"
    )
