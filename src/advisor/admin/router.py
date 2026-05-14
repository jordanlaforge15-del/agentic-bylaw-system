"""Admin endpoints — per-user credits, manual grants, analytics.

Mirrors the dormant-by-default pattern of the billing router. The
admin router is mounted only when both:

* ``ADVISOR_ADMIN_API_ENABLED=true``, and
* the request user's Clerk id is in
  ``ADVISOR_ADMIN_CLERK_USER_IDS`` (comma-separated allowlist).

When either condition fails, every admin endpoint returns 403. The
allowlist is checked at request time rather than at mount time so
adding an admin doesn't require a redeploy.

Endpoints:

* ``GET /v1/admin/users/{user_id}/credits`` — balance for a user.
* ``POST /v1/admin/users/{user_id}/credits`` — gift N credits at a tier.
* ``GET /v1/admin/cases`` — paginated list with filters.
* ``GET /v1/admin/analytics/tier-distribution`` — counts of credits
  by (tier, source, state) over a window. Powers the dashboard.
* ``GET /v1/admin/analytics/upgrade-funnel`` — counts of
  ``tier_recommended`` vs ``upgrade_offered`` vs ``upgrade_accepted``
  events. Surfaces classifier accuracy and conversion rate.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from advisor.db.cases import (
    UnknownTierError,
    credit_balance_for,
    grant_admin_credits,
)
from advisor.db.models import Case, CaseEvent, User
from advisor.db.schemas import CaseOut

logger = logging.getLogger(__name__)


def admin_clerk_user_ids() -> set[str]:
    """Parse ``ADVISOR_ADMIN_CLERK_USER_IDS`` into a set.

    Comma-separated, whitespace-tolerant. Empty / unset returns the
    empty set, which means no admins — every protected endpoint will
    403. Read at request time (not module import time) so adding an
    admin via env doesn't require a process restart.
    """
    raw = os.environ.get("ADVISOR_ADMIN_CLERK_USER_IDS") or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def is_admin_enabled() -> bool:
    raw = os.environ.get("ADVISOR_ADMIN_API_ENABLED", "").lower()
    return raw in {"1", "true", "yes", "on"}


# -- Request / response models ---------------------------------------------


class CreditBalanceItem(BaseModel):
    tier: str
    available: int
    reserved: int
    consumed: int


class UserCreditsResponse(BaseModel):
    user_id: int
    email: str
    balances: list[CreditBalanceItem]


class GrantCreditsRequest(BaseModel):
    tier: str = Field(pattern=r"^(quick|standard|complex)$")
    quantity: int = Field(ge=1, le=1000)
    reason: str = Field(min_length=1, max_length=500)


class GrantCreditsResponse(BaseModel):
    granted: int
    tier: str
    reason: str


class AdminCaseListResponse(BaseModel):
    cases: list[CaseOut]


class TierDistributionRow(BaseModel):
    tier: str
    source: str
    state: str
    count: int


class TierDistributionResponse(BaseModel):
    rows: list[TierDistributionRow]


class UpgradeFunnelRow(BaseModel):
    event_type: str
    count: int


class UpgradeFunnelResponse(BaseModel):
    rows: list[UpgradeFunnelRow]


# -- Router factory ---------------------------------------------------------


UserResolver = Callable[[Any, Session], User]


def build_admin_router(
    *,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
) -> APIRouter:
    """Assemble the admin router. Mount only behind a feature flag."""
    router = APIRouter(prefix="/v1/admin", tags=["admin"])

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

    def _require_admin(user: User) -> None:
        """403 if the caller isn't on the admin allowlist."""
        if not is_admin_enabled():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "admin_disabled"},
            )
        allowlist = admin_clerk_user_ids()
        if not allowlist or user.clerk_user_id not in allowlist:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "admin_forbidden"},
            )

    @router.get(
        "/users/{user_id}/credits", response_model=UserCreditsResponse
    )
    def get_user_credits(
        user_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> UserCreditsResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            target = db.get(User, user_id)
            if target is None:
                raise HTTPException(
                    status_code=404, detail={"code": "user_not_found"}
                )
            balances = credit_balance_for(db, user_id=target.id)
            return UserCreditsResponse(
                user_id=target.id,
                email=target.email,
                balances=[
                    CreditBalanceItem(
                        tier=b.tier,
                        available=b.available,
                        reserved=b.reserved,
                        consumed=b.consumed,
                    )
                    for b in balances
                ],
            )

    @router.post(
        "/users/{user_id}/credits", response_model=GrantCreditsResponse
    )
    def post_grant_credits(
        user_id: int,
        body: GrantCreditsRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> GrantCreditsResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            target = db.get(User, user_id)
            if target is None:
                raise HTTPException(
                    status_code=404, detail={"code": "user_not_found"}
                )
            try:
                credits = grant_admin_credits(
                    db,
                    user=target,
                    tier=body.tier,
                    quantity=body.quantity,
                    reason=f"admin:{caller.clerk_user_id}:{body.reason}",
                )
            except UnknownTierError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "unknown_tier", "message": str(exc)},
                ) from exc
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
            return GrantCreditsResponse(
                granted=len(credits),
                tier=body.tier,
                reason=body.reason,
            )

    @router.get("/cases", response_model=AdminCaseListResponse)
    def get_cases(
        status_filter: str | None = Query(default=None, alias="status"),
        tier: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        auth_session: Any = Depends(user_dependency),
    ) -> AdminCaseListResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            stmt = select(Case)
            if status_filter:
                stmt = stmt.where(Case.status == status_filter)
            if tier:
                stmt = stmt.where(Case.current_tier == tier)
            stmt = stmt.order_by(Case.last_activity_at.desc()).limit(limit)
            cases = list(db.execute(stmt).scalars().all())
            return AdminCaseListResponse(
                cases=[CaseOut.model_validate(c) for c in cases]
            )

    @router.get(
        "/analytics/tier-distribution",
        response_model=TierDistributionResponse,
    )
    def get_tier_distribution(
        auth_session: Any = Depends(user_dependency),
    ) -> TierDistributionResponse:
        # One query: COUNT(*) grouped by (tier, source, state). Cheap
        # at our scale; if the credit table grows past ~1M rows we'll
        # add a materialised view.
        from advisor.db.models import CaseCredit  # noqa: PLC0415

        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            rows = db.execute(
                select(
                    CaseCredit.tier,
                    CaseCredit.source,
                    CaseCredit.state,
                    func.count(CaseCredit.id),
                ).group_by(
                    CaseCredit.tier, CaseCredit.source, CaseCredit.state
                )
            ).all()
            return TierDistributionResponse(
                rows=[
                    TierDistributionRow(
                        tier=r[0], source=r[1], state=r[2], count=int(r[3])
                    )
                    for r in rows
                ]
            )

    @router.get(
        "/analytics/upgrade-funnel", response_model=UpgradeFunnelResponse
    )
    def get_upgrade_funnel(
        auth_session: Any = Depends(user_dependency),
    ) -> UpgradeFunnelResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            stmt = (
                select(CaseEvent.event_type, func.count(CaseEvent.id))
                .where(
                    CaseEvent.event_type.in_(
                        [
                            "tier_recommended",
                            "upgrade_offered",
                            "upgrade_accepted",
                            "upgrade_declined",
                        ]
                    )
                )
                .group_by(CaseEvent.event_type)
            )
            rows = db.execute(stmt).all()
            return UpgradeFunnelResponse(
                rows=[
                    UpgradeFunnelRow(event_type=r[0], count=int(r[1]))
                    for r in rows
                ]
            )

    return router


def build_dormant_admin_router() -> APIRouter:
    """Stub router that 403s every endpoint when admin is disabled."""
    router = APIRouter(prefix="/v1/admin", tags=["admin"])
    detail = {"code": "admin_disabled"}

    def _disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=detail
        )

    router.add_api_route("/users/{user_id}/credits", _disabled, methods=["GET", "POST"])
    router.add_api_route("/cases", _disabled, methods=["GET"])
    router.add_api_route("/analytics/tier-distribution", _disabled, methods=["GET"])
    router.add_api_route("/analytics/upgrade-funnel", _disabled, methods=["GET"])
    return router
