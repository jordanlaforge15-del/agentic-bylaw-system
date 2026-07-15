"""Top-up catalog + checkout helper (ABS-381).

The catalog is server-side truth: token quantities and prices live in code,
and the checkout helper resolves the Stripe Price ID from settings by the
``STRIPE_PRICE_TOPUP_<SKU>`` convention.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from advisor.billing.checkout import (
    PriceNotConfiguredError,
    UnknownTopupError,
    start_topup_checkout,
)
from advisor.billing.client import CheckoutSessionResult, MockStripeClient
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.topups import (
    TOPUP_ORDER,
    TOPUPS,
    all_topups,
    topup_for,
    topup_for_stripe_price_id,
)
from advisor.db.models import User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


# ---------- catalog -------------------------------------------------------


def test_catalog_quantities_and_prices() -> None:
    small = topup_for("small")
    medium = topup_for("medium")
    large = topup_for("large")
    assert (small.tokens, small.price_cents) == (20_000, 1500)
    assert (medium.tokens, medium.price_cents) == (75_000, 5000)
    assert (large.tokens, large.price_cents) == (200_000, 12000)


def test_catalog_is_cheapest_first() -> None:
    assert [t.sku for t in all_topups()] == ["small", "medium", "large"]
    assert TOPUP_ORDER == ("small", "medium", "large")
    assert set(TOPUPS) == {"small", "medium", "large"}


def test_env_var_name_convention() -> None:
    assert topup_for("medium").stripe_price_env_var == "STRIPE_PRICE_TOPUP_MEDIUM"


def test_topup_for_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        topup_for("jumbo")


def test_reverse_lookup_by_price_id() -> None:
    settings = AdvisorBillingSettings(STRIPE_PRICE_TOPUP_LARGE="price_large_x")
    assert topup_for_stripe_price_id("price_large_x", settings) is topup_for(
        "large"
    )
    assert topup_for_stripe_price_id("price_nope", settings) is None
    assert topup_for_stripe_price_id("", settings) is None


# ---------- checkout helper ----------------------------------------------


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str) -> int:
    create_all(db_url)
    with session_scope(db_url) as s:
        user = User(clerk_user_id="u1", email="u1@x.com")
        s.add(user)
        s.flush()
        return user.id


def test_start_topup_checkout_immediate_capture_and_metadata(
    tmp_path: Path,
) -> None:
    db_url = _db_url(tmp_path)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True,
        ADVISOR_PAYMENTS_ENABLED=True,
        STRIPE_PRICE_TOPUP_SMALL="price_small_x",
    )
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(
            session_id="cs_1", url="https://stripe.test/checkout/cs_1"
        )
    )
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        url = start_topup_checkout(
            s, user, sku="small", client=client, settings=settings
        )
    assert url == "https://stripe.test/checkout/cs_1"
    call = client.checkout_calls[-1]
    assert call.price_id == "price_small_x"
    assert call.mode == "payment"
    # Immediate capture: no authorize-hold on a straight top-up charge.
    assert call.capture_method is None
    assert call.metadata == {
        "advisor_user_id": str(user_id),
        "topup_sku": "small",
    }


def test_start_topup_checkout_unknown_sku(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings(ADVISOR_PAYMENTS_ENABLED=True)
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs_1", url="u")
    )
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        with pytest.raises(UnknownTopupError):
            start_topup_checkout(
                s, user, sku="jumbo", client=client, settings=settings
            )
    # No Stripe call on a bad SKU.
    assert client.checkout_calls == []


def test_start_topup_checkout_price_not_configured(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings(ADVISOR_PAYMENTS_ENABLED=True)  # no price ids
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs_1", url="u")
    )
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        with pytest.raises(PriceNotConfiguredError):
            start_topup_checkout(
                s, user, sku="small", client=client, settings=settings
            )
    assert client.checkout_calls == []
