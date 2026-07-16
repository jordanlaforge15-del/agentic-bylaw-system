"""HTTP-level coverage of the token-wallet read APIs (ABS-380).

Mounts the live and dormant billing routers on a bare FastAPI app with a
sqlite DB and a header-based user dependency, then exercises
``GET /v1/billing/wallet``, ``GET /v1/billing/wallet/transactions``, and the
additive ``token_balance`` on ``GET /v1/billing/me``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from advisor.billing.router import (
    build_billing_router,
    build_dormant_billing_router,
)
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.models import User
from advisor.db.wallet import burn_tokens, grant_tokens
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _user_dependency(x_test_user_id: str = Header(default="u1")) -> str:
    return x_test_user_id


def _user_resolver(auth_session, db) -> User:  # noqa: ANN001
    user = (
        db.query(User).filter(User.clerk_user_id == auth_session).one_or_none()
    )
    if user is None:
        user = User(clerk_user_id=auth_session, email=f"{auth_session}@x.com")
        db.add(user)
        db.flush()
    return user


def _live_client(db_url: str, *, payments_enabled: bool = True) -> TestClient:
    def _db_factory():
        return session_scope(db_url)

    router = build_billing_router(
        settings=AdvisorBillingSettings(
            ADVISOR_BILLING_ENABLED=True,
            ADVISOR_PAYMENTS_ENABLED=payments_enabled,
        ),
        client_factory=None,
        db_session_factory=_db_factory,
        user_dependency=_user_dependency,
        user_resolver=_user_resolver,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _dormant_client(db_url: str) -> TestClient:
    def _db_factory():
        return session_scope(db_url)

    router = build_dormant_billing_router(
        db_session_factory=_db_factory,
        user_dependency=_user_dependency,
        user_resolver=_user_resolver,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _seed(db_url: str, *, clerk_user_id: str, balance: int, unlimited: bool = False) -> int:
    create_all(db_url)
    with session_scope(db_url) as s:
        user = User(
            clerk_user_id=clerk_user_id,
            email=f"{clerk_user_id}@x.com",
            unlimited_credits=unlimited,
        )
        s.add(user)
        s.flush()
        if balance > 0:
            grant_tokens(s, user=user, amount=balance)
        elif balance < 0:
            burn_tokens(s, user=user, amount=-balance)
        s.commit()
        return user.id


def _headers(uid_clerk: str) -> dict:
    return {"X-Test-User-Id": uid_clerk}


# ---------- GET /wallet ---------------------------------------------------


def test_wallet_shape_and_turns(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "2500")
    monkeypatch.setenv("ADVISOR_LOW_BALANCE_WARN_TOKENS", "5000")
    monkeypatch.setenv("ADVISOR_CHAT_MIN_BALANCE_TOKENS", "0")
    db_url = _db_url(tmp_path)
    _seed(db_url, clerk_user_id="u1", balance=25_000)
    client = _live_client(db_url)

    r = client.get("/v1/billing/wallet", headers=_headers("u1"))
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "balance_tokens": 25_000,
        "approx_turns_remaining": 10,
        "tokens_per_turn": 2_500,
        "low_balance": False,
        "warn_threshold_tokens": 5_000,
        "floor_tokens": 0,
        "chat_enabled": True,
        "payments_enabled": True,
    }


def test_wallet_low_balance_flips_at_warn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_LOW_BALANCE_WARN_TOKENS", "5000")
    db_url = _db_url(tmp_path)
    _seed(db_url, clerk_user_id="u1", balance=5_000)  # exactly at threshold
    client = _live_client(db_url)
    body = client.get("/v1/billing/wallet", headers=_headers("u1")).json()
    assert body["low_balance"] is True


def test_wallet_chat_disabled_at_or_below_floor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_CHAT_MIN_BALANCE_TOKENS", "0")
    db_url = _db_url(tmp_path)
    _seed(db_url, clerk_user_id="u1", balance=0)
    client = _live_client(db_url)
    body = client.get("/v1/billing/wallet", headers=_headers("u1")).json()
    assert body["chat_enabled"] is False
    assert body["approx_turns_remaining"] == 0


def test_wallet_unlimited_credits_always_chat_enabled(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url, clerk_user_id="u1", balance=-10_000, unlimited=True)
    client = _live_client(db_url)
    body = client.get("/v1/billing/wallet", headers=_headers("u1")).json()
    assert body["chat_enabled"] is True  # negative balance, still enabled
    assert body["low_balance"] is False  # unlimited never runs "low"
    assert body["approx_turns_remaining"] == 0  # negative floors to 0


def test_wallet_factor_change_effective_without_restart(
    tmp_path: Path, monkeypatch
) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url, clerk_user_id="u1", balance=25_000)
    client = _live_client(db_url)

    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "2500")
    assert (
        client.get("/v1/billing/wallet", headers=_headers("u1")).json()[
            "approx_turns_remaining"
        ]
        == 10
    )
    # Re-tune the factor; the very next request reflects it — no restart.
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "5000")
    body = client.get("/v1/billing/wallet", headers=_headers("u1")).json()
    assert body["tokens_per_turn"] == 5_000
    assert body["approx_turns_remaining"] == 5


def test_wallet_available_on_dormant_router(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url, clerk_user_id="u1", balance=25_000)
    client = _dormant_client(db_url)
    body = client.get("/v1/billing/wallet", headers=_headers("u1")).json()
    assert body["balance_tokens"] == 25_000
    # Payments are off on the dormant router.
    assert body["payments_enabled"] is False


# ---------- GET /wallet/transactions --------------------------------------


def test_transactions_newest_first_and_paged(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    with session_scope(db_url) as s:
        user = User(clerk_user_id="u1", email="u1@x.com")
        s.add(user)
        s.flush()
        for i in range(5):
            grant_tokens(s, user=user, amount=1_000, reason=f"g{i}")
        s.commit()
    client = _live_client(db_url)

    page1 = client.get(
        "/v1/billing/wallet/transactions?limit=2", headers=_headers("u1")
    ).json()
    assert [t["reason"] for t in page1["transactions"]] == ["g4", "g3"]
    assert page1["next_before_id"] is not None

    cursor = page1["next_before_id"]
    page2 = client.get(
        f"/v1/billing/wallet/transactions?limit=2&before_id={cursor}",
        headers=_headers("u1"),
    ).json()
    assert [t["reason"] for t in page2["transactions"]] == ["g2", "g1"]


def test_transactions_are_ownership_scoped(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    with session_scope(db_url) as s:
        a = User(clerk_user_id="a", email="a@x.com")
        b = User(clerk_user_id="b", email="b@x.com")
        s.add_all([a, b])
        s.flush()
        grant_tokens(s, user=a, amount=1_000, reason="a-grant")
        grant_tokens(s, user=b, amount=9_000, reason="b-grant")
        s.commit()
    client = _live_client(db_url)

    body = client.get(
        "/v1/billing/wallet/transactions", headers=_headers("a")
    ).json()
    reasons = [t["reason"] for t in body["transactions"]]
    assert reasons == ["a-grant"]
    assert "b-grant" not in reasons


def test_transaction_row_shape(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    with session_scope(db_url) as s:
        user = User(clerk_user_id="u1", email="u1@x.com")
        s.add(user)
        s.flush()
        grant_tokens(s, user=user, amount=25_000, reason="signup_grant")
        s.commit()
    client = _live_client(db_url)
    row = client.get(
        "/v1/billing/wallet/transactions", headers=_headers("u1")
    ).json()["transactions"][0]
    assert row["entry_type"] == "grant"
    assert row["amount_tokens"] == 25_000
    assert row["balance_after"] == 25_000
    assert row["reason"] == "signup_grant"
    assert "created_at" in row and row["created_at"]


# ---------- GET /me additive token_balance --------------------------------


def test_me_includes_token_balance(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url, clerk_user_id="u1", balance=25_000)
    client = _live_client(db_url)
    body = client.get("/v1/billing/me", headers=_headers("u1")).json()
    assert body["token_balance"] == 25_000
    # Legacy fields intact.
    assert "tier_balances" in body
    assert "free_questions_remaining" in body


def test_me_token_balance_on_dormant_router(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url, clerk_user_id="u1", balance=7_000)
    client = _dormant_client(db_url)
    body = client.get("/v1/billing/me", headers=_headers("u1")).json()
    assert body["token_balance"] == 7_000
