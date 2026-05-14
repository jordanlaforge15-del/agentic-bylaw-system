"""Pydantic settings for the billing module.

All env vars are optional. The default ``enabled=False`` is the
critical safety: it means the FastAPI app boots cleanly with zero
Stripe configuration, and the billing endpoints return 503 until an
operator flips the flag. This is what lets the rest of the SaaS be
developed before a Stripe account exists.

When you flip ``ADVISOR_BILLING_ENABLED=true`` you must also provide:

* ``STRIPE_API_KEY`` — secret key (sk_test_... or sk_live_...).
* ``STRIPE_WEBHOOK_SECRET`` — endpoint signing secret (whsec_...).
* One ``STRIPE_PRICE_<TIER>_<PACK>`` env var per offered SKU. The
  webhook handler reverse-looks-up these by Price ID, so any SKU you
  want to sell must have its env-var populated. See
  ``advisor.billing.packs.PackOffer.stripe_price_env_var`` for the
  naming convention.

Validation of the "enabled but unconfigured" combination happens
lazily, where it can be surfaced to the operator as a useful error —
``LiveStripeClient.__init__`` raises if the API key is missing,
webhook verification raises if the webhook secret is missing, and so
on. Doing it here would make the module impossible to import in a
half-configured environment.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdvisorBillingSettings(BaseSettings):
    """Environment-backed configuration for Stripe billing.

    Defaults are deliberately permissive so the module can be imported
    and the FastAPI app can boot in a development environment with no
    Stripe credentials. The ``enabled`` flag gates every billing
    operation: when False, the router returns 503 from each endpoint.

    Stripe Price IDs follow the convention
    ``STRIPE_PRICE_<TIER>_<PACK>`` — see ``advisor.billing.packs`` for
    the catalog. Twelve fields below cover the 3 tiers × 4 pack SKUs.
    """

    enabled: bool = Field(default=False, alias="ADVISOR_BILLING_ENABLED")
    stripe_api_key: str | None = Field(default=None, alias="STRIPE_API_KEY")
    stripe_webhook_secret: str | None = Field(
        default=None, alias="STRIPE_WEBHOOK_SECRET"
    )

    # Quick tier — 12k tokens, $25 CAD per credit.
    stripe_price_quick_payg: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUICK_PAYG"
    )
    stripe_price_quick_starter: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUICK_STARTER"
    )
    stripe_price_quick_pro: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUICK_PRO"
    )
    stripe_price_quick_enterprise: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUICK_ENTERPRISE"
    )

    # Standard tier — 45k tokens, $65 CAD per credit.
    stripe_price_standard_payg: str | None = Field(
        default=None, alias="STRIPE_PRICE_STANDARD_PAYG"
    )
    stripe_price_standard_starter: str | None = Field(
        default=None, alias="STRIPE_PRICE_STANDARD_STARTER"
    )
    stripe_price_standard_pro: str | None = Field(
        default=None, alias="STRIPE_PRICE_STANDARD_PRO"
    )
    stripe_price_standard_enterprise: str | None = Field(
        default=None, alias="STRIPE_PRICE_STANDARD_ENTERPRISE"
    )

    # Complex tier — 130k tokens, $150 CAD per credit.
    stripe_price_complex_payg: str | None = Field(
        default=None, alias="STRIPE_PRICE_COMPLEX_PAYG"
    )
    stripe_price_complex_starter: str | None = Field(
        default=None, alias="STRIPE_PRICE_COMPLEX_STARTER"
    )
    stripe_price_complex_pro: str | None = Field(
        default=None, alias="STRIPE_PRICE_COMPLEX_PRO"
    )
    stripe_price_complex_enterprise: str | None = Field(
        default=None, alias="STRIPE_PRICE_COMPLEX_ENTERPRISE"
    )

    success_url: str = Field(
        default="http://localhost:3000/billing/success",
        alias="ADVISOR_BILLING_SUCCESS_URL",
    )
    cancel_url: str = Field(
        default="http://localhost:3000/billing/cancel",
        alias="ADVISOR_BILLING_CANCEL_URL",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> AdvisorBillingSettings:
    """Return a process-wide cached settings instance.

    Tests that need fresh settings call ``get_settings.cache_clear()``
    or instantiate ``AdvisorBillingSettings`` directly.
    """
    return AdvisorBillingSettings()
