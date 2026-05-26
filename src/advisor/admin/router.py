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
* ``POST /v1/admin/maintenance/refund-orphaned-reservations`` — one-shot
  cleanup of credits leaked by the pre-ABS-9 double-reservation bug
  (ABS-8 / ABS-11). Idempotent; safe to call from a runbook.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from advisor.db.cases import (
    UnknownTierError,
    credit_balance_for,
    grant_admin_credits,
    refund_orphaned_case_reservations,
)
from advisor.db.models import (
    Case,
    CaseCredit,
    CaseEvent,
    CasePurchase,
    ChatMessage,
    ChatSession,
    TermsAcceptance,
    User,
)
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


class SetUnlimitedCreditsRequest(BaseModel):
    enabled: bool


class SetUnlimitedCreditsResponse(BaseModel):
    user_id: int
    unlimited_credits: bool


class RefundOrphanedReservationsResponse(BaseModel):
    refunded: int


class ActiveUsersDayRow(BaseModel):
    date: str
    count: int


class ActiveUsersWeekRow(BaseModel):
    week: str
    count: int


class ActiveUsersResponse(BaseModel):
    daily: list[ActiveUsersDayRow]
    weekly: list[ActiveUsersWeekRow]


class EngagementWeekRow(BaseModel):
    week: str
    value: float


class EngagementResponse(BaseModel):
    sessions_per_user: list[EngagementWeekRow]
    messages_per_session: list[EngagementWeekRow]


class RetentionCohortRow(BaseModel):
    cohort_week: str
    signup_count: int
    retention_pcts: list[float]


class RetentionResponse(BaseModel):
    cohorts: list[RetentionCohortRow]
    week_labels: list[str]


class CreditTrendRow(BaseModel):
    date: str
    tier: str
    count: int


class CreditTrendsResponse(BaseModel):
    daily: list[CreditTrendRow]


class FunnelStage(BaseModel):
    name: str
    count: int


class FunnelResponse(BaseModel):
    stages: list[FunnelStage]


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

    @router.put(
        "/users/{user_id}/unlimited-credits",
        response_model=SetUnlimitedCreditsResponse,
    )
    def put_unlimited_credits(
        user_id: int,
        body: SetUnlimitedCreditsRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> SetUnlimitedCreditsResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            target = db.get(User, user_id)
            if target is None:
                raise HTTPException(
                    status_code=404, detail={"code": "user_not_found"}
                )
            target.unlimited_credits = body.enabled
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
            logger.info(
                "admin: set unlimited_credits=%s for user %d (caller=%s)",
                body.enabled,
                user_id,
                caller.clerk_user_id,
            )
            return SetUnlimitedCreditsResponse(
                user_id=target.id,
                unlimited_credits=target.unlimited_credits,
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

    @router.get(
        "/analytics/active-users", response_model=ActiveUsersResponse
    )
    def get_active_users(
        days: int = Query(default=30, ge=1, le=90),
        auth_session: Any = Depends(user_dependency),
    ) -> ActiveUsersResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            cutoff = func.now() - timedelta(days=days)
            day_col = cast(ChatSession.created_at, Date)
            daily_rows = db.execute(
                select(day_col, func.count(func.distinct(ChatSession.user_id)))
                .where(ChatSession.created_at >= cutoff)
                .group_by(day_col)
                .order_by(day_col)
            ).all()

            weekly: dict[str, set[int]] = defaultdict(set)
            for session in db.execute(
                select(ChatSession.created_at, ChatSession.user_id)
                .where(ChatSession.created_at >= cutoff)
            ).all():
                dt = session[0]
                iso_week = dt.strftime("%G-W%V")
                weekly[iso_week].add(session[1])

            sorted_weeks = sorted(weekly.items())
            return ActiveUsersResponse(
                daily=[
                    ActiveUsersDayRow(date=str(r[0]), count=int(r[1]))
                    for r in daily_rows
                ],
                weekly=[
                    ActiveUsersWeekRow(week=w, count=len(uids))
                    for w, uids in sorted_weeks
                ],
            )

    @router.get(
        "/analytics/engagement", response_model=EngagementResponse
    )
    def get_engagement(
        weeks: int = Query(default=12, ge=1, le=52),
        auth_session: Any = Depends(user_dependency),
    ) -> EngagementResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            cutoff = func.now() - timedelta(weeks=weeks)

            sessions = db.execute(
                select(ChatSession.id, ChatSession.user_id, ChatSession.created_at)
                .where(ChatSession.created_at >= cutoff)
            ).all()

            session_ids = [s[0] for s in sessions]
            msg_counts: dict[int, int] = {}
            if session_ids:
                msg_rows = db.execute(
                    select(
                        ChatMessage.session_id,
                        func.count(ChatMessage.id),
                    )
                    .where(ChatMessage.session_id.in_(session_ids))
                    .group_by(ChatMessage.session_id)
                ).all()
                msg_counts = {r[0]: int(r[1]) for r in msg_rows}

            week_sessions: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for sid, uid, created in sessions:
                iso_week = created.strftime("%G-W%V")
                week_sessions[iso_week].append((sid, uid))

            spu_rows = []
            mps_rows = []
            for week_key in sorted(week_sessions):
                entries = week_sessions[week_key]
                unique_users = len({uid for _, uid in entries})
                total_sessions = len(entries)
                total_msgs = sum(msg_counts.get(sid, 0) for sid, _ in entries)
                spu = round(total_sessions / max(unique_users, 1), 2)
                mps = round(total_msgs / max(total_sessions, 1), 2)
                spu_rows.append(EngagementWeekRow(week=week_key, value=spu))
                mps_rows.append(EngagementWeekRow(week=week_key, value=mps))

            return EngagementResponse(
                sessions_per_user=spu_rows,
                messages_per_session=mps_rows,
            )

    @router.get(
        "/analytics/retention", response_model=RetentionResponse
    )
    def get_retention(
        cohort_weeks: int = Query(default=8, ge=1, le=24),
        auth_session: Any = Depends(user_dependency),
    ) -> RetentionResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)

            users = db.execute(
                select(User.id, User.created_at)
            ).all()

            all_sessions = db.execute(
                select(ChatSession.user_id, ChatSession.created_at)
            ).all()

            user_sessions: dict[int, list[datetime]] = defaultdict(list)
            for uid, created in all_sessions:
                user_sessions[uid].append(created)

            cohorts: dict[str, list[int]] = defaultdict(list)
            user_signup: dict[int, datetime] = {}
            for uid, created in users:
                iso_week = created.strftime("%G-W%V")
                cohorts[iso_week].append(uid)
                user_signup[uid] = created

            sorted_cohort_keys = sorted(cohorts.keys())[-cohort_weeks:]

            result_rows = []
            max_follow_weeks = min(cohort_weeks, 8)
            for cohort_key in sorted_cohort_keys:
                user_ids = cohorts[cohort_key]
                signup_count = len(user_ids)
                retention = []
                for offset in range(1, max_follow_weeks + 1):
                    active = 0
                    for uid in user_ids:
                        signup = user_signup[uid]
                        week_start = signup + timedelta(weeks=offset)
                        week_end = week_start + timedelta(weeks=1)
                        if any(
                            week_start <= s < week_end
                            for s in user_sessions.get(uid, [])
                        ):
                            active += 1
                    pct = round(100 * active / max(signup_count, 1), 1)
                    retention.append(pct)
                result_rows.append(
                    RetentionCohortRow(
                        cohort_week=cohort_key,
                        signup_count=signup_count,
                        retention_pcts=retention,
                    )
                )

            return RetentionResponse(
                cohorts=result_rows,
                week_labels=[f"W+{i}" for i in range(1, max_follow_weeks + 1)],
            )

    @router.get(
        "/analytics/credit-trends", response_model=CreditTrendsResponse
    )
    def get_credit_trends(
        days: int = Query(default=30, ge=1, le=90),
        auth_session: Any = Depends(user_dependency),
    ) -> CreditTrendsResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            cutoff = func.now() - timedelta(days=days)
            day_col = cast(CaseCredit.consumed_at, Date)
            rows = db.execute(
                select(day_col, CaseCredit.tier, func.count(CaseCredit.id))
                .where(
                    CaseCredit.state == "consumed",
                    CaseCredit.consumed_at.is_not(None),
                    CaseCredit.consumed_at >= cutoff,
                )
                .group_by(day_col, CaseCredit.tier)
                .order_by(day_col)
            ).all()
            return CreditTrendsResponse(
                daily=[
                    CreditTrendRow(date=str(r[0]), tier=r[1], count=int(r[2]))
                    for r in rows
                ]
            )

    @router.get(
        "/analytics/funnel", response_model=FunnelResponse
    )
    def get_funnel(
        auth_session: Any = Depends(user_dependency),
    ) -> FunnelResponse:
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)

            signed_up = db.scalar(
                select(func.count(User.id))
            ) or 0

            accepted_terms = db.scalar(
                select(func.count(func.distinct(TermsAcceptance.user_id)))
            ) or 0

            first_question = db.scalar(
                select(func.count(func.distinct(ChatSession.user_id)))
                .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
                .where(ChatMessage.role == "user")
            ) or 0

            repeat_users = db.scalar(
                select(func.count()).select_from(
                    select(ChatSession.user_id)
                    .group_by(ChatSession.user_id)
                    .having(func.count(ChatSession.id) >= 2)
                    .subquery()
                )
            ) or 0

            purchased = db.scalar(
                select(func.count(func.distinct(CasePurchase.user_id)))
                .where(CasePurchase.pack_sku != "admin_grant")
            ) or 0

            return FunnelResponse(
                stages=[
                    FunnelStage(name="Signed up", count=int(signed_up)),
                    FunnelStage(name="Accepted terms", count=int(accepted_terms)),
                    FunnelStage(name="First question", count=int(first_question)),
                    FunnelStage(name="Repeat user", count=int(repeat_users)),
                    FunnelStage(name="Purchased", count=int(purchased)),
                ]
            )

    @router.post(
        "/maintenance/refund-orphaned-reservations",
        response_model=RefundOrphanedReservationsResponse,
    )
    def post_refund_orphaned_reservations(
        auth_session: Any = Depends(user_dependency),
    ) -> RefundOrphanedReservationsResponse:
        """Refund credits leaked by the pre-ABS-9 double-reservation bug.

        Safe to call repeatedly: only credits with state ``reserved``,
        ``session_id IS NULL``, and a sibling sessioned active credit on
        the same case at the same tier are refunded. Returns the count
        refunded so the caller can confirm the sweep took effect.
        """
        with _open_db() as db:
            caller = user_resolver(auth_session, db)
            _require_admin(caller)
            count = refund_orphaned_case_reservations(db)
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
            logger.info(
                "admin: refunded %d orphaned reservations (caller=%s)",
                count,
                caller.clerk_user_id,
            )
            return RefundOrphanedReservationsResponse(refunded=count)

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
    router.add_api_route("/users/{user_id}/unlimited-credits", _disabled, methods=["PUT"])
    router.add_api_route("/cases", _disabled, methods=["GET"])
    router.add_api_route("/analytics/tier-distribution", _disabled, methods=["GET"])
    router.add_api_route("/analytics/upgrade-funnel", _disabled, methods=["GET"])
    router.add_api_route("/analytics/active-users", _disabled, methods=["GET"])
    router.add_api_route("/analytics/engagement", _disabled, methods=["GET"])
    router.add_api_route("/analytics/retention", _disabled, methods=["GET"])
    router.add_api_route("/analytics/credit-trends", _disabled, methods=["GET"])
    router.add_api_route("/analytics/funnel", _disabled, methods=["GET"])
    router.add_api_route(
        "/maintenance/refund-orphaned-reservations",
        _disabled,
        methods=["POST"],
    )
    return router
