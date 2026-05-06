"""JWKSClient: caching, TTL refresh, kid rotation, unknown-kid errors."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from advisor.auth import AuthError, JWKSClient


def test_first_call_fetches_once_and_caches(
    make_keypair, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair("k1")
    http = fake_http_client_cls(responses=[fake_response_cls(make_jwks(kp))])
    client = JWKSClient(jwks_url, http_client=http, cache_ttl_s=60.0)

    key1 = client.get_signing_key("k1")
    key2 = client.get_signing_key("k1")

    assert key1 is key2
    assert http.calls == [jwks_url]


def test_unknown_kid_triggers_refetch(
    make_keypair, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp_old = make_keypair("k1")
    kp_new = make_keypair("k2")
    # First fetch: only k1. Second fetch: both (rotation).
    http = fake_http_client_cls(
        responses=[
            fake_response_cls(make_jwks(kp_old)),
            fake_response_cls(make_jwks(kp_old, kp_new)),
        ]
    )
    client = JWKSClient(jwks_url, http_client=http, cache_ttl_s=3600.0)

    client.get_signing_key("k1")
    assert len(http.calls) == 1

    # Asking for k2 forces a refresh even though TTL hasn't expired.
    client.get_signing_key("k2")
    assert len(http.calls) == 2


def test_cache_ttl_expiry_refetches(
    make_keypair, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair("k1")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    current = {"now": base}

    http = fake_http_client_cls(
        responses=[fake_response_cls(make_jwks(kp)), fake_response_cls(make_jwks(kp))]
    )
    client = JWKSClient(
        jwks_url,
        http_client=http,
        cache_ttl_s=60.0,
        clock=lambda: current["now"],
    )

    client.get_signing_key("k1")
    assert len(http.calls) == 1

    # Inside TTL -> no refetch.
    current["now"] = base + timedelta(seconds=30)
    client.get_signing_key("k1")
    assert len(http.calls) == 1

    # Past TTL -> refetch.
    current["now"] = base + timedelta(seconds=120)
    client.get_signing_key("k1")
    assert len(http.calls) == 2


def test_unknown_kid_after_refresh_raises_auth_error(
    make_keypair, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair("k1")
    # Both fetches return the same JWKS — k2 will never appear.
    http = fake_http_client_cls(responses=[fake_response_cls(make_jwks(kp))])
    client = JWKSClient(jwks_url, http_client=http, cache_ttl_s=3600.0)

    with pytest.raises(AuthError) as ei:
        client.get_signing_key("k2")
    assert ei.value.code == "unknown_kid"
    # Initial fetch + miss-driven refresh.
    assert len(http.calls) == 2


def test_clock_injection_controls_staleness_without_sleep(
    make_keypair, make_jwks, fake_response_cls, fake_http_client_cls, jwks_url
):
    """Confirms the injected clock — and only the injected clock —
    drives TTL decisions; no real sleep."""
    kp = make_keypair("k1")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    current = {"now": base}

    http = fake_http_client_cls(responses=[fake_response_cls(make_jwks(kp))])
    client = JWKSClient(
        jwks_url, http_client=http, cache_ttl_s=10.0, clock=lambda: current["now"]
    )
    client.get_signing_key("k1")

    current["now"] = base + timedelta(hours=1)
    client.get_signing_key("k1")
    assert len(http.calls) == 2


def test_malformed_jwk_entries_are_skipped_not_fatal(
    make_keypair, fake_response_cls, fake_http_client_cls, jwks_url
):
    kp = make_keypair("k1")
    payload = {
        "keys": [
            {"kid": "broken", "kty": "RSA"},  # missing n/e
            kp.public_jwk,
        ]
    }
    http = fake_http_client_cls(responses=[fake_response_cls(payload)])
    client = JWKSClient(jwks_url, http_client=http)

    assert client.get_signing_key("k1") is not None


def test_http_error_propagates(fake_response_cls, fake_http_client_cls, jwks_url):
    http = fake_http_client_cls(responses=[fake_response_cls({}, status_code=500)])
    client = JWKSClient(jwks_url, http_client=http)

    # We deliberately do NOT wrap HTTP errors as AuthError — those are
    # server-side problems and propagate naturally to FastAPI as 500.
    with pytest.raises(RuntimeError):
        client.get_signing_key("anything")
