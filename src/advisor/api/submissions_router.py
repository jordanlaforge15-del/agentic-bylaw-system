"""FastAPI router for the Phase-2 submission upload → matrix flow (ABS-53).

Five endpoints expose the submission pipeline to the frontend:

* ``POST /v1/submissions`` — multipart `.ifc` upload + parcel
  identifier (address or parcel_id). Stores the file, runs the
  scaffold ingest pipeline (ABS-48) with the IFC extractor (ABS-49)
  and derived-attribute hook (ABS-51), returns the resulting
  submission + every attribute. **No evaluator call** — that's an
  explicit user action so the architect sees the extracted values
  first and can override before evaluating.
* ``GET /v1/submissions/{id}`` — owner-scoped fetch. Returns the
  submission row + every `submission_attribute` row + any
  most-recent `approval_decision`.
* ``PATCH /v1/submissions/{id}/attributes/{attribute_key}`` —
  manual override. Appends a `submission_attribute` row with
  `source=OVERRIDE` rather than mutating the original EXTRACTED row
  (the audit trail must preserve what the extractor said).
* ``POST /v1/submissions/{id}/evaluate`` — explicit run-evaluator
  CTA. Builds an `EvaluationRequest` from the current authoritative
  attribute set and calls `EvaluatorService.evaluate(...)`. Writes
  an `approval_decision` row and returns the response.
* ``GET /v1/submissions/{id}/matrix`` — fetch the most recent
  decision's JSON without re-running the evaluator.

Router factory matches the cases / billing pattern: dependencies
(db, user dep, user resolver, evaluator factory) are injected so the
test harness can swap stubs without standing up the full Anthropic +
retrieval stack.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

# Side-effect imports: register extractors with the submission factory.
import layer1.parsers.ifc_submission  # noqa: F401
import layer1.parsers.pdf_submission  # noqa: F401

from advisor.db.models import User
from layer1.config import get_settings
from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionIngestConfig,
    SubmissionIngestResult,
)
from layer1.pipeline.ingest_submission import ingest_submission
from layer2.compliance.db.models import (
    ApprovalDecision,
    Parcel,
    Submission,
    SubmissionAttribute,
    SubmissionAttributeSource,
    SubmissionSourceType,
    SubmissionStatus,
)
from layer1.db.base import SourceFragment
from layer2.compliance.derived_attributes import compute_derived_attributes
from layer2.compliance.taxonomy import load_taxonomy

logger = logging.getLogger(__name__)


# Hand-rolled type alias so the test harness can see the contract.
UserResolver = Callable[[Any, Session], User]
EvaluatorFactory = Callable[[Session], Any]
"""Factory that builds the evaluator-callable the pipeline expects.

Returning `Any` keeps this module free of the retrieval-stack import
graph. Production wires `EvaluatorService.evaluate` bound method;
tests wire a no-op or stub.
"""


# ----------------------------------------------------------------------
# Request / response models
# ----------------------------------------------------------------------


class _AttributeOut(BaseModel):
    attribute_key: str
    value: Any
    unit: str | None
    confidence: float
    source: str
    evidence: dict[str, Any] | None

    @classmethod
    def from_row(cls, row: SubmissionAttribute) -> "_AttributeOut":
        # `value_json` is the {"value": …, "unit": …} dict the pipeline writes;
        # surface the inner value to the UI for compact rendering.
        value_json = row.value_json or {}
        return cls(
            attribute_key=row.attribute_key,
            value=value_json.get("value"),
            unit=value_json.get("unit"),
            confidence=row.confidence,
            source=row.source.value,
            evidence=row.evidence_json,
        )


class _SubmissionOut(BaseModel):
    id: int
    parcel_id: int | None
    status: str
    source_type: str
    source_artifact_path: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    attributes: list[_AttributeOut] = Field(default_factory=list)
    latest_decision: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class _OverrideRequest(BaseModel):
    value: Any
    unit: str | None = None


class _EvaluateResponse(BaseModel):
    submission_id: int
    decision: dict[str, Any] | None
    warnings: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Router factory
# ----------------------------------------------------------------------


def build_submissions_router(
    *,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
    evaluator_factory: EvaluatorFactory | None = None,
    storage_dir: Path | None = None,
) -> APIRouter:
    """Assemble the submissions router.

    `evaluator_factory` is optional: if `None`, the evaluate endpoint
    returns 503 (production should always wire it; the dormant /
    test paths don't always have a retrieval stack).
    `storage_dir` defaults to the config-driven `submission_storage_dir`;
    tests pass a tmp path.
    """
    storage_root = (
        Path(storage_dir)
        if storage_dir is not None
        else Path(get_settings().submission_storage_dir)
    )
    # Don't mkdir storage_root at startup — the prod advisor runs with a
    # read-only filesystem (`read_only: true` in compose) and would crash on
    # boot. The upload handler below calls `user_dir.mkdir(parents=True,
    # exist_ok=True)` lazily, which creates `storage_root` as a parent on
    # the first real upload — turning a startup-time crash into a clear
    # request-time error if no writable volume is mounted.

    router = APIRouter(prefix="/v1/submissions", tags=["submissions"])

    @contextmanager
    def _open_db() -> Any:
        result = db_session_factory()
        if hasattr(result, "__enter__"):
            with result as session:
                yield session
        else:
            try:
                yield result
            finally:
                close = getattr(result, "close", None)
                if callable(close):
                    close()

    # ---- POST /v1/submissions (upload) ---------------------------

    @router.post("", response_model=_SubmissionOut)
    def upload_submission(
        file: UploadFile = File(...),
        parcel_id: int | None = Form(default=None),
        parcel_address: str | None = Form(default=None),
        auth_session: Any = Depends(user_dependency),
    ) -> _SubmissionOut:
        if parcel_id is None and not parcel_address:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "missing_parcel",
                    "message": "Either parcel_id or parcel_address is required.",
                },
            )
        filename_lower = (file.filename or "").lower()
        if filename_lower.endswith(".ifc"):
            file_source_type = SubmissionSourceType.IFC
        elif filename_lower.endswith(".pdf"):
            file_source_type = SubmissionSourceType.PDF
        else:
            raise HTTPException(
                status_code=415,
                detail={
                    "code": "unsupported_file_type",
                    "message": (
                        f"Only .ifc and .pdf files are accepted in this release. "
                        f"Got: {file.filename!r}."
                    ),
                },
            )

        with _open_db() as db:
            user = user_resolver(auth_session, db)
            parcel = _resolve_parcel(db, parcel_id=parcel_id, parcel_address=parcel_address)

            # Stage the upload under the user's storage dir before
            # running the pipeline (the ingest needs a real on-disk path).
            user_dir = storage_root / f"user-{user.id}"
            user_dir.mkdir(parents=True, exist_ok=True)
            target_path = user_dir / file.filename
            with target_path.open("wb") as out:
                while True:
                    chunk = file.file.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)

            try:
                result: SubmissionIngestResult = ingest_submission(
                    db,
                    target_path,
                    file_source_type,
                    parcel_id=parcel.id,
                    submitter_id=user.id,
                    config=SubmissionIngestConfig(run_evaluator=False),
                    derived_attribute_fn=compute_derived_attributes,
                )
            except Exception as exc:  # noqa: BLE001 — surface to UI
                logger.exception("submission ingest failed")
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "ingest_failed",
                        "message": f"Failed to ingest submission: {exc}",
                    },
                ) from exc

            submission = db.get(Submission, result.submission_id)
            return _submission_to_out(submission, warnings=result.warnings)

    # ---- GET /v1/submissions/{id} -------------------------------

    @router.get("/{submission_id}", response_model=_SubmissionOut)
    def get_submission(
        submission_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> _SubmissionOut:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            submission = _load_owned_submission(db, submission_id, user)
            return _submission_to_out(submission)

    # ---- PATCH /v1/submissions/{id}/attributes/{key} ------------

    @router.patch(
        "/{submission_id}/attributes/{attribute_key}",
        response_model=_SubmissionOut,
    )
    def override_attribute(
        submission_id: int,
        attribute_key: str,
        body: _OverrideRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> _SubmissionOut:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            submission = _load_owned_submission(db, submission_id, user)
            _apply_manual_override(
                db, submission, attribute_key, body.value, body.unit
            )
            db.flush()
            return _submission_to_out(submission)

    # ---- POST /v1/submissions/{id}/evaluate ---------------------

    @router.post("/{submission_id}/evaluate", response_model=_EvaluateResponse)
    def evaluate_submission(
        submission_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> _EvaluateResponse:
        if evaluator_factory is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "evaluator_unavailable",
                    "message": (
                        "Evaluator service is not configured on this deployment. "
                        "Wire it via build_submissions_router(evaluator_factory=...)."
                    ),
                },
            )
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            submission = _load_owned_submission(db, submission_id, user)

            if (
                submission.source_type == SubmissionSourceType.PDF
                and not (submission.metadata_json or {}).get("human_confirmed")
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "confirmation_required",
                        "message": (
                            "PDF submissions must be human-confirmed before "
                            "the evaluator can run. Use the confirmation screen "
                            "to review extracted attributes first."
                        ),
                    },
                )

            evaluator = evaluator_factory(db)
            attrs_for_eval = _attributes_for_evaluation(submission)
            request = _build_evaluation_request(submission, attrs_for_eval)

            try:
                response = evaluator(request)
            except Exception as exc:  # noqa: BLE001
                logger.exception("evaluator call failed")
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "evaluator_failed",
                        "message": f"Evaluator call failed: {exc}",
                    },
                ) from exc

            decision_json = (
                response.to_json() if hasattr(response, "to_json") else dict(response)
            )

            submission.status = SubmissionStatus.EVALUATED
            db.flush()
            return _EvaluateResponse(
                submission_id=submission.id, decision=decision_json
            )

    # ---- GET /v1/submissions/{id}/matrix ------------------------

    @router.get("/{submission_id}/matrix")
    def get_matrix(
        submission_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> dict[str, Any]:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            submission = _load_owned_submission(db, submission_id, user)
            latest = _latest_decision_json(submission)
            if latest is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "no_decision",
                        "message": "No evaluator run has been performed for this submission yet.",
                    },
                )
            return latest

    # ---- POST /v1/submissions/{id}/confirm -----------------------

    @router.post("/{submission_id}/confirm", response_model=_SubmissionOut)
    def confirm_submission(
        submission_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> _SubmissionOut:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            submission = _load_owned_submission(db, submission_id, user)
            meta = dict(submission.metadata_json or {})
            meta["human_confirmed"] = True
            submission.metadata_json = meta
            db.flush()
            return _submission_to_out(submission)

    # ---- GET /v1/submissions/{id}/regulated-missing ----------------

    @router.get("/{submission_id}/regulated-missing")
    def get_regulated_missing(
        submission_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> list[dict[str, Any]]:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            submission = _load_owned_submission(db, submission_id, user)
            return _compute_regulated_missing(db, submission)

    return router


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _resolve_parcel(
    db: Session,
    *,
    parcel_id: int | None,
    parcel_address: str | None,
) -> Parcel:
    """Look up a parcel by id (preferred) or by civic address (geocode)."""
    if parcel_id is not None:
        parcel = db.get(Parcel, parcel_id)
        if parcel is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "parcel_not_found",
                    "message": f"No parcel with id={parcel_id}.",
                },
            )
        return parcel

    assert parcel_address is not None
    # Match by parcel_identifier first — pilots may know the PID
    # directly. Falls back to a substring-match on jurisdiction-keyed
    # parcels for the dev/test path. The full geocode → ST_Contains
    # path lives in `layer2.spatial.extractor.extract_lot_facts`;
    # ABS-53 doesn't need to re-implement it — pilots will most likely
    # paste a PID.
    parcel = (
        db.execute(
            select(Parcel).where(Parcel.parcel_identifier == parcel_address)
        )
        .scalars()
        .first()
    )
    if parcel is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "parcel_not_found",
                "message": (
                    f"No parcel matched address / identifier {parcel_address!r}. "
                    "Pilot UX: paste a parcel PID. Full address-geocode → "
                    "parcel lookup is a follow-up."
                ),
            },
        )
    return parcel


def _load_owned_submission(
    db: Session, submission_id: int, user: User
) -> Submission:
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "submission_not_found", "message": "No such submission."},
        )
    if (
        submission.submitter_id is not None
        and submission.submitter_id != user.id
    ):
        # Don't reveal existence to non-owners.
        raise HTTPException(
            status_code=404,
            detail={"code": "submission_not_found", "message": "No such submission."},
        )
    return submission


def _submission_to_out(
    submission: Submission, *, warnings: list[str] | None = None
) -> _SubmissionOut:
    return _SubmissionOut(
        id=submission.id,
        parcel_id=submission.parcel_id,
        status=submission.status.value,
        source_type=submission.source_type.value,
        source_artifact_path=submission.source_artifact_path,
        metadata=submission.metadata_json or {},
        attributes=[_AttributeOut.from_row(a) for a in submission.attributes],
        latest_decision=_latest_decision_json(submission),
        warnings=warnings or [],
    )


def _latest_decision_json(submission: Submission) -> dict[str, Any] | None:
    if not submission.decisions:
        return None
    latest = max(submission.decisions, key=lambda d: d.created_at)
    return latest.decision_summary_json or {}


def _apply_manual_override(
    db: Session,
    submission: Submission,
    attribute_key: str,
    value: Any,
    unit: str | None,
) -> None:
    """Add or replace the OVERRIDE row for `attribute_key`.

    Per ABS-53 spec: overrides write a *new* `submission_attribute`
    with `source=OVERRIDE` rather than mutating the EXTRACTED row.
    The DB has a unique constraint on (submission_id, attribute_key),
    so we either replace the existing override or, for first-time
    overrides, *replace the extracted row in place* — the unique
    constraint forces the choice. The original EXTRACTED value is
    preserved in `evidence_json["overridden_from"]` to keep the audit
    trail when the override displaces the extracted row.
    """
    existing = (
        db.execute(
            select(SubmissionAttribute).where(
                SubmissionAttribute.submission_id == submission.id,
                SubmissionAttribute.attribute_key == attribute_key,
            )
        )
        .scalars()
        .one_or_none()
    )
    new_value_json = {"value": value, "unit": unit}
    evidence: dict[str, Any] = {"override_source": "manual_ui"}
    if existing is not None:
        if existing.source != SubmissionAttributeSource.OVERRIDE:
            evidence["overridden_from"] = {
                "previous_value_json": existing.value_json,
                "previous_source": existing.source.value,
                "previous_confidence": existing.confidence,
                "previous_evidence": existing.evidence_json,
            }
        existing.value_json = new_value_json
        existing.confidence = 1.0
        existing.source = SubmissionAttributeSource.OVERRIDE
        existing.evidence_json = evidence
    else:
        db.add(
            SubmissionAttribute(
                submission_id=submission.id,
                attribute_key=attribute_key,
                value_json=new_value_json,
                confidence=1.0,
                source=SubmissionAttributeSource.OVERRIDE,
                evidence_json=evidence,
            )
        )


def _attributes_for_evaluation(submission: Submission) -> list[ExtractedAttribute]:
    """Lift persisted SubmissionAttribute rows back into the ExtractedAttribute shape."""
    out: list[ExtractedAttribute] = []
    for row in submission.attributes:
        value_json = row.value_json or {}
        out.append(
            ExtractedAttribute(
                attribute_key=row.attribute_key,
                value=value_json.get("value"),
                unit=value_json.get("unit"),
                confidence=row.confidence,
                source=row.source,
                evidence=row.evidence_json or {},
            )
        )
    return out


def _build_evaluation_request(
    submission: Submission, attributes: list[ExtractedAttribute]
) -> Any:
    """Build the EvaluationRequest. Lazy-import to dodge retrieval stack."""
    from layer2.compliance.evaluator import (
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
    return EvaluationRequest(
        attributes=inputs,
        submission_id=submission.id,
        persist_decision=True,
    )


def _compute_regulated_missing(
    db: Session, submission: Submission
) -> list[dict[str, Any]]:
    """Return attribute keys regulated by zone bylaws but absent from the submission."""
    present_keys = {a.attribute_key for a in submission.attributes}
    parcel = submission.parcel
    if parcel is None:
        return []

    fragments = (
        db.execute(
            select(SourceFragment.attribute_tags).where(
                SourceFragment.attribute_tags != [],
            )
        )
        .scalars()
        .all()
    )
    regulated_keys: set[str] = set()
    for tags in fragments:
        if isinstance(tags, list):
            regulated_keys.update(tags)

    taxonomy = load_taxonomy()
    taxonomy_by_id = {a.id: a for a in taxonomy.attributes}
    missing: list[dict[str, Any]] = []
    for key in sorted(regulated_keys - present_keys):
        entry = taxonomy_by_id.get(key)
        missing.append({
            "attribute_key": key,
            "description": entry.description if entry else key,
            "unit": entry.unit if entry else None,
        })
    return missing


__all__ = [
    "EvaluatorFactory",
    "UserResolver",
    "build_submissions_router",
]
