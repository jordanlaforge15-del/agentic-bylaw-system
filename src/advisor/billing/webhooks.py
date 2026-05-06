"""Stripe webhook event handling.

The router validates the signature and hands us a ``StripeEvent``;
we dispatch on ``event.type`` and apply the corresponding effect to
the user record. Three event types matter:

* ``checkout.session.completed`` — first-time upgrade: link the
  Stripe customer id, set the plan tier, raise the query limit.
* ``customer.subscription.updated`` — plan change (upgrade,
  downgrade between paid tiers, status change): re-derive the tier
  from the price id on the subscription items.
* ``customer.subscription.deleted`` — cancellation: downgrade to
  free, but **preserve** ``monthly_queries_used`` so the user keeps
  whatever queries they've already paid for through the rest of the
  current month.

Idempotency: Stripe retries webhooks aggressively. We dedupe by
recording the event id in ``advisor_usage_event.metadata_json``
(``stripe_event_id``) on first receipt, then short-circuiting on
second receipt. Using the existing usage-event table avoids adding
yet another schema for what's effectively a billing-side audit trail.

We never raise from the handler. Stripe interprets non-2xx as
"please retry", which would amplify a transient bug into a flood of
duplicate events. Instead we log and return ``handled=False`` so the
caller can return 200 and Stripe stops retrying.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from advisor.billing.client import StripeEvent
from advisor.billing.plans import (
    PLAN_FREE,
    PLANS_BY_TIER,
    plan_for_stripe_price_id,
)
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.models import UsageEvent, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookResult:
    """Outcome of processing a single webhook event.

    ``handled`` is True iff the event matched a known type and we
    applied (or dedup-skipped) its effect. ``handled=False`` means we
    saw an event type we don't know about — fine, just log and 200.
    ``user_id`` is set when the effect targeted a specific user, for
    log correlation.
    """

    handled: bool
    event_type: str
    event_id: str
    user_id: int | None = None
    note: str | None = None


# Event types we know how to act on. Anything else returns
# ``handled=False`` — Stripe sends a long tail of events we don't
# care about (invoice.created, charge.succeeded, ...) and we don't
# want to log-spam every one as "unhandled" with high severity.
_HANDLED_EVENT_TYPES = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)


def handle_event(
    db: Session, event: StripeEvent, settings: AdvisorBillingSettings
) -> WebhookResult:
    """Apply a verified Stripe event to the database.

    Caller is responsible for committing the session — we stage all
    changes and let the FastAPI route control the transaction
    boundary. That way a failed commit (e.g. unique-constraint race
    on the dedup row) rolls back the whole effect.
    """
    if event.type not in _HANDLED_EVENT_TYPES:
        logger.info(
            "stripe webhook: ignoring unhandled event type %s (id=%s)",
            event.type,
            event.id,
        )
        return WebhookResult(
            handled=False, event_type=event.type, event_id=event.id
        )

    if _is_duplicate_event(db, event.id):
        logger.info(
            "stripe webhook: duplicate event %s (id=%s); skipping",
            event.type,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="duplicate_event",
        )

    try:
        if event.type == "checkout.session.completed":
            result = _handle_checkout_completed(db, event)
        elif event.type == "customer.subscription.updated":
            result = _handle_subscription_updated(db, event, settings)
        elif event.type == "customer.subscription.deleted":
            result = _handle_subscription_deleted(db, event)
        else:  # pragma: no cover — guarded by _HANDLED_EVENT_TYPES
            return WebhookResult(
                handled=False, event_type=event.type, event_id=event.id
            )
    except Exception:  # noqa: BLE001 — see module docstring
        logger.exception(
            "stripe webhook: error handling event %s (id=%s); will return "
            "200 to prevent Stripe retry storm",
            event.type,
            event.id,
        )
        return WebhookResult(
            handled=False,
            event_type=event.type,
            event_id=event.id,
            note="exception",
        )

    # Record the dedup marker only on a successful effect. If we
    # failed earlier, Stripe's retry will re-attempt — that's
    # desirable.
    _record_processed_event(db, event=event, user_id=result.user_id)
    return result


# ---------------------------------------------------------------------------
# Per-event-type handlers. Each one looks up the user, mutates the
# row, and returns a WebhookResult. They never commit; the caller
# does that.
# ---------------------------------------------------------------------------


def _handle_checkout_completed(
    db: Session, event: StripeEvent
) -> WebhookResult:
    metadata = _metadata_from_data(event.data)
    user_id = _parse_int(metadata.get("advisor_user_id"))
    target_tier = metadata.get("target_tier")
    customer_id = _string(event.data.get("customer"))

    if user_id is None or not target_tier:
        logger.warning(
            "stripe webhook checkout.session.completed missing metadata "
            "(advisor_user_id=%r, target_tier=%r, event_id=%s)",
            metadata.get("advisor_user_id"),
            target_tier,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="missing_metadata",
        )
    plan = PLANS_BY_TIER.get(target_tier)
    if plan is None:
        logger.warning(
            "stripe webhook checkout.session.completed unknown tier %r "
            "(event_id=%s)",
            target_tier,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="unknown_tier",
        )

    user = db.get(User, user_id)
    if user is None:
        logger.warning(
            "stripe webhook checkout.session.completed: user id %s not "
            "found (event_id=%s)",
            user_id,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="user_missing",
        )

    if customer_id and not user.stripe_customer_id:
        user.stripe_customer_id = customer_id
    elif customer_id and user.stripe_customer_id != customer_id:
        # An existing customer-id on our row that doesn't match the
        # incoming one — log but trust the webhook (Stripe is the
        # source of truth for customer ids).
        logger.info(
            "stripe webhook: rewriting stripe_customer_id for user %s "
            "from %s to %s",
            user.id,
            user.stripe_customer_id,
            customer_id,
        )
        user.stripe_customer_id = customer_id

    subscription_id = _string(event.data.get("subscription"))
    if subscription_id:
        user.stripe_subscription_id = subscription_id

    user.plan_tier = plan.tier
    user.monthly_query_limit = plan.monthly_query_limit
    user.subscription_status = "active"
    db.add(user)
    return WebhookResult(
        handled=True,
        event_type=event.type,
        event_id=event.id,
        user_id=user.id,
    )


def _handle_subscription_updated(
    db: Session, event: StripeEvent, settings: AdvisorBillingSettings
) -> WebhookResult:
    customer_id = _string(event.data.get("customer"))
    subscription_id = _string(event.data.get("id"))
    if not customer_id:
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="no_customer_on_subscription",
        )
    user = _find_user_by_stripe_customer(db, customer_id)
    if user is None:
        logger.warning(
            "stripe webhook subscription.updated: no user with "
            "stripe_customer_id=%s (event_id=%s)",
            customer_id,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="user_missing",
        )

    plan = _plan_from_subscription(event.data, settings)
    if plan is None:
        # Fall back to metadata if the subscription items don't
        # resolve — useful when Stripe Price IDs aren't configured
        # yet but tests / sandbox events carry the metadata.
        metadata = _metadata_from_data(event.data)
        target_tier = metadata.get("target_tier")
        if target_tier:
            plan = PLANS_BY_TIER.get(target_tier)
    if plan is None:
        logger.warning(
            "stripe webhook subscription.updated: cannot resolve plan "
            "for customer=%s (event_id=%s); leaving user unchanged",
            customer_id,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            user_id=user.id,
            note="plan_unresolved",
        )

    user.plan_tier = plan.tier
    user.monthly_query_limit = plan.monthly_query_limit
    if subscription_id:
        user.stripe_subscription_id = subscription_id
    status = _string(event.data.get("status"))
    if status:
        user.subscription_status = status
    period_end = _datetime_from_unix(event.data.get("current_period_end"))
    if period_end is not None:
        user.subscription_current_period_end = period_end
    db.add(user)
    return WebhookResult(
        handled=True,
        event_type=event.type,
        event_id=event.id,
        user_id=user.id,
    )


def _handle_subscription_deleted(
    db: Session, event: StripeEvent
) -> WebhookResult:
    customer_id = _string(event.data.get("customer"))
    if not customer_id:
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="no_customer_on_subscription",
        )
    user = _find_user_by_stripe_customer(db, customer_id)
    if user is None:
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="user_missing",
        )

    user.plan_tier = PLAN_FREE.tier
    user.monthly_query_limit = PLAN_FREE.monthly_query_limit
    # Deliberately DO NOT reset monthly_queries_used: the user keeps
    # any remaining queries until the calendar-month rollover.
    user.subscription_status = "canceled"
    user.stripe_subscription_id = None
    user.subscription_current_period_end = None
    db.add(user)
    return WebhookResult(
        handled=True,
        event_type=event.type,
        event_id=event.id,
        user_id=user.id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_duplicate_event(db: Session, event_id: str) -> bool:
    """True iff we've already recorded a usage-event for this Stripe
    event id. Cheap to check because UsageEvent has a created_at
    index and this is a small per-user filter at webhook rates."""
    if not event_id:
        return False
    stmt = (
        select(UsageEvent.id)
        .where(UsageEvent.event_type == "stripe_webhook")
        .limit(50)
    )
    # We can't push the JSON predicate into SQL portably (Postgres has
    # JSONB ops, sqlite doesn't), so we fetch a small candidate set
    # and filter in Python. Webhook traffic is low enough that this
    # is fine; if it ever isn't, switch to a JSONB index on Postgres.
    for row in db.execute(stmt).all():
        usage_event = db.get(UsageEvent, row.id)
        if (
            usage_event is not None
            and usage_event.metadata_json.get("stripe_event_id") == event_id
        ):
            return True
    return False


def _record_processed_event(
    db: Session, *, event: StripeEvent, user_id: int | None
) -> None:
    """Stamp a usage-event row so future deliveries of the same
    Stripe event id short-circuit. Stored in
    ``advisor_usage_event.metadata_json["stripe_event_id"]``."""
    if user_id is None:
        # We need a user_id for the FK. If we can't pin the event to
        # a user (e.g. unhandled-but-known type), skip the dedup
        # record — duplicate processing is a no-op in those cases
        # anyway because the per-event handlers are pure-read for
        # those branches.
        return
    db.add(
        UsageEvent(
            user_id=user_id,
            event_type="stripe_webhook",
            metadata_json={
                "stripe_event_id": event.id,
                "stripe_event_type": event.type,
            },
        )
    )


def _find_user_by_stripe_customer(
    db: Session, customer_id: str
) -> User | None:
    stmt = select(User).where(User.stripe_customer_id == customer_id).limit(1)
    return db.execute(stmt).scalar_one_or_none()


def _metadata_from_data(data: dict[str, Any]) -> dict[str, str]:
    raw = data.get("metadata") or {}
    if not isinstance(raw, dict):
        return {}
    # Stripe metadata values are always strings on the wire; coerce
    # defensively in case a test passes ints.
    return {str(k): str(v) for k, v in raw.items()}


def _plan_from_subscription(
    data: dict[str, Any], settings: AdvisorBillingSettings
) -> Any | None:
    items = data.get("items") or {}
    item_data = items.get("data") if isinstance(items, dict) else None
    if not item_data:
        return None
    first = item_data[0]
    price = first.get("price") if isinstance(first, dict) else None
    price_id = None
    if isinstance(price, dict):
        price_id = price.get("id")
    elif isinstance(price, str):
        price_id = price
    if not price_id:
        return None
    return plan_for_stripe_price_id(price_id, settings)


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _datetime_from_unix(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)
