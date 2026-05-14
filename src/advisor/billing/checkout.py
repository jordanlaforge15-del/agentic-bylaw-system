"""Pack-checkout helper — replaces the v1 subscription checkout.

The router calls into here when a user clicks "Buy [tier] [pack]". The
helper resolves the offer (a tier × pack combination), looks up the
configured Stripe Price ID, and asks the ``StripeClient`` to mint a
Checkout session in ``payment`` mode (one-time charge, not a
subscription).

We round-trip the user id, tier, and pack SKU in metadata so the
webhook can resolve back to our schema and insert the correct
``CasePurchase`` + N ``CaseCredit`` rows.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from advisor.billing.client import StripeClient
from advisor.billing.packs import PACKS, TIERS, PackOffer, offer_for
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.models import User


class UnknownOfferError(ValueError):
    """Raised when ``(tier, pack_sku)`` is not in the catalog. Router
    translates to HTTP 400."""


class PriceNotConfiguredError(RuntimeError):
    """Raised when the requested offer has no configured Stripe Price
    ID on settings. Distinct from ``UnknownOfferError`` because this
    is an operator-misconfiguration error, not a user error. Router
    maps to HTTP 503 — the operator must finish wiring billing before
    this offer becomes purchasable."""


def start_pack_checkout(
    db: Session,
    user: User,
    *,
    tier: str,
    pack_sku: str,
    client: StripeClient,
    settings: AdvisorBillingSettings,
) -> str:
    """Create a Stripe Checkout session for a pack purchase.

    Side effects: none on the database. The ``CasePurchase`` /
    ``CaseCredit`` rows are inserted only after we receive the
    ``checkout.session.completed`` webhook — that way an abandoned
    checkout doesn't leave dangling credits.

    Metadata round-tripped through Stripe:
      * ``advisor_user_id`` — internal numeric id; webhook reads this
        rather than ``customer_email`` because emails are mutable.
      * ``tier`` — the tier identifier (``quick``/``standard``/``complex``).
      * ``pack_sku`` — the pack identifier
        (``payg``/``starter``/``pro``/``enterprise``).
      * ``quantity`` — credit count for the pack; mirrored from the
        catalog so a webhook-time catalog change doesn't change the
        in-flight purchase.
    """
    if tier not in TIERS:
        raise UnknownOfferError(
            f"unknown tier {tier!r}; valid: {sorted(TIERS)}"
        )
    if pack_sku not in PACKS:
        raise UnknownOfferError(
            f"unknown pack_sku {pack_sku!r}; valid: {sorted(PACKS)}"
        )
    offer = offer_for(tier, pack_sku)

    price_id = _resolve_price_id(offer, settings)
    if not price_id:
        raise PriceNotConfiguredError(
            f"no Stripe Price ID configured for offer "
            f"({tier!r}, {pack_sku!r}); set "
            f"{offer.stripe_price_env_var} in the environment."
        )

    metadata = {
        "advisor_user_id": str(user.id),
        "tier": tier,
        "pack_sku": pack_sku,
        "quantity": str(offer.pack.quantity),
    }
    result = client.create_checkout_session(
        customer_id=user.stripe_customer_id,
        customer_email=user.email,
        price_id=price_id,
        success_url=settings.success_url,
        cancel_url=settings.cancel_url,
        metadata=metadata,
        mode="payment",
    )
    return result.url


def _resolve_price_id(
    offer: PackOffer, settings: AdvisorBillingSettings
) -> str | None:
    """Look up the Stripe Price ID for an offer on settings.

    Convention: the env var ``STRIPE_PRICE_<TIER>_<PACK>`` maps to the
    settings attribute ``stripe_price_<tier>_<pack>`` (lowercased).
    """
    return getattr(settings, offer.stripe_price_env_var.lower(), None)
