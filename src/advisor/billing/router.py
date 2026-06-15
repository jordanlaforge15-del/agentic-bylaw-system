"""FastAPI router for the billing endpoints — case-credit model.

Five endpoints replace the v1 subscription-style trio:

* ``GET /v1/billing/catalog`` — auth-required. Returns the 12-SKU
  matrix (tier × pack) with prices and which SKUs have a Stripe Price
  ID configured. The pricing page renders this.
* ``POST /v1/billing/checkout/pack`` — auth-required. Creates a Stripe
  Checkout session for one (tier, pack) combination and returns its
  URL.
* ``POST /v1/billing/webhook`` — no auth; verified via
  ``Stripe-Signature``. Applies the event to the database (inserts
  per-credit rows on ``checkout.session.completed``).
* ``GET /v1/billing/me`` — auth-required. Returns the user's credit
  balance grouped by tier, plus their stripe_customer_id and the
  enabled flag.
* ``GET /v1/billing/purchases`` — auth-required. Returns the user's
  purchase history newest-first.

Every endpoint short-circuits to HTTP 503 when ``settings.enabled`` is
False — same dormant-by-default safety as v1.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from advisor.billing.checkout import (
    PriceNotConfiguredError,
    UnknownOfferError,
    start_pack_checkout,
)
from advisor.billing.client import StripeClient
from advisor.billing.packs import all_offers
from advisor.billing.pricing import get_pricing_settings
from advisor.billing.questions import all_questions
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
from advisor.db.cases import credit_balance_for
from advisor.db.models import CasePurchase, User

logger = logging.getLogger(__name__)


# -- Request / response models ---------------------------------------------


class CheckoutPackRequest(BaseModel):
    """Body of ``POST /v1/billing/checkout/pack``."""

    tier: str = Field(
        ..., description="Case tier identifier (quick / standard / complex)."
    )
    pack_sku: str = Field(
        ...,
        description="Pack identifier (payg / starter / pro / enterprise).",
    )


class CheckoutResponse(BaseModel):
    url: str = Field(..., description="Stripe Checkout redirect URL.")


class CatalogOffer(BaseModel):
    """One (tier, pack) offer for the public pricing page."""

    tier: str
    tier_display_name: str
    tier_token_budget: int
    pack_sku: str
    pack_display_name: str
    quantity: int
    discount_bps: int
    list_price_cents: int
    amount_due_cents: int
    currency: str = "CAD"
    available: bool = Field(
        ...,
        description=(
            "True iff the Stripe Price ID for this offer is configured. "
            "Disabled offers render as 'coming soon' on the pricing page."
        ),
    )


class CatalogResponse(BaseModel):
    """Body of ``GET /v1/billing/catalog``."""

    enabled: bool
    currency: str = "CAD"
    cad_per_usd: float = Field(
        ...,
        description=(
            "FX rate for displaying USD equivalents on marketing pages "
            "targeted at US audiences. CAD is authoritative."
        ),
    )
    offers: list[CatalogOffer]


class QuestionInputField(BaseModel):
    """One required-or-optional input for a catalog question."""

    name: str
    label: str
    required: bool
    description: str


class QuestionMenuItem(BaseModel):
    """One priced question on the launch menu."""

    slug: str
    display_name: str
    price_cents: int
    currency: str = "CAD"
    summary: str
    backing_calls: list[str]
    required_inputs: list[QuestionInputField]
    catalog_anchor: str
    available: bool = Field(
        ...,
        description=(
            "True iff the Stripe Price ID for this question is configured "
            "and billing is enabled. Disabled questions render as 'coming "
            "soon' on the menu."
        ),
    )


class QuestionMenuResponse(BaseModel):
    """Body of ``GET /v1/billing/questions`` — the priced-question menu."""

    enabled: bool
    currency: str = "CAD"
    cad_per_usd: float = Field(
        ...,
        description=(
            "FX rate for displaying USD equivalents on marketing pages. "
            "CAD is authoritative."
        ),
    )
    questions: list[QuestionMenuItem]


class TierBalance(BaseModel):
    tier: str
    available: int
    reserved: int
    consumed: int


class BillingMeResponse(BaseModel):
    enabled: bool
    stripe_customer_id: str | None
    tier_balances: list[TierBalance]
    total_available_credits: int
    free_questions_remaining: int = 0


class PurchaseSummary(BaseModel):
    """One row in the purchase-history list."""

    id: int
    tier: str
    pack_sku: str
    quantity: int
    amount_paid_cents: int
    currency: str
    created_at: str


class PurchaseHistoryResponse(BaseModel):
    purchases: list[PurchaseSummary]


# -- Shared builders --------------------------------------------------------


def _build_question_menu(
    *, settings: AdvisorBillingSettings | None, enabled: bool, currency: str
) -> list[QuestionMenuItem]:
    """Render the priced-question catalog into menu items.

    ``available`` is True only when billing is enabled AND the
    question's Stripe Price ID is configured on ``settings``. When
    ``settings`` is None (minimal dormant setups), every question is
    rendered but unavailable.
    """
    items: list[QuestionMenuItem] = []
    for question in all_questions():
        price_id = (
            getattr(
                settings, question.stripe_price_env_var.lower(), None
            )
            if settings is not None
            else None
        )
        items.append(
            QuestionMenuItem(
                slug=question.slug,
                display_name=question.display_name,
                price_cents=question.price_cents,
                currency=currency,
                summary=question.summary,
                backing_calls=list(question.backing_calls),
                required_inputs=[
                    QuestionInputField(
                        name=f.name,
                        label=f.label,
                        required=f.required,
                        description=f.description,
                    )
                    for f in question.required_inputs
                ],
                catalog_anchor=question.catalog_anchor,
                available=bool(price_id) and enabled,
            )
        )
    return items


# -- Router factory ---------------------------------------------------------


UserResolver = Callable[[Any, Session], User]


def build_billing_router(
    *,
    settings: AdvisorBillingSettings,
    client_factory: Callable[[], StripeClient] | None,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
) -> APIRouter:
    """Assemble the billing router."""
    router = APIRouter(prefix="/v1/billing", tags=["billing"])

    def _require_enabled() -> None:
        if not settings.enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_disabled",
                    "message": (
                        "Billing is not enabled on this deployment. Set "
                        "ADVISOR_BILLING_ENABLED=true and configure "
                        "STRIPE_* env vars to enable."
                    ),
                },
            )

    @contextmanager
    def _open_db() -> Any:
        result = db_session_factory()
        if hasattr(result, "__enter__"):
            with result as session:
                yield session
        else:
            try:
                yield result
            finally:
                close = getattr(result, "close", None)
                if callable(close):
                    close()

    @router.get("/catalog", response_model=CatalogResponse)
    def get_catalog() -> CatalogResponse:
        """Return the full 12-SKU pack matrix.

        Unauth-accessible by design: the pricing page is public and
        wants to show prices to anonymous visitors. The ``enabled``
        flag tells the frontend whether checkout will actually work.
        """
        pricing = get_pricing_settings()
        offers = []
        for offer in all_offers():
            price_id = getattr(
                settings, offer.stripe_price_env_var.lower(), None
            )
            offers.append(
                CatalogOffer(
                    tier=offer.tier.name,
                    tier_display_name=offer.tier.display_name,
                    tier_token_budget=offer.tier.token_budget,
                    pack_sku=offer.pack.sku,
                    pack_display_name=offer.pack.display_name,
                    quantity=offer.pack.quantity,
                    discount_bps=offer.pack.discount_bps,
                    list_price_cents=offer.list_price_cents,
                    amount_due_cents=offer.amount_due_cents,
                    currency=pricing.display_currency,
                    available=bool(price_id) and settings.enabled,
                )
            )
        return CatalogResponse(
            enabled=settings.enabled,
            currency=pricing.display_currency,
            cad_per_usd=pricing.cad_per_usd,
            offers=offers,
        )

    @router.get("/questions", response_model=QuestionMenuResponse)
    def get_questions() -> QuestionMenuResponse:
        """Return the priced-question launch menu.

        Public by design — the question menu is shown to anonymous
        visitors. The ``available`` flag per question tells the frontend
        whether checkout will actually work (Stripe Price configured +
        billing enabled).
        """
        pricing = get_pricing_settings()
        return QuestionMenuResponse(
            enabled=settings.enabled,
            currency=pricing.display_currency,
            cad_per_usd=pricing.cad_per_usd,
            questions=_build_question_menu(
                settings=settings,
                enabled=settings.enabled,
                currency=pricing.display_currency,
            ),
        )

    @router.post("/checkout/pack", response_model=CheckoutResponse)
    def post_checkout_pack(
        body: CheckoutPackRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> CheckoutResponse:
        _require_enabled()
        if client_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_misconfigured",
                    "message": "no Stripe client factory wired",
                },
            )
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            try:
                url = start_pack_checkout(
                    db,
                    user,
                    tier=body.tier,
                    pack_sku=body.pack_sku,
                    client=client_factory(),
                    settings=settings,
                )
            except UnknownOfferError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "unknown_offer",
                        "message": str(exc),
                    },
                ) from exc
            except PriceNotConfiguredError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "price_not_configured",
                        "message": str(exc),
                    },
                ) from exc
            return CheckoutResponse(url=url)

    @router.post("/webhook")
    async def post_webhook(
        request: Request,
        stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    ) -> dict[str, Any]:
        _require_enabled()
        if client_factory is None or not settings.stripe_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_misconfigured",
                    "message": "webhook handler not configured",
                },
            )
        if not stripe_signature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "missing_signature",
                    "message": "Stripe-Signature header is required",
                },
            )
        payload = await request.body()
        client = client_factory()
        try:
            event = client.construct_webhook_event(
                payload=payload,
                sig_header=stripe_signature,
                secret=settings.stripe_webhook_secret,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — Stripe SDK raises various
            logger.warning(
                "stripe webhook signature verification failed: %s", exc
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_signature",
                    "message": "webhook signature verification failed",
                },
            ) from exc

        with _open_db() as db:
            result = handle_event(db, event, settings)
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
        return {
            "handled": result.handled,
            "event_type": result.event_type,
            "event_id": result.event_id,
            "note": result.note,
        }

    @router.get("/me", response_model=BillingMeResponse)
    def get_me(
        auth_session: Any = Depends(user_dependency),
    ) -> BillingMeResponse:
        _require_enabled()
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            balances = credit_balance_for(db, user_id=user.id)
            tier_balances = [
                TierBalance(
                    tier=b.tier,
                    available=b.available,
                    reserved=b.reserved,
                    consumed=b.consumed,
                )
                for b in balances
            ]
            return BillingMeResponse(
                enabled=settings.enabled,
                stripe_customer_id=user.stripe_customer_id,
                tier_balances=tier_balances,
                total_available_credits=sum(
                    b.available for b in tier_balances
                ),
                free_questions_remaining=user.free_questions_remaining,
            )

    @router.get("/purchases", response_model=PurchaseHistoryResponse)
    def get_purchases(
        auth_session: Any = Depends(user_dependency),
    ) -> PurchaseHistoryResponse:
        _require_enabled()
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            rows = (
                db.execute(
                    select(CasePurchase)
                    .where(CasePurchase.user_id == user.id)
                    .order_by(CasePurchase.created_at.desc())
                    .limit(100)
                )
                .scalars()
                .all()
            )
            return PurchaseHistoryResponse(
                purchases=[
                    PurchaseSummary(
                        id=r.id,
                        tier=r.tier,
                        pack_sku=r.pack_sku,
                        quantity=r.quantity,
                        amount_paid_cents=r.amount_paid_cents,
                        currency=r.currency,
                        created_at=r.created_at.isoformat(),
                    )
                    for r in rows
                ]
            )

    return router


def build_dormant_billing_router(
    *,
    db_session_factory: Callable[[], Any] | None = None,
    user_dependency: Callable[..., Any] | None = None,
    user_resolver: Callable[[Any, Any], Any] | None = None,
) -> APIRouter:
    """Mount a stub router that 503s purchase endpoints but serves real
    credit balances on ``GET /me`` so the billing page accurately
    reflects admin-granted credits even before Stripe is configured.

    ``GET /catalog`` always works (price list for the pricing page).
    ``GET /me`` returns real per-tier credit counts when ``db_session_factory``,
    ``user_dependency``, and ``user_resolver`` are all provided; otherwise
    it returns an empty-balance response so the page still renders without
    crashing. Checkout / webhook endpoints always 503 when billing is
    dormant — users cannot purchase credits until Stripe is wired up.
    """
    router = APIRouter(prefix="/v1/billing", tags=["billing"])
    pricing = get_pricing_settings()

    checkout_detail = {
        "code": "billing_disabled",
        "message": (
            "Billing is not enabled on this deployment. Set "
            "ADVISOR_BILLING_ENABLED=true and configure STRIPE_* env vars."
        ),
    }

    @router.get("/catalog", response_model=CatalogResponse)
    def get_catalog_disabled() -> CatalogResponse:
        # The catalog can render without Stripe configured — every
        # offer's ``available`` flag is False so the frontend renders
        # the SKU but disables the "Buy" button.
        offers = [
            CatalogOffer(
                tier=offer.tier.name,
                tier_display_name=offer.tier.display_name,
                tier_token_budget=offer.tier.token_budget,
                pack_sku=offer.pack.sku,
                pack_display_name=offer.pack.display_name,
                quantity=offer.pack.quantity,
                discount_bps=offer.pack.discount_bps,
                list_price_cents=offer.list_price_cents,
                amount_due_cents=offer.amount_due_cents,
                currency=pricing.display_currency,
                available=False,
            )
            for offer in all_offers()
        ]
        return CatalogResponse(
            enabled=False,
            currency=pricing.display_currency,
            cad_per_usd=pricing.cad_per_usd,
            offers=offers,
        )

    @router.get("/questions", response_model=QuestionMenuResponse)
    def get_questions_disabled() -> QuestionMenuResponse:
        # The question menu renders without Stripe configured — every
        # question's ``available`` flag is False so the frontend shows
        # the menu but disables the "Buy" button.
        return QuestionMenuResponse(
            enabled=False,
            currency=pricing.display_currency,
            cad_per_usd=pricing.cad_per_usd,
            questions=_build_question_menu(
                settings=None, enabled=False, currency=pricing.display_currency
            ),
        )

    @router.post("/checkout/pack")
    def post_checkout_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=checkout_detail,
        )

    @router.post("/webhook")
    def post_webhook_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=checkout_detail,
        )

    # ``GET /me`` — return real credit balances even when billing is
    # dormant so the billing page shows the accurate tier breakdown for
    # admin-granted credits. Two flavours depending on whether DB deps
    # are wired: with deps we read from Postgres; without deps we return
    # an empty response that the frontend renders as all-zero balances.
    if (
        db_session_factory is not None
        and user_dependency is not None
        and user_resolver is not None
    ):
        @contextmanager
        def _open_db_dormant() -> Any:
            result = db_session_factory()
            if hasattr(result, "__enter__"):
                with result as session:
                    yield session
            else:
                try:
                    yield result
                finally:
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()

        @router.get("/me", response_model=BillingMeResponse)
        def get_me_dormant(
            auth_session: Any = Depends(user_dependency),
        ) -> BillingMeResponse:
            with _open_db_dormant() as db:
                user = user_resolver(auth_session, db)
                balances = credit_balance_for(db, user_id=user.id)
                tier_balances = [
                    TierBalance(
                        tier=b.tier,
                        available=b.available,
                        reserved=b.reserved,
                        consumed=b.consumed,
                    )
                    for b in balances
                ]
                return BillingMeResponse(
                    enabled=False,
                    stripe_customer_id=None,
                    tier_balances=tier_balances,
                    total_available_credits=sum(
                        b.available for b in tier_balances
                    ),
                    free_questions_remaining=user.free_questions_remaining,
                )

    else:
        @router.get("/me", response_model=BillingMeResponse)
        def get_me_dormant_no_db() -> BillingMeResponse:
            # No DB wired (minimal test setups). Return empty balances
            # rather than 503 so the frontend still renders gracefully.
            return BillingMeResponse(
                enabled=False,
                stripe_customer_id=None,
                tier_balances=[],
                total_available_credits=0,
            )

    @router.get("/purchases")
    def get_purchases_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=checkout_detail,
        )

    return router
