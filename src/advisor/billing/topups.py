"""Token top-up catalog — single source of truth for paid wallet top-ups.

The beta pivot (ABS-379) bills chat by a prepaid token wallet (see
``advisor.db.wallet``). Once a user burns through the signup grant they buy
more tokens through a **top-up**: a one-time Stripe charge that credits a
fixed token quantity to their wallet.

Like ``packs.py``, the catalog lives in code — the token quantities and CAD
prices are product copy, so changing them is a code review + test + deploy,
not an ops twiddle. Critically, **the token quantity is server-side truth**:
the webhook resolves how many tokens to credit from *this* table keyed by the
top-up SKU, never from a client- or metadata-supplied amount (which a caller
could tamper with). See ``advisor.billing.webhooks``.

The mapping ``Stripe Price ID -> topup_sku`` is environment-specific (one set
of Price IDs in test mode, another in live mode), so it lives on
``AdvisorBillingSettings`` under ``STRIPE_PRICE_TOPUP_<SKU>``. The
``topup_for_stripe_price_id`` helper does the reverse lookup at webhook time
for metadata-less events.

Business parameters
-------------------
Prices come from the design spec's business-parameters table and are
placeholders pending final launch pricing — confirm before launch. The
token quantities were rescaled on ABS-416 when ``DEFAULT_TOKENS_PER_TURN``
was recalibrated from 2,500 to 175,000 against measured prod burn: each
SKU keeps the number of turns it has always advertised (and its price),
so only the token expression of that promise changed. Without the rescale
every SKU would have rendered "~0 turns" on the pricing page.

* ``small``  —  8 turns =  1,400,000 tokens, $15 CAD.
* ``medium`` — 30 turns =  5,250,000 tokens, $50 CAD.
* ``large``  — 80 turns = 14,000,000 tokens, $120 CAD.

The quantities are literal ints, deliberately NOT derived from
``turns.tokens_per_turn()``: what a purchase credits must be stable and
auditable against the price paid, so it must not move when an operator
flips the display divisor. A future recalibration therefore has to touch
this table too — the ABS-416 regression test pins the turn counts.
"""
from __future__ import annotations

from dataclasses import dataclass


# Top-up SKU identifiers — persisted only in Stripe metadata
# (``topup_sku``) and on the ``advisor_token_transaction`` ledger row's
# ``metadata_json``. Never rename without accounting for in-flight Stripe
# sessions that already carry the old value in metadata.
TOPUP_SMALL = "small"
TOPUP_MEDIUM = "medium"
TOPUP_LARGE = "large"


@dataclass(frozen=True)
class Topup:
    """One top-up SKU — a fixed token quantity sold for a fixed price.

    Attributes:
        sku: Stable identifier (``small`` / ``medium`` / ``large``).
        display_name: Human-readable name for UI ("Small top-up").
        tokens: Token quantity credited to the wallet on purchase. This is
            the **server-side truth** the webhook credits — metadata amounts
            are never trusted.
        price_cents: List price in CAD cents. The Stripe Price for this SKU
            MUST have a display amount equal to this value — there is no sync
            mechanism, so a mismatch silently over/undercharges.
    """

    sku: str
    display_name: str
    tokens: int
    price_cents: int

    @property
    def stripe_price_env_var(self) -> str:
        """Env-var name holding the Stripe Price ID for this top-up.

        Convention: ``STRIPE_PRICE_TOPUP_<SKU>``, all uppercase. e.g.
        ``STRIPE_PRICE_TOPUP_MEDIUM``. Looked up against
        ``AdvisorBillingSettings`` at checkout / webhook time.
        """
        return f"STRIPE_PRICE_TOPUP_{self.sku.upper()}"


# Top-up catalog. Prices come straight from the design spec; token
# quantities are the ABS-416 recalibration of the same advertised turn
# counts. Adjusting either is a code change + test update.
TOPUP_SMALL_DEF = Topup(
    sku=TOPUP_SMALL,
    display_name="Small top-up",
    tokens=1_400_000,  # 8 turns @ 175k
    price_cents=1500,  # $15 CAD
)
TOPUP_MEDIUM_DEF = Topup(
    sku=TOPUP_MEDIUM,
    display_name="Medium top-up",
    tokens=5_250_000,  # 30 turns @ 175k
    price_cents=5000,  # $50 CAD
)
TOPUP_LARGE_DEF = Topup(
    sku=TOPUP_LARGE,
    display_name="Large top-up",
    tokens=14_000_000,  # 80 turns @ 175k
    price_cents=12000,  # $120 CAD
)

# Ordered low → high so the pricing page renders cheapest-first.
TOPUP_ORDER: tuple[str, ...] = (TOPUP_SMALL, TOPUP_MEDIUM, TOPUP_LARGE)

TOPUPS: dict[str, Topup] = {
    t.sku: t
    for t in (TOPUP_SMALL_DEF, TOPUP_MEDIUM_DEF, TOPUP_LARGE_DEF)
}


def topup_for(sku: str) -> Topup:
    """Return the ``Topup`` for a SKU.

    Raises ``KeyError`` if the SKU is unknown — callers translate that to
    HTTP 400 (``unknown_topup``) at the API edge, or to a logged "handled"
    no-op at the webhook edge (never a 5xx — see the Stripe retry-storm rule).
    """
    return TOPUPS[sku]


def all_topups() -> list[Topup]:
    """Iterate every top-up SKU, cheapest-first. The pricing page renders
    this."""
    return [TOPUPS[sku] for sku in TOPUP_ORDER]


def topup_for_stripe_price_id(price_id: str, settings: object) -> Topup | None:
    """Reverse-lookup a top-up SKU from a Stripe Price ID.

    Used by the webhook handler when the checkout-session metadata is
    missing (a legacy event, or a manual invoice). Compares ``price_id``
    against the configured ``stripe_price_topup_<sku>`` values on
    ``settings``.

    Returns ``None`` if the price isn't recognised as a top-up; callers must
    then fall through to the pack / question branches rather than silently
    crediting zero tokens.

    ``settings: object`` (rather than the concrete type) avoids a circular
    import with ``advisor.billing.settings``.
    """
    if not price_id:
        return None
    for topup in all_topups():
        configured = getattr(
            settings, topup.stripe_price_env_var.lower(), None
        )
        if configured and configured == price_id:
            return topup
    return None
