"""HTTP edge wrapper around the case-credit lifecycle.

The chat route doesn't want to know about the credit machinery
directly: it wants to call one helper before each turn that does
"reserve a credit if the session is new and we have one", and one
helper after to commit / refund based on what the turn actually
produced. ``reserve_credit_for_session`` and the post-turn helpers in
this module are that surface.

Replaces the v1 ``enforce_and_record_query`` (monthly query budget) —
billing is now per-credit, not per-month.

The detail payload shapes are fixed by contract with the frontend:

    {
        "code": "no_available_credit" | "case_budget_exhausted" |
                "rate_limit_exceeded",
        "tier": "<tier when applicable>",
        "message": "<human-readable>"
    }

We use 402 (Payment Required) for ``no_available_credit`` because the
remedy is "buy a credit" — the frontend renders a modal that opens
the pricing page. We use 429 for ``rate_limit_exceeded`` because the
remedy is "wait a bit". And we surface ``case_budget_exhausted`` as a
mid-stream SSE event rather than a status code, because by the time
the budget exhausts the stream is already open.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from advisor.db.cases import (
    NoAvailableCreditError,
    UnknownTierError,
    add_tokens_to_case,
    commit_credit_for_session,
    refund_credit_for_session,
)
from advisor.db.models import Case, CaseCredit, ChatSession, UsageEvent, User
from layer1.db.base import utcnow

logger = logging.getLogger(__name__)


# Per-minute request cap. Read from User.requests_per_minute_limit but
# enforced uniformly here so the chat route doesn't have to do the
# count-and-compare itself.
_RPM_WINDOW = timedelta(minutes=1)


class RateLimitExceeded(Exception):
    """Raised when the per-user request rate exceeds
    ``user.requests_per_minute_limit``."""

    def __init__(self, *, limit: int, used: int) -> None:
        super().__init__(f"rpm limit exceeded: {used}/{limit}")
        self.limit = limit
        self.used = used


def reserve_credit_for_session(
    db: Session,
    user: User,
    *,
    session: ChatSession,
    case: Case,
    tier: str,
) -> CaseCredit:
    """Pre-flight: reserve one available credit at ``tier`` against the session.

    Idempotent: if a credit is already reserved for this session, it's
    returned unchanged. Otherwise we claim the next FIFO ``available``
    credit, attach it to the session, and write the audit event.

    Raises:
        HTTPException(402): no available credit at the requested tier.
        HTTPException(400): unknown tier.
    """
    existing = (
        db.execute(
            select(CaseCredit).where(
                CaseCredit.session_id == session.id,
                CaseCredit.state.in_(["reserved", "consumed"]),
            )
        )
        .scalar_one_or_none()
    )
    if existing is not None:
        return existing

    from advisor.db.cases import _claim_available_credit, _record_event  # noqa: PLC0415

    try:
        if tier not in {"quick", "standard", "complex"}:
            raise UnknownTierError(f"unknown tier {tier!r}")
    except UnknownTierError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_tier", "message": str(exc), "tier": tier},
        ) from exc

    credit = _claim_available_credit(db, user_id=user.id, tier=tier)
    if credit is None:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "no_available_credit",
                "tier": tier,
                "message": (
                    f"No available {tier} credit. Purchase a credit to "
                    "continue."
                ),
            },
        )
    credit.state = "reserved"
    credit.case_id = case.id
    credit.session_id = session.id
    credit.reserved_at = utcnow()
    case.current_tier = credit.tier
    case.last_activity_at = utcnow()
    session.tier = credit.tier
    _record_event(
        db,
        case=case,
        user=user,
        credit=credit,
        event_type="credit_reserved",
        payload={"tier": credit.tier, "source": credit.source},
    )
    return credit


def commit_credit_for(db: Session, *, session_id: int) -> CaseCredit | None:
    """Post-turn: flip the session's reserved credit to consumed.

    Thin wrapper around ``advisor.db.cases.commit_credit_for_session``
    so the chat route imports a single module. Returns the credit row
    (or ``None`` if no reserved credit was attached, which is normal
    for legacy non-billed sessions).
    """
    return commit_credit_for_session(db, session_id=session_id)


def refund_credit_for(
    db: Session, *, session_id: int, reason: str
) -> CaseCredit | None:
    """Post-turn: return a still-reserved credit to ``available``.

    Called when the turn produced no qualifying output (empty
    assistant text, or stream error before any tool call).
    """
    return refund_credit_for_session(db, session_id=session_id, reason=reason)


def add_case_tokens(
    db: Session, *, case_id: int, input_tokens: int, output_tokens: int
) -> int | None:
    """Bump the case's running token counter; returns new total.

    Layer 1 enforcement reads this on the next turn — when it crosses
    the tier budget, the chat route surfaces an upgrade prompt.
    """
    return add_tokens_to_case(
        db, case_id=case_id, tokens=input_tokens + output_tokens
    )


def enforce_request_rate(db: Session, user: User) -> None:
    """Raise HTTP 429 if the user is over their per-minute request rate.

    Cheap to call at the top of every chat turn — counts a small
    candidate window of ``advisor_usage_event`` rows scoped to this
    user. Independent of credit gating: a malicious script with one
    active credit can still spam the chat endpoint with empty messages
    and burn our compute, so we keep RPM as cheap abuse protection.
    """
    cutoff = utcnow() - _RPM_WINDOW
    stmt = (
        select(func.count(UsageEvent.id))
        .where(UsageEvent.user_id == user.id)
        .where(UsageEvent.created_at >= cutoff)
        .where(UsageEvent.event_type.in_(["llm_call", "rate_limit_exceeded"]))
    )
    recent = int(db.execute(stmt).scalar_one() or 0)
    if recent >= user.requests_per_minute_limit:
        # Audit row so we can see the burst in analytics.
        db.add(
            UsageEvent(
                user_id=user.id,
                event_type="rate_limit_exceeded",
                metadata_json={
                    "limit_kind": "rpm",
                    "limit": user.requests_per_minute_limit,
                    "used": recent,
                },
            )
        )
        db.commit()
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limit_exceeded",
                "limit": user.requests_per_minute_limit,
                "used": recent,
                "message": (
                    f"Too many requests ({recent}/"
                    f"{user.requests_per_minute_limit} per minute). "
                    "Slow down and try again in a moment."
                ),
            },
        )


def record_llm_call(
    db: Session,
    user: User,
    *,
    session_id: int | None,
    case_id: int | None,
    model: str | None,
    provider: str | None,
) -> UsageEvent:
    """Stage an up-front ``llm_call`` audit row (tokens patched after).

    Mirrors the old v1 pattern — we record the row before the LLM call
    so a quota check has audit evidence even when the call subsequently
    raises. Returns the staged row; caller flushes / commits.
    """
    event = UsageEvent(
        user_id=user.id,
        session_id=session_id,
        case_id=case_id,
        event_type="llm_call",
        provider=provider,
        model=model,
        tokens_input=0,
        tokens_output=0,
        cost_estimate_cents=0,
        metadata_json={},
    )
    db.add(event)
    db.flush()
    return event


def update_usage_event_tokens(
    db: Session,
    *,
    usage_event_id: int,
    tokens_input: int,
    tokens_output: int,
    metadata: dict | None = None,
) -> None:
    """Patch a previously recorded ``UsageEvent`` with real token counts.

    Called from the chat route after the LLM stream finishes. A
    missing row is silently ignored.

    Unlike the v1 implementation this no longer bumps any per-user
    counters — the case-credit model has no per-month token budget,
    just per-case token budgets which are tracked on
    ``advisor_case.tokens_consumed`` via ``add_case_tokens``.

    ``metadata`` is shallow-merged into the row's existing
    ``metadata_json`` so cost-circuit-trip details land alongside the
    call they happened on.
    """
    row = db.get(UsageEvent, usage_event_id)
    if row is None:
        return
    row.tokens_input = tokens_input
    row.tokens_output = tokens_output
    if metadata:
        existing = row.metadata_json or {}
        existing.update(metadata)
        row.metadata_json = existing
