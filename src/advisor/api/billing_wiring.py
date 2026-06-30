"""Shared billing-router wiring for the FastAPI entrypoints.

The live billing router is wired identically by the production entrypoint
(:mod:`advisor.api.main`) and the dev entrypoint (:mod:`advisor.api.dev`).
The kwargs builder originally lived in ``main``, but ``main`` constructs
the production app at import time (``app = build_app()``), so ``dev`` could
not import the helper from it without triggering a full prod-app build
(Sentry init, real gateway, etc.). Extracting the builder here lets every
entrypoint share one implementation with no import-time side effects.

See ABS-341: before this module existed, ``advisor.api.dev`` never passed
billing kwargs to ``create_app`` at all, so ``ADVISOR_BILLING_ENABLED`` was
a silent no-op on the local dev server.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from advisor.api.auth import resolve_or_create_user
from advisor.auth.clerk import ClerkVerifier
from advisor.auth.fastapi import clerk_session_dependency
from advisor.billing.client import LiveStripeClient, StripeClient
from advisor.billing.settings import AdvisorBillingSettings
from layer1.db.session import session_scope


def build_billing_kwargs(
    *,
    verifier: ClerkVerifier | None,
    billing_settings: AdvisorBillingSettings,
) -> dict[str, Any]:
    """Compose the billing kwargs for :func:`advisor.api.app.create_app`.

    When billing is disabled we still pass the settings so ``create_app``
    can short-circuit to the dormant router (which still serves real
    credit balances on ``GET /me``). When enabled we additionally wire the
    Stripe client factory, the user dependency (Clerk session), and the
    user resolver.

    Raises ``RuntimeError`` when billing is enabled but Clerk isn't wired
    — the live billing router requires an authenticated caller and we
    don't allow paid/credit endpoints behind the test-header fallback.
    """
    kwargs: dict[str, Any] = {"billing_settings": billing_settings}
    if not billing_settings.enabled:
        return kwargs

    if verifier is None:
        raise RuntimeError(
            "ADVISOR_BILLING_ENABLED=true requires a Clerk verifier; "
            "set CLERK_JWKS_URL to enable real auth before enabling "
            "billing."
        )

    # ABS-322: the Stripe client is required ONLY when payments are on.
    # In payments-off / free-trial mode (ADVISOR_PAYMENTS_ENABLED=false,
    # the go-live default) the buy-an-answer flow consumes free-question
    # credits and never touches Stripe, so a STRIPE_API_KEY is not
    # needed — the live router mounts with no client factory.
    stripe_client_factory: Callable[[], StripeClient] | None = None
    if billing_settings.payments_enabled:
        api_key = billing_settings.stripe_api_key
        if not api_key:
            raise RuntimeError(
                "ADVISOR_PAYMENTS_ENABLED=true requires STRIPE_API_KEY."
            )

        def _stripe_client_factory() -> StripeClient:
            return LiveStripeClient(api_key=api_key)

        stripe_client_factory = _stripe_client_factory

    require_clerk_session = clerk_session_dependency(verifier)

    def _user_resolver(clerk_session: Any, db: Any) -> Any:
        user = resolve_or_create_user(db, clerk_session)
        # The billing router opens its own DB session and reads the
        # user from it; commit so the row is visible if we just
        # created it. ``resolve_or_create_user`` deliberately doesn't
        # commit so it composes inside larger transactions, which is
        # why we commit here.
        db.commit()
        db.refresh(user)
        return user

    kwargs.update(
        stripe_client_factory=stripe_client_factory,
        billing_db_session_factory=session_scope,
        billing_user_dependency=require_clerk_session,
        billing_user_resolver=_user_resolver,
    )
    return kwargs
