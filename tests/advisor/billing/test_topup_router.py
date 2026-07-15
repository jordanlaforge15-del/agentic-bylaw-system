"""HTTP-level coverage of the top-up catalog + checkout endpoints (ABS-381).

Mounts the live and dormant billing routers on a bare FastAPI app and drives
``GET /v1/billing/topups`` and ``POST /v1/billing/checkout/topup`` across the
payment postures.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from advisor.billing.client import CheckoutSessionResult, MockStripeClient
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


def _live_client(
    db_url: str,
    *,
    payments_enabled: bool = True,
    prices: bool = True,
    client_factory=None,
) -> TestClient:
    def _db_factory():
        return session_scope(db_url)

    kwargs = {}
    if prices:
        kwargs["STRIPE_PRICE_TOPUP_SMALL"] = "price_small_x"
        kwargs["STRIPE_PRICE_TOPUP_MEDIUM"] = "price_medium_x"
        kwargs["STRIPE_PRICE_TOPUP_LARGE"] = "price_large_x"
    router = build_billing_router(
        settings=AdvisorBillingSettings(
            ADVISOR_BILLING_ENABLED=True,
            ADVISOR_PAYMENTS_ENABLED=payments_enabled,
            **kwargs,
        ),
        client_factory=client_factory,
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


# ---------- GET /topups (public catalog) ----------------------------------


def test_topups_catalog_payments_on(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "2500")
    db_url = _db_url(tmp_path)
    _seed(db_url)
    client = _live_client(db_url)

    r = client.get("/v1/billing/topups")
    assert r.status_code == 200
    body = r.json()
    assert body["payments_enabled"] is True
    assert body["currency"] == "CAD"
    assert body["tokens_per_turn"] == 2_500
    assert [o["sku"] for o in body["options"]] == ["small", "medium", "large"]
    medium = next(o for o in body["options"] if o["sku"] == "medium")
    assert medium["tokens"] == 75_000
    assert medium["price_cents"] == 5000
    # Backend-owned turns conversion: floor(75000 / 2500) == 30.
    assert medium["approx_turns"] == 30
    # available = payments_enabled AND price id configured.
    assert all(o["available"] is True for o in body["options"])


def test_topups_available_false_when_price_unset(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    client = _live_client(db_url, payments_enabled=True, prices=False)
    body = client.get("/v1/billing/topups").json()
    assert body["payments_enabled"] is True
    assert all(o["available"] is False for o in body["options"])


def test_topups_available_false_when_payments_off(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    client = _live_client(db_url, payments_enabled=False, prices=True)
    body = client.get("/v1/billing/topups").json()
    assert body["payments_enabled"] is False
    # Price ids configured but payments off → still unavailable.
    assert all(o["available"] is False for o in body["options"])


def test_topups_catalog_on_dormant_router(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    client = _dormant_client(db_url)
    body = client.get("/v1/billing/topups").json()
    assert body["payments_enabled"] is False
    assert [o["sku"] for o in body["options"]] == ["small", "medium", "large"]
    assert all(o["available"] is False for o in body["options"])


# ---------- POST /checkout/topup ------------------------------------------


def test_checkout_topup_success(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    mock = MockStripeClient(
        checkout_result=CheckoutSessionResult(
            session_id="cs_1", url="https://stripe.test/cs_1"
        )
    )
    client = _live_client(db_url, client_factory=lambda: mock)

    r = client.post(
        "/v1/billing/checkout/topup", json={"sku": "small"}, headers=_headers()
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://stripe.test/cs_1"}
    call = mock.checkout_calls[-1]
    assert call.price_id == "price_small_x"
    assert call.metadata["topup_sku"] == "small"


def test_checkout_topup_payments_disabled(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    mock = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs_1", url="u")
    )
    client = _live_client(
        db_url, payments_enabled=False, client_factory=lambda: mock
    )
    r = client.post(
        "/v1/billing/checkout/topup", json={"sku": "small"}, headers=_headers()
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "payments_disabled"
    # No Stripe call was made.
    assert mock.checkout_calls == []


def test_checkout_topup_unknown_sku(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    mock = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs_1", url="u")
    )
    client = _live_client(db_url, client_factory=lambda: mock)
    r = client.post(
        "/v1/billing/checkout/topup", json={"sku": "jumbo"}, headers=_headers()
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unknown_topup"


def test_checkout_topup_price_not_configured(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    mock = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs_1", url="u")
    )
    client = _live_client(
        db_url, prices=False, client_factory=lambda: mock
    )
    r = client.post(
        "/v1/billing/checkout/topup", json={"sku": "small"}, headers=_headers()
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "price_not_configured"


def test_checkout_topup_dormant_router_503(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed(db_url)
    client = _dormant_client(db_url)
    r = client.post(
        "/v1/billing/checkout/topup", json={"sku": "small"}, headers=_headers()
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "payments_disabled"
