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
from advisor.db.cases import list_user_cases, open_case_free
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


# -- ABS-424: single-case lookup --------------------------------------------
#
# The footer's "CASE #N" is ``user_case_number``. It used to be hydrated by
# scanning the capped ``GET /v1/cases`` list, so an older case simply wasn't
# there and the number only appeared once a chat turn's SSE ``session`` event
# supplied it — the badge looked like it changed identity mid-conversation.
# ``GET /v1/cases/{id}`` always resolves.


def test_get_case_by_id_returns_user_case_number(wired: _Wired):
    opened = wired.client.post(
        "/v1/cases",
        json={"anchor_label": "77 Birch Ln", "anchor_kind": "address"},
    )
    case = opened.json()["case"]
    r = wired.client.get(f"/v1/cases/{case['id']}")
    assert r.status_code == 200, r.text
    fetched = r.json()["case"]
    assert fetched["id"] == case["id"]
    assert fetched["user_case_number"] == case["user_case_number"]
    assert fetched["anchor_label"] == "77 Birch Ln"


def test_get_case_by_id_resolves_case_outside_capped_list(wired: _Wired):
    """A case pushed off the newest-N list is still fetchable by id."""
    opened = wired.client.post(
        "/v1/cases",
        json={"anchor_label": "1 Old Rd", "anchor_kind": "address"},
    )
    old = opened.json()["case"]
    for i in range(60):
        wired.client.post(
            "/v1/cases",
            json={"anchor_label": f"{i} Newer St", "anchor_kind": "address"},
        )

    with session_scope(wired.db_url) as session:
        listed = list_user_cases(session, user_id=wired.user_id)
        assert old["id"] not in {c.id for c in listed}

    r = wired.client.get(f"/v1/cases/{old['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["case"]["user_case_number"] == old["user_case_number"]


def test_get_case_by_id_404s_for_another_users_case(wired: _Wired):
    with session_scope(wired.db_url) as session:
        other = User(
            clerk_user_id="test-user-2",
            email="other@example.com",
            full_name="Other User",
        )
        session.add(other)
        session.flush()
        case = open_case_free(
            session,
            user=other,
            anchor_label="404 Hidden Way",
            anchor_kind="address",
        )
        session.flush()
        foreign_case_id = case.id

    r = wired.client.get(f"/v1/cases/{foreign_case_id}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "case_not_found"


def test_get_case_by_id_does_not_shadow_match_route(wired: _Wired):
    """``/v1/cases/match`` must keep resolving as the static route."""
    r = wired.client.get(
        "/v1/cases/match",
        params={"anchor_label": "5 Cedar Cr", "anchor_kind": "address"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["matched"] is False
