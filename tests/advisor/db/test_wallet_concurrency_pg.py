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
