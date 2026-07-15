"""ABS-390: POST /v1/billing/checkout/pack is retired -> 410 packs_retired.

The beta pivot replaced the case-credit tier×pack catalog with the turn-based
token wallet. Pack checkout is gone product-wide, so the endpoint answers a
permanent 410 Gone (not a transient 503) on BOTH the live and the dormant
router — a lingering client sees an explicit, permanent retirement in every
posture. Buy turns via POST /checkout/topup instead.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from advisor.billing.router import (
    build_billing_router,
    build_dormant_billing_router,
)
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.models import User
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


def _seed(db_url: str) -> None:
    create_all(db_url)


def _live_client(db_url: str) -> TestClient:
    def _db_factory():
        return session_scope(db_url)

    router = build_billing_router(
        # Fully-configured live billing (enabled + payments on + a Stripe
        # client): the point is that pack checkout is retired even when every
        # precondition for the OLD Stripe path is satisfied — the 410 does not
        # depend on billing being disabled.
        settings=AdvisorBillingSettings(
            ADVISOR_BILLING_ENABLED=True,
            ADVISOR_PAYMENTS_ENABLED=True,
            STRIPE_PRICE_STANDARD_STARTER="price_std_starter_x",
        ),
        client_factory=lambda: object(),
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


def _headers() -> dict:
    return {"X-Test-User-Id": "u1"}


def _body() -> dict:
    return {"tier": "standard", "pack_sku": "starter"}


def test_checkout_pack_retired_on_live_router(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    client = _live_client(db_url)

    r = client.post(
        "/v1/billing/checkout/pack", json=_body(), headers=_headers()
    )
    assert r.status_code == 410, r.text
    assert r.json()["detail"]["code"] == "packs_retired"


def test_checkout_pack_retired_on_dormant_router(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    client = _dormant_client(db_url)

    r = client.post(
        "/v1/billing/checkout/pack", json=_body(), headers=_headers()
    )
    assert r.status_code == 410, r.text
    assert r.json()["detail"]["code"] == "packs_retired"
