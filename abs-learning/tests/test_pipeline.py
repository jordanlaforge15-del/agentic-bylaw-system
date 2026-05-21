"""Unit tests for the Phase 2 pipeline orchestrator.

All three agents are monkeypatched at the ``pipeline`` module level so the
tests don't touch the network, PDFs, or Anthropic.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

import pipeline
from manifest.models import (
    CitationLevel,
    CitationScheme,
    CityIntakeManifest,
    Municipality,
    ParserConfig,
    QAReport,
    SourceDocument,
    TaxonomyMap,
    ZoneDesignation,
)


# ---------------------------------------------------------------- helpers
def _municipality() -> Municipality:
    return Municipality(
        name="Testville",
        jurisdiction_code="TST",
        province="Nova Scotia",
        governing_body="Test Council",
    )


def _source() -> SourceDocument:
    return SourceDocument(
        document_name="Test Bylaw",
        document_type="bylaw",
        document_role="primary",
        parent_document=None,
        source_url="file:///tmp/test.pdf",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=10,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )


def _parser_config() -> ParserConfig:
    return ParserConfig(
        parser_version="docling:tst",
        citation_scheme=CitationScheme(
            full_citation_example="1 > (a)",
            separator=" > ",
            hierarchy=[
                CitationLevel(level="section", pattern=r"^\d+", label_format="{n}"),
            ],
        ),
        schedule_patterns=[],
        table_caption_pattern=None,
        confidence=0.9,
        flags=[],
    )


def _taxonomy() -> TaxonomyMap:
    return TaxonomyMap(
        zone_designations=[
            ZoneDesignation(code="ER-1", canonical_type="residential"),
        ],
        use_class_map={},
        standards_categories=[],
        companion_bylaws_required=[],
        confidence=0.9,
    )


def _qa_report(status: str = "PASS") -> QAReport:
    action = {
        "PASS": "approve",
        "PASS_WITH_FLAGS": "approve_with_review",
        "FAIL": "reject_and_retry",
    }[status]
    return QAReport(
        status=status,
        citation_resolution_rate=0.9,
        zone_completeness=0.9,
        pattern_coverage=0.1,
        flags=[],
        recommended_action=action,
    )


def _fake_agent_class(*, returns: Any = None, raises: Exception | None = None, method: str = "analyse"):
    class _Fake:
        def __init__(self, *args, **kwargs):
            pass

    def _call(self, *args, **kwargs):
        if raises is not None:
            raise raises
        return returns

    setattr(_Fake, method, _call)
    return _Fake


def _patch_all_agents(
    monkeypatch,
    parser_config=None,
    taxonomy=None,
    qa_report=None,
    structure_raise=None,
    semantic_raise=None,
    validation_raise=None,
):
    monkeypatch.setattr(
        pipeline,
        "StructureAnalystAgent",
        _fake_agent_class(returns=parser_config, raises=structure_raise, method="analyse"),
    )
    monkeypatch.setattr(
        pipeline,
        "SemanticMapperAgent",
        _fake_agent_class(returns=taxonomy, raises=semantic_raise, method="map"),
    )
    monkeypatch.setattr(
        pipeline,
        "ValidationAgent",
        _fake_agent_class(returns=qa_report, raises=validation_raise, method="validate"),
    )


# ----------------------------------------------------------------------- T8-A
def test_t8a_happy_path_returns_complete_manifest(monkeypatch):
    _patch_all_agents(
        monkeypatch,
        parser_config=_parser_config(),
        taxonomy=_taxonomy(),
        qa_report=_qa_report("PASS"),
    )

    manifest = pipeline.run_learning_pipeline(
        municipality=_municipality(),
        sources=[_source()],
        citation_samples=["Part I > 1"],
        anthropic_client=MagicMock(),
        retrieval_client=MagicMock(),
    )

    assert manifest.parser_config is not None
    assert manifest.taxonomy is not None
    assert manifest.qa_report is not None
    assert manifest.status == "draft"


# ----------------------------------------------------------------------- T8-B
def test_t8b_pipeline_ready_false_on_pass_with_flags(monkeypatch):
    _patch_all_agents(
        monkeypatch,
        parser_config=_parser_config(),
        taxonomy=_taxonomy(),
        qa_report=_qa_report("PASS_WITH_FLAGS"),
    )

    manifest = pipeline.run_learning_pipeline(
        municipality=_municipality(),
        sources=[_source()],
        citation_samples=["Part I > 1"],
        anthropic_client=MagicMock(),
        retrieval_client=MagicMock(),
    )
    assert manifest.pipeline_ready is False


# ----------------------------------------------------------------------- T8-C
def test_t8c_pipeline_ready_true_only_on_pass(monkeypatch):
    _patch_all_agents(
        monkeypatch,
        parser_config=_parser_config(),
        taxonomy=_taxonomy(),
        qa_report=_qa_report("PASS"),
    )

    manifest = pipeline.run_learning_pipeline(
        municipality=_municipality(),
        sources=[_source()],
        citation_samples=["Part I > 1"],
        anthropic_client=MagicMock(),
        retrieval_client=MagicMock(),
    )
    assert manifest.pipeline_ready is True


# ----------------------------------------------------------------------- T8-D
def test_t8d_structure_analyst_failure_is_graceful(monkeypatch):
    _patch_all_agents(
        monkeypatch,
        parser_config=None,
        taxonomy=_taxonomy(),
        qa_report=None,
        structure_raise=RuntimeError("simulated structure failure"),
    )

    manifest = pipeline.run_learning_pipeline(
        municipality=_municipality(),
        sources=[_source()],
        citation_samples=["Part I > 1"],
        anthropic_client=MagicMock(),
        retrieval_client=MagicMock(),
    )

    assert manifest.parser_config is None
    assert manifest.pipeline_ready is False
    # Manifest-level flag was set
    assert any("structure_analyst_failed" in f for f in manifest.flags)


# ----------------------------------------------------------------------- T8-E
def test_t8e_manifest_round_trips_through_pydantic(monkeypatch):
    _patch_all_agents(
        monkeypatch,
        parser_config=_parser_config(),
        taxonomy=_taxonomy(),
        qa_report=_qa_report("PASS"),
    )

    manifest = pipeline.run_learning_pipeline(
        municipality=_municipality(),
        sources=[_source()],
        citation_samples=["Part I > 1"],
        anthropic_client=MagicMock(),
        retrieval_client=MagicMock(),
    )

    re_parsed = CityIntakeManifest.model_validate(manifest.model_dump())
    assert re_parsed.municipality.jurisdiction_code == "TST"
    assert re_parsed.pipeline_ready == manifest.pipeline_ready
    assert re_parsed.status == manifest.status


# ----------------------------------------------------------- extra coverage
def test_no_primary_source_raises():
    with pytest.raises(ValueError, match="No primary in-scope bylaw"):
        pipeline.run_learning_pipeline(
            municipality=_municipality(),
            sources=[],
            citation_samples=[],
            anthropic_client=MagicMock(),
            retrieval_client=MagicMock(),
        )


def test_taxonomy_failure_skips_validation(monkeypatch):
    _patch_all_agents(
        monkeypatch,
        parser_config=_parser_config(),
        taxonomy=None,
        qa_report=None,
        semantic_raise=RuntimeError("oh no"),
    )
    manifest = pipeline.run_learning_pipeline(
        municipality=_municipality(),
        sources=[_source()],
        citation_samples=["a"],
        anthropic_client=MagicMock(),
        retrieval_client=MagicMock(),
    )
    assert manifest.taxonomy is None
    assert manifest.qa_report is None
    assert manifest.pipeline_ready is False
    assert any("semantic_mapper_failed" in f for f in manifest.flags)
