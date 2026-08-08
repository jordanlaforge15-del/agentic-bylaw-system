"""ABS-338 (behavioural proof, real Postgres): a slow answer turn must not be
killed by ``idle_in_transaction_session_timeout``.

The production 500 (2026-06-25) was a *behaviour* of the real backend, not of
sqlite: the request transaction sat idle for the whole ~84s LLM turn and
Postgres terminated the connection at its 60s cap, so the settling ``UPDATE``
raised ``psycopg.errors.IdleInTransactionSessionTimeout``. This test reproduces
that mechanism in miniature — a 1-second idle cap pinned on the request
connection and a turn deliberately slower than it — and asserts the answer
still settles ``captured``.

Under the pre-ABS-338 code (one transaction spanning the turn, whether or not
ABS-339's interim ``SET LOCAL`` opt-out is present) this run dies mid-turn.
Under the phased flow no transaction is open while the turn runs, so there is
nothing for the cap to kill.

Postgres-specific (sqlite has no such GUC) and skipped unless a Postgres
``DATABASE_URL`` is actually reachable — run it with the dev/e2e stack up.
The deterministic, backend-agnostic invariant is in
``test_answer_no_open_txn_across_llm.py``.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from advisor.billing import answers as answer_flow
from advisor.db.models import QuestionPurchase, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.config import get_settings
from layer1.db.session import session_scope

PERSONA = "You are a test bylaw advisor."

# The cap pinned on the request connection, and a turn slower than it. Keep
# both small: the point is the ORDER of magnitude (turn > cap), not the wait.
IDLE_CAP_MS = 1_000
TURN_DELAY_S = 2.5

_DB_URL = get_settings().database_url


def _postgres_reachable() -> bool:
    """True only when a Postgres ``DATABASE_URL`` is configured AND up.

    The URL scheme alone isn't enough: a worktree's ``.env`` pins a Postgres
    DSN whether or not that stack is running, and this test must skip (not
    error) when it isn't.
    """
    if not _DB_URL.startswith("postgresql"):
        return False
    try:
        engine = create_engine(_DB_URL, poolclass=NullPool)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:  # noqa: BLE001 — any connection failure means "skip"
        return False
    return True


pg_only = pytest.mark.skipif(
    not _postgres_reachable(),
    reason=(
        "ABS-338's idle-cap proof needs a reachable Postgres; run with the "
        "dev/e2e stack up."
    ),
)


class _StubRetrieval:
    """Grounds the answer with a successful (non-error) search."""

    def search(self, request):  # noqa: ANN001
        return RetrievalResponse(total_matches=1, matches=[], notes=[])


class _SlowGateway:
    """Wraps the ``MockGateway`` and makes the turn's first LLM call take
    longer than the idle cap pinned on the request connection."""

    name = "mock"

    def __init__(self, *, delay_s: float) -> None:
        self._inner = MockGateway(callable_=build_dispatcher())
        self._delay_s = delay_s
        self._slept = False

    async def _maybe_sleep(self) -> None:
        if not self._slept:
            self._slept = True
            await asyncio.sleep(self._delay_s)

    async def complete(self, request):  # noqa: ANN001
        await self._maybe_sleep()
        return await self._inner.complete(request)

    async def stream(self, request):  # noqa: ANN001
        await self._maybe_sleep()
        async for event in self._inner.stream(request):
            yield event


@pg_only
async def test_slow_answer_turn_survives_the_idle_in_txn_cap() -> None:
    suffix = uuid.uuid4().hex[:12]
    # Seed an authorized free-question purchase in the real DB.
    with session_scope(_DB_URL) as db:
        user = User(
            clerk_user_id=f"abs338-{suffix}",
            email=f"abs338-{suffix}@test.local",
            free_questions_remaining=1,
        )
        db.add(user)
        db.flush()
        purchase = answer_flow.start_question_free(
            db,
            user,
            question_slug="permitted_use",
            inputs={
                "address": "6184 Quinpool Road",
                "proposed_use": "law office",
            },
        )
        pid, uid = purchase.id, user.id

    # A dedicated NullPool engine so the aggressive per-session cap below can
    # never ride a pooled connection into another test.
    engine = create_engine(_DB_URL, poolclass=NullPool)
    try:
        with engine.connect() as _probe:  # noqa: F841 — fail fast if the DB died
            pass
        from sqlalchemy.orm import Session  # noqa: PLC0415

        db = Session(bind=engine, expire_on_commit=False, future=True)
        try:
            db.execute(
                text(
                    "SET SESSION idle_in_transaction_session_timeout = "
                    f"{IDLE_CAP_MS}"
                )
            )
            purchase = db.get(QuestionPurchase, pid)
            assert db.in_transaction()  # the txn the fix has to release
            purchase = await answer_flow.run_answer(
                db,
                purchase,
                gateway=_SlowGateway(delay_s=TURN_DELAY_S),
                persona=PERSONA,
                retrieval_factory=_StubRetrieval(),
                client=None,
            )
            # The turn outran the cap by 2.5x and the answer still settled.
            assert purchase.status == "captured"
            assert purchase.answer_text
            db.commit()
        finally:
            db.close()
    finally:
        engine.dispose()
        # Keep the shared dev/e2e DB clean.
        with session_scope(_DB_URL) as db:
            p = db.get(QuestionPurchase, pid)
            if p is not None:
                db.delete(p)
            u = db.get(User, uid)
            if u is not None:
                db.delete(u)
