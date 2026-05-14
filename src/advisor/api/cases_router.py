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
  in-window match) and reserve one credit at the requested tier.
* ``POST /v1/cases/{case_id}/upgrade`` — auth-required. Layer-2 / 3
  upgrade accept. Atomically swaps the active credit for one at a
  higher tier; 409 if the user has no available credit at the target
  tier.
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
    CaseStateError,
    NoAvailableCreditError,
    UnknownTierError,
    close_case as close_case_svc,
    list_user_cases,
    match_case,
    open_case,
    upgrade_case_credit,
)
from advisor.db.models import Case, User
from advisor.db.schemas import CaseOut
from advisor.llm import LLMGateway

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
    tier: str = Field(pattern=r"^(quick|standard|complex)$")


class OpenCaseResponse(BaseModel):
    case: CaseOut
    credit_id: int
    reused_existing_case: bool


class UpgradeRequest(BaseModel):
    target_tier: str = Field(pattern=r"^(standard|complex)$")
    trigger: str = Field(
        default="user_manual",
        pattern=r"^(classifier|agent_request|user_manual)$",
    )


class UpgradeResponse(BaseModel):
    case: CaseOut
    new_credit_id: int
    burned_credit_id: int


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
            try:
                case, credit = open_case(
                    db,
                    user=user,
                    anchor_label=body.anchor_label,
                    anchor_kind=body.anchor_kind,
                    tier=body.tier,
                )
            except UnknownTierError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "unknown_tier", "message": str(exc)},
                ) from exc
            except NoAvailableCreditError as exc:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "code": "no_available_credit",
                        "tier": exc.tier,
                        "message": (
                            f"No available {exc.tier} credit. Purchase a "
                            "credit to continue."
                        ),
                    },
                ) from exc
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
            return OpenCaseResponse(
                case=CaseOut.model_validate(case),
                credit_id=credit.id,
                reused_existing_case=existing.case is not None
                and existing.case.id == case.id,
            )

    @router.post("/{case_id}/upgrade", response_model=UpgradeResponse)
    def post_upgrade(
        case_id: int,
        body: UpgradeRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> UpgradeResponse:
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            case = db.get(Case, case_id)
            if case is None or case.user_id != user.id:
                # 404 (not 403) to avoid enumeration of other users' cases.
                raise HTTPException(
                    status_code=404, detail={"code": "case_not_found"}
                )
            try:
                burned, new = upgrade_case_credit(
                    db,
                    case=case,
                    target_tier=body.target_tier,
                    trigger=body.trigger,
                )
            except CaseStateError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "invalid_upgrade", "message": str(exc)},
                ) from exc
            except NoAvailableCreditError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "no_available_credit",
                        "tier": exc.tier,
                        "message": (
                            f"No available {exc.tier} credit to upgrade "
                            "into. Purchase one and retry."
                        ),
                    },
                ) from exc
            except UnknownTierError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "unknown_tier", "message": str(exc)},
                ) from exc
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
            return UpgradeResponse(
                case=CaseOut.model_validate(case),
                new_credit_id=new.id,
                burned_credit_id=burned.id,
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
