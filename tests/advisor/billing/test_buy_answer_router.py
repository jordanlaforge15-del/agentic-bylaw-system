"""HTTP-level coverage of the priced-question buy-an-answer endpoints.

Mounts ``build_billing_router`` on a bare FastAPI app with a sqlite DB, a
``MockStripeClient``, and the e2e ``MockGateway`` so the production
endpoints (checkout/question → webhook → answer → refine) and their
exception → HTTP-status mapping are exercised end to end without Clerk,
Stripe, or Anthropic.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from advisor.billing.client import CheckoutSessionResult, MockStripeClient
from advisor.billing.router import build_billing_router
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.models import User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


class _StubRetrieval:
    def search(self, request):  # noqa: ANN001
        return RetrievalResponse(total_matches=0, matches=[], notes=[])


def _settings() -> AdvisorBillingSettings:
    # ABS-312 exercises the Stripe authorize→capture path, so payments
    # are ON here. The payments-off / free-credit path (ABS-322 default)
    # is covered separately in test_buy_answer_free.py.
    return AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True,
        ADVISOR_PAYMENTS_ENABLED=True,
        # ABS-325: the off-menu "Other" path is disabled by default. These
        # tests exercise that (intact) engine end to end, so flip it on.
        ADVISOR_OTHER_QUESTION_ENABLED=True,
        STRIPE_WEBHOOK_SECRET="whsec_test",
        STRIPE_PRICE_QUESTION_PERMITTED_USE="price_permitted_use",
    )


def _build_client(tmp_path: Path) -> tuple[TestClient, MockStripeClient]:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)

    stripe = MockStripeClient(
        checkout_result=CheckoutSessionResult(
            session_id="cs_test", url="https://stripe.test/checkout"
        )
    )

    def _db_factory():
        return session_scope(db_url)

    def _user_dependency(x_test_user_id: str = Header(default="u1")) -> str:
        return x_test_user_id

    def _user_resolver(auth_session, db) -> User:
        user = (
            db.query(User)
            .filter(User.clerk_user_id == auth_session)
            .one_or_none()
        )
        if user is None:
            user = User(clerk_user_id=auth_session, email=f"{auth_session}@x.com")
            db.add(user)
            db.flush()
        return user

    router = build_billing_router(
        settings=_settings(),
        client_factory=lambda: stripe,
        db_session_factory=_db_factory,
        user_dependency=_user_dependency,
        user_resolver=_user_resolver,
        answer_gateway=MockGateway(callable_=build_dispatcher()),
        answer_persona="Test persona.",
        answer_retrieval_factory=_StubRetrieval(),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), stripe


def _authorize_via_webhook(client: TestClient, purchase_id: int) -> None:
    event = {
        "id": f"evt_{purchase_id}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test",
                "payment_intent": f"pi_{purchase_id}",
                "metadata": {"question_purchase_id": str(purchase_id)},
            }
        },
    }
    res = client.post(
        "/v1/billing/webhook",
        content=json.dumps(event),
        headers={"Stripe-Signature": "sig"},
    )
    assert res.status_code == 200, res.text


def test_full_buy_answer_flow_over_http(tmp_path: Path) -> None:
    client, stripe = _build_client(tmp_path)

    # 1. Checkout a question.
    res = client.post(
        "/v1/billing/checkout/question",
        json={
            "question_slug": "permitted_use",
            "inputs": {"address": "1234 Elm St", "proposed_use": "a duplex"},
        },
    )
    assert res.status_code == 200, res.text
    purchase_id = res.json()["purchase_id"]
    assert res.json()["url"].startswith("https://stripe.test")
    assert stripe.checkout_calls[0].capture_method == "manual"

    # 2. Authorize via the webhook.
    _authorize_via_webhook(client, purchase_id)
    res = client.get(f"/v1/billing/questions/purchases/{purchase_id}")
    assert res.json()["status"] == "authorized"

    # 3. Run the answer → grounded → captured.
    res = client.post(
        f"/v1/billing/questions/purchases/{purchase_id}/answer"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "captured"
    assert body["answer"]
    assert body["refinements_remaining"] == 3
    assert [c.action for c in stripe.payment_intent_calls] == ["capture"]

    # 4. In-window refinement is served.
    res = client.post(
        f"/v1/billing/questions/purchases/{purchase_id}/refine",
        json={"message": "Summarize in three bullet points."},
    )
    assert res.status_code == 200, res.text
    assert res.json()["refinement_count"] == 1

    # 5. A new-question follow-up is blocked and routed to a new purchase.
    res = client.post(
        f"/v1/billing/questions/purchases/{purchase_id}/refine",
        json={"message": "What about 999 Oak Avenue instead?"},
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "new_question"
    assert detail["suggested_slug"] == "permitted_use"


def test_missing_required_input_is_400(tmp_path: Path) -> None:
    client, _ = _build_client(tmp_path)
    res = client.post(
        "/v1/billing/checkout/question",
        json={"question_slug": "permitted_use", "inputs": {"address": "x"}},
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["code"] == "missing_required_inputs"
    assert "proposed_use" in detail["missing"]


def test_answer_before_authorization_is_409(tmp_path: Path) -> None:
    client, _ = _build_client(tmp_path)
    res = client.post(
        "/v1/billing/checkout/question",
        json={
            "question_slug": "permitted_use",
            "inputs": {"address": "1 A St", "proposed_use": "a duplex"},
        },
    )
    purchase_id = res.json()["purchase_id"]
    # No webhook → still "authorizing".
    res = client.post(
        f"/v1/billing/questions/purchases/{purchase_id}/answer"
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "not_authorized"


def test_void_on_ungroundable_over_http(tmp_path: Path) -> None:
    client, stripe = _build_client(tmp_path)
    res = client.post(
        "/v1/billing/checkout/question",
        json={
            "question_slug": "permitted_use",
            "inputs": {
                "address": "1234 Elm St",
                "proposed_use": "a duplex MOCK_UNGROUNDABLE",
            },
        },
    )
    purchase_id = res.json()["purchase_id"]
    _authorize_via_webhook(client, purchase_id)
    res = client.post(
        f"/v1/billing/questions/purchases/{purchase_id}/answer"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "voided"
    assert body["failure_reason"] == "zero_evidence"
    assert body["answer"] is None
    assert [c.action for c in stripe.payment_intent_calls] == ["cancel"]


# -- Off-menu "Other": free quote → buy → answer (ABS-316) ------------------


def test_quote_endpoint_is_free_and_anchored(tmp_path: Path) -> None:
    client, stripe = _build_client(tmp_path)
    res = client.post(
        "/v1/billing/questions/quote",
        json={"question": "Is a backyard suite allowed here? MOCK_QUOTE_SIMPLE"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["difficulty"] == "simple"
    assert body["price_cents"] == 7_900
    assert body["band_low_cents"] == 7_900
    assert body["band_high_cents"] == 29_900
    assert body["rationale"]
    # Quoting NEVER creates a Stripe object or charges.
    assert stripe.checkout_calls == []
    assert stripe.payment_intent_calls == []


def test_quote_endpoint_rejects_empty_question(tmp_path: Path) -> None:
    client, _ = _build_client(tmp_path)
    res = client.post("/v1/billing/questions/quote", json={"question": "  ?  "})
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "empty_question"


def test_full_other_buy_flow_over_http(tmp_path: Path) -> None:
    client, stripe = _build_client(tmp_path)

    # 1. Buy an answer to an off-menu question (server re-quotes the price).
    res = client.post(
        "/v1/billing/checkout/other",
        json={"question": "A complex custom variance ask. MOCK_QUOTE_COMPLEX"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    purchase_id = body["purchase_id"]
    assert body["price_cents"] == 29_900
    assert body["difficulty"] == "complex"
    assert body["url"].startswith("https://stripe.test")
    # Ad-hoc amount, no Price ID, manual-capture hold.
    call = stripe.checkout_calls[0]
    assert call.price_id is None
    assert call.amount_cents == 29_900
    assert call.capture_method == "manual"

    # 2. Authorize via the webhook.
    _authorize_via_webhook(client, purchase_id)

    # 3. Run the answer → grounded → capture.
    res = client.post(
        f"/v1/billing/questions/purchases/{purchase_id}/answer"
    )
    assert res.status_code == 200, res.text
    answered = res.json()
    assert answered["status"] == "captured"
    assert answered["question_slug"] == "other"
    assert answered["answer"]
    assert [c.action for c in stripe.payment_intent_calls] == ["capture"]


def _build_client_other_disabled(tmp_path: Path) -> TestClient:
    """Build a router with the off-menu path disabled (ABS-325 default).

    Identical wiring to ``_build_client`` but ``other_question_enabled`` is
    left at its default False, so the quote / checkout-other endpoints must
    reject. Everything else (catalog checkout, intake, …) stays live.
    """
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)
    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True,
        ADVISOR_PAYMENTS_ENABLED=True,
        STRIPE_WEBHOOK_SECRET="whsec_test",
        STRIPE_PRICE_QUESTION_PERMITTED_USE="price_permitted_use",
    )

    def _user_dependency(x_test_user_id: str = Header(default="u1")) -> str:
        return x_test_user_id

    def _user_resolver(auth_session, db) -> User:  # noqa: ANN001
        user = (
            db.query(User)
            .filter(User.clerk_user_id == auth_session)
            .one_or_none()
        )
        if user is None:
            user = User(clerk_user_id=auth_session, email=f"{auth_session}@x.com")
            db.add(user)
            db.flush()
        return user

    router = build_billing_router(
        settings=settings,
        client_factory=lambda: MockStripeClient(
            checkout_result=CheckoutSessionResult(
                session_id="cs_test", url="https://stripe.test/checkout"
            )
        ),
        db_session_factory=lambda: session_scope(db_url),
        user_dependency=_user_dependency,
        user_resolver=_user_resolver,
        answer_gateway=MockGateway(callable_=build_dispatcher()),
        answer_persona="Test persona.",
        answer_retrieval_factory=_StubRetrieval(),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_quote_endpoint_rejects_when_other_disabled(tmp_path: Path) -> None:
    # ABS-325: the off-menu free-form path is off by default. The quote
    # endpoint must 503 even though the engine is fully wired.
    client = _build_client_other_disabled(tmp_path)
    res = client.post(
        "/v1/billing/questions/quote",
        json={"question": "Is a backyard suite allowed? MOCK_QUOTE_SIMPLE"},
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "other_question_disabled"


def test_checkout_other_rejects_when_other_disabled(tmp_path: Path) -> None:
    # ABS-325: buying an off-menu answer must 503 when the flag is off, and
    # never authorize a card.
    client = _build_client_other_disabled(tmp_path)
    res = client.post(
        "/v1/billing/checkout/other",
        json={"question": "A complex custom variance ask. MOCK_QUOTE_COMPLEX"},
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "other_question_disabled"


# -- Consultant-style intake detection (ABS-315) ---------------------------


def test_intake_asks_for_missing_input_then_completes(tmp_path: Path) -> None:
    client, stripe = _build_client(tmp_path)

    # 1. Incomplete: only proposed_use supplied → intake asks for the address.
    res = client.post(
        "/v1/billing/questions/intake",
        json={
            "question_slug": "permitted_use",
            "conversation": (
                "Can I build a fourplex on my lot? "
                "MOCK_INPUT[proposed_use]=a four-unit dwelling|"
            ),
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["complete"] is False
    assert body["missing_required"] == ["address"]
    assert "Property address" in body["prompt"]
    # Intake never creates a Stripe object or charges.
    assert stripe.checkout_calls == []
    assert stripe.payment_intent_calls == []

    # 2. User supplies the address; the prior input is carried forward.
    res = client.post(
        "/v1/billing/questions/intake",
        json={
            "question_slug": "permitted_use",
            "conversation": "It's at 12 Pine Street. MOCK_INPUT[address]=12 Pine Street|",
            "inputs": body["inputs"],
        },
    )
    assert res.status_code == 200, res.text
    done = res.json()
    assert done["complete"] is True
    assert done["prompt"] == ""
    assert done["inputs"] == {
        "address": "12 Pine Street",
        "proposed_use": "a four-unit dwelling",
    }

    # 3. The collected inputs flow straight into checkout (no 400).
    res = client.post(
        "/v1/billing/checkout/question",
        json={"question_slug": "permitted_use", "inputs": done["inputs"]},
    )
    assert res.status_code == 200, res.text


def test_intake_unknown_question_is_400(tmp_path: Path) -> None:
    client, _ = _build_client(tmp_path)
    res = client.post(
        "/v1/billing/questions/intake",
        json={"question_slug": "nope", "conversation": "hi"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "unknown_question"


# -- Payments-off / free-trial path (ABS-322) ------------------------------


def _build_client_payments_off(
    tmp_path: Path,
) -> tuple[TestClient, str]:
    """Build a billing client with payments OFF (no Stripe). Returns the
    client plus the db_url so a test can set the user's free-question
    balance directly."""
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)

    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True,
        ADVISOR_PAYMENTS_ENABLED=False,  # free-credit mode
    )

    def _db_factory():
        return session_scope(db_url)

    def _user_dependency(x_test_user_id: str = Header(default="u1")) -> str:
        return x_test_user_id

    def _user_resolver(auth_session, db) -> User:
        user = (
            db.query(User)
            .filter(User.clerk_user_id == auth_session)
            .one_or_none()
        )
        if user is None:
            user = User(clerk_user_id=auth_session, email=f"{auth_session}@x.com")
            db.add(user)
            db.flush()
        return user

    router = build_billing_router(
        settings=settings,
        client_factory=None,  # no Stripe in payments-off mode
        db_session_factory=_db_factory,
        user_dependency=_user_dependency,
        user_resolver=_user_resolver,
        answer_gateway=MockGateway(callable_=build_dispatcher()),
        answer_persona="Test persona.",
        answer_retrieval_factory=_StubRetrieval(),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), db_url


def _set_free_questions(db_url: str, clerk_id: str, n: int) -> None:
    with session_scope(db_url) as db:
        user = (
            db.query(User).filter(User.clerk_user_id == clerk_id).one_or_none()
        )
        if user is None:
            user = User(clerk_user_id=clerk_id, email=f"{clerk_id}@x.com")
            db.add(user)
        user.free_questions_remaining = n
        db.flush()


def test_payments_off_checkout_consumes_credit_no_stripe(tmp_path: Path) -> None:
    client, db_url = _build_client_payments_off(tmp_path)
    _set_free_questions(db_url, "u1", 2)

    res = client.post(
        "/v1/billing/checkout/question",
        json={
            "question_slug": "permitted_use",
            "inputs": {"address": "1234 Elm St", "proposed_use": "a duplex"},
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # No Stripe checkout URL — purchase already authorized via free credit.
    assert body["url"] is None
    assert body["status"] == "authorized"
    assert body["payments_enabled"] is False
    purchase_id = body["purchase_id"]

    # Answer runs with no Stripe client → grounded → captured.
    res = client.post(f"/v1/billing/questions/purchases/{purchase_id}/answer")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "captured"

    # /me reflects payments-off mode and the consumed credit.
    me = client.get("/v1/billing/me").json()
    assert me["payments_enabled"] is False
    assert me["free_questions_remaining"] == 1


def test_payments_off_exhaustion_is_402(tmp_path: Path) -> None:
    client, db_url = _build_client_payments_off(tmp_path)
    _set_free_questions(db_url, "u1", 0)

    res = client.post(
        "/v1/billing/checkout/question",
        json={
            "question_slug": "permitted_use",
            "inputs": {"address": "1234 Elm St", "proposed_use": "a duplex"},
        },
    )
    assert res.status_code == 402, res.text
    assert res.json()["detail"]["code"] == "free_questions_exhausted"


def test_payments_off_questions_menu_available_without_price_ids(
    tmp_path: Path,
) -> None:
    client, _ = _build_client_payments_off(tmp_path)
    body = client.get("/v1/billing/questions").json()
    assert body["payments_enabled"] is False
    assert body["enabled"] is True
    # Every question is answerable via a free credit — available despite
    # no Stripe Price IDs configured.
    assert body["questions"]
    assert all(q["available"] for q in body["questions"])
