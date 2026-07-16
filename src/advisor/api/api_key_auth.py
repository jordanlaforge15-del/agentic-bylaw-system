"""API key authentication for machine-to-machine integration endpoints.

The Speckle Automate function (and future M2M callers) cannot use
Clerk JWTs — they run server-side without a browser session.  This
module provides a FastAPI dependency that accepts an ``X-ABS-API-Key``
header, hashes it with SHA-256, looks it up in ``advisor_api_key``,
and returns the owning ``User``.  The dependency surface is identical
to the Clerk-backed ``current_user_dependency`` so integration route
handlers are auth-agnostic.

Key lifecycle helpers (``issue_api_key``, ``revoke_api_key``) are
exported here so the admin management scripts and e2e test helpers
can share the logic without duplicating the hash / random-token
generation.
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from advisor.db.models import AdvisorApiKey, User
from layer1.db.base import utcnow

_KEY_BYTES = 32  # 256 bits of entropy → 64 hex chars


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def generate_api_key() -> tuple[str, str]:
    """Generate a new raw API key and its SHA-256 digest.

    Returns ``(raw_key, key_hash)``.  Only the ``raw_key`` is shown to
    the user — the caller must persist ``key_hash`` and discard the raw
    value.
    """
    raw = secrets.token_hex(_KEY_BYTES)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


def issue_api_key(
    db: Session,
    *,
    user_id: int,
    name: str,
) -> tuple[AdvisorApiKey, str]:
    """Create and persist a new ``AdvisorApiKey`` for *user_id*.

    Returns ``(api_key_row, raw_key)``.  The ``raw_key`` is the only
    time the secret is available in plaintext; the caller must surface
    it to the user immediately and then discard it.
    """
    raw_key, key_hash = generate_api_key()
    row = AdvisorApiKey(
        user_id=user_id,
        key_hash=key_hash,
        name=name,
    )
    db.add(row)
    db.flush()
    return row, raw_key


def revoke_api_key(db: Session, *, key_id: int, user_id: int) -> bool:
    """Mark key *key_id* as revoked.

    Returns ``True`` if the row was found and updated, ``False`` when
    the key doesn't exist or belongs to a different user.
    """
    row = db.get(AdvisorApiKey, key_id)
    if row is None or row.user_id != user_id:
        return False
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        db.flush()
    return True


# ---------------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------------


def api_key_user_dependency(
    db_session_factory: Callable[[], AbstractContextManager[Session]],
) -> Callable[..., User]:
    """Build a FastAPI dependency that authenticates via ``X-ABS-API-Key``.

    The returned dependency:
    1. Reads the ``X-ABS-API-Key`` request header (raises 401 if absent).
    2. SHA-256-hashes the value and looks it up in ``advisor_api_key``.
    3. Rejects revoked keys with 401.
    4. Stamps ``last_used_at`` and commits.
    5. Returns the owning ``User`` so integration route handlers are
       auth-strategy-agnostic.
    """

    def dependency(
        x_abs_api_key: str | None = Header(default=None),
    ) -> User:
        if not x_abs_api_key or not x_abs_api_key.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "missing_api_key",
                    "message": "X-ABS-API-Key header is required.",
                },
            )
        key_hash = hashlib.sha256(x_abs_api_key.strip().encode()).hexdigest()

        with db_session_factory() as db:
            api_key_row: AdvisorApiKey | None = (
                db.query(AdvisorApiKey)
                .filter(AdvisorApiKey.key_hash == key_hash)
                .one_or_none()
            )
            if api_key_row is None or api_key_row.revoked_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "invalid_api_key",
                        "message": "API key is invalid or has been revoked.",
                    },
                )
            api_key_row.last_used_at = utcnow()
            user: User | None = db.get(User, api_key_row.user_id)
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "invalid_api_key",
                        "message": "API key owner not found.",
                    },
                )
            db.commit()
            db.refresh(user)
            return user

    return dependency


__all__ = [
    "api_key_user_dependency",
    "generate_api_key",
    "issue_api_key",
    "revoke_api_key",
]
