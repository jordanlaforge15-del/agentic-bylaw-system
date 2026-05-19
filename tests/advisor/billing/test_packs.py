"""Pack catalog: tier definitions, pack discount math, env-var lookup."""
from __future__ import annotations

from advisor.billing.packs import (
    PACK_ENTERPRISE,
    PACK_PAYG,
    PACK_PRO,
    PACK_STARTER,
    PACKS,
    TIER_COMPLEX,
    TIER_ORDER,
    TIER_QUICK,
    TIER_STANDARD,
    TIERS,
    all_offers,
    offer_for,
    pack_for_stripe_price_id,
)
from advisor.billing.settings import AdvisorBillingSettings


def test_tier_catalog_has_three_tiers() -> None:
    assert set(TIERS) == {TIER_QUICK, TIER_STANDARD, TIER_COMPLEX}
    # Tier ordering is low-to-high; the upgrade flow relies on this.
    assert TIER_ORDER == (TIER_QUICK, TIER_STANDARD, TIER_COMPLEX)


def test_pack_catalog_has_four_packs() -> None:
    assert set(PACKS) == {PACK_PAYG, PACK_STARTER, PACK_PRO, PACK_ENTERPRISE}
    assert PACKS[PACK_PAYG].quantity == 1
    assert PACKS[PACK_PAYG].discount_bps == 0
    assert PACKS[PACK_STARTER].quantity == 5
    assert PACKS[PACK_STARTER].discount_bps == 500
    assert PACKS[PACK_PRO].quantity == 20
    assert PACKS[PACK_PRO].discount_bps == 1500
    assert PACKS[PACK_ENTERPRISE].quantity == 100
    assert PACKS[PACK_ENTERPRISE].discount_bps == 2500


def test_all_offers_yields_twelve_combinations() -> None:
    offers = all_offers()
    assert len(offers) == 12
    # No duplicates
    assert len({(o.tier.name, o.pack.sku) for o in offers}) == 12


def test_pack_offer_discount_math() -> None:
    # Standard tier ($32.50 / credit) × Pro pack (20 credits, 15% off):
    # list = 32.50 * 20 = $650 = 65_000 cents
    # discount = 65_000 * 0.15 = 9_750 cents
    # due = 55_250 cents
    offer = offer_for(TIER_STANDARD, PACK_PRO)
    assert offer.list_price_cents == 65_000
    assert offer.amount_due_cents == 55_250


def test_payg_has_no_discount() -> None:
    for tier in TIER_ORDER:
        offer = offer_for(tier, PACK_PAYG)
        assert offer.amount_due_cents == offer.list_price_cents


def test_stripe_price_env_var_naming_convention() -> None:
    # Convention is STRIPE_PRICE_<TIER>_<PACK>, all uppercase.
    assert (
        offer_for(TIER_QUICK, PACK_PAYG).stripe_price_env_var
        == "STRIPE_PRICE_QUICK_PAYG"
    )
    assert (
        offer_for(TIER_COMPLEX, PACK_ENTERPRISE).stripe_price_env_var
        == "STRIPE_PRICE_COMPLEX_ENTERPRISE"
    )


def test_pack_for_stripe_price_id_round_trips(monkeypatch) -> None:
    # Configure one offer's Price ID and verify reverse-lookup returns
    # the matching offer.
    monkeypatch.setenv("STRIPE_PRICE_STANDARD_STARTER", "price_test_123")
    settings = AdvisorBillingSettings()
    offer = pack_for_stripe_price_id("price_test_123", settings)
    assert offer is not None
    assert offer.tier.name == TIER_STANDARD
    assert offer.pack.sku == PACK_STARTER


def test_pack_for_stripe_price_id_returns_none_for_unknown() -> None:
    settings = AdvisorBillingSettings()
    assert pack_for_stripe_price_id("price_missing", settings) is None
    assert pack_for_stripe_price_id("", settings) is None
