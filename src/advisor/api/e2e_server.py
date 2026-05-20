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
import uuid
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from advisor.api.app import create_app
from advisor.db.models import InviteRequest, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from layer1.db.base import utcnow
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
            "X-Test-User-Email",
            "X-Test-User-Full-Name",
            "Authorization",
            "Last-Event-ID",
        ],
        expose_headers=["X-Session-Id"],
    )

    _mount_test_router(app)

    logger.warning(
        "advisor.api.e2e_server is running with MockGateway and the "
        "X-Test-User-Id header fallback. This entrypoint MUST NOT be "
        "exposed to the public internet."
    )
    return app


# ---------------------------------------------------------------------------
# Test-only auth-lifecycle router (mounted only by the e2e entrypoint).
#
# The Playwright suite uses these endpoints to drive the sign-up /
# admin-approval / first-login lifecycle that production wires through
# Clerk's allowlist + the /api/admin/invites/{id}/approve route. Both
# of those depend on Clerk being configured (JWKS, Backend API,
# allowlist) — none of which the e2e stack provides. By exposing a
# direct InviteRequest writer here, the auth specs can exercise the
# *post-Clerk* code path (resolve_or_create user, invite redemption,
# starter-credit gift) without re-hosting Clerk in tests.
#
# Endpoints are namespaced ``/v1/_test/...`` so they're impossible to
# confuse with the production surface. ``advisor.api.main`` never
# imports this module — the routes literally cannot exist outside the
# e2e entrypoint.
# ---------------------------------------------------------------------------


class _ApproveInviteBody(BaseModel):
    # Plain str to avoid the email-validator runtime dep — same choice
    # the rest of the codebase makes (see advisor.db.schemas).
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    starter_credits: int = Field(default=0, ge=0, le=1000)
    starter_tier: Literal["quick", "standard", "complex"] = "standard"


class _ResetUserBody(BaseModel):
    clerk_user_id: str = Field(min_length=1, max_length=255)


def _mount_test_router(app: FastAPI) -> None:
    """Wire the ``/v1/_test/...`` lifecycle endpoints onto ``app``."""

    @app.post("/v1/_test/invite-approve")
    async def invite_approve(body: _ApproveInviteBody) -> dict[str, object]:
        """Insert an ``approved`` ``InviteRequest`` for the given email.

        Production approves via Clerk's allowlist API + a DB write;
        we skip the Clerk side (no allowlist to enforce in the test
        stack) and write the row directly. On the user's first sign-in
        the e2e user-dependency redeems the row and gifts
        ``starter_credits`` of ``starter_tier``.
        """
        now = utcnow()
        with session_scope() as db:
            # Drop any existing row for this email so re-running a spec
            # doesn't trip the UNIQUE constraint on email. Cases that
            # need to test the "already redeemed" path can submit
            # through the public /api/invite first.
            existing = (
                db.query(InviteRequest)
                .filter(InviteRequest.email.ilike(body.email))
                .all()
            )
            for row in existing:
                db.delete(row)
            db.flush()
            invite = InviteRequest(
                id=f"e2e_{uuid.uuid4().hex[:12]}",
                email=body.email.lower(),
                name=body.name,
                status="approved",
                created_at=now,
                decided_at=now,
                decided_by="e2e-test",
                expires_at=None,
                granted_starter_credits=body.starter_credits,
                granted_starter_tier=body.starter_tier
                if body.starter_credits > 0
                else None,
            )
            db.add(invite)
            db.commit()
            return {
                "invite": {
                    "id": invite.id,
                    "email": invite.email,
                    "status": invite.status,
                    "starter_credits": invite.granted_starter_credits,
                    "starter_tier": invite.granted_starter_tier,
                }
            }

    @app.post("/v1/_test/reset-user")
    async def reset_user(body: _ResetUserBody) -> dict[str, object]:
        """Delete an ``advisor_user`` row + all dependent rows.

        Lets specs that mint a fresh user-id (timestamp-based) clean up
        their footprint if needed. Idempotent — missing user returns
        ``deleted=False`` rather than 404.
        """
        with session_scope() as db:
            user = (
                db.query(User)
                .filter(User.clerk_user_id == body.clerk_user_id)
                .one_or_none()
            )
            if user is None:
                return {"deleted": False}
            # advisor_user.id has ON DELETE CASCADE FKs from every
            # dependent table (cases, chat_sessions, credits, usage
            # events, purchases) — deleting the user row at the DB
            # layer cascades automatically. invite_request matches by
            # email rather than FK, so clean that up alongside.
            email = user.email
            db.delete(user)
            if email:
                db.query(InviteRequest).filter(
                    InviteRequest.email.ilike(email)
                ).delete(synchronize_session=False)
            db.commit()
            return {"deleted": True}


app = build_e2e_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "advisor.api.e2e_server:app",
        host=os.environ.get("ADVISOR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ADVISOR_PORT", "8001")),
        reload=False,
    )
