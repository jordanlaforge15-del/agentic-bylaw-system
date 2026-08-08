"""Token top-up Stripe webhook: wallet crediting + idempotency (ABS-381).

The webhook credits the prepaid wallet from a ``checkout.session.completed``
event carrying ``metadata.topup_sku``. Token quantities are resolved from the
server-side catalog — never from metadata — and the top-up branch runs BEFORE
the case-credit pack branch.
"""
from __future__ import annotations

from pathlib import Path

from advisor.billing.client import StripeEvent
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
from advisor.db.models import CasePurchase, TokenTransaction, User
from advisor.db.wallet import get_balance
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str) -> int:
    create_all(db_url)
    with session_scope(db_url) as s:
        user = User(clerk_user_id="u1", email="u1@x.com")
        s.add(user)
        s.flush()
        return user.id


def _topup_event(
    *,
    user_id: int | None,
    topup_sku: str | None,
    event_id: str = "evt_1",
    checkout_session_id: str | None = "cs_topup_1",
    price_id: str | None = None,
    tampered_tokens: int | None = None,
) -> StripeEvent:
    metadata: dict[str, str] = {}
    if user_id is not None:
        metadata["advisor_user_id"] = str(user_id)
    if topup_sku is not None:
        metadata["topup_sku"] = topup_sku
    if tampered_tokens is not None:
        # A malicious/legacy metadata token amount that must be ignored.
        metadata["tokens"] = str(tampered_tokens)
    data: dict = {
        "id": checkout_session_id,
        "customer": "cus_topup_1",
        "payment_intent": "pi_topup_1",
        "metadata": metadata,
    }
    if price_id is not None:
        data["line_items"] = {"data": [{"price": {"id": price_id}}]}
    return StripeEvent(
        id=event_id, type="checkout.session.completed", data=data
    )


def test_topup_credits_wallet_from_server_catalog(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings()

    with session_scope(db_url) as s:
        result = handle_event(
            s,
            _topup_event(
                user_id=user_id, topup_sku="medium", tampered_tokens=9_999_999
            ),
            settings,
        )
        assert result.handled is True
        assert result.note == "topup_credited"

    with session_scope(db_url) as s:
        # Server catalog says medium = 5,250,000 tokens — the tampered metadata
        # amount is ignored.
        assert get_balance(s, user_id=user_id) == 5_250_000
        txns = list(s.query(TokenTransaction).all())
        assert len(txns) == 1
        assert txns[0].entry_type == "topup"
        assert txns[0].amount_tokens == 5_250_000
        assert txns[0].stripe_checkout_session_id == "cs_topup_1"
        # No case-credit pack rows: the top-up branch ran first.
        assert s.query(CasePurchase).count() == 0


def test_topup_price_id_reverse_lookup_without_metadata(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings(STRIPE_PRICE_TOPUP_LARGE="price_large_x")

    with session_scope(db_url) as s:
        result = handle_event(
            s,
            _topup_event(
                user_id=user_id, topup_sku=None, price_id="price_large_x"
            ),
            settings,
        )
        assert result.handled is True
        assert result.note == "topup_credited"

    with session_scope(db_url) as s:
        assert get_balance(s, user_id=user_id) == 14_000_000


def test_same_event_redelivered_is_no_op(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings()
    event = _topup_event(user_id=user_id, topup_sku="small")

    with session_scope(db_url) as s:
        handle_event(s, event, settings)
    with session_scope(db_url) as s:
        result = handle_event(s, event, settings)
        assert result.handled is True
        assert result.note == "duplicate_event"

    with session_scope(db_url) as s:
        assert get_balance(s, user_id=user_id) == 1_400_000
        assert s.query(TokenTransaction).count() == 1


def test_different_event_same_session_is_no_op(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings()

    with session_scope(db_url) as s:
        handle_event(
            s,
            _topup_event(
                user_id=user_id, topup_sku="small", event_id="evt_a"
            ),
            settings,
        )
    with session_scope(db_url) as s:
        # Different event id, SAME checkout session id.
        result = handle_event(
            s,
            _topup_event(
                user_id=user_id, topup_sku="small", event_id="evt_b"
            ),
            settings,
        )
        assert result.handled is True
        assert result.note == "duplicate_topup"

    with session_scope(db_url) as s:
        assert get_balance(s, user_id=user_id) == 1_400_000
        assert s.query(TokenTransaction).count() == 1


def test_unknown_topup_sku_is_handled_no_5xx(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    user_id = _seed_user(db_url)
    settings = AdvisorBillingSettings()

    with session_scope(db_url) as s:
        result = handle_event(
            s,
            _topup_event(user_id=user_id, topup_sku="jumbo"),
            settings,
        )
        assert result.handled is True
        assert result.note == "unknown_topup"

    with session_scope(db_url) as s:
        assert get_balance(s, user_id=user_id) == 0
        assert s.query(TokenTransaction).count() == 0


def test_missing_user_id_is_handled(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _seed_user(db_url)
    settings = AdvisorBillingSettings()

    with session_scope(db_url) as s:
        result = handle_event(
            s,
            _topup_event(user_id=None, topup_sku="small"),
            settings,
        )
        assert result.handled is True
        assert result.note == "missing_metadata"
