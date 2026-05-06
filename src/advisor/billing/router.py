"""FastAPI router for the billing endpoints.

Three endpoints:

* ``POST /v1/billing/checkout`` — auth-required. Creates a Stripe
  Checkout session and returns its URL.
* ``POST /v1/billing/webhook`` — no auth; verified via
  ``Stripe-Signature``. Applies the event to the database.
* ``GET /v1/billing/me`` — auth-required. Reads the current plan
  state for the frontend's account / billing page.

Every endpoint short-circuits to HTTP 503 when
``settings.enabled is False``. That's the dormant-by-default safety:
the FastAPI app boots and serves /healthz, the frontend can probe
``/v1/billing/me`` without crashing, but no Stripe code path runs
until an operator flips the flag.

The ``build_billing_router`` factory takes the dependencies (settings,
client factory, db session factory, user dependency) so:

* Tests inject a ``MockStripeClient`` and an in-memory db.
* Production wires the ``LiveStripeClient`` factory and the real
  ``session_scope`` from layer1.
* The user dependency comes from ``advisor.auth`` in production but
  can be a stub in tests, so we don't need a working Clerk JWKS
  endpoint to test billing.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from advisor.billing.checkout import (
    FreeTierCheckoutError,
    PriceNotConfiguredError,
    UnknownTierError,
    start_checkout,
)
from advisor.billing.client import StripeClient
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
from advisor.db.models import User

logger = logging.getLogger(__name__)


class CheckoutRequest(BaseModel):
    target_tier: str = Field(
        ..., description="Target plan tier (e.g. 'pro', 'team')."
    )


class CheckoutResponse(BaseModel):
    url: str = Field(..., description="Stripe Checkout redirect URL.")


class BillingMeResponse(BaseModel):
    plan_tier: str
    monthly_query_limit: int
    monthly_queries_used: int
    stripe_customer_id: str | None
    subscription_status: str | None
    enabled: bool = Field(
        ...,
        description=(
            "Whether the backend has billing enabled at all. When False "
            "the frontend should hide upgrade CTAs."
        ),
    )


# Resolver type: the auth dependency yields some session object; the
# user resolver maps that to a ``User`` row. Production passes a real
# Clerk session through; tests can pass any object type that the
# resolver knows how to handle.
UserResolver = Callable[[Any, Session], User]


def build_billing_router(
    *,
    settings: AdvisorBillingSettings,
    client_factory: Callable[[], StripeClient] | None,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
) -> APIRouter:
    """Assemble the billing router.

    ``client_factory`` may be ``None`` when ``settings.enabled`` is
    False — we never instantiate a client in that case.
    ``db_session_factory`` is a callable that yields a ``Session``;
    we accept either a context manager or a plain factory and adapt.
    ``user_dependency`` is a FastAPI ``Depends`` callable that
    yields whatever the auth layer produces (a ``ClerkSession`` in
    production). ``user_resolver`` turns that opaque session value
    into a ``User`` row, given a db ``Session``.
    """
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

    @router.post("/checkout", response_model=CheckoutResponse)
    def post_checkout(
        body: CheckoutRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> CheckoutResponse:
        _require_enabled()
        if client_factory is None:
            # Defensive: enabled=True without a client_factory is a
            # wiring bug. Fail loud rather than silently returning a
            # broken URL.
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
                url = start_checkout(
                    db,
                    user,
                    target_tier=body.target_tier,
                    client=client_factory(),
                    settings=settings,
                )
            except (UnknownTierError, FreeTierCheckoutError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "invalid_target_tier",
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
            return BillingMeResponse(
                plan_tier=user.plan_tier,
                monthly_query_limit=user.monthly_query_limit,
                monthly_queries_used=user.monthly_queries_used,
                stripe_customer_id=user.stripe_customer_id,
                subscription_status=user.subscription_status,
                enabled=settings.enabled,
            )

    return router


def build_dormant_billing_router() -> APIRouter:
    """Mount a stub router that returns 503 on every billing path.

    Used by ``create_app`` when no billing settings are wired (or
    when ``settings.enabled`` is False AND the operator hasn't
    configured the dependencies). Lets the frontend probe the
    billing endpoints without the backend exploding.
    """
    router = APIRouter(prefix="/v1/billing", tags=["billing"])

    detail = {
        "code": "billing_disabled",
        "message": (
            "Billing is not enabled on this deployment. Set "
            "ADVISOR_BILLING_ENABLED=true and configure STRIPE_* env vars."
        ),
    }

    @router.post("/checkout")
    def post_checkout_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail
        )

    @router.post("/webhook")
    def post_webhook_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail
        )

    @router.get("/me")
    def get_me_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail
        )

    return router
