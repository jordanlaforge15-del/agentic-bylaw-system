"""Case-pack catalog — single source of truth for tiers and pack SKUs.

Replaces the subscription-era ``plans.py``. Lives in code (not env, not
DB) for the same reason ``plans.py`` did: tier names, token budgets,
prices, and pack discounts are product copy. Changing them is a code
review + test + deploy, not an ops twiddle.

The mapping ``Stripe Price ID -> (tier, pack_sku)`` is environment-
specific (one set in test mode, another in live mode), so it lives on
``AdvisorBillingSettings``. The ``pack_for_stripe_price_id`` helper does
the reverse lookup at webhook time.

Tiers
-----
Three case tiers, ordered by token budget and price:

* ``quick`` — 12k token budget, $12.50 CAD. Single-property zoning
  lookups, permitted-use checks. ~4-6 retrieval rounds.
* ``standard`` — 45k token budget, $32.50 CAD. Variance research, multi-
  bylaw cross-references, development-standards lookups. ~12-18 rounds.
* ``complex`` — 130k token budget, $75 CAD. Multi-property files,
  rezoning, deep overlay-zone analysis. ~35-50 rounds.

Token budgets are CUMULATIVE (input + output) across all sessions in
the case within the 30-day window. Margin against API cost is 98%+ at
all tiers — pricing captures value delivered, not cost incurred.

Pack SKUs
---------
Four ways to buy any tier:

* ``payg`` — quantity 1, no discount. The default "buy a credit" flow.
* ``starter`` — quantity 5, 5% discount. Light-use professionals.
* ``pro`` — quantity 20, 15% discount. Mid-volume practices.
* ``enterprise`` — quantity 100, 25% discount. Firm-wide volume.

A Pro pack of Standard is 20 individual ``case_credit`` rows, each at
``tier="standard"`` and ``source="pro"`` — the brief mandates per-credit
storage so we can run atomic tier-upgrade swaps and per-tier analytics
without an aggregate-balance hack.
"""
from __future__ import annotations

from dataclasses import dataclass


# Tier identifiers — persisted on ``advisor_case.current_tier``,
# ``advisor_case_credit.tier``, ``advisor_chat_session.tier``. Never
# rename without a data migration; values are stored as strings.
TIER_QUICK = "quick"
TIER_STANDARD = "standard"
TIER_COMPLEX = "complex"

# Pack SKU identifiers — persisted on ``advisor_case_purchase.pack_sku``
# and ``advisor_case_credit.source``. ``admin_grant`` and ``upgrade`` are
# also valid ``source`` values but never appear as ``pack_sku`` (those
# don't go through Stripe).
PACK_PAYG = "payg"
PACK_STARTER = "starter"
PACK_PRO = "pro"
PACK_ENTERPRISE = "enterprise"


@dataclass(frozen=True)
class Tier:
    """One case tier — what the user gets when they spend a credit.

    Attributes:
        name: Stable identifier (``quick`` / ``standard`` / ``complex``).
            Persisted; never rename without a migration.
        display_name: Human-readable name for UI ("Quick Lookup",
            "Standard Case", "Complex File").
        token_budget: Total tokens (input + output) the case is
            allowed to consume across all sessions within the 30-day
            window. Enforced by Layer 1 in
            ``advisor.llm.budget.case_budget_for``.
        unit_price_cents: List price for one credit at this tier in
            CAD cents. Pack discounts are applied on top.
        description: Short marketing blurb for the pricing page.
    """

    name: str
    display_name: str
    token_budget: int
    unit_price_cents: int
    description: str


@dataclass(frozen=True)
class Pack:
    """One pack SKU — quantity + discount applied to a tier's unit price.

    Attributes:
        sku: Stable identifier (``payg`` / ``starter`` / ``pro`` /
            ``enterprise``).
        display_name: Human-readable name for UI.
        quantity: Number of credits this SKU issues.
        discount_bps: Discount in basis points (10000 = 100%). 0 for
            PAYG; 500/1500/2500 for Starter/Pro/Enterprise.
    """

    sku: str
    display_name: str
    quantity: int
    discount_bps: int


# Tier catalog. Token budgets and prices come straight from the product
# brief; adjusting either is a code change + test update.
TIER_QUICK_DEF = Tier(
    name=TIER_QUICK,
    display_name="Quick Lookup",
    token_budget=12_000,
    unit_price_cents=1250,  # $12.50 CAD
    description=(
        "A single-property zoning or permitted-use lookup. ~4-6 retrieval "
        "rounds. Best for fast yes/no answers on a known address."
    ),
)
TIER_STANDARD_DEF = Tier(
    name=TIER_STANDARD,
    display_name="Standard Case",
    token_budget=45_000,
    unit_price_cents=3250,  # $32.50 CAD
    description=(
        "Variance research, multi-bylaw cross-references, and "
        "development-standards lookups for a single property. ~12-18 "
        "retrieval rounds."
    ),
)
TIER_COMPLEX_DEF = Tier(
    name=TIER_COMPLEX,
    display_name="Complex File",
    token_budget=130_000,
    unit_price_cents=7500,  # $75 CAD
    description=(
        "Rezoning, multi-overlay analysis, deep development-application "
        "files. ~35-50 retrieval rounds. Use when you need a thorough "
        "research file rather than a quick answer."
    ),
)

TIERS: dict[str, Tier] = {
    t.name: t for t in (TIER_QUICK_DEF, TIER_STANDARD_DEF, TIER_COMPLEX_DEF)
}

# Tier ordering (low → high). Used by the upgrade flow to validate that
# a target tier is strictly higher than the current tier.
TIER_ORDER: tuple[str, ...] = (TIER_QUICK, TIER_STANDARD, TIER_COMPLEX)


# Pack catalog. PAYG is included so the same DB schema/event taxonomy
# handles single-credit purchases without a special case.
PACK_PAYG_DEF = Pack(
    sku=PACK_PAYG, display_name="Pay-as-you-go", quantity=1, discount_bps=0
)
PACK_STARTER_DEF = Pack(
    sku=PACK_STARTER, display_name="Starter pack", quantity=5, discount_bps=500
)
PACK_PRO_DEF = Pack(
    sku=PACK_PRO, display_name="Pro pack", quantity=20, discount_bps=1500
)
PACK_ENTERPRISE_DEF = Pack(
    sku=PACK_ENTERPRISE,
    display_name="Enterprise pack",
    quantity=100,
    discount_bps=2500,
)

PACKS: dict[str, Pack] = {
    p.sku: p
    for p in (PACK_PAYG_DEF, PACK_STARTER_DEF, PACK_PRO_DEF, PACK_ENTERPRISE_DEF)
}


@dataclass(frozen=True)
class PackOffer:
    """A (tier, pack) combination — what a checkout session actually buys.

    Computed dynamically from ``TIERS`` and ``PACKS`` rather than
    pre-declared (12 entries) because the price-id env-var name follows
    a deterministic pattern: ``STRIPE_PRICE_<TIER>_<PACK>``.
    """

    tier: Tier
    pack: Pack

    @property
    def list_price_cents(self) -> int:
        """Pre-discount total: unit price × quantity."""
        return self.tier.unit_price_cents * self.pack.quantity

    @property
    def amount_due_cents(self) -> int:
        """Post-discount total. The Stripe Price ID for this offer is
        expected to match this amount; ``assert_offer_matches_price`` in
        the billing settings module verifies that on startup."""
        full = self.list_price_cents
        discount = (full * self.pack.discount_bps) // 10_000
        return full - discount

    @property
    def stripe_price_env_var(self) -> str:
        """Env-var name holding the Stripe Price ID for this offer.

        Convention: ``STRIPE_PRICE_<TIER>_<PACK>``, all uppercase. e.g.
        ``STRIPE_PRICE_STANDARD_PRO``. Looked up against
        ``AdvisorBillingSettings`` at checkout time.
        """
        return f"STRIPE_PRICE_{self.tier.name.upper()}_{self.pack.sku.upper()}"


def offer_for(tier_name: str, pack_sku: str) -> PackOffer:
    """Return the ``PackOffer`` for a (tier, pack) combination.

    Raises ``KeyError`` if either identifier is unknown — callers
    translate that to HTTP 400 at the API edge.
    """
    return PackOffer(tier=TIERS[tier_name], pack=PACKS[pack_sku])


def all_offers() -> list[PackOffer]:
    """Iterate every (tier, pack) offer — 12 in the current catalog.

    The pricing page renders this. Order is (tier outer, pack inner) so
    the matrix reads tier-by-tier with packs as columns.
    """
    return [
        PackOffer(tier=TIERS[tier], pack=PACKS[pack])
        for tier in TIER_ORDER
        for pack in (PACK_PAYG, PACK_STARTER, PACK_PRO, PACK_ENTERPRISE)
    ]


def pack_for_stripe_price_id(
    price_id: str, settings: object
) -> PackOffer | None:
    """Reverse-lookup a (tier, pack) from a Stripe Price ID.

    Used by the webhook handler when the metadata round-trip fails
    (e.g. an event from before metadata was attached, or a manual
    invoice). Compares ``price_id`` against the configured price-id
    values on ``settings`` — one attribute per offer.

    Returns ``None`` if the price isn't recognised; callers should
    treat that as a misconfigured pack and log loudly rather than
    silently issuing zero credits.

    ``settings: object`` (rather than the concrete type) avoids a
    circular import with ``advisor.billing.settings``.
    """
    if not price_id:
        return None
    for offer in all_offers():
        configured = getattr(
            settings, offer.stripe_price_env_var.lower(), None
        )
        if configured and configured == price_id:
            return offer
    return None
