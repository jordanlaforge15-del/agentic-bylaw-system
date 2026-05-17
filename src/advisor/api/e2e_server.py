"""End-to-end UI test entrypoint for the Halifax Bylaw Advisor API.

Run with::

    uvicorn advisor.api.e2e_server:app --host 127.0.0.1 --port 8001

Differences from ``advisor.api.dev`` and ``advisor.api.main``:

* **LLM gateway is always a ``MockGateway``** wired to
  ``advisor.llm.mock_dispatcher.build_dispatcher()``. Tests never call
  out to Anthropic, so no API key is required and responses are
  byte-deterministic — important for SSE streams whose content gets
  asserted in Playwright.
* **DB session store is enabled** (``db_session_factory=session_scope``)
  so the case-credit lifecycle, quota enforcement, and session
  persistence run for real against the test Postgres database. The
  ``DATABASE_URL`` env var must point at the test DB (default name
  ``layer1_test``).
* **No Clerk verifier.** The chat / cases / admin routers fall back to
  the ``X-Test-User-Id`` header — same path the Next.js proxy uses
  when ``CLERK_SECRET_KEY`` is unset.
* **Permissive CORS** for the Next.js dev server at
  ``http://localhost:3001`` (default; override via
  ``ADVISOR_E2E_CORS_ORIGINS``).

Never wire this entrypoint to production traffic — there is no auth.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from advisor.api.app import create_app
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from layer1.db.session import session_scope

logger = logging.getLogger(__name__)


def build_e2e_app() -> FastAPI:
    """Construct the test FastAPI app wired for end-to-end UI tests."""
    gateway = MockGateway(callable_=build_dispatcher())
    app = create_app(
        gateway=gateway,
        verifier=None,
        db_session_factory=session_scope,
    )

    origins_env = os.environ.get(
        "ADVISOR_E2E_CORS_ORIGINS", "http://localhost:3001"
    )
    origins = [o.strip() for o in origins_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-Test-User-Id",
            "Authorization",
            "Last-Event-ID",
        ],
        expose_headers=["X-Session-Id"],
    )

    logger.warning(
        "advisor.api.e2e_server is running with MockGateway and the "
        "X-Test-User-Id header fallback. This entrypoint MUST NOT be "
        "exposed to the public internet."
    )
    return app


app = build_e2e_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "advisor.api.e2e_server:app",
        host=os.environ.get("ADVISOR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ADVISOR_PORT", "8001")),
        reload=False,
    )
