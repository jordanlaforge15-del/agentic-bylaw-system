"""``start_checkout``: argument shaping + error mapping."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from advisor.billing.checkout import (
    FreeTierCheckoutError,
    PriceNotConfiguredError,
    UnknownTierError,
    start_checkout,
)
from advisor.billing.client import CheckoutSessionResult, MockStripeClient
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db import User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _make_user(s, **overrides) -> User:
    base = dict(
        clerk_user_id="clerk_co",
        email="user@example.com",
        full_name="Checkout User",
        plan_tier="free",
        monthly_query_limit=100,
        monthly_queries_used=0,
        month_started_at=date(2026, 5, 1),
    )
    base.update(overrides)
    user = User(**base)
    s.add(user)
    s.flush()
    return user


def _settings(**overrides) -> AdvisorBillingSettings:
    base = dict(
        ADVISOR_BILLING_ENABLED=True,
        STRIPE_API_KEY="sk_test",
        STRIPE_WEBHOOK_SECRET="whsec",
        STRIPE_PRICE_PRO="price_pro_123",
        STRIPE_PRICE_TEAM="price_team_456",
        ADVISOR_BILLING_SUCCESS_URL="https://app/success",
        ADVISOR_BILLING_CANCEL_URL="https://app/cancel",
    )
    base.update(overrides)
    return AdvisorBillingSettings(**base)


def test_start_checkout_passes_user_metadata_and_price(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(
            session_id="cs_x", url="https://stripe/c/x"
        )
    )
    settings = _settings()
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        url = start_checkout(
            s, user, target_tier="pro", client=client, settings=settings
        )
    assert url == "https://stripe/c/x"
    assert len(client.checkout_calls) == 1
    call = client.checkout_calls[0]
    assert call.price_id == "price_pro_123"
    assert call.success_url == "https://app/success"
    assert call.cancel_url == "https://app/cancel"
    assert call.metadata["advisor_user_id"] == str(user.id)
    assert call.metadata["target_tier"] == "pro"
    # First-time buyer with no stripe_customer_id => stripe creates
    # the customer record.
    assert call.customer_id is None
    assert call.customer_email == "user@example.com"


def test_start_checkout_passes_existing_customer_id_when_present(
    tmp_path: Path,
) -> None:
    create_all(_db_url(tmp_path))
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(
            session_id="cs_y", url="https://stripe/c/y"
        )
    )
    settings = _settings()
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s, stripe_customer_id="cus_existing")
        start_checkout(
            s, user, target_tier="team", client=client, settings=settings
        )
    call = client.checkout_calls[0]
    assert call.customer_id == "cus_existing"
    assert call.price_id == "price_team_456"


def test_start_checkout_raises_for_unknown_tier(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="x", url="x")
    )
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        with pytest.raises(UnknownTierError):
            start_checkout(
                s, user, target_tier="enterprise", client=client, settings=_settings()
            )


def test_start_checkout_rejects_free_tier(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="x", url="x")
    )
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        with pytest.raises(FreeTierCheckoutError):
            start_checkout(
                s, user, target_tier="free", client=client, settings=_settings()
            )


def test_start_checkout_raises_price_not_configured_when_missing(
    tmp_path: Path,
) -> None:
    """Operator forgot to set STRIPE_PRICE_PRO — expose loudly."""
    create_all(_db_url(tmp_path))
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="x", url="x")
    )
    settings = _settings(STRIPE_PRICE_PRO=None)
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        with pytest.raises(PriceNotConfiguredError):
            start_checkout(
                s, user, target_tier="pro", client=client, settings=settings
            )
