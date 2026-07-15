"""ABS-382: free case open + tier-upgrade retirement.

Opening a case costs nothing and reserves no CaseCredit; the
``/upgrade`` endpoint is retired and returns 410.

Router is mounted on a minimal FastAPI app + TestClient with a fresh
sqlite DB per test, mirroring test_submissions_router.py.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from advisor.api.cases_router import build_cases_router
from advisor.db.models import CaseCredit, User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


@dataclass
class _Wired:
    client: TestClient
    db_url: str
    user_id: int


@pytest.fixture()
def wired(tmp_path: Path) -> Iterator[_Wired]:
    db_url = f"sqlite:///{tmp_path / 'cases.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        user = User(
            clerk_user_id="test-user-1",
            email="test@example.com",
            full_name="Test User",
        )
        session.add(user)
        session.flush()
        user_id = user.id

    @contextmanager
    def _db_factory():
        with session_scope(db_url) as session:
            yield session

    def _user_dep() -> User:
        with session_scope(db_url) as session:
            return session.get(User, user_id)

    def _user_resolver(auth_session: Any, db) -> User:
        return db.get(User, user_id)

    router = build_cases_router(
        classifier_gateway_factory=None,
        classifier_model="stub",
        db_session_factory=_db_factory,
        user_dependency=_user_dep,
        user_resolver=_user_resolver,
    )
    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        yield _Wired(client=client, db_url=db_url, user_id=user_id)


def _credit_count(db_url: str) -> int:
    with session_scope(db_url) as session:
        return session.query(CaseCredit).count()


def test_open_case_is_free_no_credit_reserved(wired: _Wired):
    r = wired.client.post(
        "/v1/cases",
        json={"anchor_label": "123 Main St", "anchor_kind": "address"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credit_id"] is None
    assert body["case"]["current_tier"] is None
    assert body["reused_existing_case"] is False
    # No CaseCredit row created or reserved.
    assert _credit_count(wired.db_url) == 0


def test_open_case_reuses_existing_within_window(wired: _Wired):
    first = wired.client.post(
        "/v1/cases",
        json={"anchor_label": "55 Elm Ave", "anchor_kind": "address"},
    )
    assert first.status_code == 200, first.text
    second = wired.client.post(
        "/v1/cases",
        json={"anchor_label": "55 Elm Ave", "anchor_kind": "address"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["reused_existing_case"] is True
    assert first.json()["case"]["id"] == second.json()["case"]["id"]
    assert _credit_count(wired.db_url) == 0


def test_open_case_accepts_but_ignores_legacy_tier(wired: _Wired):
    r = wired.client.post(
        "/v1/cases",
        json={
            "anchor_label": "9 Oak Rd",
            "anchor_kind": "address",
            "tier": "complex",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["credit_id"] is None
    assert r.json()["case"]["current_tier"] is None


def test_upgrade_endpoint_retired_returns_410(wired: _Wired):
    opened = wired.client.post(
        "/v1/cases",
        json={"anchor_label": "1 Pine Ct", "anchor_kind": "address"},
    )
    case_id = opened.json()["case"]["id"]
    r = wired.client.post(
        f"/v1/cases/{case_id}/upgrade",
        json={"target_tier": "complex"},
    )
    assert r.status_code == 410, r.text
    assert r.json()["detail"]["code"] == "tier_model_retired"


def test_upgrade_retired_even_with_empty_body(wired: _Wired):
    r = wired.client.post("/v1/cases/999/upgrade", json={})
    assert r.status_code == 410, r.text
    assert r.json()["detail"]["code"] == "tier_model_retired"
