"""Billing router: 503 when dormant, happy paths under TestClient."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from advisor.api import InMemorySessionStore, create_app
from advisor.billing.client import (
    CheckoutSessionResult,
    MockStripeClient,
    StripeClient,
    StripeEvent,
)
from advisor.billing.router import build_billing_router, build_dormant_billing_router
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db import User
from advisor.llm.mock import MockGateway, text_response
from layer1.db.init_db import create_all
from layer1.db.session import make_session_factory


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _settings(**overrides) -> AdvisorBillingSettings:
    base = dict(
        ADVISOR_BILLING_ENABLED=True,
        STRIPE_API_KEY="sk_test",
        STRIPE_WEBHOOK_SECRET="whsec_test",
        STRIPE_PRICE_PRO="price_pro_123",
        STRIPE_PRICE_TEAM="price_team_456",
        ADVISOR_BILLING_SUCCESS_URL="https://app/success",
        ADVISOR_BILLING_CANCEL_URL="https://app/cancel",
    )
    base.update(overrides)
    return AdvisorBillingSettings(**base)


def _make_user(s, **overrides) -> User:
    base = dict(
        clerk_user_id="clerk_router",
        email="user@example.com",
        full_name="Router User",
        plan_tier="free",
        monthly_query_limit=100,
        monthly_queries_used=7,
        month_started_at=date(2026, 5, 1),
    )
    base.update(overrides)
    user = User(**base)
    s.add(user)
    s.flush()
    return user


def _build_live_app(
    *,
    tmp_path: Path,
    settings: AdvisorBillingSettings,
    client: StripeClient,
) -> tuple[FastAPI, Callable[[], object]]:
    """Wire up a live billing router against an on-disk sqlite db."""
    create_all(_db_url(tmp_path))
    factory = make_session_factory(_db_url(tmp_path))

    @contextmanager
    def _scope() -> Iterator[object]:
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # The auth dependency in tests just yields the X-User-Id header
    # directly so we don't need a Clerk JWKS server.
    from fastapi import Header, HTTPException

    def fake_user_dep(
        x_user_id: str | None = Header(default=None),
    ) -> str:
        if not x_user_id:
            raise HTTPException(status_code=401, detail="auth required")
        return x_user_id

    def resolver(auth_session: object, db) -> User:
        from sqlalchemy import select

        stmt = select(User).where(User.clerk_user_id == str(auth_session))
        return db.execute(stmt).scalar_one()

    app = FastAPI()
    app.include_router(
        build_billing_router(
            settings=settings,
            client_factory=lambda: client,
            db_session_factory=_scope,
            user_dependency=fake_user_dep,
            user_resolver=resolver,
        )
    )
    return app, factory


# ----- dormant-by-default tests ---------------------------------------------


def test_dormant_router_returns_503_for_every_endpoint() -> None:
    app = FastAPI()
    app.include_router(build_dormant_billing_router())
    with TestClient(app) as c:
        assert (
            c.post("/v1/billing/checkout", json={"target_tier": "pro"}).status_code
            == 503
        )
        assert c.post("/v1/billing/webhook", content=b"{}").status_code == 503
        assert c.get("/v1/billing/me").status_code == 503


def test_create_app_mounts_dormant_router_when_billing_disabled() -> None:
    """The full FastAPI factory still serves /v1/billing/me with a
    structured 503 even when no billing settings are passed in."""
    app = create_app(
        gateway=MockGateway(scripted=[text_response("hi")]),
        retrieval_service_factory=lambda: None,
        session_store=InMemorySessionStore(),
        persona_text="x",
    )
    with TestClient(app) as c:
        r = c.get("/v1/billing/me")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "billing_disabled"


def test_create_app_with_disabled_settings_still_mounts_dormant_router() -> None:
    settings = AdvisorBillingSettings()  # enabled=False default
    app = create_app(
        gateway=MockGateway(scripted=[text_response("hi")]),
        retrieval_service_factory=lambda: None,
        session_store=InMemorySessionStore(),
        persona_text="x",
        billing_settings=settings,
    )
    with TestClient(app) as c:
        assert c.get("/v1/billing/me").status_code == 503


# ----- live router happy paths ----------------------------------------------


def test_post_checkout_returns_url(tmp_path: Path) -> None:
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(
            session_id="cs_h", url="https://stripe/c/h"
        )
    )
    settings = _settings()
    app, factory = _build_live_app(
        tmp_path=tmp_path, settings=settings, client=client
    )
    with factory() as s:
        _make_user(s)
        s.commit()

    with TestClient(app) as c:
        r = c.post(
            "/v1/billing/checkout",
            json={"target_tier": "pro"},
            headers={"X-User-Id": "clerk_router"},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://stripe/c/h"}
    assert client.checkout_calls[0].price_id == "price_pro_123"


def test_post_checkout_rejects_unknown_tier(tmp_path: Path) -> None:
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="x", url="x")
    )
    app, factory = _build_live_app(
        tmp_path=tmp_path, settings=_settings(), client=client
    )
    with factory() as s:
        _make_user(s)
        s.commit()

    with TestClient(app) as c:
        r = c.post(
            "/v1/billing/checkout",
            json={"target_tier": "enterprise"},
            headers={"X-User-Id": "clerk_router"},
        )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_target_tier"


def test_post_webhook_happy_path(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "id": "evt_router_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_router",
                    "customer": "cus_router",
                    "subscription": "sub_router",
                    "metadata": {
                        "advisor_user_id": "1",
                        "target_tier": "pro",
                    },
                }
            },
        }
    ).encode("utf-8")
    client = MockStripeClient(
        webhook_events=[
            StripeEvent(
                id="evt_router_1",
                type="checkout.session.completed",
                data={
                    "id": "cs_router",
                    "customer": "cus_router",
                    "subscription": "sub_router",
                    "metadata": {
                        "advisor_user_id": "1",
                        "target_tier": "pro",
                    },
                },
            )
        ]
    )
    settings = _settings()
    app, factory = _build_live_app(
        tmp_path=tmp_path, settings=settings, client=client
    )
    with factory() as s:
        _make_user(s)  # id=1
        s.commit()

    with TestClient(app) as c:
        r = c.post(
            "/v1/billing/webhook",
            content=payload,
            headers={"Stripe-Signature": "t=1,v1=ok"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["handled"] is True
    assert body["event_type"] == "checkout.session.completed"

    with factory() as s:
        user = s.query(User).filter_by(clerk_user_id="clerk_router").one()
        assert user.plan_tier == "pro"
        assert user.monthly_query_limit == 1000
        assert user.stripe_customer_id == "cus_router"


def test_post_webhook_missing_signature_returns_400(tmp_path: Path) -> None:
    client = MockStripeClient()
    app, _ = _build_live_app(
        tmp_path=tmp_path, settings=_settings(), client=client
    )
    with TestClient(app) as c:
        r = c.post("/v1/billing/webhook", content=b"{}")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "missing_signature"


def test_post_webhook_bad_signature_returns_400(tmp_path: Path) -> None:
    client = MockStripeClient(signature_error=ValueError("bad signature"))
    app, _ = _build_live_app(
        tmp_path=tmp_path, settings=_settings(), client=client
    )
    with TestClient(app) as c:
        r = c.post(
            "/v1/billing/webhook",
            content=b"{}",
            headers={"Stripe-Signature": "bogus"},
        )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_signature"


def test_get_me_returns_plan_state(tmp_path: Path) -> None:
    client = MockStripeClient()
    app, factory = _build_live_app(
        tmp_path=tmp_path, settings=_settings(), client=client
    )
    with factory() as s:
        user = _make_user(
            s,
            plan_tier="pro",
            monthly_query_limit=1000,
            monthly_queries_used=42,
            stripe_customer_id="cus_me",
        )
        user.subscription_status = "active"
        s.commit()

    with TestClient(app) as c:
        r = c.get("/v1/billing/me", headers={"X-User-Id": "clerk_router"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "plan_tier": "pro",
        "monthly_query_limit": 1000,
        "monthly_queries_used": 42,
        "stripe_customer_id": "cus_me",
        "subscription_status": "active",
        "enabled": True,
    }
