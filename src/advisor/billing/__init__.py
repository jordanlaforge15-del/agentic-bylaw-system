"""Stripe billing for the Halifax Bylaw Advisor SaaS.

This module is **dormant by default**. It is built so the rest of the
application can be developed and tested with no Stripe account, no
API keys, and no network access. The dormant-vs-active toggle is a
single env var:

    ADVISOR_BILLING_ENABLED=true

When unset (or set to ``false``), the FastAPI app still mounts the
billing router, but every endpoint returns HTTP 503 — that way the
frontend can probe ``/v1/billing/me`` without the backend exploding,
and the operator can flip a feature flag the moment a Stripe account
exists.

What you need to do when your Stripe account is ready
-----------------------------------------------------

1. Create monthly recurring Prices in Stripe for each paid tier and
   capture their Price IDs (``price_...``).
2. Set the env vars:

   * ``ADVISOR_BILLING_ENABLED=true``
   * ``STRIPE_API_KEY=sk_live_...`` (or ``sk_test_...``)
   * ``STRIPE_WEBHOOK_SECRET=whsec_...``
   * ``STRIPE_PRICE_PRO=price_...``
   * ``STRIPE_PRICE_TEAM=price_...``
   * (optional) ``ADVISOR_BILLING_SUCCESS_URL`` /
     ``ADVISOR_BILLING_CANCEL_URL`` for the post-checkout redirects.
3. In the Stripe dashboard, create a webhook endpoint pointing at
   ``POST /v1/billing/webhook`` and subscribe to:

   * ``checkout.session.completed``
   * ``customer.subscription.updated``
   * ``customer.subscription.deleted``

4. ``pip install stripe`` (or rely on the entry in ``pyproject.toml``
   — the SDK is lazy-imported, so the rest of the app works without
   it).

The plan catalog (tier names + monthly query limits) lives in
``plans.py`` and is the single source of truth. Adjust there, not in
env or in the database.
"""
from advisor.billing.client import (
    CheckoutSessionResult,
    LiveStripeClient,
    MockStripeClient,
    StripeClient,
    StripeCustomer,
    StripeEvent,
    StripeSubscriptionItem,
)
from advisor.billing.plans import (
    PLAN_FREE,
    PLAN_PRO,
    PLAN_TEAM,
    PLANS_BY_TIER,
    BillingPlan,
    plan_for_stripe_price_id,
)
from advisor.billing.router import build_billing_router, build_dormant_billing_router
from advisor.billing.settings import AdvisorBillingSettings, get_settings
from advisor.billing.webhooks import WebhookResult, handle_event

__all__ = [
    "AdvisorBillingSettings",
    "BillingPlan",
    "CheckoutSessionResult",
    "LiveStripeClient",
    "MockStripeClient",
    "PLAN_FREE",
    "PLAN_PRO",
    "PLAN_TEAM",
    "PLANS_BY_TIER",
    "StripeClient",
    "StripeCustomer",
    "StripeEvent",
    "StripeSubscriptionItem",
    "WebhookResult",
    "build_billing_router",
    "build_dormant_billing_router",
    "get_settings",
    "handle_event",
    "plan_for_stripe_price_id",
]
