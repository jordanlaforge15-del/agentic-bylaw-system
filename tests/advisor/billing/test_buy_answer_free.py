"""Payments-off / free-trial buy-an-answer flow (ABS-322).

Mirrors ``test_buy_answer.py`` but for the credit-gated path: an answer
is unlocked by consuming a free-question entitlement (ABS-314), with NO
Stripe. Covers the failed-question rule in payments-off mode (a grounded
answer keeps the credit consumed; an ungroundable / over-cap answer
refunds it), input validation ordering (no credit burned on unworkable
inputs), trial exhaustion, refinement, and the admin-grant top-up — all
against a sqlite test DB with the e2e ``MockGateway`` and a stub
retrieval service.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from advisor.billing import answers as answer_flow
from advisor.billing.answers import (
    FreeQuestionsExhaustedError,
    MissingRequiredInputsError,
    UnknownQuestionError,
)
from advisor.db.cases import grant_free_questions
from advisor.db.models import QuestionPurchase, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.db.init_db import create_all
from layer1.db.session import session_scope

PERSONA = "You are a test bylaw advisor."
VALID_INPUTS = {"address": "1234 Elm St", "proposed_use": "a duplex"}


class _StubRetrieval:
    """Successful (empty) search so the grounding tool call is non-error."""

    def search(self, request):  # noqa: ANN001
        return RetrievalResponse(total_matches=0, matches=[], notes=[])


def _gateway() -> MockGateway:
    return MockGateway(callable_=build_dispatcher())


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str, *, free_questions: int, clerk_id: str = "u1") -> int:
    with session_scope(db_url) as s:
        user = User(
            clerk_user_id=clerk_id,
            email=f"{clerk_id}@x.com",
            free_questions_remaining=free_questions,
        )
        s.add(user)
        s.flush()
        return user.id


# -- start_question_free -----------------------------------------------------


def test_start_question_free_consumes_credit_and_authorizes(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=2)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = answer_flow.start_question_free(
            db, user, question_slug="permitted_use", inputs=VALID_INPUTS
        )
        # Born authorized — immediately runnable, no Stripe objects.
        assert purchase.status == "authorized"
        assert purchase.authorized_at is not None
        assert purchase.stripe_payment_intent_id is None
        assert purchase.stripe_checkout_session_id is None
        assert purchase.metadata_json.get("payments_off") is True
        # One credit reserved.
        assert db.get(User, uid).free_questions_remaining == 1


def test_start_question_free_exhausted_raises(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=0)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        with pytest.raises(FreeQuestionsExhaustedError):
            answer_flow.start_question_free(
                db, user, question_slug="permitted_use", inputs=VALID_INPUTS
            )
        # No purchase row created, balance untouched.
        assert db.query(QuestionPurchase).count() == 0
        assert db.get(User, uid).free_questions_remaining == 0


def test_missing_inputs_does_not_consume_credit(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=2)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        with pytest.raises(MissingRequiredInputsError):
            answer_flow.start_question_free(
                db,
                user,
                question_slug="permitted_use",
                inputs={"address": "1234 Elm St"},  # missing proposed_use
            )
        # Failed-question rule: unworkable inputs never cost a credit.
        assert db.get(User, uid).free_questions_remaining == 2
        assert db.query(QuestionPurchase).count() == 0


def test_unknown_question_raises(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=2)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        with pytest.raises(UnknownQuestionError):
            answer_flow.start_question_free(
                db, user, question_slug="nope", inputs=VALID_INPUTS
            )
        assert db.get(User, uid).free_questions_remaining == 2


# -- run_answer (settlement) -------------------------------------------------


async def test_run_answer_grounded_keeps_credit_consumed(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=2)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = answer_flow.start_question_free(
            db, user, question_slug="permitted_use", inputs=VALID_INPUTS
        )
        pid = purchase.id

    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        # No Stripe client passed — payments-off captures nothing.
        p = await answer_flow.run_answer(
            db,
            p,
            gateway=_gateway(),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=None,
        )
        assert p.status == "captured"
        assert p.answer_text
        assert p.window_expires_at is not None
        # Credit stays consumed on a grounded answer.
        assert db.get(User, uid).free_questions_remaining == 1


async def test_run_answer_ungroundable_refunds_credit(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=2)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = answer_flow.start_question_free(
            db,
            user,
            question_slug="permitted_use",
            inputs={
                "address": "1234 Elm St",
                "proposed_use": "a duplex MOCK_UNGROUNDABLE",
            },
        )
        pid = purchase.id
        # Credit reserved.
        assert db.get(User, uid).free_questions_remaining == 1

    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        p = await answer_flow.run_answer(
            db,
            p,
            gateway=_gateway(),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=None,
        )
        assert p.status == "voided"
        assert p.failure_reason == "zero_evidence"
        assert p.answer_text is None
        # Failed-question rule: the reserved credit is handed back.
        assert db.get(User, uid).free_questions_remaining == 2


async def test_refine_served_free_on_credit_purchase(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=2)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = answer_flow.start_question_free(
            db, user, question_slug="permitted_use", inputs=VALID_INPUTS
        )
        pid = purchase.id

    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        await answer_flow.run_answer(
            db,
            p,
            gateway=_gateway(),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=None,
        )

    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        answer = await answer_flow.run_refinement(
            db,
            p,
            message="Please reformat the answer as a bulleted list.",
            gateway=_gateway(),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
        )
        assert answer
        assert p.refinement_count == 1
        # Refinements are free — no extra credit consumed.
        assert db.get(User, uid).free_questions_remaining == 1


# -- admin grant -------------------------------------------------------------


def test_grant_free_questions_tops_up_balance(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=1)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        remaining = grant_free_questions(
            db, user=user, quantity=5, reason="support top-up"
        )
        assert remaining == 6
        assert db.get(User, uid).free_questions_remaining == 6


def test_grant_free_questions_non_positive_is_noop(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url, free_questions=3)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        assert grant_free_questions(db, user=user, quantity=0, reason="x") == 3
        assert db.get(User, uid).free_questions_remaining == 3
