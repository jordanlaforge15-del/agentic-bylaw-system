"""Token wallet ledger + service (ABS-380).

Specification tests for ``advisor.db.wallet``: the append-only ledger, the
signed running balance, idempotent top-ups, floorless burns, and the
``SUM(amount_tokens) == token_balance`` invariant that ties the two
together.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from advisor.billing.turns import signup_token_grant
from advisor.db.models import TokenTransaction, User
from advisor.db.wallet import (
    REASON_BETA_REFILL,
    REASON_SIGNUP_GRANT,
    REFILL_AVAILABLE,
    REFILL_COOLDOWN,
    REFILL_DISABLED,
    REFILL_EXHAUSTED,
    adjust_tokens,
    beta_refill_state,
    burn_tokens,
    claim_beta_refill,
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
        # Read the configured grant rather than a literal so a re-calibration
        # of the wallet parameters (ABS-416) doesn't red-herring this test.
        assert user.token_balance == signup_token_grant()

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        # Second call is a no-op — no second grant even after burning to 0.
        burn_tokens(s, user=user, amount=signup_token_grant())
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


# ---------- schema-level one-signup-grant-per-user (ABS-415) -------------


def test_second_signup_grant_row_is_rejected_by_the_schema(
    tmp_path: Path,
) -> None:
    """The DB, not the caller, is what makes the signup grant one-time.

    ABS-404 serialised the service's check-and-set on the user row, but
    that only binds callers that take the lock. This asserts the rule is
    in the schema: a second ``grant``/``signup_grant`` row for the same
    user is refused outright, however it is written.
    """
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        grant_tokens(s, user=user, amount=25_000, reason=REASON_SIGNUP_GRANT)

    with pytest.raises(IntegrityError):
        with session_scope(db_url) as s:
            user = s.get(User, uid)
            # Bypasses grant_signup_tokens_if_needed entirely — exactly the
            # shape of the production double-grant.
            grant_tokens(
                s, user=user, amount=25_000, reason=REASON_SIGNUP_GRANT
            )

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert user.token_balance == 25_000
        assert _ledger_sum(s, uid) == 25_000


def test_admin_gifts_are_still_repeatable(tmp_path: Path) -> None:
    """The index is scoped to the signup reason, not to ``grant`` rows.

    Admin gifts / goodwill top-ups are also ``grant`` entries and must
    stay repeatable — constraining them would be a regression, so pin it.
    """
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        grant_tokens(s, user=user, amount=25_000, reason=REASON_SIGNUP_GRANT)
        grant_tokens(s, user=user, amount=5_000, reason="admin_gift")
        grant_tokens(s, user=user, amount=5_000, reason="admin_gift")

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert user.token_balance == 35_000
        assert _ledger_sum(s, uid) == 35_000


def test_signup_grant_absorbs_a_constraint_violation_it_did_not_see(
    tmp_path: Path,
) -> None:
    """A racer that loses to the index gets ``False``, not a 500.

    ``grant_signup_tokens_if_needed`` runs on every authenticated request,
    so the schema backstop must degrade into "already granted" rather than
    surfacing an ``IntegrityError`` to an ordinary page load. Simulated by
    planting a grant row without the metadata flag the service checks —
    the same state a losing racer's locked read would hand back.
    """
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, uid)
        grant_tokens(s, user=user, amount=25_000, reason=REASON_SIGNUP_GRANT)
        assert user.metadata_json.get("token_grant_issued") is None

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        assert grant_signup_tokens_if_needed(s, user=user) is False
        # Flag is set on the way out so subsequent requests short-circuit
        # at the check instead of re-tripping the index every time.
        assert user.metadata_json.get("token_grant_issued") is True

    with session_scope(db_url) as s:
        user = s.get(User, uid)
        # The rolled-back savepoint took the balance move with it.
        assert user.token_balance == 25_000
        assert _ledger_sum(s, uid) == 25_000
        grants = [
            r
            for r in list_transactions(s, user_id=uid, limit=100)
            if r.entry_type == "grant"
        ]
        assert len(grants) == 1


# ---------- beta refill (ABS-405) -----------------------------------------
#
# The self-serve way out of an overdrawn wallet while payments are off.
# Policy lives entirely in the ledger: claims are ``grant`` rows stamped
# ``beta_refill``, so the cooldown and the lifetime cap are counted from the
# audit trail rather than a column that could drift away from it.


@pytest.fixture()
def refill_env(monkeypatch) -> None:
    """A small, predictable refill policy: 1,000 tokens, 3 claims, 6h apart."""
    monkeypatch.setenv("ADVISOR_BETA_REFILL_ENABLED", "true")
    monkeypatch.setenv("ADVISOR_BETA_REFILL_TOKENS", "1000")
    monkeypatch.setenv("ADVISOR_BETA_REFILL_MAX_GRANTS", "3")
    monkeypatch.setenv("ADVISOR_BETA_REFILL_COOLDOWN_HOURS", "6")


def test_refill_is_available_to_a_user_who_has_never_claimed(
    tmp_path: Path, refill_env
) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        state = beta_refill_state(s, user_id=uid)
    assert state.status == REFILL_AVAILABLE
    assert state.tokens == 1_000
    assert state.grants_used == 0
    assert state.grants_remaining == 3
    assert state.next_available_at is None


def test_claim_credits_the_wallet_and_writes_a_beta_refill_grant(
    tmp_path: Path, refill_env
) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        # Overdraw the wallet for real — a floorless burn, the case this
        # whole feature exists for.
        burn_tokens(s, user=s.get(User, uid), amount=500)
        s.commit()

    with session_scope(db_url) as s:
        state = claim_beta_refill(s, user=s.get(User, uid))
        s.commit()
    assert state.status == "granted"
    assert state.tokens_granted == 1_000
    assert state.grants_remaining == 2

    with session_scope(db_url) as s:
        # Out of overdraft: -500 + 1,000. The ledger still sums to the balance.
        assert get_balance(s, user_id=uid) == 500
        assert _ledger_sum(s, uid) == 500
        rows = list_transactions(s, user_id=uid, limit=100)
        refills = [r for r in rows if r.reason == REASON_BETA_REFILL]
        assert len(refills) == 1
        assert refills[0].entry_type == "grant"
        assert refills[0].amount_tokens == 1_000


def test_second_claim_inside_the_cooldown_is_refused_with_an_unlock_time(
    tmp_path: Path, refill_env
) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        claim_beta_refill(s, user=s.get(User, uid))
        s.commit()

    with session_scope(db_url) as s:
        state = claim_beta_refill(s, user=s.get(User, uid))
        s.commit()
    assert state.status == REFILL_COOLDOWN
    assert state.tokens_granted == 0
    assert state.next_available_at is not None
    # The unlock instant is one cooldown out from the claim we just made.
    assert state.next_available_at > datetime.now(timezone.utc)
    assert state.next_available_at <= datetime.now(timezone.utc) + timedelta(
        hours=6
    )
    with session_scope(db_url) as s:
        assert get_balance(s, user_id=uid) == 1_000  # not double-credited


def test_claim_succeeds_once_the_cooldown_has_elapsed(
    tmp_path: Path, refill_env
) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        claim_beta_refill(s, user=s.get(User, uid))
        s.commit()

    # Seven hours later — past the 6h cooldown.
    later = datetime.now(timezone.utc) + timedelta(hours=7)
    with session_scope(db_url) as s:
        state = claim_beta_refill(s, user=s.get(User, uid), now=later)
        s.commit()
    assert state.status == "granted"
    with session_scope(db_url) as s:
        assert get_balance(s, user_id=uid) == 2_000


def test_lifetime_cap_exhausts_the_refill(tmp_path: Path, refill_env) -> None:
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    base = datetime.now(timezone.utc)
    for i in range(3):
        with session_scope(db_url) as s:
            state = claim_beta_refill(
                s, user=s.get(User, uid), now=base + timedelta(hours=7 * i)
            )
            s.commit()
        assert state.status == "granted", f"claim {i} refused"

    # Cooldown long past, but the cap is spent — permanently.
    with session_scope(db_url) as s:
        state = claim_beta_refill(
            s, user=s.get(User, uid), now=base + timedelta(days=30)
        )
        s.commit()
    assert state.status == REFILL_EXHAUSTED
    assert state.grants_remaining == 0
    with session_scope(db_url) as s:
        assert get_balance(s, user_id=uid) == 3_000


def test_disabled_flag_refuses_the_claim(tmp_path: Path, refill_env, monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_BETA_REFILL_ENABLED", "false")
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        state = claim_beta_refill(s, user=s.get(User, uid))
        s.commit()
    assert state.status == REFILL_DISABLED
    with session_scope(db_url) as s:
        assert get_balance(s, user_id=uid) == 0
        assert list_transactions(s, user_id=uid) == []


def test_zero_max_grants_disables_the_refill(
    tmp_path: Path, refill_env, monkeypatch
) -> None:
    monkeypatch.setenv("ADVISOR_BETA_REFILL_MAX_GRANTS", "0")
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        assert beta_refill_state(s, user_id=uid).status == REFILL_DISABLED


def test_zero_cooldown_allows_back_to_back_claims_up_to_the_cap(
    tmp_path: Path, refill_env, monkeypatch
) -> None:
    monkeypatch.setenv("ADVISOR_BETA_REFILL_COOLDOWN_HOURS", "0")
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    for _ in range(3):
        with session_scope(db_url) as s:
            assert claim_beta_refill(s, user=s.get(User, uid)).status == "granted"
            s.commit()
    with session_scope(db_url) as s:
        assert claim_beta_refill(s, user=s.get(User, uid)).status == REFILL_EXHAUSTED
        s.commit()


def test_signup_grant_does_not_count_against_the_refill_cap(
    tmp_path: Path, refill_env
) -> None:
    """The cap counts ``beta_refill`` rows only — not every ``grant`` row.

    Otherwise the signup grant (and any admin gift, also a ``grant``) would
    silently eat a refill and a brand-new user would arrive with fewer
    claims than the policy says.
    """
    db_url = _db_url(tmp_path)
    uid = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_tokens(s, user=s.get(User, uid), amount=9_000, reason=REASON_SIGNUP_GRANT)
        grant_tokens(s, user=s.get(User, uid), amount=1_000, reason="admin_gift")
        s.commit()
    with session_scope(db_url) as s:
        state = beta_refill_state(s, user_id=uid)
    assert state.status == REFILL_AVAILABLE
    assert state.grants_remaining == 3
