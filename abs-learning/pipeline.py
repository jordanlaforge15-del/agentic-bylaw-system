"""City Learning Pipeline orchestrator.

Sequences :class:`StructureAnalystAgent`, :class:`SemanticMapperAgent`, and
:class:`ValidationAgent` for a single municipality and assembles a draft
:class:`CityIntakeManifest`. Agent failures are captured as manifest-level
flags rather than allowed to propagate.

This is a learning-time function only — it does NOT trigger ingestion or
write to any database.
"""
from __future__ import annotations

from typing import List, Optional

from agents.semantic_mapper import SemanticMapperAgent
from agents.structure_analyst import StructureAnalystAgent
from agents.validation import ValidationAgent
from manifest.models import (
    CityIntakeManifest,
    Municipality,
    SourceDocument,
)


CITATION_RESOLUTION_THRESHOLD = 0.80
ZONE_COMPLETENESS_THRESHOLD = 0.85


def _find_primary_source(sources: List[SourceDocument]) -> Optional[SourceDocument]:
    for source in sources:
        if (
            source.document_type == "bylaw"
            and source.parent_document is None
            and source.in_scope
        ):
            return source
    return None


def run_learning_pipeline(
    municipality: Municipality,
    sources: List[SourceDocument],
    citation_samples: List[str],
    anthropic_client,
    retrieval_client,
    manifest_version: str = "0.1.0",
) -> CityIntakeManifest:
    """Run StructureAnalyst → SemanticMapper → Validation for one city.

    Returns a draft :class:`CityIntakeManifest`. Sets ``pipeline_ready``
    to ``True`` only when the validation status is ``"PASS"``. Catches
    per-agent exceptions and records them as manifest-level flags.
    """
    primary = _find_primary_source(sources)
    if primary is None:
        raise ValueError(
            "No primary in-scope bylaw found "
            "(document_type='bylaw', parent_document=None, in_scope=True)"
        )

    flags: List[str] = []
    parser_config = None
    taxonomy = None
    qa_report = None

    structure_agent = StructureAnalystAgent(anthropic_client)
    try:
        parser_config = structure_agent.analyse(
            primary, jurisdiction_code=municipality.jurisdiction_code
        )
    except Exception as exc:  # noqa: BLE001 — pipeline-level capture
        flags.append(f"structure_analyst_failed: {type(exc).__name__}: {exc}")

    semantic_agent = SemanticMapperAgent(anthropic_client)
    try:
        taxonomy = semantic_agent.map(primary)
    except Exception as exc:  # noqa: BLE001
        flags.append(f"semantic_mapper_failed: {type(exc).__name__}: {exc}")

    if parser_config is not None and taxonomy is not None:
        validation_agent = ValidationAgent(retrieval_client)
        try:
            qa_report = validation_agent.validate(
                primary, parser_config, taxonomy, citation_samples
            )
        except Exception as exc:  # noqa: BLE001
            flags.append(f"validation_failed: {type(exc).__name__}: {exc}")
    else:
        flags.append(
            "validation_skipped: parser_config or taxonomy unavailable"
        )

    pipeline_ready = qa_report is not None and qa_report.status == "PASS"

    return CityIntakeManifest(
        municipality=municipality,
        sources=sources,
        parser_config=parser_config,
        taxonomy=taxonomy,
        qa_report=qa_report,
        manifest_version=manifest_version,
        status="draft",
        pipeline_ready=pipeline_ready,
        flags=flags,
    )
