"""Shared parsers for Clerk user profile payloads.

The same ``email_addresses[primary]`` / ``first_name``/``last_name`` shape
shows up in two places: the svix-delivered webhook payload (see
``advisor.auth.webhooks``) and the Clerk Backend API response from
``GET /v1/users/{id}`` (see ``advisor.auth.clerk_backend``). Keeping one
parser for both means a malformed shape can only burn us once.

Both helpers return ``None`` on a missing/unparseable value rather than
an empty string — callers must decide whether ``None`` means "skip this
update" (keep what we have) or "raise" (a fresh insert that would
otherwise violate the NOT NULL ``email`` constraint).
"""
from __future__ import annotations

from typing import Any


def primary_email(data: dict[str, Any]) -> str | None:
    """Pull the primary email out of a Clerk user payload.

    Clerk's payload shape:
        {
          "primary_email_address_id": "idn_...",
          "email_addresses": [
            {"id": "idn_...", "email_address": "user@example.com", ...},
            ...
          ],
        }

    Prefer the entry whose ``id`` matches ``primary_email_address_id``;
    if Clerk didn't mark one as primary (rare) fall back to the first
    entry. A flattened ``email_address`` string at the top level is
    also accepted (some event shapes use it).
    """
    addrs = data.get("email_addresses")
    if not isinstance(addrs, list) or not addrs:
        email = data.get("email_address")
        return email if isinstance(email, str) and email else None

    primary_id = data.get("primary_email_address_id")
    if isinstance(primary_id, str):
        for entry in addrs:
            if isinstance(entry, dict) and entry.get("id") == primary_id:
                email = entry.get("email_address")
                if isinstance(email, str) and email:
                    return email
    first = addrs[0]
    if isinstance(first, dict):
        email = first.get("email_address")
        if isinstance(email, str) and email:
            return email
    return None


def full_name(data: dict[str, Any]) -> str | None:
    """Build a display name from Clerk's first_name / last_name fields.

    Returns ``None`` if both are missing — callers treat ``None`` as
    "leave the existing value alone" rather than "blank the field."
    """
    first = data.get("first_name")
    last = data.get("last_name")
    parts = [p for p in (first, last) if isinstance(p, str) and p.strip()]
    if not parts:
        return None
    return " ".join(p.strip() for p in parts)
