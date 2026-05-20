"""FastAPI router for the Terms-and-Conditions click-wrap gate.

Two endpoints, both auth-required:

* ``GET /v1/terms/current`` — returns the live T&C body, the version
  hash, and whether the calling user has already accepted the current
  version. The body is intended to be rendered verbatim on the
  ``/app/terms`` screen — the frontend is just a markdown renderer
  over what the server sends.

* ``POST /v1/terms/accept`` — records the click. The client posts the
  ``version`` hash it saw; the server cross-checks it against the live
  hash and refuses stale hashes with 409 (i.e. the document changed
  between fetch and click — make the user re-read). On success, the
  row carries the request's IP and User-Agent so the acceptance
  evidence captures the actual client.

The 412 vs 409 distinction matters:

  * 409 (``terms_version_mismatch``) = "the version you tried to
    accept is not the current version." Likely because the document
    changed; the client should re-fetch and re-display.
  * 412 (``terms_not_accepted``) = "you haven't accepted the current
    version." Returned by ``require_accepted_current_terms`` (in
    ``advisor.api.auth``) when a guarded endpoint is called before the
    gate has been satisfied.

Both client behaviours feed the same recovery: navigate to
``/app/terms`` and click I Agree.

Builder pattern matches the other v1 routers: dependencies are
injected so tests can wire a synthetic ``X-Test-User-Id`` user
without standing up Clerk.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from advisor.db.models import User
from advisor.legal import (
    get_current_terms,
    record_acceptance,
    user_has_accepted_current_terms,
)
from layer1.db.base import utcnow

logger = logging.getLogger(__name__)


UserResolver = Callable[[Any, Session], User]


class TermsCurrentResponse(BaseModel):
    """Body of ``GET /v1/terms/current``."""

    version: str
    body: str
    accepted: bool


class TermsAcceptRequest(BaseModel):
    """Body of ``POST /v1/terms/accept``."""

    # The version hash the client is acknowledging. Required: an
    # acceptance with no version reference would be evidentially
    # useless. The server cross-checks against the live hash.
    version: str = Field(min_length=64, max_length=64)


class TermsAcceptResponse(BaseModel):
    accepted: bool
    version: str
    accepted_at: str  # ISO 8601


def build_terms_router(
    *,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
) -> APIRouter:
    """Assemble the terms router.

    ``user_resolver`` lifts whatever the auth dependency yields
    (``ClerkSession`` in production, ``_TestUser`` in the test-header
    fallback) into a real ``advisor_user`` row so the row inserted on
    acceptance points at the right user.
    """
    router = APIRouter(prefix="/v1/terms", tags=["terms"])

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

    def _resolve_user_jit(auth_session: Any, db: Session) -> User:
        """Resolve the caller's User, creating a stub row on first contact.

        In production, ``user_dependency`` is wired to
        ``current_user_dependency`` which already JIT-creates the user
        via ``resolve_or_create_user``. In the test path (``X-Test-User-Id``
        header → ``_TestUser`` shim), the standard ``user_resolver``
        raises ``LookupError`` when the user doesn't exist yet, which
        500s.

        Terms is the first endpoint a fresh user touches after sign-up
        (server-side redirect from ``/app``), so it must tolerate a
        missing row and create one. We delegate to ``user_resolver``
        first; only on lookup failure do we mint a stub with the
        external id as both ``clerk_user_id`` and ``email`` (a real
        Clerk-backed flow would have filled both via the verifier).
        """
        try:
            return user_resolver(auth_session, db)
        except LookupError:
            # Test-path fallback: the X-Test-User-Id header named a user
            # that doesn't exist yet. Insert a minimal row so the
            # acceptance check has something to point at.
            external_id = getattr(auth_session, "clerk_user_id", None)
            if not external_id:
                # No usable identifier — re-raise so the caller sees the
                # original 500 rather than a confusing silent insert.
                raise
            user = User(
                clerk_user_id=external_id,
                email=f"{external_id}@e2e.invalid",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.add(user)
            db.flush()
            db.commit()
            return user

    @router.get("/current", response_model=TermsCurrentResponse)
    def get_current(
        auth_session: Any = Depends(user_dependency),
    ) -> TermsCurrentResponse:
        """Return the live T&C body + version + this user's accept status."""
        current = get_current_terms()
        with _open_db() as db:
            user = _resolve_user_jit(auth_session, db)
            accepted = user_has_accepted_current_terms(db, user)
        return TermsCurrentResponse(
            version=current.version_hash,
            body=current.body,
            accepted=accepted,
        )

    @router.post(
        "/accept",
        response_model=TermsAcceptResponse,
        status_code=status.HTTP_200_OK,
    )
    def post_accept(
        body: TermsAcceptRequest,
        request: Request,
        auth_session: Any = Depends(user_dependency),
    ) -> TermsAcceptResponse:
        """Record acceptance of the current T&C version for the calling user."""
        current = get_current_terms()
        if body.version != current.version_hash:
            # Document changed between fetch and click. Refuse the
            # stale-hash accept so the user can re-read the live text;
            # signing an old version would defeat the click-wrap.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "terms_version_mismatch",
                    "message": (
                        "The Terms and Conditions have changed since "
                        "you opened this page. Please refresh and "
                        "review the current version before accepting."
                    ),
                    "current_version": current.version_hash,
                },
            )

        ip = _client_ip(request)
        user_agent = request.headers.get("user-agent")

        with _open_db() as db:
            user = _resolve_user_jit(auth_session, db)
            row = record_acceptance(
                db,
                user=user,
                version_hash=current.version_hash,
                ip=ip,
                user_agent=user_agent,
            )
            db.commit()
        return TermsAcceptResponse(
            accepted=True,
            version=row.version_hash,
            accepted_at=row.accepted_at.isoformat(),
        )

    return router


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP, honouring the upstream proxy header.

    The Next.js proxy forwards the original client address in
    ``x-forwarded-for``; we take the first hop. Falls back to the raw
    socket peer if the header is absent (direct-to-FastAPI test
    traffic). Returns ``None`` rather than an empty string so the DB
    column stores NULL when truly unknown.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        cleaned = real_ip.strip()
        if cleaned:
            return cleaned
    if request.client is not None and request.client.host:
        return request.client.host
    return None
