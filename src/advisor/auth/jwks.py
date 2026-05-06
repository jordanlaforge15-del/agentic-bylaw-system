"""JWKS (JSON Web Key Set) client for Clerk's public signing keys.

Clerk publishes its public keys at a JWKS endpoint and rotates them
periodically. We cache the parsed keys keyed by ``kid``. On a cache miss
for an unknown ``kid`` we refetch eagerly (a rotation just landed). We
also refetch when the cache TTL expires, even if the requested ``kid`` is
known, to pick up new keys before old ones are referenced.

The HTTP client and clock are injected so tests can be hermetic.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

import jwt
from jwt.algorithms import RSAAlgorithm

from advisor.auth.errors import AuthError


class _SyncHTTPClient(Protocol):
    """Subset of ``httpx.Client`` that we actually need.

    Only ``get(url) -> response``, where the response has ``.json()``
    and ``.raise_for_status()``. Lets tests pass a tiny fake without
    importing httpx.
    """

    def get(self, url: str) -> Any: ...  # pragma: no cover - protocol


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class JWKSClient:
    """Fetches and caches Clerk's JWKS, exposing keys by ``kid``.

    Calls fetch on first use and on cache miss for an unknown ``kid``
    (Clerk rotates keys, so we need to refetch when we see one we don't
    have). The cached set has a TTL beyond which we refetch even if the
    ``kid`` is known, to pick up rotations.

    Args:
        jwks_url: Full URL to Clerk's JWKS endpoint.
        http_client: Object exposing ``get(url)`` returning a response
            with ``.json()`` and ``.raise_for_status()``. Defaults to a
            fresh ``httpx.Client``.
        cache_ttl_s: Seconds before a fetched JWKS is considered stale.
        clock: Callable returning a tz-aware ``datetime``. Defaults to
            ``datetime.now(tz=UTC)``. Tests inject a controllable clock.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        http_client: _SyncHTTPClient | None = None,
        cache_ttl_s: float = 3600.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._http_client = http_client
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock or _utcnow
        # kid -> already-parsed cryptography public key
        self._keys: dict[str, Any] = {}
        self._fetched_at: datetime | None = None

    # -- public API ---------------------------------------------------

    def get_signing_key(self, kid: str) -> Any:
        """Return the RS256 verification key for ``kid``.

        Behavior:
          * If never fetched, fetch now.
          * If TTL elapsed, fetch now (rotation pickup).
          * If ``kid`` still unknown after that, fetch again (force
            refresh) in case rotation happened mid-window.
          * If still unknown, raise ``AuthError(code='unknown_kid')``.
        """
        if self._fetched_at is None or self._is_stale():
            self._refresh()

        key = self._keys.get(kid)
        if key is not None:
            return key

        # Unknown kid: force a refresh in case rotation just happened.
        self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise AuthError(
                f"No signing key found for kid={kid!r}",
                code="unknown_kid",
            )
        return key

    # -- internals ----------------------------------------------------

    def _is_stale(self) -> bool:
        if self._fetched_at is None:
            return True
        age = (self._clock() - self._fetched_at).total_seconds()
        return age >= self._cache_ttl_s

    def _refresh(self) -> None:
        client = self._http_client or self._default_client()
        response = client.get(self._jwks_url)
        # Both real httpx and our protocol response support these.
        response.raise_for_status()
        payload = response.json()
        keys_list = payload.get("keys", []) if isinstance(payload, dict) else []
        new_keys: dict[str, Any] = {}
        for key_dict in keys_list:
            kid = key_dict.get("kid")
            if not kid:
                continue
            try:
                # PyJWT's RSAAlgorithm parses a JWK dict (serialized as
                # JSON) into a cryptography public key suitable for
                # jwt.decode with algorithms=['RS256'].
                parsed = RSAAlgorithm.from_jwk(json.dumps(key_dict))
            except (jwt.InvalidKeyError, ValueError, TypeError):
                # Skip malformed entries instead of failing the fetch:
                # one bad key shouldn't lock out the whole JWKS.
                continue
            new_keys[kid] = parsed
        self._keys = new_keys
        self._fetched_at = self._clock()

    @staticmethod
    def _default_client() -> _SyncHTTPClient:
        # Imported lazily so tests that pass their own client don't need
        # network at all and don't pay httpx import cost twice.
        import httpx

        return httpx.Client(timeout=10.0)
