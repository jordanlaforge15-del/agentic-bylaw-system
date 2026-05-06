"""Checkout-session creation helper.

The router calls into here when a user clicks "upgrade". The helper
resolves the target plan, looks up the configured Stripe Price ID,
and asks the ``StripeClient`` to mint a Checkout session with the
metadata we need to resolve the resulting webhook back to our user.

Why ``advisor_user_id`` is in metadata: Stripe lets us put arbitrary
key/value pairs on a Checkout session and (via
``subscription_data.metadata``) on the resulting subscription. We use
that to round-trip our internal user id, so the webhook handler
doesn't have to guess from email which Clerk user owns this customer
(emails change; clerk_user_id is also stable but adds a join step).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from advisor.billing.client import StripeClient
from advisor.billing.plans import PLAN_FREE, PLANS_BY_TIER
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.models import User


class UnknownTierError(ValueError):
    """Raised when the caller asks for a tier that isn't in the
    catalog. The router translates this to HTTP 400."""


class FreeTierCheckoutError(ValueError):
    """Raised when the caller asks for the free tier — there's no
    Stripe price to charge against. The router translates to 400."""


class PriceNotConfiguredError(RuntimeError):
    """Raised when the requested tier has no configured Stripe Price
    ID on settings. Distinct from UnknownTierError because this is an
    operator-misconfiguration error, not a user error. Router maps to
    HTTP 503 — the operator must finish wiring billing before this
    tier becomes purchasable."""


def start_checkout(
    db: Session,
    user: User,
    *,
    target_tier: str,
    client: StripeClient,
    settings: AdvisorBillingSettings,
) -> str:
    """Create a Stripe Checkout session and return the redirect URL.

    Side effects: none on the database. The user record is updated
    only after we receive the ``checkout.session.completed`` webhook
    — that way an abandoned checkout doesn't leave the user record in
    a half-upgraded state.

    The metadata dict we send to Stripe (``advisor_user_id`` +
    ``target_tier``) is what the webhook reads back to apply the
    upgrade. We deliberately don't rely on ``customer_email`` for
    user resolution because emails are mutable and not unique in
    Clerk.
    """
    plan = PLANS_BY_TIER.get(target_tier)
    if plan is None:
        raise UnknownTierError(
            f"unknown plan tier {target_tier!r}; valid: "
            f"{sorted(PLANS_BY_TIER)}"
        )
    if plan is PLAN_FREE:
        raise FreeTierCheckoutError(
            "cannot start checkout for the free tier; there is no Stripe "
            "price. Use the subscription-cancellation flow to downgrade "
            "an existing subscription."
        )

    price_id = _resolve_price_id(plan.stripe_price_env_var, settings)
    if not price_id:
        raise PriceNotConfiguredError(
            f"no Stripe Price ID configured for tier {plan.tier!r}; set "
            f"{plan.stripe_price_env_var} in the environment."
        )

    metadata = {
        "advisor_user_id": str(user.id),
        "target_tier": plan.tier,
    }
    result = client.create_checkout_session(
        customer_id=user.stripe_customer_id,
        customer_email=user.email,
        price_id=price_id,
        success_url=settings.success_url,
        cancel_url=settings.cancel_url,
        metadata=metadata,
    )
    return result.url


def _resolve_price_id(
    env_var: str | None, settings: AdvisorBillingSettings
) -> str | None:
    if env_var is None:
        return None
    # Match the alias-to-attribute convention from the settings model:
    # STRIPE_PRICE_PRO -> stripe_price_pro.
    return getattr(settings, env_var.lower(), None)
