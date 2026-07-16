"""FastAPI router for the case-credit lifecycle.

Five endpoints expose the case service to the frontend:

* ``GET /v1/cases`` — auth-required. List the user's cases newest-first.
* ``GET /v1/cases/match`` — auth-required. Pre-flight match for the
  case-open form: "do you already have a case for this anchor within
  the 30-day window?" Frontend uses the response to decide whether to
  render a "Continue case" banner.
* ``POST /v1/cases/classify`` — auth-required. Layer-2 pre-flight tier
  classifier. Cheap Haiku call; returns a recommended tier + confidence
  + reasons. Surfaced as a banner on the case-open form.
* ``POST /v1/cases`` — auth-required. Open a new case (or reopen an
  in-window match). Free: no tier credit is reserved (ABS-382). Any
  ``tier`` in the body is accepted-but-ignored for old-frontend compat.
* ``POST /v1/cases/{case_id}/upgrade`` — RETIRED (ABS-382). Always
  returns 410 ``{code: "tier_model_retired"}``; the tier model is gone.
* ``POST /v1/cases/{case_id}/close`` — auth-required. User explicitly
  closes a case (refunds any reserved-but-uncommitted credit).

Builder pattern matches the billing router: dependencies are passed
in so tests can wire a mock gateway / db factory without standing up
the real Anthropic + Postgres stack.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from advisor.chat.classifier import ClassifierResult, classify_query
from advisor.db.cases import (
    close_case as close_case_svc,
    list_user_cases,
    match_case,
    open_case_free,
)
from advisor.db.models import Case, User
from advisor.db.schemas import CaseOut
from advisor.llm import LLMGateway
from layer2.spatial.extractor import extract_lot_facts

logger = logging.getLogger(__name__)


# -- Request / response models ---------------------------------------------


class MatchResponse(BaseModel):
    matched: bool
    case: CaseOut | None = None


class ClassifyRequest(BaseModel):
    anchor_label: str = Field(min_length=1, max_length=500)
    anchor_kind: str = Field(
        pattern=r"^(address|project_ref|development_application)$"
    )
    message: str = Field(min_length=1, max_length=10_000)


class ClassifyResponse(BaseModel):
    tier: str
    confidence: float
    reasons: list[str]


class OpenCaseRequest(BaseModel):
    anchor_label: str = Field(min_length=1, max_length=500)
    anchor_kind: str = Field(
        pattern=r"^(address|project_ref|development_application)$"
    )
    # ABS-382: tiers are retired. Opening a case is free and reserves no
    # credit, so ``tier`` is accepted-but-ignored (no validation pattern)
    # to keep old frontends that still POST a tier alive during rollout.
    tier: str | None = None


class OpenCaseResponse(BaseModel):
    case: CaseOut
    # ABS-382: no CaseCredit is reserved on open, so ``credit_id`` is
    # always null. Kept in the response for old-frontend compatibility.
    credit_id: int | None = None
    reused_existing_case: bool


class CaseListResponse(BaseModel):
    cases: list[CaseOut]


# -- Router factory ---------------------------------------------------------


UserResolver = Callable[[Any, Session], User]


def build_cases_router(
    *,
    classifier_gateway_factory: Callable[[], LLMGateway] | None,
    classifier_model: str,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
) -> APIRouter:
    """Assemble the cases router.

    ``classifier_gateway_factory`` is separate from the chat gateway so
    the classifier model (Haiku) and the main chat model (Opus / Sonnet)
    can be wired independently. May be ``None`` in test contexts that
    don't exercise the classifier — those endpoints will return 503.
    """
    router = APIRouter(prefix="/v1/cases", tags=["cases"])

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

    @router.get("/match", response_model=MatchResponse)
    def get_match(
        anchor_label: str,
        anchor_kind: str,
        auth_session: Any = Depends(user_dependency),
    ) -> MatchResponse:
        if anchor_kind not in {"address", "project_ref", "development_application"}:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "unknown_anchor_kind",
                    "message": (
                        "anchor_kind must be one of: address, project_ref, "
                        "development_application"
                    ),
                },
            )
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            result = match_case(
                db,
                user_id=user.id,
                anchor_label=anchor_label,
                anchor_kind=anchor_kind,
            )
            return MatchResponse(
                matched=result.case is not None,
                case=CaseOut.model_validate(result.case)
                if result.case is not None
                else None,
            )

    @router.post("/classify", response_model=ClassifyResponse)
    async def post_classify(
        body: ClassifyRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> ClassifyResponse:
        # The classifier is auth-required (it does cost a few cents per
        # call and we don't want anonymous spam) but doesn't touch the
        # DB beyond looking up the user — no transaction needed.
        if classifier_gateway_factory is None:
            # Tests / dormant deployments: return a no-op recommendation
            # so the case-open form still works.
            fallback = ClassifierResult.fallback("classifier_disabled")
            return ClassifyResponse(
                tier=fallback.tier,
                confidence=fallback.confidence,
                reasons=fallback.reasons,
            )
        with _open_db() as db:
            user_resolver(auth_session, db)
        gateway = classifier_gateway_factory()
        result = await classify_query(
            gateway,
            anchor_label=body.anchor_label,
            anchor_kind=body.anchor_kind,
            message=body.message,
            classifier_model=classifier_model,
        )
        return ClassifyResponse(
            tier=result.tier,
            confidence=result.confidence,
            reasons=result.reasons,
        )

    @router.post("", response_model=OpenCaseResponse)
    def post_open_case(
        body: OpenCaseRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> OpenCaseResponse:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            existing = match_case(
                db,
                user_id=user.id,
                anchor_label=body.anchor_label,
                anchor_kind=body.anchor_kind,
            )
            # ABS-382: opening a case is free. No tier credit is claimed
            # or reserved; ``current_tier`` stays null and the response
            # carries ``credit_id=null``. Any ``tier`` in the body is
            # ignored (see OpenCaseRequest).
            case = open_case_free(
                db,
                user=user,
                anchor_label=body.anchor_label,
                anchor_kind=body.anchor_kind,
            )
            # Compute lot spatial facts (area, frontage, depth, corner)
            # and pin them to the case so every chat turn sees them
            # without an extra tool call. Never blocks case creation:
            # any failure is captured as ``{status: unresolved, reason}``
            # so the model knows the facts aren't available rather
            # than hallucinating numbers.
            spatial_facts = extract_lot_facts(
                db,
                anchor_label=body.anchor_label,
                anchor_kind=body.anchor_kind,
            )
            metadata = dict(case.metadata_json or {})
            metadata["spatial_facts"] = spatial_facts
            case.metadata_json = metadata
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
            return OpenCaseResponse(
                case=CaseOut.model_validate(case),
                credit_id=None,
                reused_existing_case=existing.case is not None
                and existing.case.id == case.id,
            )

    @router.post("/{case_id}/upgrade")
    def post_upgrade(
        case_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> dict[str, Any]:
        # ABS-382: the tier model is retired. Upgrading a case is no
        # longer a concept — every case runs on the free wallet. Return
        # 410 Gone for any request body so old frontends surface a clear
        # "this feature is gone" signal instead of a silent success. The
        # ``upgrade_case_credit`` service fn is intentionally kept in
        # cases.py for historical data-migration tooling, but is no
        # longer reachable over HTTP.
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "tier_model_retired",
                "message": (
                    "Case tier upgrades are retired. Opening and using a "
                    "case is free; there is no tier to upgrade."
                ),
            },
        )

    @router.post("/{case_id}/close")
    def post_close(
        case_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> dict[str, Any]:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            case = db.get(Case, case_id)
            if case is None or case.user_id != user.id:
                raise HTTPException(
                    status_code=404, detail={"code": "case_not_found"}
                )
            close_case_svc(db, case=case, reason="user_request")
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
            return {"closed": True, "case_id": case.id}

    @router.get("", response_model=CaseListResponse)
    def get_cases(
        auth_session: Any = Depends(user_dependency),
    ) -> CaseListResponse:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            cases = list_user_cases(db, user_id=user.id)
            return CaseListResponse(
                cases=[CaseOut.model_validate(c) for c in cases]
            )

    return router
