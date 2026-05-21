"""In-flight Pydantic schemas for the submission-ingest pipeline.

The submission pipeline mirrors the bylaw pipeline's split between
parser output (`layer1.models.schemas.ParseResult` + friends) and the
ORM rows that get persisted (`layer2.compliance.db.models.Submission`).
These schemas are the seam between an extractor (IFC, APS, PDF, etc.)
and the persistence layer:

* `ExtractedAttribute` — one attribute value the extractor pulled out of
  the source artifact, in the shape the Phase-1 taxonomy expects.
* `SubmissionExtractionResult` — the full extractor output for one
  source file, before any DB write.
* `SubmissionIngestConfig` — knobs the pipeline reads (skip evaluator,
  taxonomy version pin, evaluator filters).
* `SubmissionIngestResult` — what `ingest_submission` returns to its
  caller (the upload route, a CLI, a test).

Keeping the seam Pydantic-typed means each extractor (ABS-49 IFC,
ABS-50 APS, future PDF / Speckle) is a drop-in: build a
`SubmissionExtractionResult`, hand it back, the pipeline persists.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from layer2.compliance.db.models import (
    SubmissionAttributeSource,
    SubmissionSourceType,
)


class ExtractedAttribute(BaseModel):
    """One attribute value produced by an extractor.

    `attribute_key` must match a Phase-1 taxonomy id; the pipeline
    drops + warns on keys it doesn't recognise rather than persisting
    silently-unmappable rows. `evidence` is free-form provenance the
    extractor records so a reviewer can trace the value back to its
    source (IFC GlobalId, APS property path, PDF page, etc.).
    """

    attribute_key: str
    value: Any
    unit: str | None = None
    confidence: float = 1.0
    source: SubmissionAttributeSource = SubmissionAttributeSource.EXTRACTED
    evidence: dict[str, Any] = Field(default_factory=dict)


class SubmissionExtractionResult(BaseModel):
    """Full extractor output for one source artifact.

    `footprint_geojson` is the optional building-footprint polygon in
    project-local coordinates, recorded here so ABS-51 (derived
    attributes) can compute setbacks / lot coverage downstream without
    re-parsing the source. `raw_metadata` is the extractor's own
    diagnostics — what library version it used, what IFC schema, etc.;
    persisted on `submission.metadata_json` for the audit trail.
    """

    source_type: SubmissionSourceType
    source_artifact_path: str | None = None
    attributes: list[ExtractedAttribute] = Field(default_factory=list)
    footprint_geojson: dict[str, Any] | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SubmissionIngestConfig(BaseModel):
    """Pipeline knobs the caller can flip per ingest.

    `run_evaluator=False` lets tests and the upload-UI "preview"
    button skip the evaluator round-trip. `taxonomy_version` pins
    which taxonomy version's keys are accepted; None means
    "whatever load_taxonomy() returns".
    """

    run_evaluator: bool = True
    taxonomy_version: str | None = None
    # JSON-serialisable evaluator filter set; forwarded into
    # EvaluationRequest.document_filters. Kept loose here so we don't
    # pull DocumentFilters into the layer-1 import graph.
    evaluator_filters: dict[str, Any] | None = None


class SubmissionIngestResult(BaseModel):
    """Return value of `ingest_submission`.

    `evaluator_response` is None when the evaluator was skipped (or
    when no extractor was wired up for the requested source_type but
    the caller still wanted a row created). `warnings` carry extractor
    warnings plus taxonomy-mismatch warnings the pipeline added.
    """

    submission_id: int
    source_type: SubmissionSourceType
    status: str
    n_attributes_persisted: int
    n_attributes_skipped: int
    evaluator_response: dict[str, Any] | None = None
    approval_decision_id: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


__all__ = [
    "ExtractedAttribute",
    "SubmissionExtractionResult",
    "SubmissionIngestConfig",
    "SubmissionIngestResult",
]
