"""ClerkVerifier: full RS256 round-trip plus every error-mapping branch."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from advisor.auth import AuthError, ClerkSession, ClerkVerifier, JWKSClient


def _build_verifier(
    keypair,
    make_jwks,
    fake_response_cls,
    fake_http_client_cls,
    jwks_url,
    *,
    audience=None,
    issuer=None,
    leeway_s=30,
):
    http = fake_http_client_cls(responses=[fake_response_cls(make_jwks(keypair))])
    jwks = JWKSClient(jwks_url, http_client=http, cache_ttl_s=3600.0)
    return ClerkVerifier(
        jwks_client=jwks,
        expected_audience=audience,
        expected_issuer=issuer,
        leeway_s=leeway_s,
    )


def test_valid_token_yields_clerk_session(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
    )

    # iat/exp must straddle "now" since PyJWT validates exp against the
    # real wall clock; we lock them to integer-second precision so the
    # round-trip through Unix timestamps is exact.
    now = datetime.fromtimestamp(int(datetime.now(tz=UTC).timestamp()), tz=UTC)
    iat = now - timedelta(seconds=10)
    exp = now + timedelta(hours=1)
    token = sign_token(
        kp,
        sub="user_2alice",
        sid="sess_2alpha",
        email="alice@example.com",
        iat=iat,
        exp=exp,
    )

    session = verifier.verify(token)
    assert isinstance(session, ClerkSession)
    assert session.user_id == "user_2alice"
    assert session.session_id == "sess_2alpha"
    assert session.email == "alice@example.com"
    assert session.issued_at == iat
    assert session.expires_at == exp
    assert session.raw_claims["sub"] == "user_2alice"


def test_expired_token_raises_expired_token(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url, leeway_s=0
    )

    past = datetime.now(tz=UTC) - timedelta(hours=2)
    token = sign_token(kp, iat=past - timedelta(minutes=5), exp=past)

    with pytest.raises(AuthError) as ei:
        verifier.verify(token)
    assert ei.value.code == "expired_token"


def test_unknown_kid_when_signed_with_other_key(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp_known = make_keypair("k-known")
    kp_unknown = make_keypair("k-unknown")
    # JWKS only has k-known; token is signed by k-unknown.
    verifier = _build_verifier(
        kp_known, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
    )
    token = sign_token(kp_unknown)

    with pytest.raises(AuthError) as ei:
        verifier.verify(token)
    assert ei.value.code == "unknown_kid"


def test_tampered_signature_raises_verification_failed(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
    )

    token = sign_token(kp)
    head, body, sig = token.split(".")
    # Flip the signature deterministically — any change yields an
    # invalid signature against the public key.
    tampered_sig = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    tampered = ".".join([head, body, tampered_sig])

    with pytest.raises(AuthError) as ei:
        verifier.verify(tampered)
    assert ei.value.code == "verification_failed"


def test_missing_kid_header_raises_missing_kid(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
    )
    token = sign_token(kp, omit_kid=True)

    with pytest.raises(AuthError) as ei:
        verifier.verify(token)
    assert ei.value.code == "missing_kid"


@pytest.mark.parametrize(
    "bad_token",
    [
        "not-a-jwt",
        "only.two",
        "way.too.many.dots.here",
        "",
    ],
)
def test_malformed_token_raises_malformed_token(
    bad_token, make_keypair, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
    )

    with pytest.raises(AuthError) as ei:
        verifier.verify(bad_token)
    assert ei.value.code == "malformed_token"


def test_audience_expected_but_missing_in_token(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp,
        make_jwks,
        fake_response_cls,
        fake_http_client_cls,
        jwks_url,
        audience="advisor-prod",
    )
    token = sign_token(kp)  # no aud claim

    with pytest.raises(AuthError) as ei:
        verifier.verify(token)
    assert ei.value.code == "audience_mismatch"


def test_audience_match_succeeds(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp,
        make_jwks,
        fake_response_cls,
        fake_http_client_cls,
        jwks_url,
        audience="advisor-prod",
    )
    token = sign_token(kp, aud="advisor-prod")

    session = verifier.verify(token)
    assert session.user_id == "user_2abc"


def test_issuer_mismatch_raises_issuer_mismatch(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp,
        make_jwks,
        fake_response_cls,
        fake_http_client_cls,
        jwks_url,
        issuer="https://clerk.expected.test",
    )
    token = sign_token(kp, iss="https://clerk.attacker.test")

    with pytest.raises(AuthError) as ei:
        verifier.verify(token)
    assert ei.value.code == "issuer_mismatch"


def test_missing_sub_raises_invalid_token(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    """We sign a token whose 'sub' is empty so PyJWT's signature check
    passes — only ClerkVerifier's claim validation should fail it."""
    kp = make_keypair()
    verifier = _build_verifier(
        kp, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
    )
    token = sign_token(kp, sub="")  # sub present but empty

    with pytest.raises(AuthError) as ei:
        verifier.verify(token)
    assert ei.value.code == "invalid_token"


def test_email_is_optional(
    make_keypair, make_jwks, sign_token, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair()
    verifier = _build_verifier(
        kp, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
    )
    token = sign_token(kp, email=None)

    session = verifier.verify(token)
    assert session.email is None
    assert session.user_id == "user_2abc"
