"""FastAPI router for the Clerk webhook endpoint.

Single route: ``POST /v1/webhooks/clerk``. No auth on the endpoint
itself — the svix signature is the authentication. The Clerk
dashboard's "Webhook signing secret" (``CLERK_WEBHOOK_SECRET``,
starts with ``whsec_``) is what proves the request came from Clerk
and not from someone who guessed the URL.

Mounted by ``advisor.api.app.create_app`` when both a Clerk verifier
AND a webhook secret are wired. Without either, the endpoint stays
unmounted — there's no useful 503 stub for this one (unlike billing
the frontend probes /v1/billing/me regardless, but nothing probes the
webhook URL except Clerk's delivery infrastructure).

Why the route returns 200 even on dispatch errors:
  Clerk treats 4xx/5xx as "please retry" and queues the event for
  several days. A bug in our handler would otherwise cause an event
  storm that we can't drain without fixing the bug and waiting out
  the retry queue. We return 200 with ``handled=false`` and a log
  message — operator sees the issue in our logs, Clerk's dashboard
  shows the delivery as successful, and there's no retry amplification.

  The exception is signature failure: that returns 400, both because
  a bad signature is never a transient error (no point retrying) AND
  because we want Clerk's dashboard to flag the failed delivery so the
  operator notices.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from advisor.auth.webhooks import handle_event, verify_signature

logger = logging.getLogger(__name__)


def build_clerk_webhook_router(
    *,
    webhook_secret: str,
    db_session_factory: Callable[[], Any],
) -> APIRouter:
    """Assemble the Clerk webhook router.

    Args:
        webhook_secret: ``CLERK_WEBHOOK_SECRET`` from env. Required —
            without it we can't verify signatures, and an unsigned
            webhook endpoint is a remote-write hole.
        db_session_factory: Callable yielding a SQLAlchemy session
            (or a context manager around one — we adapt both shapes
            for parity with the rest of the API wiring).
    """
    if not webhook_secret:
        # Defensive: factory should only be called when the secret is
        # present, but failing loud here makes the misconfiguration
        # obvious in tests instead of silently mounting an open route.
        raise ValueError("CLERK_WEBHOOK_SECRET is required to build the router")

    router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

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

    @router.post("/clerk")
    async def post_clerk_webhook(request: Request) -> dict[str, Any]:
        payload = await request.body()
        try:
            event = verify_signature(
                payload=payload,
                # Convert the multidict-style Headers object to a plain
                # dict so the helper can be tested without FastAPI in
                # the loop. Header names are case-insensitive on the
                # wire but Headers.dict() lowercases the keys, which
                # matches what verify_signature expects.
                headers=dict(request.headers),
                secret=webhook_secret,
            )
        except ValueError as exc:
            logger.warning("clerk webhook signature verification failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_signature",
                    "message": str(exc),
                },
            ) from exc

        with _open_db() as db:
            result = handle_event(db, event)
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()

        return {
            "handled": result.handled,
            "event_type": result.event_type,
            "event_id": result.event_id,
            "note": result.note,
        }

    return router
