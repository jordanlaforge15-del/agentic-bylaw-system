"""Dev-only entrypoint for the Halifax Bylaw Advisor API.

Run with::

    uvicorn advisor.api.dev:app --host 127.0.0.1 --port 8000 --reload

Two auth modes, picked by env var:

* **Default (no env)** — the chat routes accept the
  ``X-Test-User-Id`` header instead of a Clerk JWT. This is the
  test-only fallback in :func:`advisor.api.app.create_app`. Lets us
  iterate on the frontend without Clerk credentials.

* **Clerk opt-in** — set ``CLERK_JWKS_URL`` (and optionally
  ``CLERK_AUDIENCE`` / ``CLERK_ISSUER``) to wire a real
  ``ClerkVerifier``. The chat routes then require an
  ``Authorization: Bearer <jwt>`` header, exactly like production.
  Useful for integration-testing the Next.js Clerk flow against a
  local FastAPI without standing up the full prod entrypoint.

Other dev-only choices (unchanged):

* **In-memory session store** — we deliberately do *not* pass
  ``db_session_factory`` so the advisor uses ``InMemorySessionStore``
  and skips quota enforcement. Sessions die on restart; that's fine
  for dev.
* **Retrieval still hits the real DB** — the retrieval factory uses
  ``layer1.db.session.session_scope``, so chat answers are grounded in
  the ingested HRM bylaw, not a stub.
* **Permissive CORS** — allows the Next dev server at
  ``http://localhost:3000`` to call ``/v1/chat`` directly. Only the
  origins listed in ``ADVISOR_DEV_CORS_ORIGINS`` (comma-separated) get
  through; default is just ``http://localhost:3000``.

Env vars expected:
    ANTHROPIC_API_KEY  — required (read by advisor.llm.registry).
    DATABASE_URL       — required (read by layer1.config; defaults to
                         the docker-compose layer1 connection string).
    ADVISOR_DEV_CORS_ORIGINS — optional, comma-separated, defaults to
                         http://localhost:3000.
    CLERK_JWKS_URL     — optional. Set to enable real Clerk JWT
                         verification. When unset, the X-Test-User-Id
                         fallback is used.

This module is *not* imported by the production entrypoint and never
runs in prod. The fallback auth header (``X-Test-User-Id``) makes
chat trivially impersonatable, so this server must never face the
public internet without ``CLERK_JWKS_URL`` set.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from advisor.api.app import create_app
from advisor.auth.clerk import ClerkVerifier
from advisor.auth.settings import build_verifier as build_clerk_verifier
from advisor.llm.registry import build_gateway
from layer1.db.session import session_scope

logger = logging.getLogger(__name__)


def _maybe_build_verifier() -> ClerkVerifier | None:
    """Return a ``ClerkVerifier`` when ``CLERK_JWKS_URL`` is set, else ``None``.

    Mirrors the prod entrypoint's logic so the dev server can be
    flipped between modes by setting / unsetting one env var. We
    deliberately do not consult ``CLERK_SECRET_KEY`` here — the
    advisor backend never calls Clerk's REST API; it only verifies
    tokens against the public JWKS.
    """
    if not os.environ.get("CLERK_JWKS_URL"):
        return None
    return build_clerk_verifier()


def build_dev_app() -> FastAPI:
    """Construct the dev FastAPI app.

    Two wirings, chosen by ``CLERK_JWKS_URL``:

    * **Clerk mode (``CLERK_JWKS_URL`` set).** Real verifier + DB
      session factory. ``app.create_app`` then takes the production
      code path: Bearer JWT → ``ClerkVerifier`` → ``resolve_or_create_user``
      → ``User`` row. Sessions are DB-backed and persist across
      restarts. Required for hand-testing as a real Clerk user without
      a 401 on every authed request.

    * **Permissive mode (no ``CLERK_JWKS_URL``).** No verifier, no DB
      session factory. The chat / cases routes fall back to the
      ``X-Test-User-Id`` header path with an in-memory session store.
      Fast iteration mode — no Clerk credentials required, but sessions
      die on restart and quota enforcement is bypassed.
    """
    gateway = build_gateway()
    verifier = _maybe_build_verifier()
    # When Clerk is on we want the full DB-backed user-resolution
    # path; when it's off we keep the in-memory store so contributors
    # who don't have Clerk credentials can still iterate. Conflating
    # auth-mode with storage-mode is intentional — the two only make
    # sense together: real Clerk JWTs need a DB row to bind to, and
    # the X-Test-User-Id fallback is meaningless without an in-memory
    # store to back it.
    db_session_factory = session_scope if verifier is not None else None
    app = create_app(
        gateway=gateway,
        verifier=verifier,
        db_session_factory=db_session_factory,
    )

    origins_env = os.environ.get(
        "ADVISOR_DEV_CORS_ORIGINS", "http://localhost:3000"
    )
    origins = [o.strip() for o in origins_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        # Allow both the test-only header and the real Authorization
        # header so the dev server works in either auth mode without a
        # CORS reconfig.
        allow_headers=[
            "Content-Type",
            "X-Test-User-Id",
            "Authorization",
            "Last-Event-ID",
        ],
        expose_headers=["X-Session-Id"],
    )

    if verifier is None:
        logger.warning(
            "advisor.api.dev is running in PERMISSIVE mode — "
            "X-Test-User-Id header is the only auth. Do NOT expose "
            "this server to the public internet. Set CLERK_JWKS_URL to "
            "enable real Clerk JWT verification."
        )
    else:
        logger.info(
            "advisor.api.dev is running with Clerk JWT verification "
            "(CLERK_JWKS_URL=%s). The X-Test-User-Id fallback is "
            "disabled.",
            os.environ.get("CLERK_JWKS_URL"),
        )
    return app


app = build_dev_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "advisor.api.dev:app",
        host=os.environ.get("ADVISOR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ADVISOR_PORT", "8000")),
        reload=False,
    )
