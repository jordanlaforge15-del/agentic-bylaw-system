"""Tests for the ``layer1 learn-city`` CLI command.

All agents and pipeline calls are monkeypatched so these tests run without
network access, real PDFs, or Anthropic API keys.

Coverage targets from ABS-95 acceptance criteria:
  - ``layer1 learn-city --help`` displays the flags
  - A full invocation (mocked pipeline) writes a valid ``CityIntakeManifest``
  - Stub-retrieval warning is printed when ``--retrieval`` is absent
  - Exit code 1 when ``pipeline_ready=False``
  - Mutual-exclusion guard: ``--seed-url`` and ``--direct-pdf`` together
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

# ── Step 1: mock pdfplumber before ANY abs-learning module is imported ──────
# pdfplumber is in the [ingest] optional extras and may not be installed in
# the dev virtualenv.  bootstrap/pdf_reader.py imports it at module level, so
# pipeline → ValidationAgent → PdfBootstrapReader → pdfplumber would fail.
# Inserting a MagicMock into sys.modules lets the import chain succeed so we
# can patch run_learning_pipeline and never exercise the real pdfplumber code.
if "pdfplumber" not in sys.modules:
    sys.modules["pdfplumber"] = MagicMock()

# ── Step 2: ensure abs-learning is on sys.path ───────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    str(_PROJECT_ROOT / "abs-learning"),
    str(_PROJECT_ROOT / "abs-learning" / "src"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Step 3: import abs-learning pipeline so tests can patch it ──────────────
import pipeline  # noqa: E402 — path + pdfplumber mock must precede this

# ── Step 4: import the CLI app ───────────────────────────────────────────────
from layer1.cli import app  # noqa: E402


# --------------------------------------------------------------------------- #
# Shared fixture: a minimal but valid CityIntakeManifest                       #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def mock_manifest():
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

    municipality = Municipality(
        name="Testville",
        jurisdiction_code="TST-CLI",
        province="Nova Scotia",
        governing_body=None,
    )
    source = SourceDocument(
        document_name="Test Bylaw",
        document_type="bylaw",
        document_role="primary",
        parent_document=None,
        source_url="https://example.com/test.pdf",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=10,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )
    parser_config = ParserConfig(
        parser_version="docling:tst",
        citation_scheme=CitationScheme(
            full_citation_example="1 > (a)",
            separator=" > ",
            hierarchy=[
                CitationLevel(
                    level="section",
                    pattern=r"^\d+",
                    label_format="{n}",
                ),
            ],
        ),
        schedule_patterns=[],
        table_caption_pattern=None,
        confidence=0.9,
        flags=[],
    )
    taxonomy = TaxonomyMap(
        zone_designations=[ZoneDesignation(code="ER-1", canonical_type="residential")],
        use_class_map={},
        standards_categories=[],
        companion_bylaws_required=[],
        confidence=0.9,
    )
    qa_report = QAReport(
        status="PASS",
        citation_resolution_rate=0.9,
        zone_completeness=0.9,
        pattern_coverage=0.1,
        flags=[],
        recommended_action="approve",
    )
    return CityIntakeManifest(
        municipality=municipality,
        sources=[source],
        parser_config=parser_config,
        taxonomy=taxonomy,
        qa_report=qa_report,
        manifest_version="0.1.0",
        status="draft",
        pipeline_ready=True,
        flags=[],
    )


# --------------------------------------------------------------------------- #
# Tests                                                                         #
# --------------------------------------------------------------------------- #
runner = CliRunner()


def test_learn_city_help():
    """--help exits 0 and lists all key flags."""
    result = runner.invoke(app, ["learn-city", "--help"])
    assert result.exit_code == 0, result.output
    for flag in (
        "--jurisdiction-code",
        "--name",
        "--province",
        "--output",
        "--direct-pdf",
        "--seed-url",
        "--retrieval",
        "--model",
    ):
        assert flag in result.output, f"Missing flag {flag!r} from --help output"


def test_learn_city_writes_valid_manifest(tmp_path, mock_manifest):
    """End-to-end: CLI writes a valid CityIntakeManifest when pipeline succeeds."""
    output = tmp_path / "manifest.json"

    with patch.object(pipeline, "run_learning_pipeline", return_value=mock_manifest):
        result = runner.invoke(
            app,
            [
                "learn-city",
                "--jurisdiction-code", "TST-CLI",
                "--name", "Testville",
                "--province", "Nova Scotia",
                "--direct-pdf", "https://example.com/test.pdf",
                "--output", str(output),
            ],
        )

    assert result.exit_code == 0, result.output
    assert output.exists(), "Output file was not created"

    from manifest.models import CityIntakeManifest

    loaded = CityIntakeManifest.model_validate_json(output.read_text())
    assert loaded.municipality.jurisdiction_code == "TST-CLI"
    assert loaded.pipeline_ready is True
    assert loaded.qa_report is not None


def test_learn_city_stub_retrieval_warning(tmp_path, mock_manifest):
    """CLI prints a stub-retrieval warning when --retrieval is absent."""
    output = tmp_path / "manifest.json"

    with patch.object(pipeline, "run_learning_pipeline", return_value=mock_manifest):
        result = runner.invoke(
            app,
            [
                "learn-city",
                "--jurisdiction-code", "TST-CLI",
                "--name", "Testville",
                "--province", "Nova Scotia",
                "--direct-pdf", "https://example.com/test.pdf",
                "--output", str(output),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "stub client" in result.output.lower() or "0.0" in result.output


def test_learn_city_exit_1_when_not_ready(tmp_path):
    """CLI exits with code 1 when pipeline_ready=False (manifest still written)."""
    from manifest.models import (
        CityIntakeManifest,
        Municipality,
        SourceDocument,
        QAReport,
    )

    not_ready_manifest = CityIntakeManifest(
        municipality=Municipality(
            name="Testville",
            jurisdiction_code="TST-CLI",
            province="Nova Scotia",
            governing_body=None,
        ),
        sources=[
            SourceDocument(
                document_name="Test Bylaw",
                document_type="bylaw",
                document_role="primary",
                parent_document=None,
                source_url="https://example.com/test.pdf",
                format="pdf",
                access_method="direct_download",
                page_count_estimate=None,
                last_amended=None,
                amendment_series=None,
                in_scope=True,
            )
        ],
        parser_config=None,
        taxonomy=None,
        qa_report=QAReport(
            status="FAIL",
            citation_resolution_rate=0.0,
            zone_completeness=0.0,
            pattern_coverage=0.0,
            flags=[],
            recommended_action="reject_and_retry",
        ),
        manifest_version="0.1.0",
        status="draft",
        pipeline_ready=False,
        flags=["structure_analyst_failed: RuntimeError: something broke"],
    )
    output = tmp_path / "manifest.json"

    with patch.object(pipeline, "run_learning_pipeline", return_value=not_ready_manifest):
        result = runner.invoke(
            app,
            [
                "learn-city",
                "--jurisdiction-code", "TST-CLI",
                "--name", "Testville",
                "--province", "Nova Scotia",
                "--direct-pdf", "https://example.com/test.pdf",
                "--output", str(output),
            ],
        )

    assert result.exit_code == 1
    # Manifest is written even when pipeline_ready=False
    assert output.exists()


def test_learn_city_mutual_exclusion_error():
    """--seed-url and --direct-pdf together are rejected."""
    result = runner.invoke(
        app,
        [
            "learn-city",
            "--jurisdiction-code", "TST",
            "--name", "Testville",
            "--province", "Nova Scotia",
            "--seed-url", "https://example.com",
            "--direct-pdf", "https://example.com/test.pdf",
            "--output", "/tmp/out.json",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_learn_city_no_source_error():
    """Omitting both --seed-url and --direct-pdf is rejected."""
    result = runner.invoke(
        app,
        [
            "learn-city",
            "--jurisdiction-code", "TST",
            "--name", "Testville",
            "--province", "Nova Scotia",
            "--output", "/tmp/out.json",
        ],
    )
    assert result.exit_code != 0


def test_learn_city_invalid_retrieval_format():
    """--retrieval with wrong format is rejected."""
    result = runner.invoke(
        app,
        [
            "learn-city",
            "--jurisdiction-code", "TST",
            "--name", "Testville",
            "--province", "Nova Scotia",
            "--direct-pdf", "https://example.com/test.pdf",
            "--output", "/tmp/out.json",
            "--retrieval", "bad-format",
        ],
    )
    assert result.exit_code != 0


def test_learn_city_passes_model_to_pipeline(tmp_path, mock_manifest):
    """--model override is forwarded to run_learning_pipeline as the model kwarg."""
    output = tmp_path / "manifest.json"
    captured: dict = {}

    def _capture(*args, model="claude-sonnet-4-6", **kwargs):
        captured["model"] = model
        return mock_manifest

    with patch.object(pipeline, "run_learning_pipeline", side_effect=_capture):
        result = runner.invoke(
            app,
            [
                "learn-city",
                "--jurisdiction-code", "TST-CLI",
                "--name", "Testville",
                "--province", "Nova Scotia",
                "--direct-pdf", "https://example.com/test.pdf",
                "--output", str(output),
                "--model", "claude-opus-4-7",
            ],
        )

    assert result.exit_code == 0, result.output
    assert captured.get("model") == "claude-opus-4-7"


def test_learn_city_pass_pending_ingest_exits_0(tmp_path):
    """PASS_PENDING_INGEST → pipeline_ready=True → exit 0 with advice."""
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

    pending_manifest = CityIntakeManifest(
        municipality=Municipality(
            name="Testville",
            jurisdiction_code="TST-CLI",
            province="Nova Scotia",
            governing_body=None,
        ),
        sources=[
            SourceDocument(
                document_name="Test Bylaw",
                document_type="bylaw",
                document_role="primary",
                parent_document=None,
                source_url="https://example.com/test.pdf",
                format="pdf",
                access_method="direct_download",
                page_count_estimate=None,
                last_amended=None,
                amendment_series=None,
                in_scope=True,
            )
        ],
        parser_config=ParserConfig(
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
        ),
        taxonomy=TaxonomyMap(
            zone_designations=[ZoneDesignation(code="ER-1", canonical_type="residential")],
            use_class_map={},
            standards_categories=[],
            companion_bylaws_required=[],
            confidence=0.9,
        ),
        qa_report=QAReport(
            status="PASS_PENDING_INGEST",
            citation_resolution_rate=0.0,
            zone_completeness=0.9,
            pattern_coverage=0.1,
            flags=[],
            recommended_action="approve_pending_ingest",
        ),
        manifest_version="0.1.0",
        status="draft",
        pipeline_ready=True,
        flags=[],
    )
    output = tmp_path / "manifest.json"

    with patch.object(pipeline, "run_learning_pipeline", return_value=pending_manifest):
        result = runner.invoke(
            app,
            [
                "learn-city",
                "--jurisdiction-code", "TST-CLI",
                "--name", "Testville",
                "--province", "Nova Scotia",
                "--direct-pdf", "https://example.com/test.pdf",
                "--output", str(output),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "pending ingest" in result.output.lower() or "validate-manifest" in result.output


def test_learn_city_citation_samples_loaded(tmp_path, mock_manifest):
    """--citation-samples JSON file is parsed and forwarded to the pipeline."""
    output = tmp_path / "manifest.json"
    samples_file = tmp_path / "samples.json"
    samples_file.write_text(json.dumps(["Part 4 > 4.1", "Schedule C"]))
    captured: dict = {}

    def _capture(*args, citation_samples=None, **kwargs):
        captured["citation_samples"] = citation_samples
        return mock_manifest

    with patch.object(pipeline, "run_learning_pipeline", side_effect=_capture):
        result = runner.invoke(
            app,
            [
                "learn-city",
                "--jurisdiction-code", "TST-CLI",
                "--name", "Testville",
                "--province", "Nova Scotia",
                "--direct-pdf", "https://example.com/test.pdf",
                "--output", str(output),
                "--citation-samples", str(samples_file),
            ],
        )

    assert result.exit_code == 0, result.output
    assert captured.get("citation_samples") == ["Part 4 > 4.1", "Schedule C"]


# --------------------------------------------------------------------------- #
# validate-manifest command tests                                               #
# --------------------------------------------------------------------------- #

def test_validate_manifest_help():
    """--help lists required flags."""
    result = runner.invoke(app, ["validate-manifest", "--help"])
    assert result.exit_code == 0, result.output
    assert "--retrieval" in result.output


def test_validate_manifest_updates_status(tmp_path, mock_manifest):
    """validate-manifest rewrites the manifest with a fresh QAReport."""
    from manifest.models import CityIntakeManifest, QAReport

    manifest_path = tmp_path / "manifest.json"
    # Write a PASS_PENDING_INGEST manifest
    mock_manifest.qa_report.status = "PASS_PENDING_INGEST"
    mock_manifest.qa_report.citation_resolution_rate = 0.0
    mock_manifest.qa_report.recommended_action = "approve_pending_ingest"
    manifest_path.write_text(
        mock_manifest.model_dump_json(indent=2), encoding="utf-8"
    )

    # Mock the ValidationAgent to return a PASS report
    from agents.validation import ValidationAgent as _RealVA

    pass_report = QAReport(
        status="PASS",
        citation_resolution_rate=0.85,
        zone_completeness=0.9,
        pattern_coverage=0.1,
        flags=[],
        recommended_action="approve",
    )

    with patch.object(_RealVA, "validate", return_value=pass_report):
        result = runner.invoke(
            app,
            [
                "validate-manifest",
                str(manifest_path),
                "--retrieval", "document-id=1",
            ],
        )

    assert result.exit_code == 0, result.output

    updated = CityIntakeManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    assert updated.qa_report.status == "PASS"
    assert updated.qa_report.citation_resolution_rate == pytest.approx(0.85)
    assert updated.pipeline_ready is True


def test_validate_manifest_invalid_retrieval_format(tmp_path, mock_manifest):
    """--retrieval with wrong format is rejected."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        mock_manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    result = runner.invoke(
        app,
        [
            "validate-manifest",
            str(manifest_path),
            "--retrieval", "bad-format",
        ],
    )
    assert result.exit_code != 0
