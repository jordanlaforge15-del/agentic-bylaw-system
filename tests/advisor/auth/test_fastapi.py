"""FastAPI dependency: header parsing, status mapping, error codes."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from advisor.auth import (
    ClerkSession,
    ClerkVerifier,
    JWKSClient,
    clerk_session_dependency,
)


@pytest.fixture
def app_factory(make_jwks, fake_response_cls, fake_http_client_cls, jwks_url):
    """Build a FastAPI app + TestClient wired to a verifier whose JWKS
    is the public half of ``keypair``. Returns ``(app, client)``."""

    def _factory(keypair, *, audience=None, issuer=None, leeway_s=30):
        http = fake_http_client_cls(responses=[fake_response_cls(make_jwks(keypair))])
        jwks = JWKSClient(jwks_url, http_client=http, cache_ttl_s=3600.0)
        verifier = ClerkVerifier(
            jwks_client=jwks,
            expected_audience=audience,
            expected_issuer=issuer,
            leeway_s=leeway_s,
        )
        require_session = clerk_session_dependency(verifier)

        app = FastAPI()

        @app.get("/me")
        def me(session: ClerkSession = Depends(require_session)):
            return {"user_id": session.user_id, "email": session.email}

        return app, TestClient(app)

    return _factory


def test_valid_bearer_returns_200(make_keypair, sign_token, app_factory):
    kp = make_keypair()
    _, client = app_factory(kp)
    token = sign_token(kp, sub="user_2alice", email="alice@example.com")

    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"user_id": "user_2alice", "email": "alice@example.com"}


def test_lowercase_bearer_prefix_accepted(make_keypair, sign_token, app_factory):
    kp = make_keypair()
    _, client = app_factory(kp)
    token = sign_token(kp)

    resp = client.get("/me", headers={"Authorization": f"bearer {token}"})
    assert resp.status_code == 200


def test_missing_authorization_header_yields_401(make_keypair, app_factory):
    kp = make_keypair()
    _, client = app_factory(kp)

    resp = client.get("/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "missing_authorization_header"


def test_authorization_without_bearer_prefix_yields_401_malformed(
    make_keypair, sign_token, app_factory
):
    kp = make_keypair()
    _, client = app_factory(kp)
    token = sign_token(kp)

    resp = client.get("/me", headers={"Authorization": token})  # no scheme
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "malformed_token"


def test_expired_token_yields_401_expired(make_keypair, sign_token, app_factory):
    kp = make_keypair()
    _, client = app_factory(kp, leeway_s=0)

    past = datetime.now(tz=UTC) - timedelta(hours=2)
    token = sign_token(kp, iat=past - timedelta(minutes=5), exp=past)

    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "expired_token"


def test_invalid_signature_yields_401_verification_failed(
    make_keypair, sign_token, app_factory
):
    """We map InvalidSignatureError to 'verification_failed' AND to a
    401 — see ``advisor.auth.fastapi`` module docstring for rationale.
    A bad signature is user-supplied bad data, not a server fault."""
    kp = make_keypair()
    _, client = app_factory(kp)

    token = sign_token(kp)
    head, body, sig = token.split(".")
    tampered_sig = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    tampered = ".".join([head, body, tampered_sig])

    resp = client.get("/me", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "verification_failed"


def test_unknown_kid_yields_401(make_keypair, sign_token, app_factory):
    kp_known = make_keypair("k-known")
    kp_other = make_keypair("k-other")
    _, client = app_factory(kp_known)
    token = sign_token(kp_other)

    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "unknown_kid"


def test_missing_kid_yields_401(make_keypair, sign_token, app_factory):
    kp = make_keypair()
    _, client = app_factory(kp)
    token = sign_token(kp, omit_kid=True)

    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "missing_kid"


def test_audience_mismatch_yields_401(make_keypair, sign_token, app_factory):
    kp = make_keypair()
    _, client = app_factory(kp, audience="advisor-prod")
    token = sign_token(kp)  # no aud

    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "audience_mismatch"


def test_empty_authorization_string_yields_401_missing(make_keypair, app_factory):
    kp = make_keypair()
    _, client = app_factory(kp)

    resp = client.get("/me", headers={"Authorization": "   "})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "missing_authorization_header"
