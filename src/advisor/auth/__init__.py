"""Clerk JWT authentication for the Advisor SaaS app.

The chat backend protects routes with a FastAPI dependency built on
``ClerkVerifier``: it reads the ``Authorization: Bearer <jwt>`` header,
validates the token against Clerk's JWKS, and hands route handlers a
``ClerkSession`` value object.

Public surface:
- ``AuthError``: single exception type with a ``code`` attribute.
- ``ClerkSession``: validated session value object.
- ``JWKSClient``: caches Clerk's public keys, swaps the HTTP client +
  clock for testability.
- ``ClerkVerifier``: validates a token and returns a ``ClerkSession``.
- ``AdvisorAuthSettings`` / ``get_settings`` / ``build_verifier``:
  env-driven construction for production use.
- ``clerk_session_dependency``: FastAPI ``Depends`` factory.
"""
from advisor.auth.clerk import ClerkVerifier
from advisor.auth.errors import AuthError
from advisor.auth.fastapi import clerk_session_dependency
from advisor.auth.jwks import JWKSClient
from advisor.auth.session import ClerkSession
from advisor.auth.settings import (
    AdvisorAuthSettings,
    build_verifier,
    get_settings,
)

__all__ = [
    "AdvisorAuthSettings",
    "AuthError",
    "ClerkSession",
    "ClerkVerifier",
    "JWKSClient",
    "build_verifier",
    "clerk_session_dependency",
    "get_settings",
]
