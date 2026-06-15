"""Buy-an-answer flow (ABS-312): authorize → run → capture/void + refine.

Covers the failed-question rule (capture on a grounded answer, void on an
ungroundable one), the refinement window (in-window serve, exhaustion,
new-question routing), and the checkout/webhook authorization handoff —
all against a sqlite test DB with the e2e ``MockGateway`` and a stub
retrieval service so no real Stripe or Anthropic calls are made.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from advisor.billing import answers as answer_flow
from advisor.billing.answers import (
    MissingRequiredInputsError,
    NewQuestionError,
    PurchaseNotAuthorizedError,
    QuestionPriceNotConfiguredError,
    RefinementNotAvailableError,
    UnknownQuestionError,
    WindowExhaustedError,
)
from advisor.billing.client import (
    CheckoutSessionResult,
    MockStripeClient,
    StripeEvent,
)
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
from advisor.db.models import QuestionPurchase, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.db.base import utcnow
from layer1.db.init_db import create_all
from layer1.db.session import session_scope

PERSONA = "You are a test bylaw advisor."


class _StubRetrieval:
    """Minimal retrieval service: a successful (empty) search so the
    grounding tool call is recorded as non-error."""

    def search(self, request):  # noqa: ANN001
        return RetrievalResponse(total_matches=0, matches=[], notes=[])


def _gateway() -> MockGateway:
    return MockGateway(callable_=build_dispatcher())


def _settings() -> AdvisorBillingSettings:
    return AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True,
        STRIPE_PRICE_QUESTION_PERMITTED_USE="price_permitted_use",
        STRIPE_PRICE_QUESTION_DEVELOPMENT_STANDARDS="price_dev",
        STRIPE_PRICE_QUESTION_DUE_DILIGENCE="price_dd",
        STRIPE_PRICE_QUESTION_LEGAL_NONCONFORMING="price_lnc",
        STRIPE_PRICE_QUESTION_VARIANCE_JUSTIFICATION="price_var",
    )


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str, clerk_id: str = "u1") -> int:
    with session_scope(db_url) as s:
        user = User(clerk_user_id=clerk_id, email=f"{clerk_id}@x.com")
        s.add(user)
        s.flush()
        return user.id


def _authorize(db, purchase_id: int, user_id: int, slug: str) -> None:
    """Drive the real webhook handler to authorize a purchase."""
    event = StripeEvent(
        id=f"evt_{purchase_id}",
        type="checkout.session.completed",
        data={
            "id": "cs_test",
            "payment_intent": f"pi_{purchase_id}",
            "metadata": {
                "advisor_user_id": str(user_id),
                "question_purchase_id": str(purchase_id),
                "question_slug": slug,
            },
        },
    )
    handle_event(db, event, _settings())


def _start(
    db, user: User, *, slug: str = "permitted_use", inputs: dict | None = None
) -> QuestionPurchase:
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs_test", url="u")
    )
    purchase, url = answer_flow.start_question_checkout(
        db,
        user,
        question_slug=slug,
        inputs=inputs
        or {"address": "1234 Elm St", "proposed_use": "a duplex"},
        client=client,
        settings=_settings(),
    )
    assert url == "u"
    return purchase, client


# -- Checkout ---------------------------------------------------------------


def test_checkout_creates_authorizing_purchase_with_manual_capture(
    tmp_path: Path,
) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, client = _start(db, user)
        assert purchase.status == "authorizing"
        assert purchase.price_cents == 7_900
        assert purchase.inputs_json["address"] == "1234 Elm St"
        # The checkout session was created with a MANUAL-capture
        # PaymentIntent — the authorize-then-capture mechanism.
        call = client.checkout_calls[0]
        assert call.capture_method == "manual"
        assert call.metadata["question_purchase_id"] == str(purchase.id)


def test_checkout_missing_required_input_raises_before_charge(
    tmp_path: Path,
) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        client = MockStripeClient(
            checkout_result=CheckoutSessionResult(session_id="cs", url="u")
        )
        with pytest.raises(MissingRequiredInputsError) as exc:
            answer_flow.start_question_checkout(
                db,
                user,
                question_slug="permitted_use",
                inputs={"address": "1234 Elm St"},  # proposed_use missing
                client=client,
                settings=_settings(),
            )
        assert "proposed_use" in exc.value.missing
        # No Stripe session was created — the customer is never charged
        # for unworkable inputs.
        assert client.checkout_calls == []


def test_checkout_unknown_question_raises(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        client = MockStripeClient(
            checkout_result=CheckoutSessionResult(session_id="cs", url="u")
        )
        with pytest.raises(UnknownQuestionError):
            answer_flow.start_question_checkout(
                db,
                user,
                question_slug="nope",
                inputs={},
                client=client,
                settings=_settings(),
            )


def test_checkout_price_not_configured_raises(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        client = MockStripeClient(
            checkout_result=CheckoutSessionResult(session_id="cs", url="u")
        )
        with pytest.raises(QuestionPriceNotConfiguredError):
            answer_flow.start_question_checkout(
                db,
                user,
                question_slug="permitted_use",
                inputs={"address": "1 A St", "proposed_use": "x"},
                client=client,
                settings=AdvisorBillingSettings(),  # no price IDs
            )


# -- Webhook authorization --------------------------------------------------


def test_webhook_authorizes_question_purchase(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)
        pid = purchase.id
        _authorize(db, pid, uid, "permitted_use")
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        assert p.status == "authorized"
        assert p.stripe_payment_intent_id == f"pi_{pid}"
        assert p.authorized_at is not None


# -- Run answer: capture on success -----------------------------------------


async def test_run_answer_grounded_captures(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)
        pid = purchase.id
        _authorize(db, pid, uid, "permitted_use")

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
        assert p.transcript_json  # conversation persisted for refinement

    # The authorization was CAPTURED (charged), not voided.
    actions = [c.action for c in capture_client.payment_intent_calls]
    assert actions == ["capture"]
    assert capture_client.payment_intent_calls[0].payment_intent_id == f"pi_{pid}"


async def test_run_answer_ungroundable_voids(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        # MOCK_UNGROUNDABLE in an input flows into the prompt → the mock
        # answers with zero grounding tool calls → failed question.
        purchase, _ = _start(
            db,
            user,
            inputs={
                "address": "1234 Elm St",
                "proposed_use": "a duplex MOCK_UNGROUNDABLE",
            },
        )
        pid = purchase.id
        _authorize(db, pid, uid, "permitted_use")

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
        assert p.status == "voided"
        assert p.failure_reason == "zero_evidence"
        assert p.answer_text is None

    # The authorization was VOIDED (released), never captured.
    actions = [c.action for c in void_client.payment_intent_calls]
    assert actions == ["cancel"]


async def test_run_answer_requires_authorization(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)  # still "authorizing"
        with pytest.raises(PurchaseNotAuthorizedError):
            await answer_flow.run_answer(
                db,
                purchase,
                gateway=_gateway(),
                persona=PERSONA,
                retrieval_factory=_StubRetrieval(),
                client=MockStripeClient(
                    checkout_result=CheckoutSessionResult(
                        session_id="cs", url="u"
                    )
                ),
            )


async def test_run_answer_is_idempotent(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)
        pid = purchase.id
        _authorize(db, pid, uid, "permitted_use")

    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        await answer_flow.run_answer(
            db, p, gateway=_gateway(), persona=PERSONA,
            retrieval_factory=_StubRetrieval(), client=client,
        )
        # Second call is a no-op — no double capture.
        await answer_flow.run_answer(
            db, p, gateway=_gateway(), persona=PERSONA,
            retrieval_factory=_StubRetrieval(), client=client,
        )
    assert [c.action for c in client.payment_intent_calls] == ["capture"]


# -- Refinement window ------------------------------------------------------


async def _captured_purchase(db_url: str, uid: int) -> int:
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)
        pid = purchase.id
        _authorize(db, pid, uid, "permitted_use")
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        await answer_flow.run_answer(
            db, p, gateway=_gateway(), persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
            client=MockStripeClient(
                checkout_result=CheckoutSessionResult(session_id="cs", url="u")
            ),
        )
    return pid


async def test_refinement_in_window_is_served(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    pid = await _captured_purchase(db_url, uid)
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        answer = await answer_flow.run_refinement(
            db, p, message="Summarize the answer in three bullet points.",
            gateway=_gateway(), persona=PERSONA,
            retrieval_factory=_StubRetrieval(),
        )
        assert answer
        assert p.refinement_count == 1
        assert answer_flow.refinements_remaining(p) == 2


async def test_refinement_new_question_by_address_is_blocked(
    tmp_path: Path,
) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    pid = await _captured_purchase(db_url, uid)
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        with pytest.raises(NewQuestionError) as exc:
            await answer_flow.run_refinement(
                db, p, message="What about 999 Oak Avenue instead?",
                gateway=_gateway(), persona=PERSONA,
                retrieval_factory=_StubRetrieval(),
            )
        assert exc.value.suggested_slug == "permitted_use"
        # The follow-up was NOT served — count unchanged.
        assert p.refinement_count == 0


async def test_refinement_new_question_via_llm_gate(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    pid = await _captured_purchase(db_url, uid)
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        # No new address (programmatic gate passes), but interrogative
        # shape triggers the persona-gated LLM check, which the mock
        # resolves as a new question via the MOCK_NEW_QUESTION sentinel.
        with pytest.raises(NewQuestionError):
            await answer_flow.run_refinement(
                db, p,
                message="Can I also do this? MOCK_NEW_QUESTION",
                gateway=_gateway(), persona=PERSONA,
                retrieval_factory=_StubRetrieval(),
            )


async def test_refinement_window_exhausts_after_max(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    pid = await _captured_purchase(db_url, uid)
    for _ in range(answer_flow.MAX_REFINEMENTS):
        with session_scope(db_url) as db:
            p = db.get(QuestionPurchase, pid)
            await answer_flow.run_refinement(
                db, p, message="Please reformat the answer.",
                gateway=_gateway(), persona=PERSONA,
                retrieval_factory=_StubRetrieval(),
            )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        assert p.refinement_count == answer_flow.MAX_REFINEMENTS
        with pytest.raises(WindowExhaustedError) as exc:
            await answer_flow.run_refinement(
                db, p, message="One more reformat please.",
                gateway=_gateway(), persona=PERSONA,
                retrieval_factory=_StubRetrieval(),
            )
        assert exc.value.reason == "refinements_exhausted"


async def test_refinement_window_expired_is_blocked(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    pid = await _captured_purchase(db_url, uid)
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        p.window_expires_at = utcnow() - timedelta(minutes=1)
        db.flush()
        with pytest.raises(WindowExhaustedError) as exc:
            await answer_flow.run_refinement(
                db, p, message="Reformat please.",
                gateway=_gateway(), persona=PERSONA,
                retrieval_factory=_StubRetrieval(),
            )
        assert exc.value.reason == "window_expired"


async def test_refinement_requires_captured_answer(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)  # authorizing, never captured
        with pytest.raises(RefinementNotAvailableError):
            await answer_flow.run_refinement(
                db, purchase, message="Reformat please.",
                gateway=_gateway(), persona=PERSONA,
                retrieval_factory=_StubRetrieval(),
            )
