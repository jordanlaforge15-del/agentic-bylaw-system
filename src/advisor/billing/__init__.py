"""Stripe billing for the Halifax Bylaw Advisor SaaS — case-credit model.

This module is **dormant by default**. It is built so the rest of the
application can be developed and tested with no Stripe account, no
API keys, and no network access. The dormant-vs-active toggle is a
single env var:

    ADVISOR_BILLING_ENABLED=true

When unset (or set to ``false``), the FastAPI app still mounts the
billing router, but every endpoint except ``GET /v1/billing/catalog``
returns HTTP 503 — that way the pricing page can render without the
backend exploding, and the operator can flip a feature flag the
moment a Stripe account exists.

Cost model
----------
The product sells **case credits**, not subscriptions. A case is one
inquiry tied to a property / project / development application. There
are three tiers (Quick / Standard / Complex) and four pack SKUs
(PAYG / Starter / Pro / Enterprise) — 12 offers total. See
``packs.py`` for the catalog and ``cases.py`` for the lifecycle
service.

What you need to do when your Stripe account is ready
-----------------------------------------------------

1. Create one-time Prices in Stripe for each (tier, pack) combination
   you want to sell. The display price for each Price MUST equal the
   ``amount_due_cents`` from ``advisor.billing.packs`` for that offer
   — there's no sync mechanism, so a mismatch silently overcharges or
   undercharges.

2. Set the env vars (12 ``STRIPE_PRICE_<TIER>_<PACK>`` IDs, all
   uppercase):

   * ``ADVISOR_BILLING_ENABLED=true``
   * ``STRIPE_API_KEY=sk_live_...`` (or ``sk_test_...``)
   * ``STRIPE_WEBHOOK_SECRET=whsec_...``
   * ``STRIPE_PRICE_QUICK_PAYG=price_...``
     (and 11 more — see ``settings.py``).

3. In the Stripe dashboard, create a webhook endpoint pointing at
   ``POST /v1/billing/webhook`` and subscribe to:

   * ``checkout.session.completed``

   (Subscription events are no longer used; do NOT subscribe to them
   — they will be ignored, but any noise in the dashboard is wasted.)

4. ``pip install stripe`` (or rely on the entry in ``pyproject.toml``
   — the SDK is lazy-imported, so the rest of the app works without
   it).

The catalog (tier token budgets, prices, pack discounts) lives in
``packs.py`` and is the single source of truth. Adjust there, not in
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
from advisor.billing.packs import (
    PACK_ENTERPRISE_DEF,
    PACK_PAYG_DEF,
    PACK_PRO_DEF,
    PACK_STARTER_DEF,
    PACKS,
    TIER_COMPLEX_DEF,
    TIER_QUICK_DEF,
    TIER_STANDARD_DEF,
    TIERS,
    Pack,
    PackOffer,
    Tier,
    all_offers,
    offer_for,
    pack_for_stripe_price_id,
)
from advisor.billing.pricing import (
    AdvisorPricingSettings,
    get_pricing_settings,
)
from advisor.billing.router import (
    build_billing_router,
    build_dormant_billing_router,
)
from advisor.billing.settings import AdvisorBillingSettings, get_settings
from advisor.billing.webhooks import WebhookResult, handle_event

__all__ = [
    "AdvisorBillingSettings",
    "AdvisorPricingSettings",
    "CheckoutSessionResult",
    "LiveStripeClient",
    "MockStripeClient",
    "PACK_ENTERPRISE_DEF",
    "PACK_PAYG_DEF",
    "PACK_PRO_DEF",
    "PACK_STARTER_DEF",
    "PACKS",
    "Pack",
    "PackOffer",
    "StripeClient",
    "StripeCustomer",
    "StripeEvent",
    "StripeSubscriptionItem",
    "TIERS",
    "TIER_COMPLEX_DEF",
    "TIER_QUICK_DEF",
    "TIER_STANDARD_DEF",
    "Tier",
    "WebhookResult",
    "all_offers",
    "build_billing_router",
    "build_dormant_billing_router",
    "get_pricing_settings",
    "get_settings",
    "handle_event",
    "offer_for",
    "pack_for_stripe_price_id",
]
