"""Signup token-wallet grant on first sign-in + self-heal (ABS-380).

``resolve_or_create_user`` must land a brand-new user with the configured
token grant, mark the idempotency flag, self-heal pre-wallet users on their
next auth, and never re-grant a user who has already burned their grant.
The free-question machinery must be untouched.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from advisor.api.auth import resolve_or_create_user
from advisor.auth.clerk_backend import ClerkUserProfile
from advisor.auth.session import ClerkSession
from advisor.billing.turns import signup_token_grant
from advisor.db import User, burn_tokens, grant_signup_tokens_if_needed
from advisor.db.models import TokenTransaction
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _make_session(*, user_id: str = "user_wallet", email: str | None = "u@x.com") -> ClerkSession:
    now = datetime.now(tz=UTC)
    return ClerkSession(
        user_id=user_id,
        email=email,
        session_id="sess_test",
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        raw_claims={"sub": user_id},
    )


class _StubBackendClient:
    configured = True

    def fetch_user(self, clerk_user_id: str) -> ClerkUserProfile:  # pragma: no cover
        return ClerkUserProfile(email="u@x.com", full_name=None)


def _grant_rows(s, user_id: int) -> list[TokenTransaction]:
    return (
        s.query(TokenTransaction)
        .filter(
            TokenTransaction.user_id == user_id,
            TokenTransaction.entry_type == "grant",
        )
        .all()
    )


def test_new_user_gets_signup_token_grant(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    with session_scope(db_url) as s:
        user = resolve_or_create_user(
            s, _make_session(), backend_client=_StubBackendClient()  # type: ignore[arg-type]
        )
        s.commit()
        assert user.token_balance == signup_token_grant()
        assert user.metadata_json.get("token_grant_issued") is True
        grants = _grant_rows(s, user.id)
        assert len(grants) == 1
        assert grants[0].reason == "signup_grant"
        # Free-question machinery is untouched by the token grant.
        assert user.free_questions_remaining == 3


def test_grant_amount_follows_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_SIGNUP_TOKEN_GRANT", "40000")
    db_url = _db_url(tmp_path)
    create_all(db_url)
    with session_scope(db_url) as s:
        user = resolve_or_create_user(
            s, _make_session(), backend_client=_StubBackendClient()  # type: ignore[arg-type]
        )
        s.commit()
        assert user.token_balance == 40_000


def test_pre_wallet_user_self_heals_once(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    # Simulate a pre-wallet user: exists, no grant flag, balance 0.
    with session_scope(db_url) as s:
        s.add(User(clerk_user_id="user_wallet", email="u@x.com"))
        s.commit()

    with session_scope(db_url) as s:
        user = resolve_or_create_user(
            s, _make_session(), backend_client=_StubBackendClient()  # type: ignore[arg-type]
        )
        s.commit()
        assert user.token_balance == signup_token_grant()
        assert len(_grant_rows(s, user.id)) == 1


def test_no_second_grant_after_burning_to_zero(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    # First sign-in grants.
    with session_scope(db_url) as s:
        user = resolve_or_create_user(
            s, _make_session(), backend_client=_StubBackendClient()  # type: ignore[arg-type]
        )
        s.commit()
        uid = user.id
    # User burns the whole grant.
    with session_scope(db_url) as s:
        burn_tokens(s, user=s.get(User, uid), amount=signup_token_grant())
        s.commit()
    # Next sign-in must NOT re-grant.
    with session_scope(db_url) as s:
        user = resolve_or_create_user(
            s, _make_session(), backend_client=_StubBackendClient()  # type: ignore[arg-type]
        )
        s.commit()
        assert user.token_balance == 0
        assert len(_grant_rows(s, uid)) == 1


def test_invite_path_user_also_gets_grant(tmp_path: Path) -> None:
    # An invited user (approved InviteRequest) still gets the token grant.
    from advisor.db.models import InviteRequest

    db_url = _db_url(tmp_path)
    create_all(db_url)
    with session_scope(db_url) as s:
        s.add(
            InviteRequest(
                id="inv1",
                email="u@x.com",
                name="Invited User",
                status="approved",
                granted_starter_credits=0,
            )
        )
        s.commit()
    with session_scope(db_url) as s:
        user = resolve_or_create_user(
            s, _make_session(), backend_client=_StubBackendClient()  # type: ignore[arg-type]
        )
        s.commit()
        assert user.token_balance == signup_token_grant()
        assert len(_grant_rows(s, user.id)) == 1


def test_stale_in_memory_user_does_not_regrant(tmp_path: Path) -> None:
    """A ``User`` instance whose in-memory ``metadata_json`` predates the
    grant must NOT produce a second one (ABS-404).

    This is the half of the double-grant defect that reproduces without
    Postgres. The gate used to be a plain read of the caller's
    already-loaded ``user.metadata_json``; whenever that instance was
    stale relative to the row — which is exactly the state a losing
    racer is left in — the check passed and a second grant was written.
    Production recorded two ``+25,000`` entries for one user on
    2026-07-17 this way.

    The fix re-reads the flag from a ``FOR UPDATE``-locked row with
    ``populate_existing=True``. Here we force the staleness explicitly:
    load the user, let a separate session issue the grant and commit,
    then call the helper with the stale instance. The full concurrent
    race needs real row locks and is pinned in
    ``test_wallet_concurrency_pg.py``.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    with session_scope(db_url) as s:
        s.add(User(clerk_user_id="user_stale", email="stale@x.com"))
        s.commit()

    with session_scope(db_url) as reader:
        stale = (
            reader.query(User).filter(User.clerk_user_id == "user_stale").one()
        )
        uid = stale.id
        assert stale.metadata_json.get("token_grant_issued") is None

        # Another session grants and commits behind the reader's back.
        with session_scope(db_url) as writer:
            grant_signup_tokens_if_needed(writer, user=writer.get(User, uid))
            writer.commit()

        # The stale instance still says "no grant issued" in memory, but
        # the helper must consult the row, not the instance.
        assert grant_signup_tokens_if_needed(reader, user=stale) is False
        reader.commit()

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert len(_grant_rows(s, uid)) == 1
        assert user.token_balance == signup_token_grant()


def test_repeat_calls_in_one_session_grant_once(tmp_path: Path) -> None:
    """Back-to-back calls inside a single session still grant once — the
    locked re-read must not lose the flag written moments earlier by an
    unflushed sibling call."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    with session_scope(db_url) as s:
        s.add(User(clerk_user_id="user_twice", email="twice@x.com"))
        s.commit()
        user = s.query(User).filter(User.clerk_user_id == "user_twice").one()

        assert grant_signup_tokens_if_needed(s, user=user) is True
        assert grant_signup_tokens_if_needed(s, user=user) is False
        s.commit()

        assert len(_grant_rows(s, user.id)) == 1
        assert user.token_balance == signup_token_grant()
