"""End-to-end Halifax integration tests for the Structure Analyst.

These tests run the real Phase 1 pipeline against the Halifax Regional
Centre Land Use By-law PDF, hitting the Anthropic API for the
LLM-driven citation pattern extraction. They are gated behind the
``integration`` marker and the session-scoped budgeted
``anthropic_client`` fixture (see ``conftest.py``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

GROUND_TRUTH_PATH = Path(__file__).parent.parent / "fixtures" / "halifax_ground_truth.json"


@pytest.fixture
def ground_truth():
    return json.loads(GROUND_TRUTH_PATH.read_text())


@pytest.fixture
def halifax_pdf_path():
    return "/Users/christopherrafuse/dev/agentic-bylaw-system/regionalcentrelub-eff-26april13-case24469toclinked.pdf"


# --------------------------------------------------------------------------- T5-A
@pytest.mark.integration
def test_content_zone_detection(halifax_pdf_path, ground_truth):
    """Content-zone detection should land in the regulatory body of the PDF.

    No Anthropic API calls are made by this test; it exercises only the
    pdfplumber-backed bootstrap reader.
    """
    from bootstrap.pdf_reader import PdfBootstrapReader

    reader = PdfBootstrapReader(halifax_pdf_path)
    pages = reader.extract_pages()
    start, end = reader.detect_content_zone(pages)

    assert start <= 12, f"Content zone start too late: page {start}"
    zone_fraction = (end - start) / len(pages)
    assert zone_fraction >= 0.60, f"Content zone too narrow: {zone_fraction:.0%}"

    front_text = " ".join(pages[p] for p in range(1, start))
    numbered_provisions = re.findall(r'^\d{1,3}\s+[A-Z]', front_text, re.MULTILINE)
    assert len(numbered_provisions) <= 3, (
        f"Too many numbered provisions in front matter: {numbered_provisions[:5]}"
    )


# --------------------------------------------------------------------------- T5-B
@pytest.mark.integration
def test_citation_pattern_extraction(halifax_pdf_path, ground_truth, anthropic_client):
    """A single LLM call on a content-zone window should yield >= 3 levels.

    Spends 1 of the 8-call budget.
    """
    from bootstrap.pdf_reader import PdfBootstrapReader
    from agents.structure_analyst import StructureAnalystAgent

    reader = PdfBootstrapReader(halifax_pdf_path)
    pages = reader.extract_pages()
    zone = reader.detect_content_zone(pages)
    windows = reader.sample_windows(pages, zone, n_windows=4, window_size=25)

    agent = StructureAnalystAgent(anthropic_client)
    result = agent._extract_patterns_from_window(windows[1])

    assert len(result["hierarchy"]) >= 3, (
        f"Too few levels detected: {[h['level'] for h in result['hierarchy']]}"
    )

    for expected in ground_truth["citation_hierarchy"]:
        matching = [h for h in result["hierarchy"] if h["level"] == expected["level"]]
        if not matching:
            continue
        pattern = matching[0]["pattern"]
        assert re.match(pattern, expected["label_example"]), (
            f"Pattern {pattern!r} for level '{expected['level']}' does not match "
            f"{expected['label_example']!r}"
        )

    assert len(result["flags"]) <= 3, f"Too many flags: {result['flags']}"


# --------------------------------------------------------------------------- T5-C
@pytest.mark.integration
def test_full_parser_config_produces_known_citations(
    halifax_pdf_path, ground_truth, anthropic_client
):
    """Full ``analyse`` pipeline (4 windows = 4 LLM calls) should resolve
    Halifax's known citation labels with high confidence."""
    from manifest.models import SourceDocument
    from agents.structure_analyst import StructureAnalystAgent

    source_doc = SourceDocument(
        document_name="Regional Centre Land Use By-Law",
        document_type="bylaw",
        document_role="Primary land-use bylaw",
        parent_document=None,
        source_url=f"file://{halifax_pdf_path}",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=457,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )

    agent = StructureAnalystAgent(anthropic_client)
    config = agent.analyse(source_doc, jurisdiction_code="halifax")

    label_to_level = {
        "Part I": "part",
        "1": "section",
        "(a)": "clause",
        "(i)": "subclause",
    }
    level_patterns = {h.level: h.pattern for h in config.citation_scheme.hierarchy}

    resolved = sum(
        1
        for label, lvl in label_to_level.items()
        if lvl in level_patterns and re.match(level_patterns[lvl], label)
    )
    rate = resolved / len(label_to_level)
    assert rate >= 0.75, (
        f"Pattern resolution rate too low: {rate:.0%}. Patterns: {level_patterns}"
    )
    assert config.confidence >= 0.70, f"Confidence too low: {config.confidence}"
