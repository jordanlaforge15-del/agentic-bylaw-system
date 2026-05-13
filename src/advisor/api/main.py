"""Production entrypoint for the Halifax Bylaw Advisor API.

Run with::

    uvicorn advisor.api.main:app --host 0.0.0.0 --port 8000

This module reads configuration from environment variables (or a local
``.env`` file via pydantic-settings) and constructs a fully wired
``FastAPI`` application via :func:`advisor.api.app.create_app`.

What's wired
------------

* **LLM gateway** — :func:`advisor.llm.registry.build_gateway` reads
  ``ADVISOR_LLM_PROVIDER`` / ``ADVISOR_LLM_MODEL`` /
  ``ANTHROPIC_API_KEY`` and returns the configured backend. We fail
  loud if ``ANTHROPIC_API_KEY`` is unset because this is the
  production entrypoint — silently falling back to a mock would be a
  trap.

* **Auth** — :func:`advisor.auth.settings.build_verifier` builds a
  ``ClerkVerifier`` from ``CLERK_JWKS_URL`` / ``CLERK_AUDIENCE`` /
  ``CLERK_ISSUER``. If ``CLERK_JWKS_URL`` is unset we log a WARNING
  and leave the verifier unwired — the chat routes then fall back to
  the ``X-Test-User-Id`` header. That fallback is intended for local
  dev only; production deployments must set ``CLERK_JWKS_URL``.

* **DB session factory** — bound to layer1's ``session_scope``,
  which reads ``DATABASE_URL`` via ``layer1.config.get_settings``.
  The advisor models share ``Base.metadata`` with layer1 so a single
  factory covers both schemas.

* **Billing** — :func:`advisor.billing.settings.get_settings` reads
  ``ADVISOR_BILLING_ENABLED`` and the ``STRIPE_*`` keys. When
  ``enabled=False`` (the default), :func:`create_app` mounts the
  dormant router that 503s every billing endpoint — this is the
  pre-Stripe-account state. When ``enabled=True`` we wire the live
  router using :class:`LiveStripeClient`, the Clerk verifier as the
  user dependency, and ``resolve_or_create_user`` as the user
  resolver. Enabling billing without a Clerk verifier is rejected at
  startup because the live billing router needs an authenticated
  user; falling back to the test header for paid endpoints would be
  unsafe.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI

from advisor.api.app import create_app
from advisor.api.auth import resolve_or_create_user
from advisor.auth.clerk import ClerkVerifier
from advisor.auth.fastapi import clerk_session_dependency
from advisor.auth.settings import build_verifier as build_clerk_verifier
from advisor.billing.client import LiveStripeClient, StripeClient
from advisor.billing.settings import (
    AdvisorBillingSettings,
    get_settings as get_billing_settings,
)
from advisor.llm.registry import build_gateway
from layer1.db.session import session_scope

logger = logging.getLogger(__name__)


def _build_verifier_or_none() -> ClerkVerifier | None:
    """Return a configured ``ClerkVerifier``, or ``None`` if unset.

    Returning ``None`` lets the chat routes fall back to the
    ``X-Test-User-Id`` header — the only auth mode that works without
    Clerk wiring. We log loud at startup so the operator can see the
    deployment is running in a permissive mode.
    """
    if not os.environ.get("CLERK_JWKS_URL"):
        logger.warning(
            "CLERK_JWKS_URL is not set — Clerk auth is disabled and the "
            "chat API will accept the X-Test-User-Id header instead. "
            "Set CLERK_JWKS_URL (and CLERK_AUDIENCE / CLERK_ISSUER) to "
            "enable real auth."
        )
        return None
    return build_clerk_verifier()


def _build_billing_kwargs(
    *,
    verifier: ClerkVerifier | None,
    billing_settings: AdvisorBillingSettings,
) -> dict[str, Any]:
    """Compose the billing kwargs for ``create_app``.

    When billing is disabled we still pass the settings so
    ``create_app`` can short-circuit to the dormant router. When
    enabled we additionally wire the Stripe client factory, the user
    dependency (Clerk session), and the user resolver.

    Raises ``RuntimeError`` when billing is enabled but Clerk isn't
    wired — the live billing router requires an authenticated caller
    and we don't allow paid endpoints behind the test-header
    fallback.
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

    api_key = billing_settings.stripe_api_key
    if not api_key:
        raise RuntimeError(
            "ADVISOR_BILLING_ENABLED=true requires STRIPE_API_KEY."
        )

    def _stripe_client_factory() -> StripeClient:
        return LiveStripeClient(api_key=api_key)

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
        stripe_client_factory=_stripe_client_factory,
        billing_db_session_factory=session_scope,
        billing_user_dependency=require_clerk_session,
        billing_user_resolver=_user_resolver,
    )
    return kwargs


def build_app() -> FastAPI:
    """Construct the production FastAPI app from environment config.

    Called at module import time to populate the module-level ``app``
    that uvicorn references. Tests should call
    :func:`advisor.api.app.create_app` directly with their own
    fixtures rather than going through this builder.
    """
    gateway = build_gateway()
    verifier = _build_verifier_or_none()
    billing_settings = get_billing_settings()
    billing_kwargs = _build_billing_kwargs(
        verifier=verifier, billing_settings=billing_settings
    )
    webhook_secret = os.environ.get("CLERK_WEBHOOK_SECRET") or None
    if verifier is None and webhook_secret:
        # Mounting the webhook without the JWT verifier is weird but
        # not unsafe — webhooks authenticate via svix signature, not
        # Bearer tokens — so we only log here, not raise.
        logger.warning(
            "CLERK_WEBHOOK_SECRET is set but CLERK_JWKS_URL is not. "
            "Webhook delivery will still work, but the chat API is "
            "running in the X-Test-User-Id fallback. Set CLERK_JWKS_URL "
            "before any public traffic hits /v1/chat."
        )
    elif verifier is not None and not webhook_secret:
        logger.warning(
            "CLERK_JWKS_URL is set but CLERK_WEBHOOK_SECRET is not — "
            "Clerk user lifecycle events (user.created / .updated / "
            ".deleted) will not be synced. Set CLERK_WEBHOOK_SECRET "
            "and configure the endpoint in your Clerk dashboard."
        )
    return create_app(
        gateway=gateway,
        verifier=verifier,
        db_session_factory=session_scope,
        clerk_webhook_secret=webhook_secret,
        **billing_kwargs,
    )


app = build_app()


if __name__ == "__main__":  # pragma: no cover — convenience for ``python -m``
    import uvicorn

    uvicorn.run(
        "advisor.api.main:app",
        host=os.environ.get("ADVISOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("ADVISOR_PORT", "8000")),
        reload=False,
    )
