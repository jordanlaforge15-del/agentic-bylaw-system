"""Auth errors raised by the Clerk verifier and related machinery.

A single ``AuthError`` exception type carries a stable ``code`` so the
FastAPI dependency layer can map it to the appropriate HTTP status without
catching multiple exception types. The codes are part of the public
contract — clients (and tests) match on them.
"""
from __future__ import annotations

# All valid AuthError codes. Listed here for documentation and so callers
# can reason about the closed set without importing PyJWT exceptions.
AUTH_ERROR_CODES: frozenset[str] = frozenset(
    {
        "invalid_token",
        "expired_token",
        "missing_kid",
        "unknown_kid",
        "verification_failed",
        "malformed_token",
        "missing_authorization_header",
        "audience_mismatch",
        "issuer_mismatch",
    }
)


class AuthError(Exception):
    """Raised when authentication of a Clerk-issued JWT fails.

    Attributes:
        code: Stable machine-readable code (see ``AUTH_ERROR_CODES``).
            FastAPI maps this to an HTTP status; clients can log/match it.
    """

    code: str

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code

    def __repr__(self) -> str:  # pragma: no cover — convenience only
        return f"AuthError(code={self.code!r}, message={self.args[0]!r})"
