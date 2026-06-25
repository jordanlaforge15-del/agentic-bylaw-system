"""ABS (Option C, interim): the buy-an-answer request must opt its own
transaction out of Postgres' idle_in_transaction_session_timeout so a long LLM
turn cannot be killed mid-flight. Durable fix tracked in ABS-338; remove this
opt-out (and this test) when ABS-338 lands.

Postgres-specific (sqlite has no such GUC): gated on a Postgres DATABASE_URL,
skips otherwise. Run with the dev/e2e Postgres stack up.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from advisor.billing import answers as answer_flow
from advisor.db.models import QuestionPurchase, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.config import get_settings
from layer1.db.session import session_scope

PERSONA = "You are a test bylaw advisor."

_DB_URL = get_settings().database_url
pg_only = pytest.mark.skipif(
    not _DB_URL.startswith("postgresql"),
    reason="Option C opt-out is Postgres-specific; run with the dev/e2e Postgres stack up.",
)


class _IdleTimeoutProbeRetrieval:
    """Grounds the answer AND, mid-turn, reads the effective
    idle_in_transaction_session_timeout on the *request* connection."""

    def __init__(self, request_session) -> None:  # noqa: ANN001
        self._db = request_session
        self.idle_timeout_in_txn: str | None = None

    def search(self, request):  # noqa: ANN001
        if self.idle_timeout_in_txn is None:
            self.idle_timeout_in_txn = self._db.execute(
                text("SELECT current_setting('idle_in_transaction_session_timeout')")
            ).scalar()
        return RetrievalResponse(total_matches=1, matches=[], notes=[])


@pg_only
async def test_answer_request_txn_opts_out_of_idle_timeout() -> None:
    suffix = uuid.uuid4().hex[:12]
    # Seed an authorized free-question purchase in the real DB.
    with session_scope(_DB_URL) as db:
        user = User(
            clerk_user_id=f"abs-optc-{suffix}",
            email=f"abs-optc-{suffix}@test.local",
            free_questions_remaining=1,
        )
        db.add(user)
        db.flush()
        purchase = answer_flow.start_question_free(
            db, user, question_slug="permitted_use",
            inputs={"address": "6184 Quinpool Road", "proposed_use": "law office"},
        )
        pid, uid = purchase.id, user.id

    try:
        with session_scope(_DB_URL) as db:
            purchase = db.get(QuestionPurchase, pid)
            probe = _IdleTimeoutProbeRetrieval(db)
            purchase = await answer_flow.run_answer(
                db, purchase,
                gateway=MockGateway(callable_=build_dispatcher()),
                persona=PERSONA,
                retrieval_factory=probe,
                client=None,
            )
            assert purchase.status == "captured"
            # The opt-out is active on THIS request's transaction.
            assert probe.idle_timeout_in_txn == "0"
    finally:
        # Keep the shared dev/e2e DB clean.
        with session_scope(_DB_URL) as db:
            p = db.get(QuestionPurchase, pid)
            if p is not None:
                db.delete(p)
            u = db.get(User, uid)
            if u is not None:
                db.delete(u)
