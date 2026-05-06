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
Callers currently pass ``tokens_input=0`` / ``tokens_output=0`` because
real per-turn token accounting is a follow-up. The call site is wired
end-to-end so the audit row exists with ``event_type="llm_call"``;
plumbing the actual token counts through the synthetic-streaming path
in ``advisor.chat.session`` is the next deliverable. See the chat
route in ``advisor.api.app`` for the call site.
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
        # Commit the staged ``monthly_quota_exceeded`` event before we
        # raise — otherwise the audit trail of the rejection is lost
        # when the request handler unwinds. ``record_query`` only
        # stages; we do the commit here because we own the response.
        db.commit()
        raise HTTPException(
            status_code=429,
            detail={
                "code": "monthly_quota_exceeded",
                "limit": exc.limit,
                "used": exc.used,
                "message": (
                    f"Monthly query limit reached ({exc.used}/{exc.limit}). "
                    "Upgrade your plan or wait for the next billing cycle."
                ),
            },
        ) from exc
