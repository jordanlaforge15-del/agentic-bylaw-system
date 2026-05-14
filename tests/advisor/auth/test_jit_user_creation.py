"""JIT user creation path: never write `""` to advisor_user.email.

Repro target for the prod bug where three of four advisor_user rows
landed with blank emails because Clerk's default JWT template omits
`email` and the JIT path coalesced `None` to `""`.

The fix has three cases that need to behave correctly:

  1. JWT carries `email` → row created with it, no Backend API call.
  2. JWT missing `email`, Backend API returns email → row created with
     the API-sourced email.
  3. JWT missing `email`, Backend API yields nothing (no key, network
     error, or empty profile) → 503, no row inserted.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from advisor.api.auth import resolve_or_create_user
from advisor.auth.clerk_backend import (
    ClerkBackendClient,
    ClerkBackendError,
    ClerkUserProfile,
)
from advisor.auth.session import ClerkSession
from advisor.db import User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _make_session(email: str | None) -> ClerkSession:
    now = datetime.now(tz=UTC)
    return ClerkSession(
        user_id="user_jit_test",
        email=email,
        session_id="sess_test",
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        raw_claims={"sub": "user_jit_test"},
    )


class _StubBackendClient:
    """ClerkBackendClient stub with controllable behaviour.

    Subclassing the real client means the production code path uses
    the same interface; tests just swap the network call out.
    """

    def __init__(
        self,
        *,
        configured: bool = True,
        profile: ClerkUserProfile | None = None,
        raises: ClerkBackendError | None = None,
    ) -> None:
        self._configured = configured
        self._profile = profile
        self._raises = raises
        self.calls: list[str] = []

    @property
    def configured(self) -> bool:
        return self._configured

    def fetch_user(self, clerk_user_id: str) -> ClerkUserProfile:
        self.calls.append(clerk_user_id)
        if self._raises is not None:
            raise self._raises
        assert self._profile is not None
        return self._profile


def test_jwt_with_email_creates_row_no_backend_call(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    backend = _StubBackendClient(configured=True, profile=ClerkUserProfile(None, None))
    with session_scope(_db_url(tmp_path)) as s:
        user = resolve_or_create_user(
            s,
            _make_session(email="alice@example.com"),
            backend_client=backend,  # type: ignore[arg-type]
        )
        s.commit()
        assert user.email == "alice@example.com"
        assert backend.calls == []  # Backend API not consulted


def test_jwt_missing_email_falls_back_to_backend(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    backend = _StubBackendClient(
        configured=True,
        profile=ClerkUserProfile(email="bob@example.com", full_name="Bob Builder"),
    )
    with session_scope(_db_url(tmp_path)) as s:
        user = resolve_or_create_user(
            s,
            _make_session(email=None),
            backend_client=backend,  # type: ignore[arg-type]
        )
        s.commit()
        assert user.email == "bob@example.com"
        assert user.full_name == "Bob Builder"
        assert backend.calls == ["user_jit_test"]


def test_jwt_and_backend_both_empty_raises_503(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    backend = _StubBackendClient(
        configured=True,
        profile=ClerkUserProfile(email=None, full_name=None),
    )
    with session_scope(_db_url(tmp_path)) as s:
        with pytest.raises(HTTPException) as excinfo:
            resolve_or_create_user(
                s,
                _make_session(email=None),
                backend_client=backend,  # type: ignore[arg-type]
            )
        assert excinfo.value.status_code == 503
        detail = excinfo.value.detail
        assert isinstance(detail, dict) and detail.get("code") == "email_unavailable"

    # The exception must bubble before any row is inserted.
    with session_scope(_db_url(tmp_path)) as s:
        assert (
            s.query(User).filter(User.clerk_user_id == "user_jit_test").one_or_none()
            is None
        )


def test_jwt_missing_email_unconfigured_backend_raises_503(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    backend = _StubBackendClient(configured=False, profile=ClerkUserProfile(None, None))
    with session_scope(_db_url(tmp_path)) as s:
        with pytest.raises(HTTPException) as excinfo:
            resolve_or_create_user(
                s,
                _make_session(email=None),
                backend_client=backend,  # type: ignore[arg-type]
            )
        assert excinfo.value.status_code == 503
        assert backend.calls == []  # We must not call fetch_user when unconfigured

    with session_scope(_db_url(tmp_path)) as s:
        assert (
            s.query(User).filter(User.clerk_user_id == "user_jit_test").one_or_none()
            is None
        )


def test_jwt_missing_email_backend_error_raises_503(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    backend = _StubBackendClient(
        configured=True,
        raises=ClerkBackendError("network down"),
    )
    with session_scope(_db_url(tmp_path)) as s:
        with pytest.raises(HTTPException) as excinfo:
            resolve_or_create_user(
                s,
                _make_session(email=None),
                backend_client=backend,  # type: ignore[arg-type]
            )
        assert excinfo.value.status_code == 503


def test_existing_user_no_backend_call_when_email_present(tmp_path: Path) -> None:
    """Drift refresh on an already-existing user must not poke the Backend API.

    The Backend API is a fallback for fresh inserts only — calling it
    on every authenticated request would cost a round-trip per call.
    """
    create_all(_db_url(tmp_path))
    backend = _StubBackendClient(
        configured=True,
        profile=ClerkUserProfile(email="should-not-be-used@example.com", full_name=None),
    )
    with session_scope(_db_url(tmp_path)) as s:
        user = User(
            clerk_user_id="user_jit_test",
            email="alice@example.com",
            full_name="Alice",
        )
        s.add(user)
        s.commit()

    with session_scope(_db_url(tmp_path)) as s:
        resolve_or_create_user(
            s,
            _make_session(email=None),
            backend_client=backend,  # type: ignore[arg-type]
        )
        s.commit()
    assert backend.calls == []
