"""Pydantic settings + factory for the Clerk verifier.

Production callers do::

    from advisor.auth import build_verifier, clerk_session_dependency
    verifier = build_verifier()
    require_session = clerk_session_dependency(verifier)

Tests construct ``ClerkVerifier`` directly with a fake ``JWKSClient`` so
they don't touch settings, env vars, or the network at all.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from advisor.auth.clerk import ClerkVerifier
from advisor.auth.jwks import JWKSClient


class AdvisorAuthSettings(BaseSettings):
    """Environment-backed configuration for Clerk verification.

    Env vars (all prefixed with ``CLERK_``):
      * ``CLERK_JWKS_URL`` (required) — full JWKS endpoint URL.
      * ``CLERK_AUDIENCE`` (optional) — expected ``aud`` claim.
      * ``CLERK_ISSUER`` (optional) — expected ``iss`` claim.
      * ``CLERK_JWKS_CACHE_TTL_S`` (default 3600) — JWKS cache TTL.
      * ``CLERK_LEEWAY_S`` (default 30) — JWT clock-skew leeway.
    """

    clerk_jwks_url: str = Field(..., alias="CLERK_JWKS_URL")
    clerk_audience: str | None = Field(default=None, alias="CLERK_AUDIENCE")
    clerk_issuer: str | None = Field(default=None, alias="CLERK_ISSUER")
    jwks_cache_ttl_s: float = Field(default=3600.0, alias="CLERK_JWKS_CACHE_TTL_S")
    leeway_s: int = Field(default=30, alias="CLERK_LEEWAY_S")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> AdvisorAuthSettings:
    """Return a process-wide cached settings instance.

    Cached because settings are loaded from env / .env at first call and
    don't change at runtime. Tests that need fresh settings can call
    ``get_settings.cache_clear()``.
    """
    return AdvisorAuthSettings()  # type: ignore[call-arg]


def build_verifier(settings: AdvisorAuthSettings | None = None) -> ClerkVerifier:
    """Build a ``ClerkVerifier`` from settings (env-loaded if omitted).

    Production callers use this; tests construct ``ClerkVerifier``
    directly with their own ``JWKSClient`` so they don't depend on env.
    """
    cfg = settings or get_settings()
    jwks_client = JWKSClient(
        cfg.clerk_jwks_url,
        cache_ttl_s=cfg.jwks_cache_ttl_s,
    )
    return ClerkVerifier(
        jwks_client=jwks_client,
        expected_audience=cfg.clerk_audience,
        expected_issuer=cfg.clerk_issuer,
        leeway_s=cfg.leeway_s,
    )
