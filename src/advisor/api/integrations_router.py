"""Integration endpoints authenticated via API key (ABS-59).

Provides the same submission ingest + evaluate + matrix surface as
``/v1/submissions`` but accepts ``X-ABS-API-Key`` instead of a Clerk
JWT.  Intended for M2M callers (Speckle Automate function, CI scripts)
that run server-side without a browser session.

Endpoints:
* ``POST /v1/integrations/submissions`` — IFC upload identical to
  ``POST /v1/submissions`` but API-key-authenticated.
* ``POST /v1/integrations/submissions/{id}/evaluate`` — run evaluator.
* ``GET /v1/integrations/submissions/{id}/matrix`` — fetch latest matrix.

The router delegates all business logic to the same helpers used by
the Clerk-authenticated submissions router so the two paths stay in
sync automatically.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

# Side-effect import: registers the IFC extractor with the submission factory.
import layer1.parsers.ifc_submission  # noqa: F401

from advisor.db.models import User
from advisor.api.api_key_auth import api_key_user_dependency
from advisor.api.submission_storage import resolve_storage_root, stage_upload
from layer1.models.submission_schemas import (
    SubmissionIngestConfig,
    SubmissionIngestResult,
)
from layer1.pipeline.ingest_submission import ingest_submission
from layer2.compliance.db.models import (
    Submission,
    SubmissionSourceType,
    SubmissionStatus,
)
from layer2.compliance.derived_attributes import compute_derived_attributes

# Re-use the response models and helpers from the submissions router to
# avoid duplicating the serialisation logic.
from advisor.api.submissions_router import (
    _EvaluateResponse,
    _SubmissionOut,
    _attributes_for_evaluation,
    _build_evaluation_request,
    _load_owned_submission,
    _resolve_parcel,
    _submission_to_out,
)

logger = logging.getLogger(__name__)

EvaluatorFactory = Callable[[Any], Any]


def build_integrations_router(
    *,
    db_session_factory: Callable[[], Any],
    evaluator_factory: EvaluatorFactory | None = None,
    storage_dir: Path | None = None,
) -> APIRouter:
    """Build the integrations router.

    ``evaluator_factory`` is optional; the /evaluate endpoint returns
    503 when it's absent (matching the submissions router behaviour).
    """
    storage_root = resolve_storage_root(storage_dir)

    require_api_key_user = api_key_user_dependency(db_session_factory)

    router = APIRouter(prefix="/v1/integrations", tags=["integrations"])

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

    # ---- POST /v1/integrations/submissions -----------------------

    @router.post("/submissions", response_model=_SubmissionOut)
    def upload_submission(
        file: UploadFile = File(...),
        parcel_id: int | None = Form(default=None),
        parcel_address: str | None = Form(default=None),
        user: User = Depends(require_api_key_user),
    ) -> _SubmissionOut:
        if parcel_id is None and not parcel_address:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "missing_parcel",
                    "message": "Either parcel_id or parcel_address is required.",
                },
            )
        if not file.filename or not file.filename.lower().endswith(".ifc"):
            raise HTTPException(
                status_code=415,
                detail={
                    "code": "unsupported_file_type",
                    "message": (
                        f"Only .ifc files are accepted. Got: {file.filename!r}."
                    ),
                },
            )

        with _open_db() as db:
            parcel = _resolve_parcel(db, parcel_id=parcel_id, parcel_address=parcel_address)

            # Same staging helper as the Clerk-authenticated router: an
            # unwritable storage root surfaces as 503, not a 500 (ABS-87).
            target_path = stage_upload(
                storage_root,
                user_id=user.id,
                filename=file.filename or "",
                fileobj=file.file,
            )

            try:
                result: SubmissionIngestResult = ingest_submission(
                    db,
                    target_path,
                    SubmissionSourceType.IFC,
                    parcel_id=parcel.id,
                    submitter_id=user.id,
                    config=SubmissionIngestConfig(run_evaluator=False),
                    derived_attribute_fn=compute_derived_attributes,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("integration submission ingest failed")
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "ingest_failed",
                        "message": f"Failed to ingest submission: {exc}",
                    },
                ) from exc

            submission = db.get(Submission, result.submission_id)
            return _submission_to_out(submission, warnings=result.warnings)

    # ---- POST /v1/integrations/submissions/{id}/evaluate ---------

    @router.post("/submissions/{submission_id}/evaluate", response_model=_EvaluateResponse)
    def evaluate_submission(
        submission_id: int,
        user: User = Depends(require_api_key_user),
    ) -> _EvaluateResponse:
        if evaluator_factory is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "evaluator_unavailable",
                    "message": (
                        "Evaluator service is not configured on this deployment."
                    ),
                },
            )
        with _open_db() as db:
            submission = _load_owned_submission(db, submission_id, user)
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

    # ---- GET /v1/integrations/submissions/{id}/matrix ------------

    @router.get("/submissions/{submission_id}/matrix")
    def get_matrix(
        submission_id: int,
        user: User = Depends(require_api_key_user),
    ) -> dict[str, Any]:
        with _open_db() as db:
            submission = _load_owned_submission(db, submission_id, user)
            if not submission.decisions:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "no_decision",
                        "message": "No evaluator run has been performed for this submission yet.",
                    },
                )
            latest = max(submission.decisions, key=lambda d: d.created_at)
            return latest.decision_summary_json or {}

    return router


__all__ = ["build_integrations_router"]
