"""FastAPI integration for the Clerk verifier.

Exposes a single factory ``clerk_session_dependency(verifier)`` that
returns a dependency function suitable for ``Depends(...)`` on protected
routes. Route handlers receive a ``ClerkSession`` and don't need to know
anything about JWTs or Clerk.

Status-code mapping
-------------------

We deliberately treat *all* user-supplied bad tokens as **401**,
including ``InvalidSignatureError`` (mapped to code
``'verification_failed'``). A 500 is reserved for *server-side* causes
of verification failure (e.g. JWKS endpoint unreachable). Today we don't
catch a distinct "JWKS unreachable" condition here — those bubble up as
``httpx`` errors and FastAPI returns 500 naturally — so in practice the
dependency only ever raises 401 from this code path. The 500 mapping
remains in place as documented behavior in case the verifier grows a
dedicated server-side error code in the future.
"""
from __future__ import annotations

from typing import Callable

from fastapi import Header, HTTPException, status

from advisor.auth.clerk import ClerkVerifier
from advisor.auth.errors import AuthError
from advisor.auth.session import ClerkSession

# Codes that always mean "the caller's token / header is bad" -> 401.
_UNAUTHORIZED_CODES: frozenset[str] = frozenset(
    {
        "missing_authorization_header",
        "invalid_token",
        "expired_token",
        "malformed_token",
        "missing_kid",
        "unknown_kid",
        "audience_mismatch",
        "issuer_mismatch",
        # InvalidSignatureError -> 'verification_failed' is also the
        # caller's fault (they sent a tampered or wrong-key token), so
        # treat it as 401 too. See module docstring.
        "verification_failed",
    }
)


def _strip_bearer(authorization: str) -> str:
    """Return the token portion of ``authorization`` or raise.

    Accepts ``"Bearer <token>"`` case-insensitively. Anything else is
    treated as malformed input.
    """
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthError(
            "Authorization header must be of the form 'Bearer <token>'",
            code="malformed_token",
        )
    return parts[1].strip()


def clerk_session_dependency(verifier: ClerkVerifier) -> Callable[..., ClerkSession]:
    """Build a FastAPI dependency that authenticates via Clerk.

    Usage::

        verifier = build_verifier()
        require_session = clerk_session_dependency(verifier)

        @app.get("/protected")
        def protected(session: ClerkSession = Depends(require_session)):
            return {"user_id": session.user_id}

    The returned dependency:
      * reads the ``Authorization`` header,
      * strips ``Bearer`` (case-insensitive),
      * calls ``verifier.verify(token)``,
      * returns the resulting ``ClerkSession``,
      * raises ``HTTPException`` with status ``401`` (any caller-fault
        code) or ``500`` (any unexpected server-fault code).

    The error body is a JSON object with ``code`` and ``message`` fields
    so SPAs can branch on the code without parsing prose.
    """

    def dependency(authorization: str | None = Header(None)) -> ClerkSession:
        if authorization is None or not authorization.strip():
            _raise(
                status.HTTP_401_UNAUTHORIZED,
                code="missing_authorization_header",
                message="Authorization header is required",
            )

        try:
            token = _strip_bearer(authorization)
        except AuthError as exc:
            _raise(status.HTTP_401_UNAUTHORIZED, code=exc.code, message=str(exc))

        try:
            return verifier.verify(token)
        except AuthError as exc:
            http_status = (
                status.HTTP_401_UNAUTHORIZED
                if exc.code in _UNAUTHORIZED_CODES
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            _raise(http_status, code=exc.code, message=str(exc))

    return dependency


def _raise(http_status: int, *, code: str, message: str) -> None:
    raise HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message},
    )
