"""Shared test fixtures for advisor.auth.

Generates RSA keypairs at runtime, builds JWKS payloads from public
keys, and signs JWTs with the matching private key. Everything is
hermetic — no Clerk, no network.

Helpers are exposed as pytest fixtures (no relative-import gymnastics
needed since this dir has no ``__init__.py`` per existing convention).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


@dataclass
class KeyPair:
    """RSA keypair plus its JWK-format public-key dict."""

    kid: str
    private_pem: bytes
    public_jwk: dict[str, Any]


def _make_keypair(kid: str = "test-kid-1") -> KeyPair:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key = serialization.load_pem_public_key(public_pem)
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = kid
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return KeyPair(kid=kid, private_pem=private_pem, public_jwk=jwk)


def _make_jwks(*keypairs: KeyPair) -> dict[str, Any]:
    return {"keys": [kp.public_jwk for kp in keypairs]}


def _sign_token(
    keypair: KeyPair,
    *,
    sub: str = "user_2abc",
    sid: str | None = "sess_2abc",
    email: str | None = "user@example.com",
    aud: str | list[str] | None = None,
    iss: str | None = None,
    iat: datetime | None = None,
    exp: datetime | None = None,
    extra_claims: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    omit_kid: bool = False,
) -> str:
    now = datetime.now(tz=UTC)
    iat = iat or now
    exp = exp or (now + timedelta(hours=1))
    claims: dict[str, Any] = {
        "sub": sub,
        "iat": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if sid is not None:
        claims["sid"] = sid
    if email is not None:
        claims["email"] = email
    if aud is not None:
        claims["aud"] = aud
    if iss is not None:
        claims["iss"] = iss
    if extra_claims:
        claims.update(extra_claims)

    final_headers: dict[str, Any] = dict(headers or {})
    if not omit_kid and "kid" not in final_headers:
        final_headers["kid"] = keypair.kid

    return jwt.encode(
        claims,
        keypair.private_pem,
        algorithm="RS256",
        headers=final_headers,
    )


# -- Fake HTTP plumbing for the JWKSClient ---------------------------


class FakeResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if not (200 <= self.status_code < 300):
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class FakeHTTPClient:
    """Records calls; returns a queue of responses (last reused)."""

    def __init__(
        self,
        responses: list[FakeResponse] | None = None,
        response_factory: Callable[[str], FakeResponse] | None = None,
    ) -> None:
        if responses is None and response_factory is None:
            raise ValueError("Provide responses or response_factory")
        self._responses = list(responses) if responses else None
        self._factory = response_factory
        self.calls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.calls.append(url)
        if self._factory is not None:
            return self._factory(url)
        assert self._responses is not None
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


# -- Pytest fixtures -------------------------------------------------


@pytest.fixture
def make_keypair() -> Callable[..., KeyPair]:
    return _make_keypair


@pytest.fixture
def make_jwks() -> Callable[..., dict[str, Any]]:
    return _make_jwks


@pytest.fixture
def sign_token() -> Callable[..., str]:
    return _sign_token


@pytest.fixture
def fake_response_cls() -> type[FakeResponse]:
    return FakeResponse


@pytest.fixture
def fake_http_client_cls() -> type[FakeHTTPClient]:
    return FakeHTTPClient


@pytest.fixture
def jwks_url() -> str:
    return "https://example.clerk.test/.well-known/jwks.json"
