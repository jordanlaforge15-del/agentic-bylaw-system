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

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from advisor.db.models import UsageEvent, User
from advisor.db.schemas import MonthlyQuota


class QuotaExceeded(Exception):
    """Raised when a usage limit is reached.

    ``kind`` tells the API edge which limit fired so it can render a
    targeted error message. The four kinds:
      * ``"queries"`` — monthly request-count cap.
      * ``"input_tokens"`` — monthly input-token cap.
      * ``"output_tokens"`` — monthly output-token cap.
      * ``"rpm"`` — requests-per-minute rate cap.

    ``limit`` / ``used`` are the corresponding numeric pair so the
    frontend can display "X / Y" without having to read the user row
    separately.
    """

    KINDS = frozenset({"queries", "input_tokens", "output_tokens", "rpm"})

    def __init__(self, *, kind: str, limit: int, used: int) -> None:
        if kind not in self.KINDS:
            raise ValueError(f"unknown QuotaExceeded kind: {kind!r}")
        super().__init__(
            f"{kind} limit exceeded: {used}/{limit}"
        )
        self.kind = kind
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
    this resets ``monthly_queries_used`` / ``monthly_input_tokens_used`` /
    ``monthly_output_tokens_used`` to 0, sets ``month_started_at`` to
    the first of the current month, and emits a
    ``monthly_quota_reset`` ``UsageEvent`` for audit. Caller is
    responsible for committing.
    """
    today = _utc_today()
    if _needs_window_reset(today=today, month_started_at=user.month_started_at):
        previous_used = user.monthly_queries_used
        previous_input = user.monthly_input_tokens_used
        previous_output = user.monthly_output_tokens_used
        user.monthly_queries_used = 0
        user.monthly_input_tokens_used = 0
        user.monthly_output_tokens_used = 0
        user.month_started_at = _first_of_month(today)
        session.add(user)

        reset_event = UsageEvent(
            user_id=user.id,
            event_type="monthly_quota_reset",
            metadata_json={
                "previous_used": previous_used,
                "previous_input_tokens": previous_input,
                "previous_output_tokens": previous_output,
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


def _count_recent_requests(
    session: Session, *, user_id: int, window: timedelta
) -> int:
    """Count usage events in the last ``window`` that count toward the
    rate cap. Includes successful ``llm_call`` rows AND prior
    ``rate_limit_exceeded`` rows so a flood of rejected requests
    doesn't reset the window."""
    from layer1.db.base import utcnow

    cutoff = utcnow() - window
    stmt = (
        select(func.count(UsageEvent.id))
        .where(UsageEvent.user_id == user_id)
        .where(UsageEvent.created_at >= cutoff)
        .where(
            UsageEvent.event_type.in_(
                ["llm_call", "rate_limit_exceeded"]
            )
        )
    )
    return int(session.execute(stmt).scalar_one())


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

    # Check all four limits in deterministic order. We check rate
    # before monthly because a runaway client is the most urgent
    # signal, then queries (cheapest counter), then token caps
    # (which are the actual cost ceiling).
    recent = _count_recent_requests(
        session, user_id=user.id, window=timedelta(minutes=1)
    )
    if recent >= user.requests_per_minute_limit:
        _emit_exceeded(
            session,
            user=user,
            kind="rpm",
            limit=user.requests_per_minute_limit,
            used=recent,
            event_type="rate_limit_exceeded",
            session_id=session_id,
            model=model,
            provider=provider,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_estimate_cents=cost_estimate_cents,
            metadata=metadata,
        )

    if user.monthly_queries_used >= user.monthly_query_limit:
        _emit_exceeded(
            session,
            user=user,
            kind="queries",
            limit=user.monthly_query_limit,
            used=user.monthly_queries_used,
            event_type="monthly_quota_exceeded",
            session_id=session_id,
            model=model,
            provider=provider,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_estimate_cents=cost_estimate_cents,
            metadata=metadata,
        )

    if user.monthly_input_tokens_used >= user.monthly_input_token_limit:
        _emit_exceeded(
            session,
            user=user,
            kind="input_tokens",
            limit=user.monthly_input_token_limit,
            used=user.monthly_input_tokens_used,
            event_type="monthly_quota_exceeded",
            session_id=session_id,
            model=model,
            provider=provider,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_estimate_cents=cost_estimate_cents,
            metadata=metadata,
        )

    if user.monthly_output_tokens_used >= user.monthly_output_token_limit:
        _emit_exceeded(
            session,
            user=user,
            kind="output_tokens",
            limit=user.monthly_output_token_limit,
            used=user.monthly_output_tokens_used,
            event_type="monthly_quota_exceeded",
            session_id=session_id,
            model=model,
            provider=provider,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_estimate_cents=cost_estimate_cents,
            metadata=metadata,
        )

    user.monthly_queries_used += 1
    # Token counters get bumped here with whatever was reported
    # (typically 0 — actual aggregate is patched after the stream).
    if tokens_input:
        user.monthly_input_tokens_used += tokens_input
    if tokens_output:
        user.monthly_output_tokens_used += tokens_output
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


def _emit_exceeded(
    session: Session,
    *,
    user: User,
    kind: str,
    limit: int,
    used: int,
    event_type: str,
    session_id: int | None,
    model: str | None,
    provider: str | None,
    tokens_input: int,
    tokens_output: int,
    cost_estimate_cents: int,
    metadata: dict | None,
) -> None:
    """Stage an exceedance audit row and raise ``QuotaExceeded``.

    Pulled out of ``record_query`` to keep the four limit checks in
    that function readable. Shared structure: every limit failure
    records a usage_event row whose ``event_type`` identifies the
    kind (rate_limit_exceeded vs monthly_quota_exceeded) and whose
    metadata carries kind/limit/used for analytics.
    """
    exceeded_event = UsageEvent(
        user_id=user.id,
        session_id=session_id,
        event_type=event_type,
        provider=provider,
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_estimate_cents=cost_estimate_cents,
        metadata_json={
            **(metadata or {}),
            "limit_kind": kind,
            "limit": limit,
            "used": used,
        },
    )
    session.add(exceeded_event)
    raise QuotaExceeded(kind=kind, limit=limit, used=used)
