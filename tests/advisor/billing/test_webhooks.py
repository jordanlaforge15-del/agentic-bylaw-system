"""Webhook handler: per-event-type effects + idempotency."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from advisor.billing.client import StripeEvent
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
from advisor.db import UsageEvent, User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _settings(**overrides) -> AdvisorBillingSettings:
    base = dict(
        ADVISOR_BILLING_ENABLED=True,
        STRIPE_API_KEY="sk_test",
        STRIPE_WEBHOOK_SECRET="whsec",
        STRIPE_PRICE_PRO="price_pro_123",
        STRIPE_PRICE_TEAM="price_team_456",
    )
    base.update(overrides)
    return AdvisorBillingSettings(**base)


def _make_user(s, **overrides) -> User:
    base = dict(
        clerk_user_id="clerk_wh",
        email="user@example.com",
        full_name="Webhook User",
        plan_tier="free",
        monthly_query_limit=100,
        monthly_queries_used=0,
        month_started_at=date(2026, 5, 1),
    )
    base.update(overrides)
    user = User(**base)
    s.add(user)
    s.flush()
    return user


# ---------- checkout.session.completed --------------------------------------


def test_checkout_completed_links_customer_and_upgrades(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        event = StripeEvent(
            id="evt_1",
            type="checkout.session.completed",
            data={
                "id": "cs_1",
                "customer": "cus_new",
                "subscription": "sub_new",
                "metadata": {
                    "advisor_user_id": str(user.id),
                    "target_tier": "pro",
                },
            },
        )
        result = handle_event(s, event, _settings())
        assert result.handled is True
        assert result.user_id == user.id
        s.flush()
        s.refresh(user)
        assert user.stripe_customer_id == "cus_new"
        assert user.stripe_subscription_id == "sub_new"
        assert user.plan_tier == "pro"
        assert user.monthly_query_limit == 1000
        assert user.subscription_status == "active"


def test_checkout_completed_with_missing_metadata_returns_handled_no_change(
    tmp_path: Path,
) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        event = StripeEvent(
            id="evt_x",
            type="checkout.session.completed",
            data={"id": "cs_x", "customer": "cus_x", "metadata": {}},
        )
        result = handle_event(s, event, _settings())
        # Handled (not retried) but no effect.
        assert result.handled is True
        assert result.note == "missing_metadata"
        s.flush()
        s.refresh(user)
        assert user.plan_tier == "free"


# ---------- customer.subscription.updated -----------------------------------


def test_subscription_updated_changes_plan_via_price_id(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(
            s,
            stripe_customer_id="cus_a",
            plan_tier="pro",
            monthly_query_limit=1000,
        )
        event = StripeEvent(
            id="evt_sub_upd",
            type="customer.subscription.updated",
            data={
                "id": "sub_a",
                "customer": "cus_a",
                "status": "active",
                "current_period_end": 1_900_000_000,
                "items": {
                    "data": [
                        {"price": {"id": "price_team_456"}}
                    ]
                },
            },
        )
        result = handle_event(s, event, _settings())
        assert result.handled is True
        s.flush()
        s.refresh(user)
        assert user.plan_tier == "team"
        assert user.monthly_query_limit == 10000
        assert user.subscription_status == "active"
        assert user.stripe_subscription_id == "sub_a"
        assert user.subscription_current_period_end is not None


def test_subscription_updated_falls_back_to_metadata_when_price_unmapped(
    tmp_path: Path,
) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s, stripe_customer_id="cus_meta", plan_tier="free")
        event = StripeEvent(
            id="evt_sub_meta",
            type="customer.subscription.updated",
            data={
                "id": "sub_meta",
                "customer": "cus_meta",
                "status": "active",
                "metadata": {"target_tier": "pro"},
                "items": {"data": [{"price": {"id": "price_unknown"}}]},
            },
        )
        result = handle_event(s, event, _settings())
        assert result.handled is True
        s.flush()
        s.refresh(user)
        assert user.plan_tier == "pro"


def test_subscription_updated_no_user_for_customer(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        event = StripeEvent(
            id="evt_no_user",
            type="customer.subscription.updated",
            data={"id": "sub_x", "customer": "cus_unknown", "items": {"data": []}},
        )
        result = handle_event(s, event, _settings())
        assert result.handled is True
        assert result.note == "user_missing"


# ---------- customer.subscription.deleted -----------------------------------


def test_subscription_deleted_downgrades_but_preserves_used(
    tmp_path: Path,
) -> None:
    """Cancellation drops the plan but the user keeps any remaining
    queries until the calendar window resets — so we must NOT zero
    ``monthly_queries_used``."""
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(
            s,
            stripe_customer_id="cus_del",
            plan_tier="team",
            monthly_query_limit=10000,
            monthly_queries_used=4242,
        )
        # Pre-set the columns the deletion is supposed to clear.
        user.stripe_subscription_id = "sub_del"
        user.subscription_status = "active"
        s.flush()
        event = StripeEvent(
            id="evt_del",
            type="customer.subscription.deleted",
            data={"id": "sub_del", "customer": "cus_del"},
        )
        result = handle_event(s, event, _settings())
        assert result.handled is True
        s.flush()
        s.refresh(user)
        assert user.plan_tier == "free"
        assert user.monthly_query_limit == 100
        # Critical: the count must NOT be reset.
        assert user.monthly_queries_used == 4242
        assert user.subscription_status == "canceled"
        assert user.stripe_subscription_id is None


# ---------- unknown event type ----------------------------------------------


def test_unknown_event_type_returns_handled_false(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        event = StripeEvent(
            id="evt_unknown",
            type="invoice.paid",
            data={"customer": "cus_x"},
        )
        result = handle_event(s, event, _settings())
        assert result.handled is False
        assert result.event_type == "invoice.paid"


# ---------- idempotency ------------------------------------------------------


def test_idempotency_same_event_id_processed_twice_only_once(
    tmp_path: Path,
) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        event = StripeEvent(
            id="evt_dup",
            type="checkout.session.completed",
            data={
                "id": "cs_dup",
                "customer": "cus_dup",
                "subscription": "sub_dup",
                "metadata": {
                    "advisor_user_id": str(user.id),
                    "target_tier": "pro",
                },
            },
        )
        first = handle_event(s, event, _settings())
        s.flush()
        # Mutate the user record so we can detect re-application.
        user.plan_tier = "free"
        user.monthly_query_limit = 100
        s.flush()

        second = handle_event(s, event, _settings())
        s.flush()
        s.refresh(user)
    assert first.handled is True
    assert second.handled is True
    assert second.note == "duplicate_event"
    # The second run did NOT re-apply the upgrade.
    assert user.plan_tier == "free"
    assert user.monthly_query_limit == 100


def test_idempotency_records_marker_in_usage_event(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        event = StripeEvent(
            id="evt_marker",
            type="checkout.session.completed",
            data={
                "id": "cs_m",
                "customer": "cus_m",
                "metadata": {
                    "advisor_user_id": str(user.id),
                    "target_tier": "pro",
                },
            },
        )
        handle_event(s, event, _settings())
        s.flush()
        markers = [
            ev
            for ev in s.query(UsageEvent).all()
            if ev.event_type == "stripe_webhook"
        ]
        assert len(markers) == 1
        assert markers[0].metadata_json["stripe_event_id"] == "evt_marker"
        assert (
            markers[0].metadata_json["stripe_event_type"]
            == "checkout.session.completed"
        )
