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
from advisor.billing.questions import question_for
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
from advisor.db.models import QuestionPurchase, User
from advisor.llm.budget import default_cumulative_token_budget
from advisor.llm.mock import MockGateway, text_response, tool_use_response
from advisor.llm.mock_dispatcher import _count_search_rounds, build_dispatcher
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


# -- ABS-360: per-question reasoning budget ---------------------------------

# A routine dev-standards input (address + project_details); the flagship
# $149 question requires both.
DEV_STANDARDS_INPUTS = {
    "address": "1250 Robie St, Halifax",
    "project_details": (
        "4-storey multi-unit residential: 14.5 m height, 12 units, front "
        "setback 3.0 m, rear 5.0 m, side 1.5 m each, lot coverage 48%, "
        "8 parking spaces."
    ),
}


def _start_uniq(db, user, *, slug, inputs, session_id):
    """Like ``_start`` but with a caller-supplied Stripe session id, so a
    single test can open more than one purchase without colliding on the
    UNIQUE ``stripe_checkout_session_id`` column."""
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id=session_id, url="u")
    )
    purchase, _url = answer_flow.start_question_checkout(
        db, user, question_slug=slug, inputs=inputs,
        client=client, settings=_settings(),
    )
    return purchase


def _heavy_grounding_gateway(*, searches: int, preamble_chars: int = 30_000):
    """A gateway that grounds every round with a big filler preamble.

    Each search round carries ``preamble_chars`` of full-price text, so the
    turn's cumulative billed-equivalent estimate climbs round over round —
    the same mechanism as ``MOCK_CUMULATIVE_TRIP`` — until, after
    ``searches`` grounded rounds, it emits the final answer. Whether the run
    grounds or trips the ABS-305 cumulative breaker is therefore decided
    purely by the cumulative budget in force. Calibrated so ``searches=7``
    (~214k billed-equivalent) trips the 165k default but grounds under the
    260k dev-standards ceiling.
    """
    filler = "H" * preamble_chars

    def _dispatch(request):  # noqa: ANN001, ANN202
        # Tools stripped → the forced-synthesis / final-answer turn.
        if not request.tools:
            return text_response(
                "Compliance evaluation complete; grounded in RC-LUB.",
                stop_reason="end_turn",
            )
        done = _count_search_rounds(request)
        if done < searches:
            return tool_use_response(
                tool_id=f"t-devstd-{done + 1}",
                tool_name="search_bylaw_evidence",
                tool_input={"query": f"development standard {done + 1}"},
                preamble=filler,
            )
        return text_response(
            "Compliance evaluation complete; grounded in RC-LUB.",
            stop_reason="end_turn",
        )

    return MockGateway(callable_=_dispatch)


def test_dev_standards_resolves_to_its_elevated_budget() -> None:
    # The catalog question's per-question ceiling is what the run uses;
    # a question with no override defers to the session default (None).
    from advisor.billing.answers import _resolve_run_inputs

    dev = QuestionPurchase(
        question_slug="development_standards", inputs_json=DEV_STANDARDS_INPUTS
    )
    _prompt, budget = _resolve_run_inputs(dev)
    assert budget == question_for("development_standards").cumulative_token_budget
    assert budget == 260_000

    permitted = QuestionPurchase(
        question_slug="permitted_use",
        inputs_json={"address": "1234 Elm St", "proposed_use": "a duplex"},
    )
    _p2, budget2 = _resolve_run_inputs(permitted)
    assert budget2 is None  # defers to default_cumulative_token_budget()


async def test_run_answer_threads_per_question_budget_into_the_turn(
    tmp_path: Path, monkeypatch
) -> None:
    # The elevated ceiling must actually reach the tool loop, not just sit
    # on the catalog. Spy on run_turn (mirrors test_buy_other) and assert
    # the budget it receives per question.
    captured: dict = {}

    async def _spy_run_turn(*, cumulative_token_budget=None, **kwargs):  # noqa: ANN001, ANN202
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
    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )

    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = _start_uniq(
            db, user, slug="development_standards",
            inputs=DEV_STANDARDS_INPUTS, session_id="cs_devstd_spy",
        )
        pid = purchase.id
        _authorize(db, pid, uid, "development_standards")
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        await answer_flow.run_answer(
            db, p, gateway=_gateway(), persona=PERSONA,
            retrieval_factory=_StubRetrieval(), client=client,
        )
    assert captured["budget"] == 260_000

    # A question with no override threads None (session default).
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = _start_uniq(
            db, user, slug="permitted_use",
            inputs={"address": "1234 Elm St", "proposed_use": "a duplex"},
            session_id="cs_permitted_spy",
        )
        pid2 = purchase.id
        _authorize(db, pid2, uid, "permitted_use")
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid2)
        await answer_flow.run_answer(
            db, p, gateway=_gateway(), persona=PERSONA,
            retrieval_factory=_StubRetrieval(), client=client,
        )
    assert captured["budget"] is None


async def test_dev_standards_grounds_where_the_default_budget_would_void(
    tmp_path: Path,
) -> None:
    # The behavioural heart of ABS-360: the SAME grounding-heavy run that
    # trips the flat default (and would settle voided/"cost_ceiling") now
    # completes and captures under the dev-standards ceiling. Calibrated so
    # 7 grounded rounds land between the 165k default and the 260k ceiling.
    assert default_cumulative_token_budget() == 165_000
    assert question_for("development_standards").cumulative_token_budget == 260_000

    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)

    # development_standards → runs under 260k → grounds → captures.
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = _start_uniq(
            db, user, slug="development_standards",
            inputs=DEV_STANDARDS_INPUTS, session_id="cs_devstd_ground",
        )
        pid = purchase.id
        _authorize(db, pid, uid, "development_standards")
    capture_client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        p = await answer_flow.run_answer(
            db, p, gateway=_heavy_grounding_gateway(searches=7),
            persona=PERSONA, retrieval_factory=_StubRetrieval(),
            client=capture_client,
        )
        assert p.status == "captured"
        assert p.failure_reason is None
        assert p.answer_text
    assert [c.action for c in capture_client.payment_intent_calls] == ["capture"]

    # Same load under the default budget (permitted_use, no override) →
    # trips the cumulative breaker → voided as "cost_ceiling", no charge.
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = _start_uniq(
            db, user, slug="permitted_use",
            inputs={"address": "1234 Elm St", "proposed_use": "a duplex"},
            session_id="cs_permitted_void",
        )  # permitted_use → default 165k
        pid2 = purchase.id
        _authorize(db, pid2, uid, "permitted_use")
    void_client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs2", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid2)
        p = await answer_flow.run_answer(
            db, p, gateway=_heavy_grounding_gateway(searches=7),
            persona=PERSONA, retrieval_factory=_StubRetrieval(),
            client=void_client,
        )
        assert p.status == "voided"
        assert p.failure_reason == "cost_ceiling"
    assert [c.action for c in void_client.payment_intent_calls] == ["cancel"]


# -- ABS-370: Variance ($299) and Zoning due-diligence ($199) budgets --------

# Routine inputs for the two SKUs that were voiding on cost_ceiling.
VARIANCE_INPUTS = {
    "address": "6242 North St, Halifax",
    "requested_variance": (
        "reduce the required rear-yard setback from 7.5 m to 5.0 m"
    ),
    "hardship_rationale": "irregular pie-shaped lot with a steep rear grade",
}

DUE_DILIGENCE_INPUTS = {"address": "2367 Brunswick St, Halifax"}


def test_variance_and_due_diligence_resolve_to_elevated_budgets() -> None:
    # ABS-370: both SKUs' ceilings must reach the run, mirroring the
    # dev-standards threading above.
    from advisor.billing.answers import _resolve_run_inputs

    for slug, inputs in (
        ("variance_justification", VARIANCE_INPUTS),
        ("due_diligence", DUE_DILIGENCE_INPUTS),
    ):
        purchase = QuestionPurchase(question_slug=slug, inputs_json=inputs)
        _prompt, budget = _resolve_run_inputs(purchase)
        assert budget == question_for(slug).cumulative_token_budget
        assert budget == 260_000


@pytest.mark.parametrize(
    ("slug", "inputs", "session_id"),
    [
        ("variance_justification", VARIANCE_INPUTS, "cs_var_ground"),
        ("due_diligence", DUE_DILIGENCE_INPUTS, "cs_dd_ground"),
    ],
)
async def test_variance_and_due_diligence_ground_where_the_default_would_void(
    tmp_path: Path, slug: str, inputs: dict, session_id: str
) -> None:
    # The behavioural heart of ABS-370, mirroring the ABS-360 guard above:
    # the SAME grounding-heavy load that trips the flat 165k default (see
    # the permitted_use control in
    # test_dev_standards_grounds_where_the_default_budget_would_void)
    # completes and captures under the per-SKU 260k ceiling. Measured
    # basis: real transcripts showed variance runs needing up to 171.6k
    # and due-diligence up to 193.0k — both past the default, both under
    # 260k. Reverting either budget bump turns this red.
    assert default_cumulative_token_budget() == 165_000
    assert question_for(slug).cumulative_token_budget == 260_000

    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)

    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase = _start_uniq(
            db, user, slug=slug, inputs=inputs, session_id=session_id,
        )
        pid = purchase.id
        _authorize(db, pid, uid, slug)
    capture_client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        p = await answer_flow.run_answer(
            db, p, gateway=_heavy_grounding_gateway(searches=7),
            persona=PERSONA, retrieval_factory=_StubRetrieval(),
            client=capture_client,
        )
        assert p.status == "captured"
        assert p.failure_reason is None
        assert p.answer_text
    assert [c.action for c in capture_client.payment_intent_calls] == ["capture"]


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


# -- Stale-generation rescue (ABS-354) --------------------------------------


def _wedge_generating(db, purchase_id: int, *, age: timedelta) -> None:
    """Strand a purchase in ``generating`` with an aged ``updated_at`` — the
    shape of a background job (ABS-343) orphaned by a server restart."""
    p = db.get(QuestionPurchase, purchase_id)
    p.status = "generating"
    p.updated_at = utcnow() - age
    db.flush()


async def test_settle_stale_generating_voids_and_fails(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)
        pid = purchase.id
        _authorize(db, pid, uid, "permitted_use")
        _wedge_generating(db, pid, age=timedelta(minutes=20))

    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        settled = answer_flow.settle_stale_generating(db, p, client=client)
        assert settled is True
        assert p.status == "failed"
        assert p.failure_reason == "internal_error"
        assert p.settled_at is not None

    # The orphaned authorization was VOIDED — the customer is never charged.
    assert [c.action for c in client.payment_intent_calls] == ["cancel"]


async def test_settle_stale_generating_leaves_fresh_row(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)
        pid = purchase.id
        _authorize(db, pid, uid, "permitted_use")
        _wedge_generating(db, pid, age=timedelta(seconds=30))

    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        settled = answer_flow.settle_stale_generating(db, p, client=client)
        assert settled is False
        # A genuinely in-flight run is left alone: status and hold untouched.
        assert p.status == "generating"
    assert client.payment_intent_calls == []


async def test_settle_stale_generating_ignores_settled_row(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    uid = _seed_user(db_url)
    with session_scope(db_url) as db:
        user = db.get(User, uid)
        purchase, _ = _start(db, user)
        pid = purchase.id
        _authorize(db, pid, uid, "permitted_use")
        p = db.get(QuestionPurchase, pid)
        p.status = "captured"
        p.updated_at = utcnow() - timedelta(hours=2)
        db.flush()

    client = MockStripeClient(
        checkout_result=CheckoutSessionResult(session_id="cs", url="u")
    )
    with session_scope(db_url) as db:
        p = db.get(QuestionPurchase, pid)
        # A settled (captured) row is never a rescue candidate, however old.
        assert answer_flow.settle_stale_generating(db, p, client=client) is False
        assert p.status == "captured"
    assert client.payment_intent_calls == []


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
