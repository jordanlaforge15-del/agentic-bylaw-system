"""Settings: dormant defaults + env loading."""
from __future__ import annotations

import pytest

from advisor.billing.settings import AdvisorBillingSettings, get_settings


def test_defaults_are_dormant() -> None:
    """No env vars => billing is disabled and there are no creds.
    This is the safety property the rest of the system relies on:
    importing the settings model in a fresh process must not require
    any Stripe configuration."""
    s = AdvisorBillingSettings()
    assert s.enabled is False
    assert s.stripe_api_key is None
    assert s.stripe_webhook_secret is None
    assert s.stripe_price_pro is None
    assert s.stripe_price_team is None
    # Default redirect URLs let local frontend dev work without env.
    assert "localhost" in s.success_url
    assert "localhost" in s.cancel_url


def test_env_loading_picks_up_billing_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISOR_BILLING_ENABLED", "true")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_xyz")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_xyz")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro_xyz")
    monkeypatch.setenv("STRIPE_PRICE_TEAM", "price_team_xyz")
    monkeypatch.setenv(
        "ADVISOR_BILLING_SUCCESS_URL", "https://app.example.com/success"
    )
    monkeypatch.setenv(
        "ADVISOR_BILLING_CANCEL_URL", "https://app.example.com/cancel"
    )
    s = AdvisorBillingSettings()
    assert s.enabled is True
    assert s.stripe_api_key == "sk_test_xyz"
    assert s.stripe_webhook_secret == "whsec_xyz"
    assert s.stripe_price_pro == "price_pro_xyz"
    assert s.stripe_price_team == "price_team_xyz"
    assert s.success_url == "https://app.example.com/success"
    assert s.cancel_url == "https://app.example.com/cancel"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVISOR_BILLING_ENABLED", "true")
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
