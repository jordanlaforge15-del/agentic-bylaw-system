"""Clerk JWT verifier.

Wraps PyJWT to validate a Clerk-issued Bearer token end-to-end:

  1. Decode the unverified header to discover ``kid``.
  2. Look up the matching signing key via ``JWKSClient``.
  3. Verify signature, ``exp``, optional ``aud``, optional ``iss``.
  4. Map PyJWT's exception zoo to the small ``AuthError`` code set.
  5. Build a ``ClerkSession`` from the validated claims.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

import jwt

from advisor.auth.errors import AuthError
from advisor.auth.jwks import JWKSClient
from advisor.auth.session import ClerkSession


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class ClerkVerifier:
    """Validate Clerk-issued RS256 JWTs and produce ``ClerkSession``.

    Args:
        jwks_client: Source of public signing keys keyed by ``kid``.
        expected_audience: If set, ``aud`` claim is verified against
            this. If ``None``, audience is not validated.
        expected_issuer: If set, ``iss`` claim is verified against this.
            If ``None``, issuer is not validated.
        leeway_s: Clock skew tolerance for ``exp`` / ``nbf`` / ``iat``,
            in seconds. PyJWT's default is 0; we keep a small allowance
            to absorb minor server-clock drift.
        clock: Callable returning a tz-aware UTC ``datetime``. Reserved
            for future expiry-window logic; PyJWT itself does not accept
            an injected clock today, so this is held for parity with the
            JWKS client and forward compatibility.
    """

    def __init__(
        self,
        *,
        jwks_client: JWKSClient,
        expected_audience: str | None = None,
        expected_issuer: str | None = None,
        leeway_s: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._jwks = jwks_client
        self._audience = expected_audience
        self._issuer = expected_issuer
        self._leeway_s = leeway_s
        self._clock = clock or _utcnow

    def verify(self, token: str) -> ClerkSession:
        """Validate ``token`` and return the resulting ``ClerkSession``.

        Raises ``AuthError`` with one of the codes listed in
        ``advisor.auth.errors.AUTH_ERROR_CODES``.
        """
        kid = self._extract_kid(token)
        # JWKSClient raises AuthError(code='unknown_kid') itself.
        signing_key = self._jwks.get_signing_key(kid)

        decode_kwargs: dict[str, Any] = {
            "key": signing_key,
            "algorithms": ["RS256"],
            "leeway": self._leeway_s,
        }
        if self._audience is not None:
            decode_kwargs["audience"] = self._audience
        if self._issuer is not None:
            decode_kwargs["issuer"] = self._issuer

        try:
            claims = jwt.decode(token, **decode_kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise AuthError(str(exc) or "Token has expired", code="expired_token") from exc
        except jwt.MissingRequiredClaimError as exc:
            # Most commonly this fires when audience/issuer is expected
            # but the corresponding claim is absent. Map by claim name.
            claim = getattr(exc, "claim", None)
            if claim == "aud":
                raise AuthError(str(exc), code="audience_mismatch") from exc
            if claim == "iss":
                raise AuthError(str(exc), code="issuer_mismatch") from exc
            raise AuthError(str(exc), code="invalid_token") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError(str(exc), code="audience_mismatch") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError(str(exc), code="issuer_mismatch") from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthError(str(exc), code="verification_failed") from exc
        except jwt.DecodeError as exc:
            # Header/body unreadable, base64 garbage, etc. Treat as
            # malformed-token if structure is the issue, otherwise as a
            # verification failure. DecodeError is the broad bucket.
            raise AuthError(str(exc), code="verification_failed") from exc
        except jwt.InvalidTokenError as exc:
            # Catch-all for everything else PyJWT defines (e.g. nbf
            # not yet valid).
            raise AuthError(str(exc), code="verification_failed") from exc

        return self._build_session(claims)

    # -- helpers ------------------------------------------------------

    @staticmethod
    def _extract_kid(token: str) -> str:
        # Cheap structural check before handing to PyJWT — gives us a
        # specific 'malformed_token' code for non-JWT input.
        if not isinstance(token, str) or token.count(".") != 2:
            raise AuthError("Token is not a well-formed JWT", code="malformed_token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.DecodeError as exc:
            raise AuthError(str(exc), code="malformed_token") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError(str(exc), code="malformed_token") from exc

        kid = header.get("kid") if isinstance(header, dict) else None
        if not kid:
            raise AuthError("JWT header is missing 'kid'", code="missing_kid")
        return kid

    @staticmethod
    def _build_session(claims: dict[str, Any]) -> ClerkSession:
        sub = claims.get("sub")
        if not sub:
            raise AuthError("JWT is missing required 'sub' claim", code="invalid_token")
        try:
            iat = datetime.fromtimestamp(int(claims["iat"]), tz=UTC)
            exp = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError(
                f"JWT 'iat'/'exp' claims missing or unparseable: {exc}",
                code="invalid_token",
            ) from exc

        email = claims.get("email")
        if email is not None and not isinstance(email, str):
            email = None
        return ClerkSession(
            user_id=sub,
            email=email,
            session_id=str(claims.get("sid", "")),
            issued_at=iat,
            expires_at=exp,
            raw_claims=dict(claims),
        )
