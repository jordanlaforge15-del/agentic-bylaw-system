"""Token wallet ledger + service (ABS-380).

Specification tests for ``advisor.db.wallet``: the append-only ledger, the
signed running balance, idempotent top-ups, floorless burns, and the
``SUM(amount_tokens) == token_balance`` invariant that ties the two
together.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from advisor.db.models import TokenTransaction, User
from advisor.db.wallet import (
    adjust_tokens,
    burn_tokens,
    credit_topup,
    get_balance,
    grant_signup_tokens_if_needed,
    grant_tokens,
    list_transactions,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)
    return db_url


def _seed_user(db_url: str, *, clerk_user_id: str = "w1", balance: int = 0) -> int:
    with session_scope(db_url) as s:
        user = User(
            clerk_user_id=clerk_user_id,
            email=f"{clerk_user_id}@x.com",
            token_balance=balance,
        )
        s.add(user)
        s.flush()
        return user.id


def _ledger_sum(s, user_id: int) -> int:
    return int(
        s.execute(
            select(func.coalesce(func.sum(TokenTransaction.amount_tokens), 0)).where(
                TokenTransaction.user_id == user_id
            )
        ).scalar_one()
    )


# ---------- grant ---------------------------------------------------------


def test_grant_tokens_sets_balance_and_writes_one_grant_row(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        txn = grant_tokens(s, user=user, amount=25_000, reason="signup_grant")
        assert txn.entry_type == "grant"
        assert txn.amount_tokens == 25_000
        assert txn.balance_after == 25_000

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert user.token_balance == 25_000
        rows = list_transactions(s, user_id=uid, limit=100)
        assert len(rows) == 1
        assert rows[0].entry_type == "grant"
        assert rows[0].balance_after == 25_000


def test_grant_amount_must_be_positive(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        with pytest.raises(ValueError):
            grant_tokens(s, user=user, amount=0)


# ---------- invariant: SUM(amount_tokens) == token_balance ----------------


def test_ledger_sum_equals_balance_under_interleaving(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        grant_tokens(s, user=user, amount=25_000, reason="signup_grant")
        credit_topup(
            s, user=user, amount=40_000, stripe_checkout_session_id="cs_a"
        )
        burn_tokens(s, user=user, amount=3_200)
        adjust_tokens(s, user=user, amount=-1_500, reason="support_correction")
        burn_tokens(s, user=user, amount=9_999)
        adjust_tokens(s, user=user, amount=250, reason="goodwill")
        s.flush()
        user = s.get(User, uid)
        assert _ledger_sum(s, uid) == user.token_balance
        # sanity: 25000 + 40000 - 3200 - 1500 - 9999 + 250
        assert user.token_balance == 50_551


# ---------- top-up idempotency -------------------------------------------


def test_duplicate_topup_credits_once(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        first = credit_topup(
            s, user=user, amount=40_000, stripe_checkout_session_id="cs_dup"
        )
        assert first is not None
        second = credit_topup(
            s, user=user, amount=40_000, stripe_checkout_session_id="cs_dup"
        )
        # The duplicate is absorbed — it neither raises nor double-credits.
        s.flush()

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert user.token_balance == 40_000
        rows = list_transactions(s, user_id=uid, limit=100)
        topups = [r for r in rows if r.entry_type == "topup"]
        assert len(topups) == 1
        assert _ledger_sum(s, uid) == user.token_balance


def test_topup_after_duplicate_can_still_credit_a_new_session(tmp_path: Path) -> None:
    # A duplicate must leave the session usable for the next real top-up.
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        credit_topup(s, user=user, amount=10_000, stripe_checkout_session_id="cs_1")
        credit_topup(s, user=user, amount=10_000, stripe_checkout_session_id="cs_1")
        credit_topup(s, user=user, amount=5_000, stripe_checkout_session_id="cs_2")
        s.flush()
        user = s.get(User, uid)
        assert user.token_balance == 15_000
        assert _ledger_sum(s, uid) == 15_000


# ---------- floorless burn / overdraw -------------------------------------


def test_burn_overdraws_below_zero_without_raising(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url, balance=100)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        txn = burn_tokens(s, user=user, amount=350)
        assert txn.amount_tokens == -350
        assert txn.balance_after == -250

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert user.token_balance == -250


# ---------- read helpers --------------------------------------------------


def test_get_balance_reads_current_balance(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url, balance=7_000)
    with session_scope(db_url) as s:
        assert get_balance(s, user_id=uid) == 7_000
    with session_scope(db_url) as s:
        assert get_balance(s, user_id=999_999) == 0


def test_list_transactions_is_newest_first_and_cursor_paged(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        for i in range(5):
            grant_tokens(s, user=user, amount=1_000, reason=f"g{i}")
        s.flush()

    with session_scope(db_url) as s:
        page1 = list_transactions(s, user_id=uid, limit=2)
        assert [r.reason for r in page1] == ["g4", "g3"]
        page2 = list_transactions(
            s, user_id=uid, limit=2, before_id=page1[-1].id
        )
        assert [r.reason for r in page2] == ["g2", "g1"]


def test_list_transactions_is_ownership_scoped(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid_a = _seed_user(db_url, clerk_user_id="a")
    uid_b = _seed_user(db_url, clerk_user_id="b")
    with session_scope(db_url) as s:
        grant_tokens(s, user=s.get(User, uid_a), amount=1_000)
        grant_tokens(s, user=s.get(User, uid_b), amount=2_000)
        s.flush()
    with session_scope(db_url) as s:
        rows = list_transactions(s, user_id=uid_a, limit=100)
        assert all(r.user_id == uid_a for r in rows)
        assert len(rows) == 1


# ---------- signup grant self-heal ---------------------------------------


def test_signup_grant_is_idempotent(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert grant_signup_tokens_if_needed(s, user=user) is True
        assert user.metadata_json.get("token_grant_issued") is True
        assert user.token_balance == 25_000

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        # Second call is a no-op — no second grant even after burning to 0.
        burn_tokens(s, user=user, amount=25_000)
        assert grant_signup_tokens_if_needed(s, user=user) is False

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert user.token_balance == 0
        grants = [
            r
            for r in list_transactions(s, user_id=uid, limit=100)
            if r.entry_type == "grant"
        ]
        assert len(grants) == 1


def test_signup_grant_amount_follows_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_SIGNUP_TOKEN_GRANT", "12345")
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        grant_signup_tokens_if_needed(s, user=user)
        assert user.token_balance == 12_345
