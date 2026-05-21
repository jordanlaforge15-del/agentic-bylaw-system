"""Pack-purchase Stripe webhook: per-credit issuance + idempotency."""
from __future__ import annotations

from pathlib import Path

from advisor.billing.client import StripeEvent
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
from advisor.db.models import CaseCredit, CasePurchase, User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str) -> int:
    with session_scope(db_url) as s:
        user = User(clerk_user_id="u1", email="u1@x.com")
        s.add(user)
        s.flush()
        return user.id


def _checkout_event(*, user_id: int, tier: str, pack_sku: str, quantity: int,
                    amount_cents: int = 0, event_id: str = "evt_1",
                    checkout_session_id: str | None = "cs_test_1") -> StripeEvent:
    return StripeEvent(
        id=event_id,
        type="checkout.session.completed",
        data={
            "id": checkout_session_id,
            "customer": "cus_test_1",
            "payment_intent": "pi_test_1",
            "amount_total": amount_cents,
            "metadata": {
                "advisor_user_id": str(user_id),
                "tier": tier,
                "pack_sku": pack_sku,
                "quantity": str(quantity),
            },
        },
    )


def test_pack_purchase_inserts_one_credit_per_quantity(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings()

    with session_scope(db_url) as s:
        result = handle_event(
            s,
            _checkout_event(
                user_id=user_id,
                tier="standard",
                pack_sku="pro",
                quantity=20,
                amount_cents=55_250,  # $552.50 = pro discount on 20×$32.50
            ),
            settings,
        )
        assert result.handled is True

    with session_scope(db_url) as s:
        purchases = list(s.query(CasePurchase).all())
        assert len(purchases) == 1
        assert purchases[0].pack_sku == "pro"
        assert purchases[0].tier == "standard"
        assert purchases[0].quantity == 20
        assert purchases[0].amount_paid_cents == 55_250

        credits = list(s.query(CaseCredit).all())
        assert len(credits) == 20
        assert all(c.tier == "standard" for c in credits)
        assert all(c.source == "pro" for c in credits)
        assert all(c.state == "available" for c in credits)
        # All linked to one purchase row.
        assert {c.purchase_id for c in credits} == {purchases[0].id}


def test_duplicate_event_id_is_no_op(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings()

    event = _checkout_event(
        user_id=user_id,
        tier="quick",
        pack_sku="payg",
        quantity=1,
        amount_cents=1250,
        event_id="evt_dup",
    )

    with session_scope(db_url) as s:
        handle_event(s, event, settings)

    with session_scope(db_url) as s:
        result = handle_event(s, event, settings)
        assert result.handled is True
        assert result.note == "duplicate_event"

    with session_scope(db_url) as s:
        # Still exactly one credit, not two.
        assert s.query(CaseCredit).count() == 1


def test_duplicate_checkout_session_id_is_caught(tmp_path: Path) -> None:
    """Even if event-level dedupe fails (different event id, same session),
    the unique constraint on stripe_checkout_session_id prevents
    double-issuance."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings()

    first = _checkout_event(
        user_id=user_id,
        tier="quick",
        pack_sku="starter",
        quantity=5,
        event_id="evt_a",
        checkout_session_id="cs_unique",
    )
    duplicate = _checkout_event(
        user_id=user_id,
        tier="quick",
        pack_sku="starter",
        quantity=5,
        event_id="evt_b",  # DIFFERENT event id
        checkout_session_id="cs_unique",  # SAME checkout session
    )

    with session_scope(db_url) as s:
        handle_event(s, first, settings)

    with session_scope(db_url) as s:
        result = handle_event(s, duplicate, settings)
        # Constraint violation surfaces as note='duplicate_purchase'.
        assert result.handled is True
        assert result.note == "duplicate_purchase"

    with session_scope(db_url) as s:
        # Still exactly one purchase + 5 credits, not 10.
        assert s.query(CasePurchase).count() == 1
        assert s.query(CaseCredit).count() == 5


def test_unknown_offer_is_handled_gracefully(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings()

    event = _checkout_event(
        user_id=user_id,
        tier="luxury",  # not a real tier
        pack_sku="payg",
        quantity=1,
    )

    with session_scope(db_url) as s:
        result = handle_event(s, event, settings)
        assert result.handled is True
        assert result.note == "unknown_offer"


def test_missing_metadata_is_handled_gracefully(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    settings = AdvisorBillingSettings()

    event = StripeEvent(
        id="evt_missing",
        type="checkout.session.completed",
        data={"id": "cs_x", "customer": "cus_x", "metadata": {}},
    )
    with session_scope(db_url) as s:
        result = handle_event(s, event, settings)
        assert result.handled is True
        assert result.note == "missing_metadata"


def test_unknown_event_type_is_skipped(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    settings = AdvisorBillingSettings()

    event = StripeEvent(
        id="evt_invoice",
        type="invoice.created",
        data={},
    )
    with session_scope(db_url) as s:
        result = handle_event(s, event, settings)
        assert result.handled is False
