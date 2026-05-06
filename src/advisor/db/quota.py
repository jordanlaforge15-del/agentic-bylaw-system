"""Monthly query-quota helpers.

The advisor SaaS bills on a per-month query budget. Two operations
matter on the hot path:

1. Reading the current quota — does the user have queries left? This
   reads the denormalised counters off ``advisor_user`` so the chat
   backend doesn't pay for an aggregation on every message.
2. Recording a query — increment the counter, raise if over limit,
   emit an audit row in ``advisor_usage_event``.

Both are small, transactional, and scoped to one user. The caller
controls commit vs rollback.

Window logic: the monthly window is anchored to the first of the
current calendar month. We chose calendar months over a rolling
"N days ago" window because:

- It matches the customer's intuition of a billing cycle.
- It produces deterministic ``window_start`` values that frontends
  can cache for "your plan resets on Aug 1".
- It avoids a creeping-window pathology where heavy use late in one
  window triggers an immediate counter reset 30 days later.

If the customer signs up mid-month, their first window is shorter —
that's intentional and matches Stripe's typical free-trial behaviour.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from advisor.db.models import UsageEvent, User
from advisor.db.schemas import MonthlyQuota


class QuotaExceeded(Exception):
    """Raised by ``record_query`` when the user is at their limit.

    Carries ``limit`` and ``used`` so HTTP handlers can render a
    structured error to the frontend. Keep it a plain exception (not a
    pydantic model) so it can travel up the stack without serialization
    overhead; convert to a response at the API edge.
    """

    def __init__(self, *, limit: int, used: int) -> None:
        super().__init__(
            f"monthly query limit exceeded: {used}/{limit}"
        )
        self.limit = limit
        self.used = used


def _first_of_month(today: date) -> date:
    return today.replace(day=1)


def _needs_window_reset(
    *, today: date, month_started_at: date | None
) -> bool:
    """True iff the user's recorded window starts in an earlier month
    than ``today``. Day-of-month doesn't matter — only year + month."""
    if month_started_at is None:
        return True
    return (
        month_started_at.year != today.year
        or month_started_at.month != today.month
    )


def _utc_today() -> date:
    """Imported lazily so tests can monkeypatch ``utcnow``."""
    from layer1.db.base import utcnow

    return utcnow().date()


def get_monthly_quota(session: Session, user: User) -> MonthlyQuota:
    """Return the user's current quota, applying any pending window
    reset as a side effect.

    If ``today`` falls in a calendar month after ``user.month_started_at``,
    this resets ``monthly_queries_used`` to 0, sets
    ``month_started_at`` to the first of the current month, and emits a
    ``monthly_quota_reset`` ``UsageEvent`` for audit. Caller is
    responsible for committing.
    """
    today = _utc_today()
    if _needs_window_reset(today=today, month_started_at=user.month_started_at):
        previous_used = user.monthly_queries_used
        user.monthly_queries_used = 0
        user.month_started_at = _first_of_month(today)
        session.add(user)

        reset_event = UsageEvent(
            user_id=user.id,
            event_type="monthly_quota_reset",
            metadata_json={
                "previous_used": previous_used,
                "previous_window_start": (
                    None
                    if user.month_started_at is None
                    else user.month_started_at.isoformat()
                ),
                "new_window_start": user.month_started_at.isoformat(),
            },
        )
        session.add(reset_event)

    used = user.monthly_queries_used
    limit = user.monthly_query_limit
    return MonthlyQuota(
        limit=limit,
        used=used,
        remaining=max(0, limit - used),
        window_start=user.month_started_at,
    )


def record_query(
    session: Session,
    user: User,
    *,
    event_type: str = "llm_call",
    tokens_input: int = 0,
    tokens_output: int = 0,
    model: str | None = None,
    provider: str | None = None,
    session_id: int | None = None,
    cost_estimate_cents: int = 0,
    metadata: dict | None = None,
) -> UsageEvent:
    """Charge one query against the user's monthly budget.

    Order of operations:

    1. Apply any pending window reset (delegates to
       ``get_monthly_quota`` for the side effect).
    2. If the user is already at their limit, emit a
       ``monthly_quota_exceeded`` audit event and raise
       ``QuotaExceeded`` — the caller is expected to translate this
       into a 402/429 at the API edge.
    3. Otherwise, increment ``monthly_queries_used`` and emit a single
       ``UsageEvent`` of the given ``event_type`` (default
       ``"llm_call"``).

    The whole sequence is staged on the session; the caller commits.
    A failed commit rolls back both the increment and the event,
    keeping the counter and the audit trail consistent.
    """
    # Trigger any pending reset before evaluating the limit so a
    # fresh window doesn't immediately raise QuotaExceeded.
    get_monthly_quota(session, user)

    if user.monthly_queries_used >= user.monthly_query_limit:
        exceeded_event = UsageEvent(
            user_id=user.id,
            session_id=session_id,
            event_type="monthly_quota_exceeded",
            provider=provider,
            model=model,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_estimate_cents=cost_estimate_cents,
            metadata_json={
                **(metadata or {}),
                "limit": user.monthly_query_limit,
                "used": user.monthly_queries_used,
            },
        )
        session.add(exceeded_event)
        raise QuotaExceeded(
            limit=user.monthly_query_limit,
            used=user.monthly_queries_used,
        )

    user.monthly_queries_used += 1
    session.add(user)

    event = UsageEvent(
        user_id=user.id,
        session_id=session_id,
        event_type=event_type,
        provider=provider,
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_estimate_cents=cost_estimate_cents,
        metadata_json=dict(metadata or {}),
    )
    session.add(event)
    return event
