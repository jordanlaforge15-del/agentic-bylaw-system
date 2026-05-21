"""Submission-side ingest pipeline.

Parallel to `layer1.pipeline.ingest.ingest_file` for the bylaw side.
Drives a development submission from a raw source artifact (IFC, RVT,
PDF, etc.) through extraction → taxonomy normalisation → persistence
→ optional evaluation. The actual extractor implementations land in
ABS-49 (IFC) and ABS-50 (APS); this module is the scaffold + seam.

Flow:

1. Validate inputs (source_path exists, source_type is a registered
   extractor — or `extractor=` override is supplied for tests).
2. Insert a `submission` row in DRAFT.
3. Call the extractor → `SubmissionExtractionResult`.
4. Optionally merge in `manual_attributes` (advisor-UI inline values).
5. Normalise attribute keys against the Phase-1 taxonomy; drop +
   warn on unknown keys.
6. Optionally run `derived_attribute_fn` to compute setbacks /
   coverage / FAR from extracted attrs + parcel (ABS-51 hook;
   `None` here, the scaffold persists only EXTRACTED rows).
7. Upsert `submission_attribute` rows.
8. Optionally call the evaluator → persist `approval_decision`.
9. Transition submission status to EVALUATED (when evaluated) or
   leave DRAFT.

The function returns a `SubmissionIngestResult` with the persisted
submission id, attribute counts, evaluator response (if any), and
collected warnings/errors.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionExtractionResult,
    SubmissionIngestConfig,
    SubmissionIngestResult,
)
from layer1.parsers.submission_factory import (
    Extractor,
    extract_submission,
)
from layer2.compliance.db.models import (
    ApprovalDecision,
    Submission,
    SubmissionAttribute,
    SubmissionAttributeSource,
    SubmissionSourceType,
    SubmissionStatus,
)
from layer2.compliance.taxonomy import Taxonomy, load_taxonomy


# ----------------------------------------------------------------------
# Pluggable evaluator + derived-attribute hooks
# ----------------------------------------------------------------------


# Importing EvaluatorService at the top of this module would pull in
# the retrieval stack at scaffold import time (slow, side-effecty for
# tests). Declare a Protocol-shaped callable instead so callers pass
# in whatever object has `.evaluate(EvaluationRequest)` — the real
# EvaluatorService, a test double, a thin lambda wrapper. The pipeline
# only ever reads the method; the import stays loose.
EvaluatorCallable = Callable[[Any], Any]
"""Anything callable as `evaluator(EvaluationRequest) -> EvaluationResponse`.

The real production wiring (ABS-53 web UI) passes
`EvaluatorService.evaluate` bound method. Tests pass a stub function.
"""


DerivedAttributeFn = Callable[
    [Session, Submission, SubmissionExtractionResult],
    list[ExtractedAttribute],
]
"""ABS-51 hook: compute DERIVED attributes from extracted + parcel.

The scaffold ships with this default = None (no derived attrs). ABS-51
will land a real implementation in `layer2.compliance.derived_attributes`
and callers will pass it in via the `derived_attribute_fn=` kwarg.
"""


@dataclass
class _PersistenceCounts:
    """Internal bookkeeping for the result row."""

    persisted: int = 0
    skipped: int = 0


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


def ingest_submission(
    session: Session,
    source_path: Path,
    source_type: SubmissionSourceType,
    *,
    parcel_id: int | None = None,
    submitter_id: int | None = None,
    config: SubmissionIngestConfig | None = None,
    extractor: Extractor | None = None,
    manual_attributes: list[ExtractedAttribute] | None = None,
    derived_attribute_fn: DerivedAttributeFn | None = None,
    evaluator: EvaluatorCallable | None = None,
    build_evaluation_request: Callable[..., Any] | None = None,
    taxonomy: Taxonomy | None = None,
) -> SubmissionIngestResult:
    """Run the full submission-ingest pipeline.

    Most arguments default to "production wiring" but every seam is
    overridable so tests can inject stubs:

    * `extractor=` skips the factory registry (lets a test pass an
      ad-hoc extractor closure without registering it globally).
    * `derived_attribute_fn=` is the ABS-51 plug-in; None = skip.
    * `evaluator=` is the ABS-53/UI wiring; None = skip evaluation.
    * `build_evaluation_request=` lets callers customise how the
      submission's attribute rows map to an `EvaluationRequest`
      (useful when the evaluator filter set changes per call). The
      default (`_default_build_evaluation_request`) is sufficient for
      Phase 2.
    * `taxonomy=` lets tests pass a pinned synthetic taxonomy without
      loading the YAML.

    Returns a `SubmissionIngestResult`. On extractor failure the
    submission row is still committed (status DRAFT) so the UI can
    show the user a row with the error — the evaluator step just
    doesn't run. Re-raises only on unrecoverable DB errors.
    """
    config = config or SubmissionIngestConfig()
    counts = _PersistenceCounts()
    warnings: list[str] = []
    errors: list[str] = []

    # ---- 1. validate inputs ------------------------------------------
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    # ---- 2. create submission row (DRAFT) ----------------------------
    submission = Submission(
        parcel_id=parcel_id,
        submitter_id=submitter_id,
        status=SubmissionStatus.DRAFT,
        source_type=source_type,
        source_artifact_path=str(source_path),
        metadata_json={},
    )
    session.add(submission)
    session.flush()

    # ---- 3. extract --------------------------------------------------
    extraction: SubmissionExtractionResult | None = None
    try:
        if extractor is not None:
            extraction = extractor(source_path, config)
        else:
            extraction = extract_submission(
                source_path, source_type, config=config
            )
    except Exception as exc:  # noqa: BLE001 — surface to caller via result
        errors.append(f"extractor failed: {exc}")

    if extraction is None:
        # We still committed the submission row above; return early
        # so the caller can show "upload accepted, extraction failed".
        return SubmissionIngestResult(
            submission_id=submission.id,
            source_type=source_type,
            status=submission.status.value,
            n_attributes_persisted=0,
            n_attributes_skipped=0,
            warnings=warnings,
            errors=errors,
        )

    warnings.extend(extraction.warnings)
    if extraction.raw_metadata:
        # Preserve provenance on the submission for the audit trail.
        merged = dict(submission.metadata_json or {})
        merged.setdefault("extractor", {}).update(extraction.raw_metadata)
        if extraction.footprint_geojson is not None:
            # ABS-51 reads this back to compute setbacks; stashing on
            # metadata_json (rather than a dedicated column) keeps the
            # Phase-1 schema unchanged.
            merged["footprint_geojson"] = extraction.footprint_geojson
        submission.metadata_json = merged

    # ---- 4. merge manual overrides ----------------------------------
    candidate_attrs: list[ExtractedAttribute] = list(extraction.attributes)
    if manual_attributes:
        # Manual values win over extracted ones for the same key (the
        # advisor user typing a measurement overrides whatever the IFC
        # said). Tag them as MANUAL regardless of what the caller set.
        manual_map = {a.attribute_key: a for a in manual_attributes}
        candidate_attrs = [
            a for a in candidate_attrs if a.attribute_key not in manual_map
        ]
        for a in manual_map.values():
            candidate_attrs.append(
                a.model_copy(update={"source": SubmissionAttributeSource.MANUAL})
            )

    # ---- 5. normalise against taxonomy ------------------------------
    taxonomy = taxonomy or load_taxonomy()
    valid_keys = set(taxonomy.ids)
    if config.taxonomy_version and taxonomy.version != config.taxonomy_version:
        warnings.append(
            f"taxonomy version mismatch: pipeline loaded {taxonomy.version!r}, "
            f"caller asked for {config.taxonomy_version!r}"
        )
    accepted_attrs: list[ExtractedAttribute] = []
    for attr in candidate_attrs:
        if attr.attribute_key not in valid_keys:
            warnings.append(
                f"unknown attribute_key {attr.attribute_key!r} dropped — "
                f"not in taxonomy {taxonomy.version!r}"
            )
            counts.skipped += 1
            continue
        accepted_attrs.append(attr)

    # ---- 6. derived attributes (ABS-51 hook) ------------------------
    if derived_attribute_fn is not None:
        try:
            derived = derived_attribute_fn(session, submission, extraction)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"derived-attribute computation failed: {exc}")
            derived = []
        for attr in derived:
            if attr.attribute_key not in valid_keys:
                warnings.append(
                    f"derived attribute_key {attr.attribute_key!r} dropped — "
                    f"not in taxonomy {taxonomy.version!r}"
                )
                counts.skipped += 1
                continue
            tagged = attr.model_copy(
                update={"source": SubmissionAttributeSource.DERIVED}
            )
            accepted_attrs.append(tagged)

    # ---- 7. persist attribute rows ----------------------------------
    counts.persisted = _persist_attributes(session, submission.id, accepted_attrs)
    session.flush()

    # ---- 8. optional evaluator --------------------------------------
    evaluator_response_json: dict[str, Any] | None = None
    approval_decision_id: int | None = None
    if config.run_evaluator and evaluator is not None:
        try:
            request_builder = (
                build_evaluation_request or _default_build_evaluation_request
            )
            request = request_builder(
                submission=submission,
                attributes=accepted_attrs,
                config=config,
            )
            response = evaluator(request)
            evaluator_response_json = (
                response.to_json() if hasattr(response, "to_json") else dict(response)
            )
            approval_decision_id = getattr(response, "approval_decision_id", None)
            submission.status = SubmissionStatus.EVALUATED
        except Exception as exc:  # noqa: BLE001
            errors.append(f"evaluator failed: {exc}")

    session.flush()
    return SubmissionIngestResult(
        submission_id=submission.id,
        source_type=source_type,
        status=submission.status.value,
        n_attributes_persisted=counts.persisted,
        n_attributes_skipped=counts.skipped,
        evaluator_response=evaluator_response_json,
        approval_decision_id=approval_decision_id,
        warnings=warnings,
        errors=errors,
    )


# ----------------------------------------------------------------------
# Persistence helpers
# ----------------------------------------------------------------------


def _persist_attributes(
    session: Session,
    submission_id: int,
    attrs: list[ExtractedAttribute],
) -> int:
    """Upsert `submission_attribute` rows.

    The (submission_id, attribute_key) unique constraint means
    re-running with the same key updates the existing row in-place;
    that's the right behaviour for a re-run of the same submission
    after the user corrects an attribute. For a freshly-inserted
    submission this is a pure insert.
    """
    if not attrs:
        return 0
    existing = {
        row.attribute_key: row
        for row in session.query(SubmissionAttribute).filter_by(
            submission_id=submission_id
        )
    }
    persisted = 0
    for attr in attrs:
        value_json = _coerce_value_json(attr.value, unit=attr.unit)
        if attr.attribute_key in existing:
            row = existing[attr.attribute_key]
            row.value_json = value_json
            row.confidence = attr.confidence
            row.source = attr.source
            row.evidence_json = attr.evidence or None
        else:
            row = SubmissionAttribute(
                submission_id=submission_id,
                attribute_key=attr.attribute_key,
                value_json=value_json,
                confidence=attr.confidence,
                source=attr.source,
                evidence_json=attr.evidence or None,
            )
            session.add(row)
        persisted += 1
    return persisted


def _coerce_value_json(value: Any, *, unit: str | None) -> dict[str, Any]:
    """Wrap a scalar value into the `value_json` shape.

    The taxonomy stores values as `{value, unit}` dicts so the
    evaluator has the unit on hand without joining back to the
    taxonomy. Existing dict payloads pass through untouched (the
    extractor already shaped them).
    """
    if isinstance(value, dict) and "value" in value:
        return value
    return {"value": value, "unit": unit}


def _default_build_evaluation_request(
    *,
    submission: Submission,
    attributes: list[ExtractedAttribute],
    config: SubmissionIngestConfig,
) -> Any:
    """Build an `EvaluationRequest` from the persisted submission.

    Imports `EvaluationRequest` lazily so the layer-1 pipeline doesn't
    drag the retrieval/evaluator stack at import time. If callers want
    finer control (custom location resolution, taxonomy pinning), they
    pass their own `build_evaluation_request=` callable instead.
    """
    from layer2.compliance.evaluator import (  # local import — see docstring
        DocumentFilters,
        EvaluationRequest,
        SubmissionAttributeInput,
    )

    inputs = [
        SubmissionAttributeInput(
            attribute_key=a.attribute_key,
            value=a.value,
            unit=a.unit,
            source=a.source,
            confidence=a.confidence,
        )
        for a in attributes
    ]
    filters = None
    if config.evaluator_filters:
        filters = DocumentFilters(
            municipality=config.evaluator_filters.get("municipality"),
            bylaw_name=config.evaluator_filters.get("bylaw_name"),
            citation_path_prefix=config.evaluator_filters.get("citation_path_prefix"),
            document_id=config.evaluator_filters.get("document_id"),
        )
    return EvaluationRequest(
        attributes=inputs,
        submission_id=submission.id,
        document_filters=filters,
        taxonomy_version=config.taxonomy_version,
        persist_decision=True,
    )


__all__ = [
    "DerivedAttributeFn",
    "EvaluatorCallable",
    "ingest_submission",
]
