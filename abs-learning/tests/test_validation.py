"""Unit tests for the Phase 2 Validation agent.

All PDF reads and retrieval lookups are mocked; no I/O occurs.
"""
from __future__ import annotations

from typing import Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from agents.validation import ValidationAgent
from manifest.models import (
    CitationLevel,
    CitationScheme,
    ParserConfig,
    QAReport,
    SourceDocument,
    TaxonomyMap,
    ZoneDesignation,
)


def _stub_source_doc(tmp_path) -> SourceDocument:
    dummy = tmp_path / "stub.pdf"
    dummy.write_bytes(b"%PDF-stub")
    return SourceDocument(
        document_name="Stub Bylaw",
        document_type="bylaw",
        document_role="primary",
        parent_document=None,
        source_url=f"file://{dummy}",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=10,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )


def _make_taxonomy(codes: List[str]) -> TaxonomyMap:
    return TaxonomyMap(
        zone_designations=[
            ZoneDesignation(code=c, canonical_type="residential") for c in codes
        ],
        use_class_map={},
        standards_categories=[],
        companion_bylaws_required=[],
        confidence=1.0,
    )


def _stub_reader(
    pages: Dict[int, str], content_zone: Tuple[int, int]
):
    class _FakeReader:
        def __init__(self, _path):
            pass

        def extract_pages(self):
            return pages

        def detect_content_zone(self, _pages):
            return content_zone

    return _FakeReader


# ----------------------------------------------------------------------- T7-A
def test_t7a_citation_resolution_counts_correctly():
    client = MagicMock()
    client.lookup_citation.side_effect = (
        [{"text": f"fragment {i}"} for i in range(8)] + [None, None]
    )
    agent = ValidationAgent(client)

    rate, flags = agent._check_citation_resolution(
        [f"Part I > {i}" for i in range(10)]
    )
    assert rate == pytest.approx(0.80)
    assert len(flags) == 2
    assert all(f.get("code") == "citation_unresolved" for f in flags)


# ----------------------------------------------------------------------- T7-B
def test_t7b_zone_completeness_detects_missing_codes(monkeypatch, tmp_path):
    in_tax = [f"Z{chr(ord('A') + i)}-1" for i in range(20)]   # 20 codes
    out_of_tax = [f"Y{chr(ord('A') + i)}-1" for i in range(5)]  # 5 extras

    # Each code is repeated so it clears the min-occurrence filter — this
    # asserts the "ratio of distinct codes in taxonomy / distinct codes
    # found" semantics, not the noise-filtering behaviour (covered elsewhere).
    pages_text = " ".join((in_tax + out_of_tax) * 5)
    pages = {1: pages_text, 2: "", 3: ""}
    monkeypatch.setattr(
        "agents.validation.PdfBootstrapReader", _stub_reader(pages, (1, 3))
    )

    agent = ValidationAgent(MagicMock())
    source_doc = _stub_source_doc(tmp_path)
    taxonomy = _make_taxonomy(in_tax)

    completeness, flags = agent._check_zone_completeness(source_doc, taxonomy)
    assert completeness == pytest.approx(20 / 25)
    assert len(flags) == 5
    flagged_codes = {f["zone_code"] for f in flags}
    assert flagged_codes == set(out_of_tax)


def test_zone_completeness_filters_low_occurrence_noise(monkeypatch, tmp_path):
    """One-off matches must not count as zone codes."""
    taxonomy_codes = ["ZA-1", "ZB-1"]
    # Each taxonomy code appears 10 times; noise appears once.
    pages = {
        1: " ".join(taxonomy_codes * 10) + " " + "F-4 CH-7 E-5 DN-1",
    }
    monkeypatch.setattr(
        "agents.validation.PdfBootstrapReader", _stub_reader(pages, (1, 1))
    )

    agent = ValidationAgent(MagicMock())
    source_doc = _stub_source_doc(tmp_path)
    taxonomy = _make_taxonomy(taxonomy_codes)

    completeness, flags = agent._check_zone_completeness(source_doc, taxonomy)
    # F-4, CH-7, E-5, DN-1 each occur once and are below the min-count
    # threshold; they must be filtered out (not flagged as missing).
    assert completeness == pytest.approx(1.0)
    assert flags == []


# ----------------------------------------------------------------------- T7-C
def test_t7c_pattern_coverage_detects_broken_patterns(monkeypatch, tmp_path):
    pages = {p: "alpha\nbeta\ngamma" for p in range(1, 80)}
    monkeypatch.setattr(
        "agents.validation.PdfBootstrapReader", _stub_reader(pages, (1, 79))
    )

    parser_config = ParserConfig(
        parser_version="docling:test",
        citation_scheme=CitationScheme(
            full_citation_example="x",
            separator=" > ",
            hierarchy=[
                CitationLevel(level="section", pattern="^ZZZNOMATCH", label_format="X")
            ],
        ),
        schedule_patterns=[],
        table_caption_pattern=None,
        confidence=1.0,
        flags=[],
    )

    agent = ValidationAgent(MagicMock())
    coverage, flags = agent._check_pattern_coverage(
        _stub_source_doc(tmp_path), parser_config
    )
    assert coverage == 0.0
    error_flags = [f for f in flags if f.get("severity") == "error"]
    assert error_flags, f"Expected at least one error flag, got {flags!r}"


# ----------------------------------------------------------------------- T7-D
@pytest.mark.parametrize(
    ("cite", "zone", "cov", "flags", "expected_status", "expected_action"),
    [
        (0.82, 0.90, 0.10, [], "PASS", "approve"),
        (0.70, 0.75, 0.10, [{"severity": "warn"}], "PASS_WITH_FLAGS", "approve_with_review"),
        (0.60, 0.90, 0.10, [], "FAIL_WITH_RETRIEVAL_GAP", "investigate_retrieval"),
    ],
)
def test_t7d_status_thresholds(cite, zone, cov, flags, expected_status, expected_action):
    agent = ValidationAgent(MagicMock())
    status, action = agent._determine_status(cite, zone, cov, flags)
    assert status == expected_status
    assert action == expected_action


def test_t7d_error_flag_blocks_pass():
    agent = ValidationAgent(MagicMock())
    status, _ = agent._determine_status(
        0.82, 0.90, 0.10, [{"severity": "error"}]
    )
    # Error flag pushes a would-be PASS down into PASS_WITH_FLAGS (since
    # minimum-acceptable thresholds are still met).
    assert status == "PASS_WITH_FLAGS"


# -------------------------------------------------------- ABS-97: pending ingest
def test_abs97_pass_pending_ingest_when_citation_unavailable():
    """Pre-ingest scenario: zone+pattern pass, citation can't be tested."""
    agent = ValidationAgent(MagicMock())
    status, action = agent._determine_status(
        0.0, 0.90, 0.10, [], citation_unavailable=True
    )
    assert status == "PASS_PENDING_INGEST"
    assert action == "approve_pending_ingest"


def test_abs97_pending_ingest_requires_zone_pattern_pass():
    """citation_unavailable alone isn't enough — zone/pattern must also pass."""
    agent = ValidationAgent(MagicMock())
    status, _ = agent._determine_status(
        0.0, 0.50, 0.10, [], citation_unavailable=True
    )
    assert status == "FAIL"


def test_abs97_pending_ingest_blocked_by_error_flag():
    """Error flags prevent PASS_PENDING_INGEST even if zone/pattern thresholds met."""
    agent = ValidationAgent(MagicMock())
    status, _ = agent._determine_status(
        0.0, 0.90, 0.10, [{"severity": "error"}], citation_unavailable=True
    )
    assert status != "PASS_PENDING_INGEST"


def test_abs97_fail_with_retrieval_gap():
    """Post-ingest: zone+pattern pass, citation > 0 but below threshold."""
    agent = ValidationAgent(MagicMock())
    status, action = agent._determine_status(
        0.40, 0.90, 0.10, [], citation_unavailable=False
    )
    assert status == "FAIL_WITH_RETRIEVAL_GAP"
    assert action == "investigate_retrieval"


def test_abs97_retrieval_gap_not_triggered_at_zero_rate():
    """citation_rate=0 with errors should be plain FAIL, not retrieval gap."""
    agent = ValidationAgent(MagicMock())
    status, _ = agent._determine_status(
        0.0, 0.90, 0.10, [], citation_unavailable=False
    )
    assert status == "FAIL"


def test_abs97_validate_detects_stub_retrieval(monkeypatch, tmp_path):
    """Full validate() call with a stub (all-None) client produces PASS_PENDING_INGEST."""
    client = MagicMock()
    client.lookup_citation.return_value = None
    agent = ValidationAgent(client)
    monkeypatch.setattr(
        agent, "_check_zone_completeness", lambda src, tax, cfg: (0.90, [])
    )
    monkeypatch.setattr(
        agent, "_check_pattern_coverage", lambda src, cfg: (0.10, [])
    )

    parser_config = ParserConfig(
        parser_version="docling:test",
        citation_scheme=CitationScheme(
            full_citation_example="x",
            separator=" > ",
            hierarchy=[
                CitationLevel(level="section", pattern=r"^\d+", label_format="{n}")
            ],
        ),
        schedule_patterns=[],
        table_caption_pattern=None,
        confidence=1.0,
        flags=[],
    )
    taxonomy = _make_taxonomy(["ER-1"])
    report = agent.validate(
        _stub_source_doc(tmp_path),
        parser_config,
        taxonomy,
        citation_samples=["Part I > 1", "Part II > 2"],
    )

    assert report.status == "PASS_PENDING_INGEST"
    assert report.citation_resolution_rate == 0.0


def test_abs97_validate_with_real_retrieval_passes(monkeypatch, tmp_path):
    """When retrieval returns data, normal PASS logic applies."""
    client = MagicMock()
    client.lookup_citation.return_value = {"text": "fragment content"}
    agent = ValidationAgent(client)
    monkeypatch.setattr(
        agent, "_check_zone_completeness", lambda src, tax, cfg: (0.90, [])
    )
    monkeypatch.setattr(
        agent, "_check_pattern_coverage", lambda src, cfg: (0.10, [])
    )

    parser_config = ParserConfig(
        parser_version="docling:test",
        citation_scheme=CitationScheme(
            full_citation_example="x",
            separator=" > ",
            hierarchy=[
                CitationLevel(level="section", pattern=r"^\d+", label_format="{n}")
            ],
        ),
        schedule_patterns=[],
        table_caption_pattern=None,
        confidence=1.0,
        flags=[],
    )
    taxonomy = _make_taxonomy(["ER-1"])
    report = agent.validate(
        _stub_source_doc(tmp_path),
        parser_config,
        taxonomy,
        citation_samples=["Part I > 1"],
    )

    assert report.status == "PASS"
    assert report.citation_resolution_rate == 1.0


# ----------------------------------------------------------------------- T7-E
def test_t7e_validate_returns_valid_qa_report(monkeypatch, tmp_path):
    agent = ValidationAgent(MagicMock())
    monkeypatch.setattr(
        agent, "_check_citation_resolution", lambda samples: (0.82, [])
    )
    monkeypatch.setattr(
        agent, "_check_zone_completeness", lambda src, tax, cfg: (0.90, [])
    )
    monkeypatch.setattr(
        agent, "_check_pattern_coverage", lambda src, cfg: (0.10, [])
    )

    parser_config = ParserConfig(
        parser_version="docling:test",
        citation_scheme=CitationScheme(
            full_citation_example="x",
            separator=" > ",
            hierarchy=[
                CitationLevel(level="section", pattern=r"^\d+", label_format="{n}")
            ],
        ),
        schedule_patterns=[],
        table_caption_pattern=None,
        confidence=1.0,
        flags=[],
    )
    taxonomy = _make_taxonomy(["ER-1"])
    report = agent.validate(
        _stub_source_doc(tmp_path),
        parser_config,
        taxonomy,
        citation_samples=["Part I > 1"],
    )

    assert isinstance(report, QAReport)
    assert report.status in (
        "PASS", "PASS_WITH_FLAGS", "PASS_PENDING_INGEST",
        "FAIL", "FAIL_WITH_RETRIEVAL_GAP",
    )
    assert report.citation_resolution_rate == pytest.approx(0.82)
    assert report.zone_completeness == pytest.approx(0.90)
    assert report.pattern_coverage == pytest.approx(0.10)
    # round-trip through Pydantic
    re_parsed = QAReport.model_validate(report.model_dump())
    assert re_parsed.status == report.status


# ------------------------------------------------ extra: citation lookup error
def test_citation_lookup_error_is_flagged():
    client = MagicMock()
    client.lookup_citation.side_effect = [
        {"text": "ok"},
        ValueError("not found"),
        {"text": "also ok"},
    ]
    agent = ValidationAgent(client)

    rate, flags = agent._check_citation_resolution(["a", "b", "c"])
    assert rate == pytest.approx(2 / 3)
    assert len(flags) == 1
    assert flags[0]["code"] == "citation_lookup_error"


# --------------------------------------------------------------------- ABS-91
# Schedule labels like "ZM-1", "ZM-2" must not be flagged as missing zone
# codes. The parser_config knows they're schedules; thread it through and
# subtract before the taxonomy diff.

def _parser_config_with_schedule_patterns(patterns: List[str]) -> ParserConfig:
    return ParserConfig(
        parser_version="docling:test",
        citation_scheme=CitationScheme(
            full_citation_example="1(a)",
            separator=" > ",
            hierarchy=[
                CitationLevel(level="section", pattern=r"^\d+", label_format="{n}")
            ],
        ),
        schedule_patterns=patterns,
        table_caption_pattern=None,
        confidence=1.0,
        flags=[],
    )


def test_abs91_schedule_zm_labels_excluded_from_missing_zones(monkeypatch, tmp_path):
    """ZM-1, ZM-2 occur frequently as zoning-map labels — they must be
    filtered out via parser_config.schedule_patterns rather than flagged."""
    taxonomy_codes = ["R-1", "R-2", "C-1"]
    # Each taxonomy code 10x, ZM-1 and ZM-2 each 20x (schedule references).
    pages = {
        1: " ".join(taxonomy_codes * 10)
        + " "
        + " ".join(["ZM-1"] * 20 + ["ZM-2"] * 20),
    }
    monkeypatch.setattr(
        "agents.validation.PdfBootstrapReader", _stub_reader(pages, (1, 1))
    )

    agent = ValidationAgent(MagicMock())
    source_doc = _stub_source_doc(tmp_path)
    taxonomy = _make_taxonomy(taxonomy_codes)
    parser_config = _parser_config_with_schedule_patterns(
        [r"^Schedule ZM-\d+", r"^ZM-\d+", r"^Schedule [A-Z]"]
    )

    completeness, flags = agent._check_zone_completeness(
        source_doc, taxonomy, parser_config
    )

    assert completeness == pytest.approx(1.0), (
        f"Expected 1.0 once ZM-N labels are excluded, got {completeness}"
    )
    assert flags == [], f"No missing-zone flags should remain, got {flags!r}"


def test_abs91_normal_hyphenated_zones_not_filtered(monkeypatch, tmp_path):
    """A real zone like 'R-1' must not be incorrectly filtered just because
    schedule patterns are configured."""
    pages = {
        1: " ".join(["R-1", "R-2", "ZM-1"] * 10),
    }
    monkeypatch.setattr(
        "agents.validation.PdfBootstrapReader", _stub_reader(pages, (1, 1))
    )

    agent = ValidationAgent(MagicMock())
    source_doc = _stub_source_doc(tmp_path)
    # Only R-1 is in taxonomy; R-2 should still be flagged as missing.
    # ZM-1 should be filtered (matches the schedule pattern).
    taxonomy = _make_taxonomy(["R-1"])
    parser_config = _parser_config_with_schedule_patterns([r"^ZM-\d+"])

    completeness, flags = agent._check_zone_completeness(
        source_doc, taxonomy, parser_config
    )
    flagged = {f["zone_code"] for f in flags}
    assert flagged == {"R-2"}, (
        f"Expected only R-2 flagged as missing, got {flagged}"
    )
    # Two codes in `found` (R-1 in taxonomy, R-2 missing); ZM-1 excluded.
    assert completeness == pytest.approx(0.5)


def test_abs91_no_parser_config_preserves_legacy_behavior(monkeypatch, tmp_path):
    """Backward-compat: when parser_config is None (or not passed), the
    schedule-filter is a no-op and existing behavior is preserved."""
    pages = {1: " ".join(["R-1", "ZM-1"] * 10)}
    monkeypatch.setattr(
        "agents.validation.PdfBootstrapReader", _stub_reader(pages, (1, 1))
    )
    agent = ValidationAgent(MagicMock())
    source_doc = _stub_source_doc(tmp_path)
    taxonomy = _make_taxonomy(["R-1"])

    # No parser_config arg → ZM-1 still gets flagged (legacy behavior).
    completeness, flags = agent._check_zone_completeness(source_doc, taxonomy)
    flagged = {f["zone_code"] for f in flags}
    assert flagged == {"ZM-1"}
    assert completeness == pytest.approx(0.5)


def test_abs91_schedule_code_patterns_helper():
    """The helper strips ^ anchors and Schedule/Appendix prefixes, then
    bi-anchors so the resulting regex matches bare codes."""
    from agents.validation import _schedule_code_patterns

    patterns = _schedule_code_patterns(
        [r"^Schedule ZM-\d+", r"^Appendix [A-Z]", r"^ZM-\d+", r""]
    )
    # Each pattern should match a bare-code instance.
    matchers = [p.pattern for p in patterns]
    assert any("ZM-" in m for m in matchers)

    # Behavioural checks: ZM-1 matches schedule patterns, R-1 doesn't.
    from agents.validation import _matches_any
    assert _matches_any("ZM-1", patterns)
    assert not _matches_any("R-1", patterns)
    # The Appendix [A-Z] pattern should match a single uppercase letter,
    # which the bare-code detection never produces — confirming the
    # helper doesn't accidentally over-match hyphenated codes.
    assert not _matches_any("AB-1", patterns)
