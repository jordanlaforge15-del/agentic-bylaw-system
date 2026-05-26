"""Unit tests for the e2e mock Clerk infrastructure (ABS-19)."""
from __future__ import annotations

import time

import jwt
import pytest

from advisor.auth.errors import AuthError
from advisor.auth.mock_clerk import (
    MockJWKSClient,
    build_mock_verifier,
    mint_test_jwt,
)


class TestMintTestJwt:
    def test_produces_valid_jwt(self):
        token = mint_test_jwt(sub="user_test_1", email="a@b.com")
        assert isinstance(token, str)
        assert token.count(".") == 2

    def test_claims_round_trip(self):
        token = mint_test_jwt(
            sub="user_test_2",
            email="test@e2e.test",
            full_name="Test User",
            session_id="sess_custom",
        )
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["sub"] == "user_test_2"
        assert claims["email"] == "test@e2e.test"
        assert claims["name"] == "Test User"
        assert claims["sid"] == "sess_custom"
        assert claims["exp"] > claims["iat"]

    def test_lifetime_is_configurable(self):
        token = mint_test_jwt(sub="user_test_3", lifetime_s=120)
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["exp"] - claims["iat"] == 120

    def test_extra_claims_merged(self):
        token = mint_test_jwt(
            sub="user_test_4", extra_claims={"org_id": "org_abc"}
        )
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["org_id"] == "org_abc"

    def test_header_includes_kid(self):
        token = mint_test_jwt(sub="user_test_5")
        header = jwt.get_unverified_header(token)
        assert header["kid"] == "e2e-test-key-1"
        assert header["alg"] == "RS256"


class TestMockJWKSClient:
    def test_returns_key_for_known_kid(self):
        client = MockJWKSClient()
        key = client.get_signing_key("e2e-test-key-1")
        assert key is not None

    def test_raises_for_unknown_kid(self):
        client = MockJWKSClient()
        with pytest.raises(AuthError, match="No signing key found"):
            client.get_signing_key("unknown-kid-999")


class TestBuildMockVerifier:
    def test_verify_round_trip(self):
        verifier = build_mock_verifier()
        token = mint_test_jwt(
            sub="user_rt_1",
            email="rt@test.com",
            full_name="Round Trip",
        )
        session = verifier.verify(token)
        assert session.user_id == "user_rt_1"
        assert session.email == "rt@test.com"
        assert session.session_id is not None

    def test_verify_rejects_tampered_token(self):
        verifier = build_mock_verifier()
        token = mint_test_jwt(sub="user_tamper")
        parts = token.split(".")
        parts[2] = parts[2][::-1]
        tampered = ".".join(parts)
        with pytest.raises(AuthError):
            verifier.verify(tampered)

    def test_verify_rejects_expired_token(self):
        verifier = build_mock_verifier()
        # Use extra_claims to set exp in the past (beyond the 30s leeway).
        now = int(time.time())
        token = mint_test_jwt(
            sub="user_expired",
            extra_claims={"exp": now - 60},
        )
        with pytest.raises(AuthError, match="expired"):
            verifier.verify(token)

    def test_verify_without_email(self):
        verifier = build_mock_verifier()
        token = mint_test_jwt(sub="user_no_email")
        session = verifier.verify(token)
        assert session.user_id == "user_no_email"
        assert session.email is None
