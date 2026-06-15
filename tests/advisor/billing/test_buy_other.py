"""Off-menu "Other" buy-an-answer flow (ABS-316).

The off-menu path reuses the ABS-312 authorize→capture/void machinery but
prices the question via a FREE LLM quote and charges an ad-hoc amount
(``price_data``, no pre-created Stripe Price). These tests prove:

* the quoted price is bound from the server-side quote and the checkout
  uses an ad-hoc amount (not a Price ID);
* a grounded off-menu answer CAPTURES the card, an ungroundable one VOIDS
  it (the failed-question rule applies identically off-menu);
* the answer runs under the quote's ABS-305 cost envelope (a pricier
  question gets a larger cumulative ceiling).

All against a sqlite test DB with the e2e ``MockGateway`` + a stub
retrieval service — no real Stripe or Anthropic calls.
"""
from __future__ import annotations

from pathlib import Path

from advisor.billing import answers as answer_flow
from advisor.billing.client import (
    CheckoutSessionResult,
    MockStripeClient,
    StripeEvent,
)
from advisor.billing.quote import TIERS, quote_question
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
from advisor.db.models import QuestionPurchase, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.db.init_db import create_all
from layer1.db.session import session_scope

PERSONA = "You are a test bylaw advisor."


class _StubRetrieval:
    def search(self, request):  # noqa: ANN001
        return RetrievalResponse(total_matches=0, matches=[], notes=[])


def _gateway() -> MockGateway:
    return MockGateway(callable_=build_dispatcher())


def _settings() -> AdvisorBillingSettings:
    return AdvisorBillingSettings(ADVISOR_BILLING_ENABLED=True)


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str, clerk_id: str = "u1") -> int:
    with session_scope(db_url) as s:
        user = User(clerk_user_id=clerk_id, email=f"{clerk_id}@x.com")
        s.add(user)
        s.flush()
        return user.id


def _authorize(db, purchase_id: int, user_id: int) -> None:
    event = StripeEvent(
        id=f"evt_{purchase_id}",
        type="checkout.session.completed",
        data={
            "id": "cs_test",
            "payment_intent": f"pi_{purchase_id}",
            "metadata": {
                "advisor_user_id": str(user_id),
                "question_purchase_id": str(purchase_id),
                "question_slug": "other",
            },
        },
    )
    handle_event(db, event, _settings())


async def _start_other(db, user: User, question: str):
    quote = await quote_question(_gateway(), question)
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs_test", url="u")
    )
    purchase, url = answer_flow.start_other_checkout(
        db, user, quote=quote, client=client, settings=_settings()
    )
    assert url == "u"
    return purchase, client, quote


# -- Checkout ---------------------------------------------------------------


async def test_other_checkout_uses_adhoc_amount_not_price_id(
    tmp_path: Path,
) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, client, quote = await _start_other(
            db, user, "A complex variance question. MOCK_QUOTE_COMPLEX"
        )
        assert purchase.status == "authorizing"
        assert purchase.question_slug == "other"
        assert purchase.price_cents == quote.price_cents == 29_900
        assert purchase.inputs_json["question"].startswith("A complex variance")
        # The quote envelope (difficulty + cost ceiling) is persisted for
        # the answer run.
        assert purchase.metadata_json["difficulty"] == "complex"
        assert purchase.metadata_json["cumulative_token_budget"] == (
            TIERS["complex"].cumulative_token_budget
        )
        # Ad-hoc pricing: an amount, NOT a Stripe Price ID, and a manual
        # capture hold.
        call = client.checkout_calls[0]
        assert call.price_id is None
        assert call.amount_cents == 29_900
        assert call.currency == "cad"
        assert call.capture_method == "manual"
        assert call.metadata["question_purchase_id"] == str(purchase.id)


# -- Run answer -------------------------------------------------------------


async def test_other_grounded_answer_captures(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _client, _quote = await _start_other(
            db, user, "Is a corner store allowed at 5 Oak St? MOCK_QUOTE_SIMPLE"
        )
        pid = purchase.id
        _authorize(db, pid, uid)

    capture_client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        p = await answer_flow.run_answer(
            db,
            p,
            gateway=_gateway(),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=capture_client,
        )
        assert p.status == "captured"
        assert p.answer_text
        assert p.window_expires_at is not None

    actions = [c.action for c in capture_client.payment_intent_calls]
    assert actions == ["capture"]


async def test_other_ungroundable_answer_voids(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _client, _quote = await _start_other(
            db,
            user,
            "An ungroundable off-menu question MOCK_UNGROUNDABLE",
        )
        pid = purchase.id
        _authorize(db, pid, uid)

    void_client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        p = await answer_flow.run_answer(
            db,
            p,
            gateway=_gateway(),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=void_client,
        )
        # Failed-question rule applies off-menu: no charge.
        assert p.status == "voided"
        assert p.failure_reason == "zero_evidence"
        assert p.answer_text is None

    actions = [c.action for c in void_client.payment_intent_calls]
    assert actions == ["cancel"]


async def test_other_answer_runs_under_quote_cost_envelope(
    tmp_path: Path, monkeypatch
) -> None:
    """The off-menu answer's ABS-305 ceiling comes from the quote tier."""
    captured: dict = {}

    async def _spy_run_turn(*, cumulative_token_budget=None, **kwargs):  # noqa: ANN001
        captured["budget"] = cumulative_token_budget
        return answer_flow.TurnOutcome(
            answer_text="ok",
            grounded=True,
            failure_reason=None,
            terminated_reason="end_turn",
            messages=[],
        )

    monkeypatch.setattr(answer_flow, "run_turn", _spy_run_turn)

    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _client, quote = await _start_other(
            db, user, "An exceptionally hard question. MOCK_QUOTE_EXCEPTIONAL"
        )
        pid = purchase.id
        _authorize(db, pid, uid)

    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        await answer_flow.run_answer(
            db,
            p,
            gateway=_gateway(),
            persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=client,
        )

    assert captured["budget"] == quote.cumulative_token_budget
    assert captured["budget"] == TIERS["exceptional"].cumulative_token_budget
