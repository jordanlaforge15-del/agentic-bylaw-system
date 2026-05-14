"""Clerk Backend API client — fallback for missing JWT claims.

The JIT user-creation path in ``advisor.api.auth.resolve_or_create_user``
needs ``email`` (and optionally ``full_name``) to insert a new
``advisor_user`` row. The Bearer-token JWT carries both *only if* the
Clerk dashboard's JWT template explicitly includes them; the default
template omits ``email``. When the template is misconfigured (or the
operator hasn't gotten to it yet), this client is the safety net.

Why not call this on every request:
    Each call is an outbound HTTPS round-trip to api.clerk.com. The
    JWT path covers the common case for free. We only reach for the
    Backend API when the JWT didn't supply an email — typically once
    per user, on first sign-in.

Why a sync client (``httpx.Client``, not ``AsyncClient``):
    The JIT path runs inside a sync FastAPI dependency that already
    holds a SQLAlchemy ``Session``. Mixing async into that path would
    cost more than the occasional Backend API call.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from advisor.auth.email_extract import full_name, primary_email

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://api.clerk.com"
_DEFAULT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ClerkUserProfile:
    """Result of a Backend-API user lookup.

    Both fields may be ``None`` — Clerk users without an email exist
    (phone-only sign-ups, dashboard-created test fixtures with no
    email_address).
    """

    email: str | None
    full_name: str | None


class ClerkBackendError(Exception):
    """Raised when the Backend API call fails for a non-404 reason.

    A 404 is treated as "user does not exist on Clerk's side" and
    surfaces as a ``ClerkUserProfile(None, None)`` rather than an
    exception, so the caller can fall through to its 503 path
    without a try/except.
    """


class ClerkBackendClient:
    """Thin wrapper around ``GET /v1/users/{user_id}``.

    Construct with no args to pick up ``CLERK_SECRET_KEY`` /
    ``CLERK_API_BASE_URL`` from the environment, or pass explicit
    values for tests.
    """

    def __init__(
        self,
        *,
        secret_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._secret_key = secret_key or os.environ.get("CLERK_SECRET_KEY")
        self._base_url = (base_url or os.environ.get("CLERK_API_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s
        self._transport = transport

    @property
    def configured(self) -> bool:
        """True when we have a secret key to authenticate with.

        Callers check this before reaching for the client; an
        unconfigured client must NOT be used to "try anyway" — that
        would surface as a 401 from Clerk on every signup.
        """
        return bool(self._secret_key)

    def fetch_user(self, clerk_user_id: str) -> ClerkUserProfile:
        if not self._secret_key:
            raise ClerkBackendError("CLERK_SECRET_KEY is not configured")
        url = f"{self._base_url}/v1/users/{clerk_user_id}"
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                resp = client.get(
                    url,
                    headers={"Authorization": f"Bearer {self._secret_key}"},
                )
        except httpx.HTTPError as exc:
            raise ClerkBackendError(f"clerk backend network error: {exc}") from exc

        if resp.status_code == 404:
            logger.warning(
                "clerk backend: user %s not found (404); returning empty profile",
                clerk_user_id,
            )
            return ClerkUserProfile(email=None, full_name=None)
        if resp.status_code >= 400:
            raise ClerkBackendError(
                f"clerk backend returned {resp.status_code} for {clerk_user_id}: {resp.text[:200]}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise ClerkBackendError(f"clerk backend returned non-JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ClerkBackendError("clerk backend returned non-object body")

        return ClerkUserProfile(
            email=primary_email(data),
            full_name=full_name(data),
        )
