"""Unit tests for advisor.api.api_key_auth (ABS-59).

Tests the issue/revoke helpers and the FastAPI dependency produced by
api_key_user_dependency.  Uses an in-memory SQLite DB; no network calls.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from advisor.api.api_key_auth import (
    api_key_user_dependency,
    generate_api_key,
    issue_api_key,
    revoke_api_key,
)
from advisor.db.models import AdvisorApiKey, User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'auth.db'}"
    create_all(url)
    return url


@pytest.fixture()
def seeded_user(db_url: str) -> int:
    with session_scope(db_url) as db:
        user = User(
            clerk_user_id="speckle-service",
            email="speckle@example.com",
            full_name="Speckle Bot",
        )
        db.add(user)
        db.flush()
        user_id = user.id
        db.commit()
    return user_id


# ---------------------------------------------------------------------------
# generate_api_key
# ---------------------------------------------------------------------------


def test_generate_api_key_returns_hex_pair() -> None:
    raw, digest = generate_api_key()
    assert len(raw) == 64  # 32 bytes → 64 hex chars
    assert digest == hashlib.sha256(raw.encode()).hexdigest()


def test_generate_api_key_unique() -> None:
    keys = {generate_api_key()[0] for _ in range(20)}
    assert len(keys) == 20


# ---------------------------------------------------------------------------
# issue_api_key / revoke_api_key
# ---------------------------------------------------------------------------


def test_issue_and_revoke(db_url: str, seeded_user: int) -> None:
    with session_scope(db_url) as db:
        row, raw_key = issue_api_key(db, user_id=seeded_user, name="Speckle Automate")
        assert row.id is not None
        assert row.key_hash == hashlib.sha256(raw_key.encode()).hexdigest()
        assert row.revoked_at is None
        key_id = row.id
        db.commit()

    with session_scope(db_url) as db:
        revoked = revoke_api_key(db, key_id=key_id, user_id=seeded_user)
        assert revoked is True
        row2 = db.get(AdvisorApiKey, key_id)
        assert row2.revoked_at is not None
        db.commit()


def test_revoke_wrong_user_returns_false(db_url: str, seeded_user: int) -> None:
    with session_scope(db_url) as db:
        row, _ = issue_api_key(db, user_id=seeded_user, name="k")
        key_id = row.id
        db.commit()

    with session_scope(db_url) as db:
        result = revoke_api_key(db, key_id=key_id, user_id=seeded_user + 999)
        assert result is False
        db.commit()


# ---------------------------------------------------------------------------
# api_key_user_dependency (FastAPI integration)
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_app(db_url: str, seeded_user: int) -> tuple[TestClient, str]:
    @contextmanager
    def _db_factory():
        with session_scope(db_url) as session:
            yield session

    dep = api_key_user_dependency(_db_factory)
    app = FastAPI()

    from fastapi import Depends

    @app.get("/whoami")
    def whoami(user: User = Depends(dep)):
        return {"user_id": user.id, "email": user.email}

    # Issue a key and return the raw value alongside the TestClient
    with session_scope(db_url) as db:
        _, raw_key = issue_api_key(db, user_id=seeded_user, name="test")
        db.commit()

    return TestClient(app), raw_key


def test_valid_key_resolves_user(api_app) -> None:
    tc, raw_key = api_app
    resp = tc.get("/whoami", headers={"X-ABS-API-Key": raw_key})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "speckle@example.com"


def test_missing_key_returns_401(api_app) -> None:
    tc, _ = api_app
    resp = tc.get("/whoami")
    assert resp.status_code == 401


def test_wrong_key_returns_401(api_app) -> None:
    tc, _ = api_app
    resp = tc.get("/whoami", headers={"X-ABS-API-Key": "deadbeef" * 8})
    assert resp.status_code == 401


def test_revoked_key_returns_401(api_app, db_url: str, seeded_user: int) -> None:
    tc, raw_key = api_app
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    with session_scope(db_url) as db:
        row = db.query(AdvisorApiKey).filter_by(key_hash=key_hash).one()
        revoke_api_key(db, key_id=row.id, user_id=seeded_user)
        db.commit()

    resp = tc.get("/whoami", headers={"X-ABS-API-Key": raw_key})
    assert resp.status_code == 401
