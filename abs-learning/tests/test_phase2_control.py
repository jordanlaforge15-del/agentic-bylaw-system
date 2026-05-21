"""End-to-end Phase 2 integration tests against the Halifax bylaw.

These tests exercise the full Phase 2 pipeline (Structure Analyst,
Semantic Mapper, Validation Agent) against the real Halifax Regional
Centre Land Use By-Law PDF and the Halifax document fragments stored in
the local Postgres retrieval DB.

The full pipeline is executed exactly ONCE per pytest session via the
``halifax_full_run`` session-scoped fixture; the five T9-* tests then
read components off the resulting :class:`CityIntakeManifest`. This
keeps the suite well under the ≤20 Anthropic-call budget for a full
city run.

All tests are gated behind ``-m integration`` and the budgeted
``anthropic_client`` fixture in ``conftest.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from manifest.models import (
    CityIntakeManifest,
    Municipality,
    SourceDocument,
)

GROUND_TRUTH_PATH = (
    Path(__file__).parent.parent / "fixtures" / "halifax_ground_truth.json"
)
HALIFAX_PDF_PATH = (
    "/Users/christopherrafuse/dev/agentic-bylaw-system/"
    "regionalcentrelub-eff-26april13-case24469toclinked.pdf"
)
HALIFAX_DOCUMENT_ID = 4


# ------------------------------------------------------------------- helpers
def make_halifax_source_doc(pdf_path: str) -> SourceDocument:
    return SourceDocument(
        document_name="Regional Centre Land Use By-Law",
        document_type="bylaw",
        document_role="Primary land-use bylaw",
        parent_document=None,
        source_url=f"file://{pdf_path}",
        format="pdf",
        access_method="direct_download",
        page_count_estimate=457,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )


def make_halifax_municipality() -> Municipality:
    return Municipality(
        name="Halifax Regional Municipality",
        jurisdiction_code="HRM",
        province="Nova Scotia",
        governing_body="Halifax Regional Council",
    )


class HalifaxRetrievalClient:
    """Thin adapter over :class:`bylaw_retrieval.retrieval.RetrievalService`.

    Exposes the single-string ``lookup_citation(citation_path)`` shape
    expected by :class:`ValidationAgent`. Returns ``None`` on miss or
    error rather than raising — Validation Agent treats both as
    resolution failures.
    """

    def __init__(self, document_id: int = HALIFAX_DOCUMENT_ID) -> None:
        self.document_id = document_id

    def lookup_citation(self, citation_path: str) -> Optional[dict]:
        from bylaw_retrieval.retrieval import (
            CitationLookupRequest,
            RetrievalService,
        )
        from layer1.db.session import session_scope

        try:
            with session_scope() as session:
                service = RetrievalService(session)
                match = service.lookup_citation(
                    CitationLookupRequest(
                        citation_path=citation_path,
                        document_id=self.document_id,
                    )
                )
                return match.model_dump(mode="json")
        except ValueError:
            return None


# ------------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def ground_truth() -> dict:
    return json.loads(GROUND_TRUTH_PATH.read_text())


@pytest.fixture(scope="session")
def halifax_pdf_path() -> str:
    if not Path(HALIFAX_PDF_PATH).exists():
        pytest.skip(f"Halifax PDF not found at {HALIFAX_PDF_PATH}")
    return HALIFAX_PDF_PATH


@pytest.fixture(scope="session")
def halifax_source_doc(halifax_pdf_path) -> SourceDocument:
    return make_halifax_source_doc(halifax_pdf_path)


@pytest.fixture(scope="session")
def retrieval_client():
    """Real DB-backed retrieval client; skips the suite if the DB or
    parent-repo packages are unavailable.
    """
    try:
        from bylaw_retrieval.retrieval import (  # noqa: F401
            CitationLookupRequest,
            RetrievalService,
        )
        from layer1.db.session import session_scope  # noqa: F401
    except ImportError as exc:
        pytest.skip(
            f"Parent-repo retrieval modules unavailable: {exc}. "
            "Phase 2 integration tests require the agentic-bylaw-system "
            "source tree on sys.path and a populated Halifax DB."
        )
    client = HalifaxRetrievalClient()
    try:
        probe = client.lookup_citation("Part I > 1")
    except Exception as exc:
        pytest.skip(f"Halifax retrieval DB unavailable: {exc}")
    if probe is None:
        pytest.skip(
            "Halifax retrieval DB does not contain 'Part I > 1' "
            "for document_id=4 — please reseed before running these tests."
        )
    return client


@pytest.fixture(scope="session")
def halifax_full_run(
    halifax_source_doc,
    ground_truth,
    anthropic_client,
    retrieval_client,
) -> CityIntakeManifest:
    """Run the full Phase 2 pipeline ONCE for the session.

    Spends one full-pipeline budget (~20 Anthropic calls). All five
    T9-* tests read components off this manifest.
    """
    from pipeline import run_learning_pipeline

    citation_samples = [s["citation_path"] for s in ground_truth["citation_samples"]]
    manifest = run_learning_pipeline(
        municipality=make_halifax_municipality(),
        sources=[halifax_source_doc],
        citation_samples=citation_samples,
        anthropic_client=anthropic_client,
        retrieval_client=retrieval_client,
    )
    return manifest


# ----------------------------------------------------------------------- T9-A
@pytest.mark.integration
def test_t9a_semantic_mapper_zone_coverage(halifax_full_run, ground_truth):
    taxonomy = halifax_full_run.taxonomy
    assert taxonomy is not None, (
        "Semantic Mapper did not produce a taxonomy. "
        f"Manifest flags: {halifax_full_run.flags}"
    )

    found_codes = {z.code for z in taxonomy.zone_designations}
    required = {"ER-1", "UC-1", "CEN-1", "DD", "INS", "CLI", "CH-1", "HR-1"}
    missing = required - found_codes
    assert not missing, (
        f"Semantic Mapper missed required zone codes: {sorted(missing)}. "
        f"Found: {sorted(found_codes)}"
    )

    unknowns = [
        z for z in taxonomy.zone_designations if z.canonical_type == "unknown"
    ]
    assert len(unknowns) <= 3, (
        f"Too many unknown zone types: {[z.code for z in unknowns]}"
    )


# ----------------------------------------------------------------------- T9-B
@pytest.mark.integration
def test_t9b_semantic_mapper_use_classes(halifax_full_run, ground_truth):
    taxonomy = halifax_full_run.taxonomy
    assert taxonomy is not None
    required_uses = ground_truth["known_use_classes"]
    found_uses = set(taxonomy.use_class_map.keys())
    matched = [u for u in required_uses if u in found_uses]
    assert len(matched) >= 4, (
        f"Only {len(matched)}/{len(required_uses)} known use classes found. "
        f"Missing: {[u for u in required_uses if u not in found_uses]}. "
        f"Found keys: {sorted(found_uses)[:20]}"
    )


# ----------------------------------------------------------------------- T9-C
@pytest.mark.integration
def test_t9c_semantic_mapper_standards(halifax_full_run, ground_truth):
    taxonomy = halifax_full_run.taxonomy
    assert taxonomy is not None
    required = set(ground_truth["known_standards_categories"])
    found = set(taxonomy.standards_categories)
    missing = required - found
    assert not missing, (
        f"Missing standards categories: {sorted(missing)}. "
        f"Found: {sorted(found)}"
    )


# ----------------------------------------------------------------------- T9-D
@pytest.mark.integration
def test_t9d_validation_agent_on_halifax(halifax_full_run):
    qa = halifax_full_run.qa_report
    assert qa is not None, (
        "Validation Agent did not produce a qa_report. "
        f"Manifest flags: {halifax_full_run.flags}"
    )
    assert qa.citation_resolution_rate >= 0.80, (
        f"Citation resolution too low: {qa.citation_resolution_rate:.0%}"
    )
    assert qa.zone_completeness >= 0.80, (
        f"Zone completeness too low: {qa.zone_completeness:.0%}"
    )
    assert qa.status in ("PASS", "PASS_WITH_FLAGS"), (
        f"QA failed outright: {qa.status}\nFlags: {qa.flags}"
    )


# ----------------------------------------------------------------------- T9-E
@pytest.mark.integration
def test_t9e_full_pipeline_produces_valid_manifest(halifax_full_run, ground_truth):
    manifest = halifax_full_run
    assert manifest.municipality.jurisdiction_code == "HRM"
    assert manifest.parser_config is not None, (
        f"Missing parser_config; flags: {manifest.flags}"
    )
    assert manifest.taxonomy is not None, (
        f"Missing taxonomy; flags: {manifest.flags}"
    )
    assert manifest.qa_report is not None, (
        f"Missing qa_report; flags: {manifest.flags}"
    )
    assert manifest.status == "draft"
    assert isinstance(manifest.pipeline_ready, bool)

    re_parsed = CityIntakeManifest.model_validate_json(manifest.model_dump_json())
    assert re_parsed.municipality.jurisdiction_code == "HRM"
    assert re_parsed.qa_report is not None
