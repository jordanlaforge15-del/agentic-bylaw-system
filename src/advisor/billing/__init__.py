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

   The launch product is the **priced-question catalog** (see
   ``questions.py``), not packs. For it, create one one-time Price per
   catalog question — its display amount MUST equal the question's
   ``price_cents`` — and set the matching ``STRIPE_PRICE_QUESTION_<SLUG>``
   env var (5 total, e.g. ``STRIPE_PRICE_QUESTION_PERMITTED_USE``). The
   pure per-question model has no pack SKUs and no credit ledger.

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
from advisor.billing.questions import (
    QUESTION_DEVELOPMENT_STANDARDS_DEF,
    QUESTION_DUE_DILIGENCE_DEF,
    QUESTION_LEGAL_NONCONFORMING_DEF,
    QUESTION_ORDER,
    QUESTION_PERMITTED_USE_DEF,
    QUESTION_VARIANCE_JUSTIFICATION_DEF,
    QUESTIONS,
    InputField,
    Question,
    all_questions,
    question_for,
    question_for_stripe_price_id,
)
from advisor.billing.router import (
    build_billing_router,
    build_dormant_billing_router,
)
from advisor.billing.settings import AdvisorBillingSettings, get_settings
from advisor.billing.topups import (
    TOPUP_LARGE_DEF,
    TOPUP_MEDIUM_DEF,
    TOPUP_SMALL_DEF,
    TOPUPS,
    Topup,
    all_topups,
    topup_for,
    topup_for_stripe_price_id,
)
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
    "QUESTIONS",
    "QUESTION_DEVELOPMENT_STANDARDS_DEF",
    "QUESTION_DUE_DILIGENCE_DEF",
    "QUESTION_LEGAL_NONCONFORMING_DEF",
    "QUESTION_ORDER",
    "QUESTION_PERMITTED_USE_DEF",
    "QUESTION_VARIANCE_JUSTIFICATION_DEF",
    "InputField",
    "Question",
    "all_questions",
    "question_for",
    "question_for_stripe_price_id",
    "StripeClient",
    "StripeCustomer",
    "StripeEvent",
    "StripeSubscriptionItem",
    "TIERS",
    "TIER_COMPLEX_DEF",
    "TIER_QUICK_DEF",
    "TIER_STANDARD_DEF",
    "Tier",
    "TOPUPS",
    "TOPUP_LARGE_DEF",
    "TOPUP_MEDIUM_DEF",
    "TOPUP_SMALL_DEF",
    "Topup",
    "WebhookResult",
    "all_offers",
    "all_topups",
    "topup_for",
    "topup_for_stripe_price_id",
    "build_billing_router",
    "build_dormant_billing_router",
    "get_pricing_settings",
    "get_settings",
    "handle_event",
    "offer_for",
    "pack_for_stripe_price_id",
]
