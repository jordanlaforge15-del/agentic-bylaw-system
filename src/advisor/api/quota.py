"""HTTP edge wrapper around ``advisor.db.quota.record_query``.

The chat route doesn't want to know about ``QuotaExceeded``: it wants
to call one helper, get a ``UsageEvent`` back on success, and have a
structured 429 raised on failure. ``enforce_and_record_query`` is that
helper.

The detail payload shape is fixed by contract with the frontend:

    {
        "code": "monthly_quota_exceeded",
        "limit": <int>,
        "used": <int>,
        "message": "<human-readable>"
    }

We use 429 (Too Many Requests) rather than 402 (Payment Required)
because the limit is a per-month *rate*, not an unpaid invoice — 429
is what every billing-aware SaaS lands on for quota-exhaustion.

Token-counting note
-------------------
The up-front ``enforce_and_record_query`` call passes ``tokens_input=0``
/ ``tokens_output=0`` because we haven't called the LLM yet. After the
chat stream finishes, the route calls ``update_usage_event_tokens`` to
patch the row with the real aggregate from ``ChatSession.last_turn_usage``.
The two-step pattern (record up front, update after) keeps quota
enforcement strictly *before* the LLM call (so a user over their limit
can never run a free LLM call) while still recording accurate per-event
token counts in the audit trail.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from advisor.db.models import UsageEvent, User
from advisor.db.quota import QuotaExceeded, record_query


def enforce_and_record_query(
    db: Session,
    user: User,
    *,
    event_type: str = "llm_call",
    session_id: int | None = None,
    model: str | None = None,
    provider: str | None = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_estimate_cents: int = 0,
) -> UsageEvent:
    """Charge one query against the user's monthly budget or 429.

    Wraps ``advisor.db.quota.record_query``: on ``QuotaExceeded`` the
    audit row has already been staged on the session by
    ``record_query`` (an exceedance event), and we translate the
    Python exception into the contracted HTTP shape. The caller is
    expected to commit ``db`` after this returns successfully — and
    on the 429 path, the caller should still commit so the
    ``monthly_quota_exceeded`` audit row lands in the DB.
    """
    try:
        return record_query(
            db,
            user,
            event_type=event_type,
            session_id=session_id,
            model=model,
            provider=provider,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_estimate_cents=cost_estimate_cents,
        )
    except QuotaExceeded as exc:
        # Commit the staged exceedance event before we raise —
        # otherwise the audit trail of the rejection is lost when
        # the request handler unwinds. ``record_query`` only stages;
        # we do the commit here because we own the response.
        db.commit()
        raise HTTPException(
            status_code=429,
            detail={
                "code": _CODE_BY_KIND[exc.kind],
                "kind": exc.kind,
                "limit": exc.limit,
                "used": exc.used,
                "message": _MESSAGE_BY_KIND[exc.kind].format(
                    used=exc.used, limit=exc.limit
                ),
            },
        ) from exc


_CODE_BY_KIND: dict[str, str] = {
    "queries": "monthly_quota_exceeded",
    "input_tokens": "monthly_input_token_limit_exceeded",
    "output_tokens": "monthly_output_token_limit_exceeded",
    "rpm": "rate_limit_exceeded",
}


_MESSAGE_BY_KIND: dict[str, str] = {
    "queries": (
        "Monthly query limit reached ({used}/{limit}). Wait for the next "
        "billing cycle or ask your admin to raise your cap."
    ),
    "input_tokens": (
        "Monthly input-token limit reached ({used}/{limit}). Wait for the "
        "next billing cycle or ask your admin to raise your cap."
    ),
    "output_tokens": (
        "Monthly output-token limit reached ({used}/{limit}). Wait for the "
        "next billing cycle or ask your admin to raise your cap."
    ),
    "rpm": (
        "Too many requests ({used}/{limit} per minute). Slow down and try "
        "again in a moment."
    ),
}


def update_usage_event_tokens(
    db: Session,
    *,
    usage_event_id: int,
    tokens_input: int,
    tokens_output: int,
    metadata: dict | None = None,
) -> None:
    """Patch a previously recorded ``UsageEvent`` with real token counts.

    Called from the chat route after the LLM stream finishes — by that
    point ``ChatSession.last_turn_usage`` carries the aggregate counts
    that weren't known at quota-enforcement time. A missing row is
    silently ignored; the most likely cause is that the up-front
    ``record_query`` raised before the row was committed, in which
    case there's nothing to update.

    ``metadata``, when provided, is shallow-merged into the row's
    existing ``metadata_json``. The chat route uses this to attach
    cost-circuit trip details (estimated input tokens, budget,
    iteration) on turns where ``ChatSession.last_turn_circuit_trip``
    is set, so the trip is queryable in analytics without inventing a
    separate audit row.
    """
    row = db.get(UsageEvent, usage_event_id)
    if row is None:
        return
    # Track the delta so we can bump the user's denormalised counters
    # by the same amount. ``enforce_and_record_query`` was called
    # up-front with (0, 0) most of the time, so the delta equals the
    # new values — but if the caller later patches a row twice, the
    # delta logic keeps the user counter consistent.
    delta_input = tokens_input - (row.tokens_input or 0)
    delta_output = tokens_output - (row.tokens_output or 0)
    row.tokens_input = tokens_input
    row.tokens_output = tokens_output
    if metadata:
        # ``metadata_json`` is a MutableDict; mutating it in place lets
        # SQLAlchemy track the change. Shallow merge keeps existing
        # keys (set up front by ``enforce_and_record_query``) intact.
        existing = row.metadata_json or {}
        existing.update(metadata)
        row.metadata_json = existing

    if delta_input or delta_output:
        user = db.get(User, row.user_id)
        if user is not None:
            if delta_input:
                user.monthly_input_tokens_used = max(
                    0, user.monthly_input_tokens_used + delta_input
                )
            if delta_output:
                user.monthly_output_tokens_used = max(
                    0, user.monthly_output_tokens_used + delta_output
                )
