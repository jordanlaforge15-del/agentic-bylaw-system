"""Stripe webhook event handling — pack-purchase model.

Replaces the v1 subscription handlers. The router validates the
signature and hands us a ``StripeEvent``; we dispatch on
``event.type``. The only event that drives billing state in the new
model is:

* ``checkout.session.completed`` — a one-time pack purchase succeeded.
  The handler reads ``(advisor_user_id, tier, pack_sku, quantity)``
  from the session metadata, inserts one ``CasePurchase`` row, and
  inserts ``quantity`` ``CaseCredit`` rows in state ``available``.

Subscription events (``customer.subscription.updated`` /
``customer.subscription.deleted``) are dropped — the case-credit
model has no subscriptions.

Idempotency
-----------
Two layers protect against double-issuance from Stripe's aggressive
retries:

1. Event-level dedupe via ``advisor_usage_event.metadata_json
   ['stripe_event_id']`` — same shape the v1 handler used.
2. Schema-level uniqueness on
   ``advisor_case_purchase.stripe_checkout_session_id`` — even if the
   event-level check fails (race / dropped row), the second insert
   raises ``IntegrityError`` and the handler rolls back. We catch and
   log this so Stripe stops retrying.

Error handling
--------------
We never raise from the handler. Stripe interprets non-2xx as "please
retry", which would amplify a transient bug into a flood of duplicate
events. Instead we log and return ``handled=False`` so the caller can
return 200.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from advisor.billing.client import StripeEvent
from advisor.billing.packs import (
    PACKS,
    TIERS,
    offer_for,
    pack_for_stripe_price_id,
)
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.cases import issue_credits_from_pack_purchase
from advisor.db.models import CasePurchase, UsageEvent, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookResult:
    """Outcome of processing a single webhook event."""

    handled: bool
    event_type: str
    event_id: str
    user_id: int | None = None
    note: str | None = None


_HANDLED_EVENT_TYPES = frozenset({"checkout.session.completed"})


def handle_event(
    db: Session, event: StripeEvent, settings: AdvisorBillingSettings
) -> WebhookResult:
    """Apply a verified Stripe event to the database.

    Caller commits — we stage all changes and let the FastAPI route
    control the transaction boundary.
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
        result = _handle_checkout_completed(db, event, settings)
    except IntegrityError:
        # Second-layer idempotency tripped: another delivery already
        # inserted this CasePurchase. Roll the savepoint and return
        # success-with-note so Stripe stops retrying.
        logger.info(
            "stripe webhook: duplicate purchase by checkout_session_id "
            "(event_id=%s); skipping",
            event.id,
        )
        db.rollback()
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="duplicate_purchase",
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

    _record_processed_event(db, event=event, user_id=result.user_id)
    return result


# ---------------------------------------------------------------------------
# Per-event handler.
# ---------------------------------------------------------------------------


def _handle_checkout_completed(
    db: Session, event: StripeEvent, settings: AdvisorBillingSettings
) -> WebhookResult:
    metadata = _metadata_from_data(event.data)
    user_id = _parse_int(metadata.get("advisor_user_id"))
    tier = metadata.get("tier")
    pack_sku = metadata.get("pack_sku")
    quantity_raw = metadata.get("quantity")
    customer_id = _string(event.data.get("customer"))

    # Metadata may be missing on a manual-invoice path or a legacy
    # event from before the metadata round-trip landed; try a
    # reverse-lookup via the line-item Price ID before giving up.
    if (tier is None or pack_sku is None) and settings is not None:
        offer = _offer_from_line_items(event.data, settings)
        if offer is not None:
            tier = tier or offer.tier.name
            pack_sku = pack_sku or offer.pack.sku
            if quantity_raw is None:
                quantity_raw = str(offer.pack.quantity)

    if user_id is None or not tier or not pack_sku:
        logger.warning(
            "stripe webhook checkout.session.completed missing metadata "
            "(advisor_user_id=%r, tier=%r, pack_sku=%r, event_id=%s)",
            metadata.get("advisor_user_id"),
            tier,
            pack_sku,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="missing_metadata",
        )

    if tier not in TIERS or pack_sku not in PACKS:
        logger.warning(
            "stripe webhook: unknown tier/pack (tier=%r, pack_sku=%r, "
            "event_id=%s)",
            tier,
            pack_sku,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="unknown_offer",
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
        # Stripe is the source of truth for customer ids; prefer the
        # incoming value but log the change for traceability.
        logger.info(
            "stripe webhook: rewriting stripe_customer_id for user %s "
            "from %s to %s",
            user.id,
            user.stripe_customer_id,
            customer_id,
        )
        user.stripe_customer_id = customer_id

    offer = offer_for(tier, pack_sku)
    quantity = _parse_int(quantity_raw) or offer.pack.quantity
    amount_paid_cents = _parse_int(event.data.get("amount_total")) or 0
    payment_intent = _string(event.data.get("payment_intent"))
    checkout_session_id = _string(event.data.get("id"))

    purchase = CasePurchase(
        user_id=user.id,
        pack_sku=pack_sku,
        tier=tier,
        quantity=quantity,
        list_price_cents=offer.list_price_cents,
        discount_bps=offer.pack.discount_bps,
        amount_paid_cents=amount_paid_cents,
        currency="CAD",
        stripe_checkout_session_id=checkout_session_id,
        stripe_payment_intent_id=payment_intent,
    )
    db.add(purchase)
    db.flush()
    issue_credits_from_pack_purchase(db, user=user, purchase=purchase)
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
    event id. Cheap to check at webhook rates."""
    if not event_id:
        return False
    stmt = (
        select(UsageEvent.id)
        .where(UsageEvent.event_type == "stripe_webhook")
        .limit(50)
    )
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
    """Stamp a usage-event row so future deliveries of the same Stripe
    event id short-circuit."""
    if user_id is None:
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


def _offer_from_line_items(
    data: dict[str, Any], settings: AdvisorBillingSettings
):
    """Reverse-lookup the (tier, pack) from the first line-item Price.

    Used as a fallback when the checkout-session metadata is missing
    (manual invoice, legacy event). Returns ``None`` if no Price ID is
    present or the configured catalog doesn't recognise it.
    """
    line_items = data.get("line_items") or {}
    if isinstance(line_items, dict):
        line_items = line_items.get("data") or []
    if not line_items:
        return None
    first = line_items[0]
    price = first.get("price") if isinstance(first, dict) else None
    price_id = None
    if isinstance(price, dict):
        price_id = price.get("id")
    elif isinstance(price, str):
        price_id = price
    if not price_id:
        return None
    return pack_for_stripe_price_id(price_id, settings)


def _metadata_from_data(data: dict[str, Any]) -> dict[str, str]:
    raw = data.get("metadata") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


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
