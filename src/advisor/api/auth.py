"""Clerk authentication + user-row resolution for the chat API.

Two helpers live here:

* ``resolve_or_create_user`` — given a verified ``ClerkSession`` and a
  SQLAlchemy ``Session``, find the matching ``advisor.db.models.User``
  row by ``clerk_user_id`` (creating it on first contact), and refresh
  ``email``/``full_name`` if the upstream Clerk profile has changed.
  The function does NOT commit; the caller decides when persistence
  happens so callers can compose this with their own transaction.

* ``current_user_dependency`` — composes ``clerk_session_dependency``
  with ``resolve_or_create_user`` to produce a FastAPI dependency that
  yields a ``User``.

Why resolve to the internal ``User.id`` instead of just passing the
Clerk id around:

  Foreign keys in ``advisor_chat_session`` / ``advisor_usage_event``
  reference ``advisor_user.id`` (an integer), not the opaque Clerk
  string. Re-resolving the FK from a Clerk string on every downstream
  query would mean an extra ``WHERE clerk_user_id = ?`` lookup per
  call. Doing it once at the top of the request and handing route
  handlers the already-loaded ``User`` keeps later code simple and
  efficient.

Why we commit before the route handler runs:

  Route handlers may pass the ``User`` (or its id) into other code
  paths that open their own SQLAlchemy session — e.g. the chat session
  store, a tool that records a usage event, a billing helper. If we
  hadn't committed the new user row by then, those parallel sessions
  would not see it. Committing inside the dependency, before the
  handler is invoked, makes the user row durable for the rest of the
  request lifecycle regardless of how many sessions touch it.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from advisor.auth.clerk import ClerkVerifier
from advisor.auth.clerk_backend import ClerkBackendClient, ClerkBackendError
from advisor.auth.fastapi import clerk_session_dependency
from advisor.auth.session import ClerkSession
from advisor.db.models import InviteRequest, User
from layer1.db.base import utcnow

logger = logging.getLogger(__name__)


def resolve_or_create_user(
    db: Session,
    clerk_session: ClerkSession,
    *,
    backend_client: ClerkBackendClient | None = None,
) -> User:
    """Return the ``User`` row for ``clerk_session`` or create one.

    Lookup is by the unique ``clerk_user_id`` index. If the row exists
    and the ``email`` or ``full_name`` from Clerk has drifted, the row
    is updated in-place and ``updated_at`` is bumped — the next commit
    flushes the change.

    The caller is responsible for committing. We deliberately don't
    commit here so this helper composes inside any larger transaction
    (e.g. a webhook that batches several user updates).

    Args:
        db: Open SQLAlchemy session bound to the advisor schema.
        clerk_session: Already-verified Clerk session.
        backend_client: Optional Clerk Backend API client used to fill
            ``email``/``full_name`` when the JWT didn't supply them.
            Pass ``None`` to construct a default client that reads
            ``CLERK_SECRET_KEY`` from the environment. The fallback
            only triggers when ``clerk_session.email`` is empty AND
            we're inserting a brand-new row — committing ``""`` to a
            NOT NULL column defeats the constraint.

    Returns:
        The persistent ``User`` (newly added or fetched). The row is
        flushed so ``.id`` is populated; the caller's commit makes the
        row visible to other sessions.

    Raises:
        HTTPException(503): When we need to insert a new row but
            neither the JWT nor the Backend API yields an email. The
            request fails loudly instead of silently writing ``""`` —
            an operator-visible signal that the JWT template or
            backend key needs attention.
    """
    clerk_user_id = clerk_session.user_id
    email = clerk_session.email or ""
    full_name = _extract_full_name(clerk_session.raw_claims)

    user = (
        db.query(User).filter(User.clerk_user_id == clerk_user_id).one_or_none()
    )
    if user is None:
        if not email:
            # JWT didn't carry the email claim (default Clerk template
            # omits it). Ask Clerk's Backend API once, on first sign-in,
            # so this user lands with a real email instead of "".
            email, full_name = _fetch_profile_for_insert(
                clerk_user_id=clerk_user_id,
                fallback_full_name=full_name,
                backend_client=backend_client,
            )
        user = User(
            clerk_user_id=clerk_user_id,
            email=email,
            full_name=full_name,
        )
        db.add(user)
        # Flush so ``user.id`` is populated for the caller. The commit
        # is left to the caller (see module docstring).
        db.flush()
        # If this user came in via an approved invite_request row with
        # ``granted_starter_credits > 0``, gift those credits now.
        # Lookup is by email case-insensitively. Treat the invite as
        # redeemed in the same transaction so the expiry sweep stops
        # considering it for cleanup. Importing inside the branch
        # avoids the case-service module being a hard dependency of
        # the auth module — tests that don't exercise invites don't
        # need to wire ``advisor.db.cases``.
        if email:
            invite = (
                db.query(InviteRequest)
                .filter(
                    InviteRequest.email.ilike(email),
                    InviteRequest.status == "approved",
                )
                .one_or_none()
            )
            if invite is not None:
                if invite.granted_starter_credits > 0 and invite.granted_starter_tier:
                    from advisor.db.cases import grant_admin_credits  # noqa: PLC0415

                    grant_admin_credits(
                        db,
                        user=user,
                        tier=invite.granted_starter_tier,
                        quantity=invite.granted_starter_credits,
                        reason=f"invite_redemption:{invite.id}",
                    )
                invite.redeemed_at = utcnow()
                db.add(invite)
        # Safety net: every brand-new user gets the default trial pack
        # if nothing upstream (invite redemption, admin grant) already
        # gave them credits. Without this, users created outside the
        # invite flow — and users whose invite carried 0 starter
        # credits — would land on /app with no way to open a case.
        from advisor.db.cases import (  # noqa: PLC0415
            grant_starter_credits_if_needed,
        )

        grant_starter_credits_if_needed(db, user=user)
        return user

    # Refresh the mutable profile fields if Clerk has new values. We
    # only overwrite when Clerk actually provided a non-empty value;
    # an absent ``email`` claim shouldn't blank out a previously stored
    # email.
    changed = False
    if email and user.email != email:
        user.email = email
        changed = True
    if full_name is not None and user.full_name != full_name:
        user.full_name = full_name
        changed = True
    if changed:
        user.updated_at = utcnow()
        db.flush()
    # Self-heal existing users who pre-date the starter-grant logic
    # (or whose account was created via a code path that skipped it).
    # The helper is a no-op when the user already has any credits in
    # any state, so this is safe to call on every authenticated request.
    from advisor.db.cases import (  # noqa: PLC0415
        grant_starter_credits_if_needed,
    )

    grant_starter_credits_if_needed(db, user=user)
    return user


def _fetch_profile_for_insert(
    *,
    clerk_user_id: str,
    fallback_full_name: str | None,
    backend_client: ClerkBackendClient | None,
) -> tuple[str, str | None]:
    """Resolve ``(email, full_name)`` for a fresh ``User`` insert.

    The JWT didn't carry ``email`` — usually because Clerk's default
    session-token template omits it. We try the Backend API once. A
    transient error or a still-empty result is fatal: the alternative
    is writing ``""`` to a NOT NULL column, which is exactly the bug
    we're fixing.
    """
    client = backend_client or ClerkBackendClient()
    if not client.configured:
        logger.warning(
            "jit user create: no email in JWT and CLERK_SECRET_KEY unset; "
            "refusing to insert clerk_user_id=%s with blank email",
            clerk_user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "email_unavailable",
                "message": (
                    "Could not determine email for new user. Configure the "
                    "Clerk JWT template to include 'email' or set "
                    "CLERK_SECRET_KEY for Backend API lookup."
                ),
            },
        )
    try:
        profile = client.fetch_user(clerk_user_id)
    except ClerkBackendError as exc:
        logger.warning(
            "jit user create: clerk backend lookup failed for %s: %s",
            clerk_user_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "email_unavailable",
                "message": "Clerk Backend API lookup failed.",
            },
        ) from exc
    if not profile.email:
        logger.warning(
            "jit user create: clerk backend returned no email for %s",
            clerk_user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "email_unavailable",
                "message": "Clerk user has no primary email address.",
            },
        )
    return profile.email, profile.full_name or fallback_full_name


def current_user_dependency(
    verifier: ClerkVerifier,
    db_session_factory: Callable[[], AbstractContextManager[Session]],
    *,
    backend_client: ClerkBackendClient | None = None,
) -> Callable[..., User]:
    """Build a FastAPI dependency that returns the current ``User``.

    The returned dependency:
      1. Verifies the ``Authorization: Bearer <jwt>`` header via Clerk
         (raising 401 on bad / missing tokens — see
         ``advisor.auth.fastapi``).
      2. Opens a DB session via ``db_session_factory`` (a context
         manager — typically ``layer1.db.session.session_scope`` or a
         test stub).
      3. Calls ``resolve_or_create_user`` to find or insert the user.
      4. Commits the session so the user row is durable before the
         route handler runs.
      5. Yields the ``User`` for the route handler to consume.

    The DB session is opened-and-closed entirely within the dependency
    so handlers get a detached, fully-loaded ``User`` and don't have
    to worry about stale-session lifetimes. The expire-on-commit=False
    setting in ``make_session_factory`` keeps the returned attributes
    accessible after the close.
    """
    require_clerk_session = clerk_session_dependency(verifier)

    def dependency(
        clerk_session: ClerkSession = Depends(require_clerk_session),
    ) -> User:
        with db_session_factory() as db:
            user = resolve_or_create_user(
                db, clerk_session, backend_client=backend_client
            )
            # Commit explicitly so the row is visible to any other
            # sessions opened later in the request (chat-store writers,
            # usage-event recorders, etc.). See module docstring.
            db.commit()
            # Re-fetch so attribute access after close is safe even on
            # SQLAlchemy configurations that DO expire on commit. With
            # expire_on_commit=False this is a no-op cheap path.
            db.refresh(user)
            return user

    return dependency


def _extract_full_name(claims: dict[str, Any]) -> str | None:
    """Pull a display name out of Clerk's claim shapes.

    Clerk's hosted JWT template puts the human name under one of a
    few keys depending on tenant config. We try the common shapes and
    fall back to ``None`` if none are populated — the User row keeps
    its existing ``full_name`` rather than getting blanked out.
    """
    name = claims.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()

    first = claims.get("given_name") or claims.get("first_name")
    last = claims.get("family_name") or claims.get("last_name")
    parts = [p for p in (first, last) if isinstance(p, str) and p.strip()]
    if parts:
        return " ".join(p.strip() for p in parts)
    return None
