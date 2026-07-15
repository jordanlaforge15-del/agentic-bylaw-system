"""ABS-384 — per-report gates via ``ADVISOR_ENABLED_QUESTIONS``.

Each of the five report slugs is independently enable/disableable at
request time (no redeploy). The env var is comma-separated slugs; ``*``
means all; unset/empty means NONE (deny-by-default). The gate is read at
REQUEST time (``os.environ``), so monkeypatching it changes behaviour
between two requests with no app restart.

Contract under test:

* ``GET /v1/billing/questions`` menu is exactly the enabled subset, on
  BOTH the live and dormant routers; the envelope fields still render.
* A disabled slug 503s (``question_disabled``) on ``checkout/question``,
  ``questions/intake``, ``questions/free-start`` and ``.../answer`` (when
  the purchase is ``authorized``) — no credit consumed, no Stripe object,
  purchase status unchanged.
* An already-``captured`` purchase of a now-disabled slug stays fully
  accessible: get-purchase returns the answer, refine works in-window,
  and the reports list still shows it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from advisor.billing.client import CheckoutSessionResult, MockStripeClient
from advisor.billing.router import (
    build_billing_router,
    build_dormant_billing_router,
)
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.models import QuestionPurchase, User
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher
from bylaw_retrieval.retrieval import RetrievalResponse
from layer1.db.init_db import create_all
from layer1.db.session import session_scope

ALL_SLUGS = [
    "permitted_use",
    "development_standards",
    "due_diligence",
    "legal_nonconforming",
    "variance_justification",
]


class _StubRetrieval:
    def search(self, request):  # noqa: ANN001
        return RetrievalResponse(total_matches=0, matches=[], notes=[])


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


def _live_client(tmp_path: Path) -> tuple[TestClient, MockStripeClient, str]:
    """Live router, payments ON (Stripe authorize→capture path)."""
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)
    stripe = MockStripeClient(
        checkout_result=CheckoutSessionResult(
            session_id="cs_test", url="https://stripe.test/checkout"
        )
    )
    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True,
        ADVISOR_PAYMENTS_ENABLED=True,
        STRIPE_WEBHOOK_SECRET="whsec_test",
        STRIPE_PRICE_QUESTION_PERMITTED_USE="price_permitted_use",
        STRIPE_PRICE_QUESTION_DUE_DILIGENCE="price_due_diligence",
    )
    router = build_billing_router(
        settings=settings,
        client_factory=lambda: stripe,
        db_session_factory=lambda: session_scope(db_url),
        user_dependency=_user_dependency,
        user_resolver=_user_resolver,
        answer_gateway=MockGateway(callable_=build_dispatcher()),
        answer_persona="Test persona.",
        answer_retrieval_factory=_StubRetrieval(),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), stripe, db_url


def _payments_off_client(tmp_path: Path) -> tuple[TestClient, str]:
    """Live router, payments OFF (free-credit path)."""
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)
    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True,
        ADVISOR_PAYMENTS_ENABLED=False,
    )
    router = build_billing_router(
        settings=settings,
        client_factory=None,
        db_session_factory=lambda: session_scope(db_url),
        user_dependency=_user_dependency,
        user_resolver=_user_resolver,
        answer_gateway=MockGateway(callable_=build_dispatcher()),
        answer_persona="Test persona.",
        answer_retrieval_factory=_StubRetrieval(),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), db_url


def _dormant_client(tmp_path: Path) -> tuple[TestClient, str]:
    """Dormant router with DB deps wired (free-start + delivery mounted)."""
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)
    router = build_dormant_billing_router(
        db_session_factory=lambda: session_scope(db_url),
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


# -- Menu subsets, on both routers -----------------------------------------


@pytest.mark.parametrize("builder", ["live", "dormant"])
def test_menu_one_slug(builder, tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "permitted_use")
    client = (
        _live_client(tmp_path)[0]
        if builder == "live"
        else _dormant_client(tmp_path)[0]
    )
    body = client.get("/v1/billing/questions").json()
    slugs = [q["slug"] for q in body["questions"]]
    assert slugs == ["permitted_use"]


@pytest.mark.parametrize("builder", ["live", "dormant"])
def test_menu_all_slugs(builder, tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "*")
    client = (
        _live_client(tmp_path)[0]
        if builder == "live"
        else _dormant_client(tmp_path)[0]
    )
    body = client.get("/v1/billing/questions").json()
    assert {q["slug"] for q in body["questions"]} == set(ALL_SLUGS)


@pytest.mark.parametrize("builder", ["live", "dormant"])
def test_menu_zero_slugs_when_unset(builder, tmp_path, monkeypatch):
    monkeypatch.delenv("ADVISOR_ENABLED_QUESTIONS", raising=False)
    client = (
        _live_client(tmp_path)[0]
        if builder == "live"
        else _dormant_client(tmp_path)[0]
    )
    res = client.get("/v1/billing/questions")
    assert res.status_code == 200, res.text
    body = res.json()
    # Deny-by-default: no items, but the envelope fields still render.
    assert body["questions"] == []
    assert "enabled" in body
    assert "conversation_enabled" in body
    assert "payments_enabled" in body


def test_menu_empty_string_is_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "  ")
    client = _live_client(tmp_path)[0]
    assert client.get("/v1/billing/questions").json()["questions"] == []


def test_menu_reads_env_at_request_time(tmp_path, monkeypatch):
    """The gate is re-read per request — no app restart needed."""
    client = _live_client(tmp_path)[0]

    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "*")
    assert len(client.get("/v1/billing/questions").json()["questions"]) == 5

    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "due_diligence")
    slugs = [q["slug"] for q in client.get("/v1/billing/questions").json()["questions"]]
    assert slugs == ["due_diligence"]


# -- Purchase paths reject a disabled slug ---------------------------------


def test_checkout_question_disabled_slug_is_503(tmp_path, monkeypatch):
    client, stripe, _ = _live_client(tmp_path)
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "permitted_use")
    res = client.post(
        "/v1/billing/checkout/question",
        json={
            "question_slug": "due_diligence",
            "inputs": {"address": "1234 Elm St"},
        },
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "question_disabled"
    # No Stripe object created.
    assert stripe.checkout_calls == []
    assert stripe.payment_intent_calls == []


def test_checkout_question_disabled_slug_consumes_no_credit(tmp_path, monkeypatch):
    client, db_url = _payments_off_client(tmp_path)
    _set_free_questions(db_url, "u1", 2)
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "permitted_use")
    res = client.post(
        "/v1/billing/checkout/question",
        json={
            "question_slug": "due_diligence",
            "inputs": {"address": "1234 Elm St"},
        },
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "question_disabled"
    # The free-question credit was NOT consumed.
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "*")
    assert client.get("/v1/billing/me").json()["free_questions_remaining"] == 2


def test_intake_disabled_slug_is_503(tmp_path, monkeypatch):
    client, _, _ = _live_client(tmp_path)
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "permitted_use")
    res = client.post(
        "/v1/billing/questions/intake",
        json={"question_slug": "due_diligence", "conversation": "hi"},
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "question_disabled"


def test_intake_unknown_slug_still_400(tmp_path, monkeypatch):
    """A disabled KNOWN slug 503s; a genuinely unknown slug still 400s."""
    client, _, _ = _live_client(tmp_path)
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "*")
    res = client.post(
        "/v1/billing/questions/intake",
        json={"question_slug": "nope", "conversation": "hi"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "unknown_question"


def test_free_start_disabled_slug_is_503_no_credit(tmp_path, monkeypatch):
    client, db_url = _dormant_client(tmp_path)
    _set_free_questions(db_url, "u1", 2)
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "permitted_use")
    res = client.post(
        "/v1/billing/questions/free-start",
        json={"question_slug": "due_diligence", "inputs": {"address": "x"}},
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "question_disabled"
    with session_scope(db_url) as db:
        user = db.query(User).filter(User.clerk_user_id == "u1").one()
        assert user.free_questions_remaining == 2


def test_free_start_enabled_slug_consumes_credit(tmp_path, monkeypatch):
    """Regression: an ENABLED slug still consumes a free question."""
    client, db_url = _dormant_client(tmp_path)
    _set_free_questions(db_url, "u1", 2)
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "permitted_use")
    res = client.post(
        "/v1/billing/questions/free-start",
        json={
            "question_slug": "permitted_use",
            "inputs": {"address": "1 A St", "proposed_use": "a duplex"},
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["free_questions_remaining"] == 1


def test_answer_authorized_disabled_slug_is_503(tmp_path, monkeypatch):
    """A purchase that became authorized while its slug was enabled must
    503 (status unchanged) if the slug is disabled before the answer runs."""
    client, db_url = _dormant_client(tmp_path)
    _set_free_questions(db_url, "u1", 2)
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "due_diligence")
    res = client.post(
        "/v1/billing/questions/free-start",
        json={"question_slug": "due_diligence", "inputs": {"address": "1 A St"}},
    )
    assert res.status_code == 200, res.text
    purchase_id = res.json()["purchase_id"]
    assert res.json()["status"] == "authorized"

    # Operator disables the slug before the answer is run.
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "permitted_use")
    res = client.post(f"/v1/billing/questions/purchases/{purchase_id}/answer")
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "question_disabled"

    # Status unchanged — still authorized, not generating/failed.
    with session_scope(db_url) as db:
        assert db.get(QuestionPurchase, purchase_id).status == "authorized"


# -- Already-purchased reports stay accessible forever ----------------------


def test_captured_purchase_of_disabled_slug_stays_accessible(tmp_path, monkeypatch):
    client, db_url = _dormant_client(tmp_path)

    # Seed a captured purchase directly.
    with session_scope(db_url) as db:
        user = _user_resolver("u1", db)
        db.add(
            QuestionPurchase(
                user_id=user.id,
                question_slug="due_diligence",
                inputs_json={"address": "17 Edward St"},
                price_cents=19_900,
                status="captured",
                answer_text="Grounded due-diligence answer.",
            )
        )
        db.flush()
        purchase_id = (
            db.query(QuestionPurchase)
            .filter(QuestionPurchase.user_id == user.id)
            .one()
            .id
        )

    # Disable the slug entirely.
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "permitted_use")

    # get-purchase still returns the answer.
    res = client.get(f"/v1/billing/questions/purchases/{purchase_id}")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "captured"
    assert res.json()["answer"] == "Grounded due-diligence answer."

    # The reports list still shows it.
    reports = client.get("/v1/billing/questions/purchases").json()["reports"]
    assert any(r["id"] == purchase_id for r in reports)
