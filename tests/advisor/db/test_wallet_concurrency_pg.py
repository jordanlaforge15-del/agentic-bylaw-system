"""Concurrent burns serialise under Postgres ``SELECT … FOR UPDATE`` (ABS-380).

Postgres-only: sqlite has no real row locking, so ``with_for_update()`` is a
no-op there and this race would be masked. Gated on a Postgres DATABASE_URL;
skipped otherwise. Run with the dev/e2e Postgres stack up.
"""
from __future__ import annotations

import threading
import uuid

import pytest

from advisor.db.models import TokenTransaction, User
from advisor.db.wallet import burn_tokens
from layer1.config import get_settings
from layer1.db.session import session_scope

_DB_URL = get_settings().database_url
pg_only = pytest.mark.skipif(
    not _DB_URL.startswith("postgresql"),
    reason="row-locking is Postgres-specific; run with the dev/e2e Postgres stack up.",
)


@pg_only
def test_ten_concurrent_burns_serialise() -> None:
    suffix = uuid.uuid4().hex[:12]
    with session_scope(_DB_URL) as db:
        user = User(
            clerk_user_id=f"abs380-burn-{suffix}",
            email=f"abs380-burn-{suffix}@test.local",
            token_balance=100,
        )
        db.add(user)
        db.flush()
        uid = user.id

    start = threading.Barrier(10)

    def _burn() -> None:
        start.wait()
        with session_scope(_DB_URL) as db:
            user = db.get(User, uid)
            burn_tokens(db, user=user, amount=10)

    threads = [threading.Thread(target=_burn) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with session_scope(_DB_URL) as db:
        user = db.get(User, uid)
        assert user.token_balance == 0  # start 100 - 10*10
        rows = (
            db.query(TokenTransaction)
            .filter(TokenTransaction.user_id == uid)
            .all()
        )
        assert len(rows) == 10
        # Every burn recorded a distinct, monotonically-decreasing snapshot.
        balances_after = sorted(r.balance_after for r in rows)
        assert balances_after == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]


@pg_only
def test_concurrent_signup_grants_issue_exactly_once() -> None:
    """The signup grant must fire once even when several authenticated
    requests for the same brand-new user land simultaneously (ABS-404).

    ``grant_signup_tokens_if_needed`` runs on EVERY authenticated
    request, and a fresh sign-in fires a handful of them in parallel.
    When its check-and-set was a plain in-memory read of
    ``metadata_json``, every racing request saw the flag unset and wrote
    its own grant row. Production did exactly that on 2026-07-17: two
    ``+25,000`` entries for one user, same timestamp to the second — the
    user silently received double the free trial, and the ledger's
    "grant issued once" invariant was false.

    Ten threads, one barrier, one user: exactly one grant row, and a
    balance of exactly one grant.
    """
    from advisor.billing.turns import signup_token_grant
    from advisor.db.wallet import grant_signup_tokens_if_needed

    suffix = uuid.uuid4().hex[:12]
    with session_scope(_DB_URL) as db:
        user = User(
            clerk_user_id=f"abs404-grant-{suffix}",
            email=f"abs404-grant-{suffix}@test.local",
            token_balance=0,
        )
        db.add(user)
        db.flush()
        uid = user.id

    grant = signup_token_grant()
    start = threading.Barrier(10)

    def _grant() -> None:
        with session_scope(_DB_URL) as db:
            # Load the user BEFORE the barrier so every thread enters the
            # helper holding a pre-grant snapshot. Reading after the
            # barrier makes the test depend on thread scheduling: whoever
            # commits first leaves the others reading post-grant state,
            # and the race quietly fails to reproduce. Pinning the read
            # here is what the losing racer's session actually looks like
            # in production, where the auth dependency resolved the user
            # at the top of the request.
            user = db.get(User, uid)
            assert user.metadata_json.get("token_grant_issued") is None
            start.wait()
            grant_signup_tokens_if_needed(db, user=user)

    threads = [threading.Thread(target=_grant) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with session_scope(_DB_URL) as db:
        user = db.get(User, uid)
        rows = (
            db.query(TokenTransaction)
            .filter(
                TokenTransaction.user_id == uid,
                TokenTransaction.entry_type == "grant",
            )
            .all()
        )
        assert len(rows) == 1, (
            f"signup grant fired {len(rows)} times under concurrency; "
            "the check-and-set is not serialised on the user row"
        )
        assert user.token_balance == grant
        assert user.metadata_json.get("token_grant_issued") is True


@pg_only
def test_schema_refuses_a_second_signup_grant_without_the_app_lock() -> None:
    """The one-grant rule holds even with the service's lock out of the
    picture (ABS-415).

    ABS-404's ``SELECT … FOR UPDATE`` fixed the racing *caller*; this pins
    the backstop underneath it. Ten threads write the signup grant
    directly through ``grant_tokens`` — no flag check, no user-row lock,
    exactly the shape of the production double-grant — and the partial
    unique index ``uq_advisor_token_transaction_signup_grant`` is the only
    thing standing between them and a doubled wallet. Exactly one commits;
    the rest die on an IntegrityError with their balance move rolled back
    alongside their ledger row.
    """
    from sqlalchemy.exc import IntegrityError

    from advisor.db.wallet import REASON_SIGNUP_GRANT, grant_tokens

    suffix = uuid.uuid4().hex[:12]
    with session_scope(_DB_URL) as db:
        user = User(
            clerk_user_id=f"abs415-schema-{suffix}",
            email=f"abs415-schema-{suffix}@test.local",
            token_balance=0,
        )
        db.add(user)
        db.flush()
        uid = user.id

    start = threading.Barrier(10)
    outcomes: list[bool] = []
    lock = threading.Lock()

    def _grant() -> None:
        start.wait()
        try:
            with session_scope(_DB_URL) as db:
                user = db.get(User, uid)
                grant_tokens(
                    db, user=user, amount=25_000, reason=REASON_SIGNUP_GRANT
                )
            ok = True
        except IntegrityError:
            ok = False
        with lock:
            outcomes.append(ok)

    threads = [threading.Thread(target=_grant) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(outcomes) == 1, (
        f"{sum(outcomes)} of 10 concurrent signup grants committed; "
        "the partial unique index is not enforcing one-per-user"
    )
    with session_scope(_DB_URL) as db:
        user = db.get(User, uid)
        rows = (
            db.query(TokenTransaction)
            .filter(
                TokenTransaction.user_id == uid,
                TokenTransaction.entry_type == "grant",
            )
            .all()
        )
        assert len(rows) == 1
        # The balance moved exactly once: a rejected insert must not leave
        # its half of the read-modify-write behind.
        assert user.token_balance == 25_000


@pg_only
def test_concurrent_beta_refill_claims_grant_exactly_one(monkeypatch) -> None:
    """A double-tapped "Add more turns" button claims once, not twice
    (ABS-405).

    The refill's cooldown and lifetime cap are counted from the ledger, so
    a check-then-grant that isn't serialised is the classic race: two
    requests both count zero prior claims, both grant, and the cap the
    whole feature's cost control rests on is worth nothing. This is the
    exact shape that shipped two signup grants to production on 2026-07-17
    (ABS-404).

    ``claim_beta_refill`` re-evaluates the policy under the user row's
    write lock, so the losers block until the winner commits, then see its
    ledger row and correctly report cooldown. Ten threads, one grant.
    """
    from advisor.db.wallet import REASON_BETA_REFILL, claim_beta_refill

    monkeypatch.setenv("ADVISOR_BETA_REFILL_ENABLED", "true")
    monkeypatch.setenv("ADVISOR_BETA_REFILL_TOKENS", "1000")
    monkeypatch.setenv("ADVISOR_BETA_REFILL_MAX_GRANTS", "3")
    monkeypatch.setenv("ADVISOR_BETA_REFILL_COOLDOWN_HOURS", "6")

    suffix = uuid.uuid4().hex[:12]
    with session_scope(_DB_URL) as db:
        user = User(
            clerk_user_id=f"abs405-refill-{suffix}",
            email=f"abs405-refill-{suffix}@test.local",
            token_balance=0,
        )
        db.add(user)
        db.flush()
        uid = user.id

    start = threading.Barrier(10)
    statuses: list[str] = []
    lock = threading.Lock()

    def _claim() -> None:
        start.wait()
        with session_scope(_DB_URL) as db:
            state = claim_beta_refill(db, user=db.get(User, uid))
            db.commit()
        with lock:
            statuses.append(state.status)

    threads = [threading.Thread(target=_claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert statuses.count("granted") == 1, (
        f"{statuses.count('granted')} of 10 concurrent claims granted; "
        "the refill check-and-grant is not serialised on the user row"
    )
    with session_scope(_DB_URL) as db:
        user = db.get(User, uid)
        rows = (
            db.query(TokenTransaction)
            .filter(
                TokenTransaction.user_id == uid,
                TokenTransaction.reason == REASON_BETA_REFILL,
            )
            .all()
        )
        assert len(rows) == 1
        assert user.token_balance == 1_000
