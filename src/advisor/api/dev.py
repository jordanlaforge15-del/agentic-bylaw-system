"""Dev-only entrypoint for the Halifax Bylaw Advisor API.

Run with::

    uvicorn advisor.api.dev:app --host 127.0.0.1 --port 8000 --reload

Differences from :mod:`advisor.api.main` (production):

* **No Clerk verifier** — the chat routes accept the ``X-Test-User-Id``
  header instead of a Clerk JWT. This is the existing test-only
  fallback in :func:`advisor.api.app.create_app`. Lets us iterate on
  the frontend without standing up Clerk.
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

This module is *not* imported by the production entrypoint and never
runs in prod. The fallback auth header (``X-Test-User-Id``) makes
chat trivially impersonatable, so this server must never face the
public internet.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from advisor.api.app import create_app
from advisor.llm.registry import build_gateway

logger = logging.getLogger(__name__)


def build_dev_app() -> FastAPI:
    """Construct the dev FastAPI app.

    No Clerk, no DB-backed session store, no quota — but real LLM and
    real retrieval. Identity comes from ``X-Test-User-Id``.
    """
    gateway = build_gateway()
    # Deliberately omit verifier and db_session_factory: that activates
    # the X-Test-User-Id fallback and the in-memory store. Retrieval
    # uses the default factory, which opens a layer1 session_scope per
    # tool call — same as production.
    app = create_app(gateway=gateway)

    origins_env = os.environ.get(
        "ADVISOR_DEV_CORS_ORIGINS", "http://localhost:3000"
    )
    origins = [o.strip() for o in origins_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        # X-Test-User-Id is the dev auth header. We expose Last-Event-ID
        # for SSE reconnects, though we don't currently honour them.
        allow_headers=["Content-Type", "X-Test-User-Id", "Last-Event-ID"],
        expose_headers=["X-Session-Id"],
    )

    logger.warning(
        "advisor.api.dev is running in PERMISSIVE mode — X-Test-User-Id "
        "header is the only auth. Do NOT expose this server to the "
        "public internet."
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
