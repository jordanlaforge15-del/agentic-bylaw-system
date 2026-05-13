"""resolve_or_create_user copies invite-granted caps onto new users
and marks the invite redeemed.

These behaviours close the loop between the admin approving an
invite (which stamps granted_* values on invite_request) and the
chat backend enforcing those values on the user's monthly budget.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from advisor.api.auth import resolve_or_create_user
from advisor.auth.session import ClerkSession
from advisor.db.models import InviteRequest, User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _clerk_session(email: str, *, user_id: str = "user_abc") -> ClerkSession:
    now = datetime.now(tz=UTC)
    return ClerkSession(
        user_id=user_id,
        email=email,
        session_id="sess_xyz",
        issued_at=now,
        expires_at=now,
        raw_claims={"sub": user_id, "email": email, "name": "Test User"},
    )


def test_resolve_copies_invite_caps_on_first_contact(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        s.add(
            InviteRequest(
                id="ABS-1111",
                email="invited@example.com",
                name="Invited User",
                status="approved",
                granted_query_limit=250,
                granted_monthly_input_tokens=2_000_000,
                granted_monthly_output_tokens=400_000,
                granted_rpm=12,
            )
        )
        s.commit()

        user = resolve_or_create_user(
            s, _clerk_session("invited@example.com")
        )
        s.commit()

        assert user.monthly_query_limit == 250
        assert user.monthly_input_token_limit == 2_000_000
        assert user.monthly_output_token_limit == 400_000
        assert user.requests_per_minute_limit == 12

        invite = (
            s.query(InviteRequest)
            .filter(InviteRequest.id == "ABS-1111")
            .one()
        )
        assert invite.redeemed_at is not None


def test_resolve_uses_defaults_without_invite(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = resolve_or_create_user(
            s, _clerk_session("never-invited@example.com")
        )
        s.commit()
        # Defaults from the User model.
        assert user.monthly_query_limit == 100
        assert user.monthly_input_token_limit == 500_000
        assert user.monthly_output_token_limit == 100_000
        assert user.requests_per_minute_limit == 6


def test_resolve_email_match_is_case_insensitive(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        s.add(
            InviteRequest(
                id="ABS-2222",
                email="Mixed.Case@Example.com",
                name="Casey",
                status="approved",
                granted_query_limit=500,
            )
        )
        s.commit()
        user = resolve_or_create_user(
            s, _clerk_session("mixed.case@example.com")
        )
        s.commit()
        assert user.monthly_query_limit == 500


def test_resolve_does_not_apply_pending_invite(tmp_path: Path) -> None:
    """A pending (not-yet-approved) invite must not grant caps even
    if the user somehow signs up — Clerk's allowlist should have
    blocked them, but we belt-and-suspender it here."""
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        s.add(
            InviteRequest(
                id="ABS-3333",
                email="pending@example.com",
                name="Pending",
                status="pending",
                granted_query_limit=9999,
            )
        )
        s.commit()
        user = resolve_or_create_user(s, _clerk_session("pending@example.com"))
        s.commit()
        # Defaults, not the granted_query_limit from a pending invite.
        assert user.monthly_query_limit == 100
