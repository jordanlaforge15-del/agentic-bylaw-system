"""Plan catalog: tier lookups and reverse Price-ID lookup."""
from __future__ import annotations

from advisor.billing.plans import (
    PLAN_FREE,
    PLAN_PRO,
    PLAN_TEAM,
    PLANS_BY_TIER,
    plan_for_stripe_price_id,
)
from advisor.billing.settings import AdvisorBillingSettings


def test_plan_constants_have_documented_limits() -> None:
    assert PLAN_FREE.tier == "free"
    assert PLAN_FREE.monthly_query_limit == 100
    assert PLAN_FREE.stripe_price_env_var is None

    assert PLAN_PRO.tier == "pro"
    assert PLAN_PRO.monthly_query_limit == 1000
    assert PLAN_PRO.stripe_price_env_var == "STRIPE_PRICE_PRO"

    assert PLAN_TEAM.tier == "team"
    assert PLAN_TEAM.monthly_query_limit == 10000
    assert PLAN_TEAM.stripe_price_env_var == "STRIPE_PRICE_TEAM"


def test_plans_by_tier_is_keyed_on_tier_string() -> None:
    assert set(PLANS_BY_TIER) == {"free", "pro", "team"}
    assert PLANS_BY_TIER["pro"] is PLAN_PRO
    assert PLANS_BY_TIER["team"] is PLAN_TEAM
    assert PLANS_BY_TIER["free"] is PLAN_FREE


def test_plan_for_price_id_returns_pro_when_configured() -> None:
    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True,
        STRIPE_PRICE_PRO="price_pro_123",
        STRIPE_PRICE_TEAM="price_team_456",
    )
    assert plan_for_stripe_price_id("price_pro_123", settings) is PLAN_PRO


def test_plan_for_price_id_returns_team_when_configured() -> None:
    settings = AdvisorBillingSettings(
        STRIPE_PRICE_PRO="price_pro_123",
        STRIPE_PRICE_TEAM="price_team_456",
    )
    assert plan_for_stripe_price_id("price_team_456", settings) is PLAN_TEAM


def test_plan_for_price_id_returns_none_for_unknown_id() -> None:
    settings = AdvisorBillingSettings(
        STRIPE_PRICE_PRO="price_pro_123",
        STRIPE_PRICE_TEAM="price_team_456",
    )
    assert plan_for_stripe_price_id("price_unknown", settings) is None


def test_plan_for_price_id_returns_none_for_empty() -> None:
    settings = AdvisorBillingSettings()
    assert plan_for_stripe_price_id("", settings) is None


def test_plan_for_price_id_skips_unconfigured_prices() -> None:
    """If STRIPE_PRICE_PRO is unset, an incoming pro price id can't
    match anything — even if it happens to equal another env's
    value."""
    settings = AdvisorBillingSettings(STRIPE_PRICE_TEAM="price_team_456")
    # No pro price configured.
    assert plan_for_stripe_price_id("price_pro_123", settings) is None
    # Team still resolves.
    assert plan_for_stripe_price_id("price_team_456", settings) is PLAN_TEAM
