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

from pydantic import Field, field_validator
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

    # ABS-322 — payments-off / free-trial master switch.
    #
    # ``enabled`` (ADVISOR_BILLING_ENABLED) turns the billing/questions
    # subsystem ON (mounts the live router). ``payments_enabled``
    # (ADVISOR_PAYMENTS_ENABLED) decides, within that live subsystem,
    # whether an answer is unlocked by a **Stripe charge** (True) or by
    # **consuming a free-question credit** (False).
    #
    # The go-live config is ``enabled=True`` + ``payments_enabled=False``
    # + no Stripe Price IDs: a trial user picks a question and the
    # buy-an-answer backend consumes a free-question entitlement (ABS-314)
    # — no checkout session, no bank account needed. Flip
    # ``ADVISOR_PAYMENTS_ENABLED=true`` and configure the
    # ``STRIPE_PRICE_QUESTION_*`` IDs (banks ready) and the existing
    # authorize→capture Stripe path (ABS-312) lights up with no code
    # change. Default False so the trial never touches Stripe.
    payments_enabled: bool = Field(
        default=False, alias="ADVISOR_PAYMENTS_ENABLED"
    )

    # ABS-325 — off-menu "Other" free-form question kill switch.
    #
    # The off-menu path (ABS-316) lets a user type a free-form question,
    # has the LLM quote a price, and sells an answer. It is too open-ended
    # to expose at launch (unbounded scope / quoting / liability), so it is
    # disabled by default: the ``/questions/quote`` and ``/checkout/other``
    # endpoints reject with 503 when this is False. The quote/answer engine
    # (advisor.billing.quote + the off-menu branch in answers.py) is left
    # fully intact — re-enabling the launch path is a flag flip
    # (``ADVISOR_OTHER_QUESTION_ENABLED=true``), not a rebuild. The catalog
    # ``/checkout/question`` + free-start paths are unaffected.
    other_question_enabled: bool = Field(
        default=False, alias="ADVISOR_OTHER_QUESTION_ENABLED"
    )
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

    # Priced-question catalog (launch product — see
    # ``advisor.billing.questions``). One Stripe Price per catalog
    # question, named ``STRIPE_PRICE_QUESTION_<SLUG>``. The webhook
    # handler reverse-looks-up these by Price ID, so any question you
    # want to sell must have its env var populated. The display amount
    # of each Price MUST equal the question's ``price_cents``.
    stripe_price_question_permitted_use: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUESTION_PERMITTED_USE"
    )
    stripe_price_question_development_standards: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUESTION_DEVELOPMENT_STANDARDS"
    )
    stripe_price_question_due_diligence: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUESTION_DUE_DILIGENCE"
    )
    stripe_price_question_legal_nonconforming: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUESTION_LEGAL_NONCONFORMING"
    )
    stripe_price_question_variance_justification: str | None = Field(
        default=None, alias="STRIPE_PRICE_QUESTION_VARIANCE_JUSTIFICATION"
    )

    # Token top-up catalog (ABS-381 — beta pivot wallet). One Stripe Price
    # per top-up SKU, named ``STRIPE_PRICE_TOPUP_<SKU>``. The token quantity
    # each SKU credits is server-side truth in ``advisor.billing.topups`` —
    # the webhook reverse-looks-up these Price IDs for metadata-less events.
    # The display amount of each Price MUST equal the SKU's ``price_cents``.
    stripe_price_topup_small: str | None = Field(
        default=None, alias="STRIPE_PRICE_TOPUP_SMALL"
    )
    stripe_price_topup_medium: str | None = Field(
        default=None, alias="STRIPE_PRICE_TOPUP_MEDIUM"
    )
    stripe_price_topup_large: str | None = Field(
        default=None, alias="STRIPE_PRICE_TOPUP_LARGE"
    )

    success_url: str = Field(
        default="http://localhost:3000/billing/success",
        alias="ADVISOR_BILLING_SUCCESS_URL",
    )
    cancel_url: str = Field(
        default="http://localhost:3000/billing/cancel",
        alias="ADVISOR_BILLING_CANCEL_URL",
    )

    @field_validator(
        "stripe_api_key",
        "stripe_webhook_secret",
        "stripe_price_quick_payg",
        "stripe_price_quick_starter",
        "stripe_price_quick_pro",
        "stripe_price_quick_enterprise",
        "stripe_price_standard_payg",
        "stripe_price_standard_starter",
        "stripe_price_standard_pro",
        "stripe_price_standard_enterprise",
        "stripe_price_complex_payg",
        "stripe_price_complex_starter",
        "stripe_price_complex_pro",
        "stripe_price_complex_enterprise",
        "stripe_price_question_permitted_use",
        "stripe_price_question_development_standards",
        "stripe_price_question_due_diligence",
        "stripe_price_question_legal_nonconforming",
        "stripe_price_question_variance_justification",
        "stripe_price_topup_small",
        "stripe_price_topup_medium",
        "stripe_price_topup_large",
        mode="before",
    )
    @classmethod
    def coerce_empty_to_none(cls, v: str | None) -> str | None:
        return None if v == "" else v

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
