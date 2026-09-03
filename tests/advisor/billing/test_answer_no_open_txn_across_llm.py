"""ABS-338: the buy-an-answer flow must not hold a DB transaction open
across the long LLM turn.

Production 500 (2026-06-25): ``post_run_answer`` opened a request transaction
with two SELECTs (user_resolver + _load_owned_purchase), ran the ~84s tool loop
with that transaction idle, then issued the settling UPDATE via ``db.flush()``.
Postgres' ``idle_in_transaction_session_timeout`` (60s in dev/e2e,
``docker-compose.yml``, ABS-100) terminated the connection mid-turn, so the
final flush raised ``psycopg.errors.IdleInTransactionSessionTimeout`` → HTTP
500. (Not the ABS-332 NUL bug: different exception class; scrubbers intact;
persisted values clean.)

SQLite has no idle timeout, but ``Session.in_transaction()`` is
backend-agnostic, so the invariant that fixes the bug at the source is
checkable here: while the LLM turn runs, the request session must hold NO open
transaction. Two probes assert it from both ends of the turn — the gateway
(every LLM call, including ``run_refinement``'s new-question gate) and a bylaw
tool firing inside the tool loop.

The Postgres-side behavioural proof — a real idle cap that would kill the
connection — lives in ``test_answer_idle_txn_timeout_pg.py`` and in
``web/e2e/functional/abs338-answer-idle-in-txn.spec.ts``.
"""
from __future__ import annotations

from pathlib import Path

from advisor.billing import answers as answer_flow
from advisor.db.models import QuestionPurchase, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.db.init_db import create_all
from layer1.db.session import session_scope

PERSONA = "You are a test bylaw advisor."


class _TxnProbeGateway:
    """Wraps the ``MockGateway`` and records whether the *request* session
    held an open transaction at each LLM call of the turn."""

    name = "mock"

    def __init__(self, request_session) -> None:  # noqa: ANN001
        self._db = request_session
        self._inner = MockGateway(callable_=build_dispatcher())
        self.in_txn_at_call: list[bool] = []

    async def complete(self, request):  # noqa: ANN001
        self.in_txn_at_call.append(self._db.in_transaction())
        return await self._inner.complete(request)

    async def stream(self, request):  # noqa: ANN001
        self.in_txn_at_call.append(self._db.in_transaction())
        async for event in self._inner.stream(request):
            yield event


class _TxnAssertingRetrieval:
    """Grounds the answer AND records whether the *request* session held an
    open transaction at the moment a bylaw tool ran inside the LLM turn."""

    def __init__(self, request_session) -> None:  # noqa: ANN001
        self._db = request_session
        self.in_txn_during_turn: bool | None = None

    def search(self, request):  # noqa: ANN001
        if self.in_txn_during_turn is None:
            self.in_txn_during_turn = self._db.in_transaction()
        return RetrievalResponse(total_matches=1, matches=[], notes=[])


def _seed_authorized_purchase(db_url: str) -> int:
    create_all(db_url)
    with session_scope(db_url) as db:
        user = User(
            clerk_user_id="u1", email="u1@x.com", free_questions_remaining=1
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
        return purchase.id


async def test_run_answer_holds_no_open_txn_during_llm_turn(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    pid = _seed_authorized_purchase(db_url)

    with session_scope(db_url) as db:
        purchase = db.get(QuestionPurchase, pid)  # SELECT -> opens the read txn
        assert db.in_transaction()  # the txn the fix has to release
        probe = _TxnAssertingRetrieval(db)
        gateway = _TxnProbeGateway(db)
        purchase = await answer_flow.run_answer(
            db,
            purchase,
            gateway=gateway,
            persona=PERSONA,
            retrieval_factory=probe,
            client=None,
        )
        # Delivery must not regress: the answer still grounds and settles.
        assert purchase.status == "captured"
        assert purchase.answer_text
        assert purchase.transcript_json
        # 'captured' guarantees the grounding tool ran, so the probe is set.
        assert probe.in_txn_during_turn is not None, "grounding tool never ran"
        # The fix: no request transaction is held open across the LLM turn.
        assert probe.in_txn_during_turn is False
        assert gateway.in_txn_at_call, "the turn never called the gateway"
        assert not any(gateway.in_txn_at_call)


async def test_run_refinement_holds_no_open_txn_during_llm_turn(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    pid = _seed_authorized_purchase(db_url)

    # Capture an answer first — refinement only runs on a captured purchase.
    with session_scope(db_url) as db:
        purchase = db.get(QuestionPurchase, pid)
        purchase = await answer_flow.run_answer(
            db,
            purchase,
            gateway=MockGateway(callable_=build_dispatcher()),
            persona=PERSONA,
            retrieval_factory=_TxnAssertingRetrieval(db),
            client=None,
        )
        assert purchase.status == "captured"

    with session_scope(db_url) as db:
        purchase = db.get(QuestionPurchase, pid)  # SELECT -> opens the read txn
        assert db.in_transaction()
        probe = _TxnAssertingRetrieval(db)
        gateway = _TxnProbeGateway(db)
        answer = await answer_flow.run_refinement(
            db,
            purchase,
            message="Please summarize the answer in three bullet points.",
            gateway=gateway,
            persona=PERSONA,
            retrieval_factory=probe,
        )
        # Delivery must not regress: the follow-up is served and persisted.
        assert answer
        assert purchase.refinement_count == 1
        assert purchase.answer_text
        # The refinement turn (and any LLM new-question gate call before it)
        # ran with no request transaction open.
        assert gateway.in_txn_at_call, "the turn never called the gateway"
        assert not any(gateway.in_txn_at_call)
        if probe.in_txn_during_turn is not None:
            assert probe.in_txn_during_turn is False
