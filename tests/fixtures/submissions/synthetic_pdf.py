"""Builders for synthetic PDF submissions used by the ABS-55 tests.

Three shapes the test matrix exercises:

* ``write_vector_pdf`` — a one-page vector-native PDF with selectable
  title-block text and dimension strings. Mimics a CAD-export PDF.
* ``write_raster_pdf`` — a one-page image-only PDF (the page's only
  content is an embedded raster). Mimics a scanned drawing.
* ``write_mixed_pdf`` — multi-page PDF with one vector page + one
  raster page. Mimics a submission package that includes both a clean
  vector site plan and a scanned heritage drawing.

The builders write PDFs to disk via PyMuPDF (``fitz``); each test owns
its own ``tmp_path``-scoped file so fixtures stay hermetic. Keep these
deliberately tiny — the extractor logic is exercised against a stubbed
vision LLM, so we only need the PDFs to *exist* and roughly look like
the shape the extractor is asked to handle (text spans, vector vs.
raster pages).
"""
from __future__ import annotations

import io
from pathlib import Path


def write_vector_pdf(
    path: Path,
    *,
    title_text: str = "ARCH-001 Site Plan",
    dimension_text: str = "FRONT SETBACK 7.5 m",
    extra_text: list[str] | None = None,
) -> Path:
    """Write a single-page vector PDF with selectable text.

    ``title_text`` lands in the bottom-right (the conventional
    title-block corner); ``dimension_text`` lands in the page body;
    ``extra_text`` is appended as additional spans so tests can stuff
    multiple dimension strings on the page.
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=842, height=595)  # A4 landscape, pts
    page.insert_text((50, 80), dimension_text, fontsize=12)
    if extra_text:
        y = 110
        for line in extra_text:
            page.insert_text((50, y), line, fontsize=12)
            y += 24
    page.insert_text((550, 560), title_text, fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


def write_raster_pdf(
    path: Path,
    *,
    image_size: tuple[int, int] = (600, 400),
    page_size_pts: tuple[float, float] = (842.0, 595.0),
) -> Path:
    """Write a single-page raster-only PDF.

    Embeds a solid PNG image as the entire page content — no selectable
    text. The extractor's vector-detection branch should mark this page
    ``is_vector_page=False`` and rely entirely on the vision LLM.
    """
    import fitz

    png_bytes = _solid_png(image_size, color=(220, 220, 230))
    doc = fitz.open()
    page = doc.new_page(width=page_size_pts[0], height=page_size_pts[1])
    rect = fitz.Rect(0, 0, page_size_pts[0], page_size_pts[1])
    page.insert_image(rect, stream=png_bytes)
    doc.save(str(path))
    doc.close()
    return path


def write_mixed_pdf(
    path: Path,
    *,
    title_text: str = "ARCH-002 Multi-Sheet",
    dimension_text: str = "REAR SETBACK 3.0 m",
) -> Path:
    """Write a two-page PDF: one vector page + one raster-only page.

    Used to verify per-page provenance: the extractor should record
    ``is_vector_text=True`` on page 1 and ``False`` on page 2 in its
    raw_metadata.
    """
    import fitz

    doc = fitz.open()

    vector_page = doc.new_page(width=842, height=595)
    vector_page.insert_text((50, 80), dimension_text, fontsize=12)
    vector_page.insert_text((550, 560), title_text, fontsize=10)

    raster_page = doc.new_page(width=842, height=595)
    png_bytes = _solid_png((600, 400), color=(245, 245, 250))
    raster_page.insert_image(fitz.Rect(0, 0, 842, 595), stream=png_bytes)

    doc.save(str(path))
    doc.close()
    return path


def _solid_png(size: tuple[int, int], *, color: tuple[int, int, int]) -> bytes:
    """Build a tiny PNG (no third-party deps) for the raster fixtures."""
    from PIL import Image

    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
