"""Authenticated Clerk session value object.

``ClerkSession`` is what the FastAPI dependency hands the route handler
once a Bearer token is verified. It is a frozen dataclass — equality and
hashing come for free, and downstream code can't accidentally mutate it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ClerkSession:
    """Decoded, validated Clerk session derived from a JWT.

    Attributes:
        user_id: Clerk user identifier (``"user_2abc..."``), from ``sub``.
        email: User email if present in claims; ``None`` otherwise.
        session_id: Clerk session identifier (``"sess_2abc..."``), from
            the ``sid`` claim. Empty string if Clerk omitted it.
        issued_at: ``iat`` claim as a tz-aware UTC datetime.
        expires_at: ``exp`` claim as a tz-aware UTC datetime.
        raw_claims: Full decoded claim dict for callers who need fields
            beyond the structured ones.
    """

    user_id: str
    email: str | None
    session_id: str
    issued_at: datetime
    expires_at: datetime
    raw_claims: dict[str, Any] = field(default_factory=dict)
