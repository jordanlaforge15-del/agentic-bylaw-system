"""Plan catalog — the single source of truth for tier names and limits.

The plan catalog lives in code rather than env (or the database) for
two reasons:

1. The mapping ``tier -> monthly query limit`` is product copy, not
   operational config. Changing it should be a code review, a test
   run, and a deploy — not an env-var twiddle.
2. Tests can reach in and reason about plan structure without
   spinning up a settings instance.

The mapping ``Stripe Price ID -> tier`` is intentionally NOT here —
Price IDs are environment-specific (one set in test mode, another in
live mode), so they live on ``AdvisorBillingSettings``. The
``plan_for_stripe_price_id`` helper does the reverse lookup at
webhook time.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BillingPlan:
    """A tier of the SaaS plan.

    Attributes:
        tier: Stable identifier (``"free"`` / ``"pro"`` / ``"team"``).
            Persisted on ``advisor_user.plan_tier``; never rename
            without a migration.
        monthly_query_limit: Number of LLM queries this plan allows
            per calendar month. Mirrored onto ``advisor_user``.
        stripe_price_env_var: Name of the env var that holds the
            Stripe Price ID for this plan, or ``None`` for the free
            plan (which doesn't have a Stripe Price). Looked up
            against ``AdvisorBillingSettings`` at webhook time.
    """

    tier: str
    monthly_query_limit: int
    stripe_price_env_var: str | None


# Plan limits chosen to match the v1 default of 100 / 1000 / 10000 —
# adjust these (and the test that asserts them) when product changes
# the offering.
PLAN_FREE = BillingPlan(
    tier="free", monthly_query_limit=100, stripe_price_env_var=None
)
PLAN_PRO = BillingPlan(
    tier="pro", monthly_query_limit=1000, stripe_price_env_var="STRIPE_PRICE_PRO"
)
PLAN_TEAM = BillingPlan(
    tier="team",
    monthly_query_limit=10000,
    stripe_price_env_var="STRIPE_PRICE_TEAM",
)

PLANS_BY_TIER: dict[str, BillingPlan] = {
    p.tier: p for p in (PLAN_FREE, PLAN_PRO, PLAN_TEAM)
}


def plan_for_stripe_price_id(
    price_id: str, settings: object
) -> BillingPlan | None:
    """Reverse-lookup a plan from a Stripe Price ID.

    Compares ``price_id`` against the configured price-id values on
    ``settings`` (one attribute per plan). Returns the matching
    ``BillingPlan`` or ``None`` if the price isn't recognised.

    The free plan never has a Stripe price, so it can never be
    returned by this function — callers that need the free plan use
    ``PLAN_FREE`` directly (e.g. on subscription cancellation).

    Why ``settings: object`` not the concrete type: avoids a circular
    import with ``advisor.billing.settings``. The duck-typed access
    is fine — ``BaseSettings`` always exposes the configured attrs.
    """
    if not price_id:
        return None
    for plan in PLANS_BY_TIER.values():
        if plan.stripe_price_env_var is None:
            continue
        # ``stripe_price_env_var`` names the env var; the matching
        # attribute on settings has the same lowercase name minus the
        # ``STRIPE_`` prefix. We map by hand to avoid a string-magic
        # convention (and to keep this function trivially auditable).
        configured = _settings_value_for_env(plan.stripe_price_env_var, settings)
        if configured and configured == price_id:
            return plan
    return None


def _settings_value_for_env(env_var: str, settings: object) -> str | None:
    """Pull a price id off settings using the env var alias.

    Maps ``STRIPE_PRICE_PRO`` -> ``stripe_price_pro``. Kept private
    because it bakes in the alias-to-attribute convention used by
    ``AdvisorBillingSettings``.
    """
    attr = env_var.lower()
    return getattr(settings, attr, None)
