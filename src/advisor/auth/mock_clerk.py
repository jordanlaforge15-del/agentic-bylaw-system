"""Mock Clerk infrastructure for e2e tests.

Provides a self-contained RSA key pair, a mock JWKS client, and a JWT
minting function so the e2e test stack can exercise the real
``ClerkVerifier`` → ``clerk_session_dependency`` → ``resolve_or_create_user``
code path without a live Clerk account.

The test RSA key is generated once at module load. It is deterministic
per process but NOT fixed across runs — there is no reason to persist
it, and rotating it on every start prevents accidental reuse outside
tests.

Usage::

    from advisor.auth.mock_clerk import build_mock_verifier, mint_test_jwt

    verifier = build_mock_verifier()
    token = mint_test_jwt(sub="user_test_123", email="a@b.com")
    session = verifier.verify(token)   # ClerkSession
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from jwt.algorithms import RSAAlgorithm

from advisor.auth.clerk import ClerkVerifier
from advisor.auth.jwks import JWKSClient

_TEST_KID = "e2e-test-key-1"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()

_private_key_pem = _private_key.private_bytes(
    Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
)


class MockJWKSClient:
    """In-memory JWKS client that serves the test public key.

    Implements the same ``get_signing_key(kid)`` interface as
    ``advisor.auth.jwks.JWKSClient`` so it can be injected into
    ``ClerkVerifier`` without touching the network.
    """

    def get_signing_key(self, kid: str) -> Any:
        if kid != _TEST_KID:
            from advisor.auth.errors import AuthError

            raise AuthError(
                f"No signing key found for kid={kid!r}",
                code="unknown_kid",
            )
        return _public_key


def mint_test_jwt(
    *,
    sub: str,
    email: str | None = None,
    full_name: str | None = None,
    session_id: str = "sess_e2e_test",
    lifetime_s: int = 3600,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a Clerk-shaped JWT with the test private key.

    The token is valid for ``lifetime_s`` seconds from now. Claims
    mirror the shape ``ClerkVerifier._build_session`` expects:
    ``sub``, ``iat``, ``exp``, optional ``email``, optional ``name``,
    ``sid``.
    """
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": sub,
        "iat": now,
        "exp": now + lifetime_s,
        "sid": session_id,
    }
    if email is not None:
        claims["email"] = email
    if full_name is not None:
        claims["name"] = full_name
    if extra_claims:
        claims.update(extra_claims)

    return jwt.encode(
        claims,
        _private_key_pem,
        algorithm="RS256",
        headers={"kid": _TEST_KID},
    )


def build_mock_verifier() -> ClerkVerifier:
    """Return a ``ClerkVerifier`` wired to the in-memory test key."""
    return ClerkVerifier(jwks_client=MockJWKSClient())  # type: ignore[arg-type]
