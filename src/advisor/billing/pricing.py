"""Pricing-presentation settings (FX rate, locale, display currency).

Separate from ``AdvisorBillingSettings`` because billing settings carry
operational state (Stripe credentials, enabled flag) while pricing
settings are presentation-layer (how do we render a Canadian-dollar
price to a user the system thinks might be in USD).

Today we ship with CAD as the only currency the catalog prices in
(``packs.py`` stores cents in CAD). The FX rate exists so we can
*display* an approximate USD equivalent for marketing pages targeted at
US audiences without re-storing every price in two currencies.

If we ever sell in USD natively, that's a schema change (per-currency
unit_price columns on Tier), not a settings change.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdvisorPricingSettings(BaseSettings):
    """Environment-backed pricing-display configuration.

    The defaults work for a Canadian-dollar deployment with no USD
    surfacing. Override ``ADVISOR_PRICING_CAD_PER_USD`` if you need
    to display USD equivalents on the marketing pages.
    """

    cad_per_usd: float = Field(
        default=1.37,
        alias="ADVISOR_PRICING_CAD_PER_USD",
        description=(
            "FX rate used to convert CAD-denominated catalog prices to "
            "USD for display purposes only. Default 1.37 reflects the "
            "approximate CAD/USD rate as of mid-2026; operators should "
            "refresh this quarterly to stay within ~5% of spot."
        ),
    )
    display_currency: str = Field(
        default="CAD",
        alias="ADVISOR_PRICING_DISPLAY_CURRENCY",
        description=(
            "ISO-4217 code shown alongside catalog prices. Stays CAD "
            "until we sell in another currency natively."
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def cents_cad_to_usd_display(self, cents_cad: int) -> float:
        """Convert CAD cents to a USD-dollars float for display.

        Returns a float (not cents) because the only consumer is a
        marketing page that renders ``$NN.NN USD``. Never used for
        actual money movement.
        """
        if self.cad_per_usd <= 0:
            return 0.0
        return (cents_cad / 100.0) / self.cad_per_usd


@lru_cache
def get_pricing_settings() -> AdvisorPricingSettings:
    """Process-wide cached pricing settings instance.

    Tests that need to vary the FX rate call
    ``get_pricing_settings.cache_clear()`` or instantiate
    ``AdvisorPricingSettings`` directly.
    """
    return AdvisorPricingSettings()
