"""Buy-an-answer orchestration — the priced-question spend path (ABS-312).

This module is the engine behind the "buy an answer" launch product
(``docs/decisions/2026-06-priced-question-catalog.md``): the user picks a
fixed-price catalog question, we AUTHORIZE the card at checkout, run the
question through the existing chat tool-loop bounded by the ABS-305 cost
breaker, then **capture** the authorization on a grounded answer or
**void** it on a failed question (ungroundable / zero-evidence / cost
ceiling). The customer is charged only when we deliver.

It is deliberately independent of the case-credit ledger
(``advisor.db.cases``): one Stripe charge per question, no balance, no
packs. The same functions back both the production billing router and the
e2e ``/v1/_test/*`` harness, so the failed-question rule and the
refinement window are exercised through identical code in tests and prod.

Refinement window
-----------------
A purchase includes the answer PLUS a bounded set of follow-up
refinement turns scoped to that answer: up to ``MAX_REFINEMENTS``
follow-ups within a ``WINDOW_HOURS`` window. Each follow-up is another
tool-loop turn bounded by the same per-turn breaker, served free under
the original purchase. A follow-up that is materially a *different*
question (new address / use / determination) is detected and routed to a
new purchase rather than served free (``NewQuestionError``).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from advisor.billing.client import StripeClient
from advisor.billing.questions import Question, question_for
from advisor.billing.quote import OTHER_QUESTION_SLUG, Quote
from advisor.billing.settings import AdvisorBillingSettings
from advisor.db.cases import consume_free_question, refund_free_question
from advisor.db.jsonsafe import json_safe, scrub_text
from advisor.db.models import QuestionPurchase, User
from advisor.llm import LLMGateway, Message, TextBlock
from layer1.db.base import utcnow

logger = logging.getLogger(__name__)


# -- Refinement-window policy ------------------------------------------------
# A purchase buys the answer plus up to this many in-window refinement
# follow-ups (default 3), each scoped to the original answer and bounded
# by the ABS-305 per-turn cost breaker. The window also expires after
# WINDOW_HOURS so a purchase can't be refined indefinitely.
MAX_REFINEMENTS = 3
WINDOW_HOURS = 24

# -- Orphaned-generation rescue (ABS-354) ------------------------------------
# ABS-343 runs the answer engine as an in-memory FastAPI ``BackgroundTasks``
# job. If the server restarts (deploy, crash, dev reload) while a job is in
# flight the task is lost and the row is stranded in ``generating`` forever —
# the client polls to its 2-minute timeout and every refresh repeats the
# cycle. A ``generating`` row untouched for longer than this threshold is by
# definition orphaned (a real run finishes in tens of seconds), so a read
# force-settles it to ``failed`` and voids any Stripe hold. Comfortably longer
# than the client poll window so a genuinely in-flight run is never killed.
STALE_GENERATING_AFTER = timedelta(minutes=10)


# Tools whose successful (non-error) output grounds an answer in real
# bylaw evidence. A turn that produces NONE of these is a zero-evidence
# synthesis — a failed question under the failed-question rule. Pure
# navigation tools (list_documents / get_document_outline) are excluded:
# they don't, on their own, ground a determination.
GROUNDING_TOOLS = frozenset(
    {
        "search_bylaw_evidence",
        "get_address_profile",
        "get_zone_profile",
        "evaluate_submission_against_bylaws",
        "lookup_citation",
        "bylaw_query",
    }
)

# Marker embedded in the persona-gated new-question classification prompt
# so a mock gateway can resolve a deterministic verdict in tests.
FOLLOWUP_CLASSIFY_MARKER = "__FOLLOWUP_CLASSIFY__"


# -- Errors -----------------------------------------------------------------


class AnswerFlowError(Exception):
    """Base class for buy-an-answer errors. Each subclass maps to a
    specific HTTP status at the API edge."""


class UnknownQuestionError(AnswerFlowError):
    """Question slug isn't in the catalog. → HTTP 400."""


class MissingRequiredInputsError(AnswerFlowError):
    """A required input for the question wasn't supplied. This is the
    "unworkable inputs" failed-question case caught BEFORE payment, so no
    authorization is ever placed. → HTTP 400.

    ``missing`` lists the missing input names.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            "missing required input(s): " + ", ".join(missing)
        )


class QuestionPriceNotConfiguredError(AnswerFlowError):
    """The question has no Stripe Price ID configured on settings —
    operator misconfiguration, not a user error. → HTTP 503."""


class FreeQuestionsExhaustedError(AnswerFlowError):
    """Payments-off (ABS-322): the trial user has no free-question credit
    left to consume and Stripe payments are not enabled, so there is no
    way to unlock another answer yet. → HTTP 402 ("free trial used —
    paid answers coming soon"). The entry flow (ABS-320) renders this as
    the exhaustion state rather than a dead checkout button."""


class PurchaseNotAuthorizedError(AnswerFlowError):
    """The answer was requested before the card authorization landed (no
    ``checkout.session.completed`` webhook yet) or the purchase is in a
    non-runnable state. → HTTP 409."""


class NewQuestionError(AnswerFlowError):
    """A refinement follow-up is materially a different question and must
    be bought separately rather than served free. → HTTP 409.

    ``suggested_slug`` is the original purchase's question slug — the
    frontend routes the user to a fresh purchase of (typically) the same
    question type against the new subject.
    """

    def __init__(self, suggested_slug: str) -> None:
        self.suggested_slug = suggested_slug
        super().__init__(
            "follow-up is a new question; route to a new purchase"
        )


class RefinementNotAvailableError(AnswerFlowError):
    """Refinement requested on a purchase that hasn't produced a captured
    answer (still authorizing / voided / failed). → HTTP 409."""


class WindowExhaustedError(AnswerFlowError):
    """The refinement window is closed — either the follow-up budget is
    spent or the time window elapsed. → HTTP 409.

    ``reason`` is ``"refinements_exhausted"`` or ``"window_expired"``.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"refinement window closed: {reason}")


# -- Result DTO -------------------------------------------------------------


@dataclass(frozen=True)
class TurnOutcome:
    """Internal result of running one tool-loop turn for a question."""

    answer_text: str
    grounded: bool
    failure_reason: str | None
    terminated_reason: str
    messages: list[Message]


# -- Checkout ---------------------------------------------------------------


def stripe_price_id_for(
    question: Question, settings: AdvisorBillingSettings
) -> str | None:
    """The configured Stripe Price ID for a question, or ``None``."""
    return getattr(settings, question.stripe_price_env_var.lower(), None)


def validate_inputs(question: Question, inputs: dict) -> dict:
    """Return the cleaned input dict or raise ``MissingRequiredInputsError``.

    Missing *required* inputs are the "unworkable inputs" failed-question
    case — caught here, before any card authorization, so the customer is
    never charged for a question we can't run. Optional inputs are
    defaulted to an empty string so ``prompt_template.format`` never
    raises on an absent ``{placeholder}``.
    """
    cleaned: dict = {}
    missing: list[str] = []
    for field in question.required_inputs:
        raw = inputs.get(field.name)
        value = "" if raw is None else str(raw).strip()
        if field.required and not value:
            missing.append(field.name)
        cleaned[field.name] = value
    if missing:
        raise MissingRequiredInputsError(missing)
    return cleaned


def start_question_checkout(
    db: Session,
    user: User,
    *,
    question_slug: str,
    inputs: dict,
    client: StripeClient,
    settings: AdvisorBillingSettings,
) -> tuple[QuestionPurchase, str]:
    """Create a manual-capture checkout session for one catalog question.

    Inserts a ``QuestionPurchase`` row in ``authorizing`` state and
    returns it alongside the Stripe Checkout URL. No charge happens here:
    the card is only authorized when the user completes checkout (the
    ``checkout.session.completed`` webhook flips the row to
    ``authorized``). Raises before creating any Stripe object if the
    question is unknown, its price isn't configured, or a required input
    is missing.
    """
    try:
        question = question_for(question_slug)
    except KeyError as exc:
        raise UnknownQuestionError(f"unknown question {question_slug!r}") from exc

    cleaned = validate_inputs(question, inputs)

    price_id = stripe_price_id_for(question, settings)
    if not price_id:
        raise QuestionPriceNotConfiguredError(
            f"no Stripe Price ID configured for question {question_slug!r}; "
            f"set {question.stripe_price_env_var} in the environment."
        )

    purchase = QuestionPurchase(
        user_id=user.id,
        question_slug=question.slug,
        inputs_json=dict(cleaned),
        price_cents=question.price_cents,
        currency="CAD",
        status="authorizing",
    )
    db.add(purchase)
    db.flush()  # assign purchase.id for the metadata round-trip

    metadata = {
        "advisor_user_id": str(user.id),
        "question_purchase_id": str(purchase.id),
        "question_slug": question.slug,
    }
    result = client.create_checkout_session(
        customer_id=user.stripe_customer_id,
        customer_email=user.email,
        price_id=price_id,
        success_url=settings.success_url,
        cancel_url=settings.cancel_url,
        metadata=metadata,
        mode="payment",
        capture_method="manual",
    )
    purchase.stripe_checkout_session_id = result.session_id
    db.flush()
    return purchase, result.url


# Marker stored on ``QuestionPurchase.metadata_json`` for a payments-off
# (ABS-322) purchase: the answer was unlocked by consuming a free-question
# credit, not a Stripe charge. ``run_answer`` / ``_void`` branch on this to
# skip Stripe capture/void and instead refund the free credit on failure.
PAYMENTS_OFF_KEY = "payments_off"


def _is_free_purchase(purchase: QuestionPurchase) -> bool:
    """True when the purchase was unlocked by a free-question credit
    (payments-off / ABS-322) rather than a Stripe authorization."""
    return bool((purchase.metadata_json or {}).get(PAYMENTS_OFF_KEY))


def start_question_free(
    db: Session,
    user: User,
    *,
    question_slug: str,
    inputs: dict,
) -> QuestionPurchase:
    """Open an answerable catalog-question purchase by consuming one free
    credit — the payments-off (ABS-322) equivalent of
    checkout→webhook→authorize, with NO Stripe.

    The principle: an answer is unlocked by consuming a question-credit,
    not by a payment. This reserves one free-question entitlement
    (ABS-314) and inserts a ``QuestionPurchase`` straight into
    ``authorized`` state — ready for ``run_answer`` — without ever
    creating a Stripe object or Price-ID lookup.

    Order of operations honours the failed-question rule: unworkable
    (missing required) inputs raise BEFORE any credit is reserved, so a
    question we can't even run never costs the user a credit. If the
    answer later turns out ungroundable / over the ABS-305 cost cap,
    ``run_answer`` refunds the reserved credit (the void equivalent).

    Raises ``UnknownQuestionError`` (bad slug),
    ``MissingRequiredInputsError`` (unworkable inputs, no credit
    reserved), or ``FreeQuestionsExhaustedError`` (no free credit left —
    the trial is used up).
    """
    try:
        question = question_for(question_slug)
    except KeyError as exc:
        raise UnknownQuestionError(
            f"unknown question {question_slug!r}"
        ) from exc

    cleaned = validate_inputs(question, inputs)

    # Reserve the credit only after inputs validate. consume_free_question
    # is a FOR UPDATE decrement; False means the counter is already 0.
    if not consume_free_question(db, user=user):
        raise FreeQuestionsExhaustedError(
            "no free-question credits remaining and payments are disabled"
        )

    purchase = QuestionPurchase(
        user_id=user.id,
        question_slug=question.slug,
        inputs_json=dict(cleaned),
        price_cents=question.price_cents,
        currency="CAD",
        # No Stripe authorize step exists in payments-off mode, so the
        # purchase is born ``authorized`` (credit already reserved) and is
        # immediately runnable. No stripe_* ids are ever set.
        status="authorized",
        authorized_at=utcnow(),
        metadata_json={PAYMENTS_OFF_KEY: True},
    )
    db.add(purchase)
    db.flush()
    return purchase


def start_other_checkout(
    db: Session,
    user: User,
    *,
    quote: Quote,
    client: StripeClient,
    settings: AdvisorBillingSettings,
) -> tuple[QuestionPurchase, str]:
    """Create a manual-capture checkout for an off-menu "Other" question.

    The off-menu path (ABS-316) has no pre-created Stripe Price — the
    amount is the LLM-quoted price — so this uses an inline ``price_data``
    amount instead of a ``price_id``. Otherwise identical to
    ``start_question_checkout``: inserts a ``QuestionPurchase`` in
    ``authorizing`` state under the sentinel ``other`` slug, with the
    free-form question in ``inputs_json`` and the quote (difficulty,
    rationale, and the ABS-305 cost envelope the answer will run under) in
    ``metadata_json``. No charge happens here — the card is only
    authorized when the user completes checkout.

    The price is bound from the server-side ``quote``; callers must NOT
    accept a client-supplied price (the user could otherwise pick their
    own). Re-quote on the server before calling this.
    """
    purchase = QuestionPurchase(
        user_id=user.id,
        question_slug=OTHER_QUESTION_SLUG,
        inputs_json={"question": quote.question_text},
        price_cents=quote.price_cents,
        currency=quote.currency,
        status="authorizing",
        metadata_json={
            "difficulty": quote.difficulty,
            "difficulty_display_name": quote.difficulty_display_name,
            "rationale": quote.rationale,
            "cumulative_token_budget": quote.cumulative_token_budget,
            "anchor": quote.anchor,
        },
    )
    db.add(purchase)
    db.flush()  # assign purchase.id for the metadata round-trip

    metadata = {
        "advisor_user_id": str(user.id),
        "question_purchase_id": str(purchase.id),
        "question_slug": OTHER_QUESTION_SLUG,
    }
    result = client.create_checkout_session(
        customer_id=user.stripe_customer_id,
        customer_email=user.email,
        success_url=settings.success_url,
        cancel_url=settings.cancel_url,
        metadata=metadata,
        mode="payment",
        capture_method="manual",
        amount_cents=quote.price_cents,
        product_name="Bylaw answer — custom question",
        currency=quote.currency.lower(),
    )
    purchase.stripe_checkout_session_id = result.session_id
    db.flush()
    return purchase, result.url


def apply_question_authorization(
    db: Session,
    *,
    purchase_id: int,
    payment_intent_id: str | None,
    checkout_session_id: str | None,
) -> QuestionPurchase | None:
    """Mark a question purchase ``authorized`` from a completed checkout.

    Called by the webhook handler when a ``checkout.session.completed``
    event carries a ``question_purchase_id``. Idempotent: a second
    delivery (Stripe retries aggressively) is a no-op once the row has
    advanced past ``authorizing``. Returns the purchase, or ``None`` if
    the id doesn't resolve.
    """
    purchase = db.get(QuestionPurchase, purchase_id)
    if purchase is None:
        logger.warning(
            "question authorization for unknown purchase id %s", purchase_id
        )
        return None
    if purchase.status != "authorizing":
        # Already authorized (or further along) — idempotent no-op.
        return purchase
    if payment_intent_id:
        purchase.stripe_payment_intent_id = payment_intent_id
    if checkout_session_id and not purchase.stripe_checkout_session_id:
        purchase.stripe_checkout_session_id = checkout_session_id
    purchase.status = "authorized"
    purchase.authorized_at = utcnow()
    db.flush()
    return purchase


# -- Running the answer ------------------------------------------------------


def _default_model() -> str:
    from advisor.llm.registry import get_settings  # noqa: PLC0415

    return get_settings().main_model


def _answer_text(messages: list[Message]) -> str:
    """Concatenate the text of the final assistant turn."""
    for message in reversed(messages):
        if message.role.value == "assistant" and isinstance(message.content, list):
            return "".join(
                b.text for b in message.content if isinstance(b, TextBlock)
            ).strip()
    return ""


def _serialize(messages: list[Message]) -> list:
    return [json_safe(m.model_dump(mode="json")) for m in messages]


def _deserialize(raw: list) -> list[Message]:
    return [Message.model_validate(d) for d in (raw or [])]


async def run_turn(
    *,
    gateway: LLMGateway,
    persona: str,
    retrieval_factory,
    user_text: str,
    prior_messages: list[Message] | None = None,
    model: str | None = None,
    cumulative_token_budget: int | None = None,
) -> TurnOutcome:
    """Run one bounded tool-loop turn and classify the result.

    Builds a standalone ``ChatSession`` (no case-credit entanglement),
    sends ``user_text``, and reads the per-turn metrics to decide whether
    the answer is grounded. The ABS-305 cumulative cost breaker is active
    by default on the session, so a runaway turn is forced to synthesize
    and reported as ``cost_ceiling``.

    ``cumulative_token_budget`` overrides the session's default ABS-305
    ceiling. The off-menu "Other" flow (ABS-316) passes the quote's
    envelope here so a pricier (harder) question is allowed proportionally
    more reasoning while the served cost still cannot exceed the quoted
    band. ``None`` keeps the env-configured default (catalog questions).
    """
    # Imported lazily to keep the billing package importable without the
    # chat layer present (mirrors the lazy Stripe-SDK pattern).
    from advisor.chat.session import ChatSession  # noqa: PLC0415
    from advisor.chat.tools import build_bylaw_tools  # noqa: PLC0415

    tool_defs, tool_handlers = build_bylaw_tools(retrieval_factory)
    session_kwargs: dict = {}
    if cumulative_token_budget is not None:
        session_kwargs["cumulative_token_budget"] = cumulative_token_budget
    session = ChatSession(
        session_id=f"qp-{uuid.uuid4().hex[:12]}",
        user_id="question-purchase",
        system_prompt=persona,
        tool_defs=tool_defs,
        tool_handlers=tool_handlers,
        model=model or _default_model(),
        **session_kwargs,
    )
    session.messages = list(prior_messages or [])
    await session.send_user_message_blocking(gateway, user_text)

    grounded, reason = _classify(session.last_tool_loop_metrics)
    terminated = (
        session.last_tool_loop_metrics.terminated_reason
        if session.last_tool_loop_metrics is not None
        else "unknown"
    )
    return TurnOutcome(
        answer_text=_answer_text(session.messages),
        grounded=grounded,
        failure_reason=reason,
        terminated_reason=terminated,
        messages=list(session.messages),
    )


def _classify(metrics) -> tuple[bool, str | None]:
    """Decide grounded-vs-failed from the tool-loop metrics.

    Failed (void the hold) when:
    * the cost breaker tripped — a runaway / ungroundable turn, or
    * no grounding tool produced a successful result — a zero-evidence
      synthesis.

    Otherwise grounded (capture the hold).
    """
    if metrics is None:
        return False, "internal_error"
    if metrics.terminated_reason in {"cost_circuit_trip", "cumulative_cost_trip"}:
        return False, "cost_ceiling"
    grounded_calls = [
        t
        for t in metrics.tool_calls
        if not t.is_error and t.name in GROUNDING_TOOLS
    ]
    if not grounded_calls:
        return False, "zero_evidence"
    return True, None


def _resolve_run_inputs(purchase: QuestionPurchase) -> tuple[str, int | None]:
    """Return ``(prompt, cumulative_token_budget)`` for a purchase.

    Catalog questions render their ``prompt_template`` from the collected
    inputs and run under the session's default ABS-305 ceiling
    (``None``). The off-menu "Other" path (ABS-316) runs the user's raw
    free-form question text under the quote's envelope, persisted on
    ``metadata_json`` at checkout.
    """
    if purchase.question_slug == OTHER_QUESTION_SLUG:
        question_text = str(
            (purchase.inputs_json or {}).get("question", "")
        ).strip()
        budget = (purchase.metadata_json or {}).get("cumulative_token_budget")
        return question_text, (int(budget) if budget else None)
    question = question_for(purchase.question_slug)
    return render_prompt(question, purchase.inputs_json), None


def _relax_idle_timeout(db: Session) -> None:
    """Opt *this request's* transaction out of Postgres'
    ``idle_in_transaction_session_timeout`` for the duration of the long
    LLM turn.

    INTERIM (ABS-339) — REMOVE when ABS-338 lands. The buy-an-answer
    request holds one DB transaction open across the ~84s LLM turn while
    that transaction is idle; the global cap
    (``docker-compose.yml`` ``idle_in_transaction_session_timeout``, ABS-100)
    terminates the idle connection at 60s, so the settling ``UPDATE``
    raises ``psycopg.errors.IdleInTransactionSessionTimeout`` → a 500.

    ``SET LOCAL`` is *transaction-scoped*: it resets at commit/rollback, so
    this opt-out can never leak to a pooled connection reused by another
    request. The global cap (and the wedged-connection / held-lock
    protection ABS-100 added) therefore stays in force for every other
    connection — this is the scoped, responsible unblock, not the blunt
    global override.

    No-op on non-Postgres backends (sqlite unit tests have no such GUC).
    ``db.execute`` autobegins a transaction if one isn't open yet, so the
    ``SET LOCAL`` always lands inside a transaction.
    """
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SET LOCAL idle_in_transaction_session_timeout = 0"))


async def run_answer(
    db: Session,
    purchase: QuestionPurchase,
    *,
    gateway: LLMGateway,
    persona: str,
    retrieval_factory,
    client: StripeClient | None = None,
    model: str | None = None,
) -> QuestionPurchase:
    """Run the purchased question and settle the authorization.

    Two settlement modes share one code path (the failed-question rule is
    identical in both):

    * **Stripe (payments-on):** capture the card hold on a grounded
      answer; void it on a failed question. Requires ``client``.
    * **Free credit (payments-off / ABS-322):** the credit was already
      reserved at ``start_question_free``; a grounded answer simply
      confirms the consumption (nothing to capture), and a failed answer
      refunds the credit (the void equivalent). ``client`` may be ``None``.

    Idempotent: a purchase already settled
    (``captured``/``voided``/``failed``) is returned unchanged. Raises
    ``PurchaseNotAuthorizedError`` if the card hasn't been authorized
    yet.
    """
    if purchase.status in {"captured", "voided", "failed"}:
        return purchase
    # ABS-343: the answer run is dispatched as a background job that first
    # flips the purchase to ``generating``; accept that (in-flight) state as
    # runnable alongside the synchronous ``authorized`` entry.
    if purchase.status not in {"authorized", "generating"}:
        raise PurchaseNotAuthorizedError(
            f"purchase {purchase.id} is {purchase.status!r}, not authorized"
        )

    # ABS-339 (interim; remove under ABS-338): this transaction stays open
    # and idle across the long LLM turn below — opt it out of the global
    # idle-in-transaction cap so it isn't killed mid-flight.
    _relax_idle_timeout(db)

    prompt, cumulative_budget = _resolve_run_inputs(purchase)
    purchase.answered_at = utcnow()

    try:
        outcome = await run_turn(
            gateway=gateway,
            persona=persona,
            retrieval_factory=retrieval_factory,
            user_text=prompt,
            model=model,
            cumulative_token_budget=cumulative_budget,
        )
    except Exception:  # noqa: BLE001 — never leave a hold uncaptured
        logger.exception(
            "error running answer for purchase %s; voiding authorization",
            purchase.id,
        )
        _void(db, client, purchase, reason="internal_error", status="failed")
        db.flush()
        return purchase

    purchase.transcript_json = _serialize(outcome.messages)
    if outcome.grounded:
        # Payments-off: the free credit was already consumed at checkout,
        # so there is nothing to capture — the grounded answer simply
        # confirms the consumption. Stripe path: capture the card hold.
        if not _is_free_purchase(purchase):
            client.capture_payment_intent(purchase.stripe_payment_intent_id)
        purchase.status = "captured"
        purchase.answer_text = scrub_text(outcome.answer_text)
        purchase.window_expires_at = utcnow() + timedelta(hours=WINDOW_HOURS)
        purchase.settled_at = utcnow()
    else:
        _void(
            db, client, purchase, reason=outcome.failure_reason, status="voided"
        )
    db.flush()
    return purchase


def _void(
    db: Session,
    client: StripeClient | None,
    purchase: QuestionPurchase,
    *,
    reason: str | None,
    status: str,
) -> None:
    """Settle a failed question without charging the customer.

    Payments-off (ABS-322): refund the reserved free-question credit —
    the failed-question rule says an ungroundable / over-cap question
    must not consume the trial credit. Stripe path: release the card
    authorization.
    """
    if _is_free_purchase(purchase):
        try:
            refund_free_question(db, user=purchase.user)
        except Exception:  # noqa: BLE001 — log, don't mask the failure state
            logger.exception(
                "failed to refund free question for purchase %s", purchase.id
            )
    elif purchase.stripe_payment_intent_id and client is not None:
        try:
            client.cancel_payment_intent(purchase.stripe_payment_intent_id)
        except Exception:  # noqa: BLE001 — log, don't mask the failure state
            logger.exception(
                "failed to void PaymentIntent %s for purchase %s",
                purchase.stripe_payment_intent_id,
                purchase.id,
            )
    purchase.status = status
    purchase.failure_reason = reason or "unknown"
    purchase.settled_at = utcnow()


def settle_stale_generating(
    db: Session,
    purchase: QuestionPurchase,
    *,
    client: StripeClient | None,
    now: datetime | None = None,
) -> bool:
    """Rescue a purchase wedged in ``generating`` past the staleness cutoff.

    ABS-354: ``BackgroundTasks`` are in-memory (ABS-343). A server restart
    mid-run orphans the job and leaves the row ``generating`` forever, so the
    six-step screen polls to its 2-minute timeout and every refresh repeats.
    When a ``generating`` row has not been touched for longer than
    ``STALE_GENERATING_AFTER`` its job is orphaned by definition; void any
    Stripe hold (or refund the free credit) and flip it to
    ``failed``/``internal_error`` so the client settles to the "wasn't
    charged" error state and the user can retry.

    Idempotent and cheap: a non-``generating`` or still-fresh row is left
    untouched and returns ``False``. ``now`` is injectable for tests. The
    caller owns the commit.
    """
    if purchase.status != "generating":
        return False
    updated_at = purchase.updated_at
    if updated_at is None:
        return False
    # Postgres stores tz-aware timestamps; sqlite (unit tests) hands them back
    # naive — normalize to UTC so the comparison never raises.
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    reference = now or utcnow()
    if reference - updated_at < STALE_GENERATING_AFTER:
        return False
    logger.warning(
        "purchase %s wedged in generating since %s; force-settling to failed",
        purchase.id,
        updated_at,
    )
    _void(db, client, purchase, reason="internal_error", status="failed")
    db.flush()
    return True


async def run_refinement(
    db: Session,
    purchase: QuestionPurchase,
    *,
    message: str,
    gateway: LLMGateway,
    persona: str,
    retrieval_factory,
    model: str | None = None,
) -> str:
    """Serve one in-window refinement follow-up; return the refined answer.

    Enforces the refinement window (captured purchase, follow-up budget,
    time window) and the new-question rule before running the turn. No
    additional charge: refinements are served free under the original
    purchase. Raises ``RefinementNotAvailableError`` /
    ``WindowExhaustedError`` / ``NewQuestionError`` as appropriate.
    """
    if purchase.status != "captured":
        raise RefinementNotAvailableError(
            f"purchase {purchase.id} has no captured answer to refine"
        )
    now = utcnow()
    expires = _as_aware_utc(purchase.window_expires_at)
    if expires is not None and now >= expires:
        raise WindowExhaustedError("window_expired")
    if purchase.refinement_count >= MAX_REFINEMENTS:
        raise WindowExhaustedError("refinements_exhausted")

    # ABS-339 (interim; remove under ABS-338): the refinement turn (and the
    # LLM new-question gate below) holds this transaction open and idle —
    # opt it out of the global idle-in-transaction cap before either runs.
    _relax_idle_timeout(db)

    # New-question gate: programmatic first (cheap, deterministic), then
    # an optional persona-gated LLM check for ambiguous follow-ups.
    if is_new_question_programmatic(purchase, message):
        raise NewQuestionError(purchase.question_slug)
    if _is_ambiguous_followup(message) and await llm_is_new_question(
        gateway, purchase, message
    ):
        raise NewQuestionError(purchase.question_slug)

    prior = _deserialize(purchase.transcript_json)
    _, cumulative_budget = _resolve_run_inputs(purchase)
    outcome = await run_turn(
        gateway=gateway,
        persona=persona,
        retrieval_factory=retrieval_factory,
        user_text=message,
        prior_messages=prior,
        model=model,
        cumulative_token_budget=cumulative_budget,
    )
    purchase.transcript_json = _serialize(outcome.messages)
    purchase.refinement_count += 1
    purchase.answer_text = scrub_text(outcome.answer_text)
    db.flush()
    return outcome.answer_text


def refinements_remaining(purchase: QuestionPurchase) -> int:
    """Follow-ups still available on this purchase (never negative)."""
    return max(0, MAX_REFINEMENTS - (purchase.refinement_count or 0))


def _as_aware_utc(dt: datetime | None) -> datetime | None:
    """Normalize a stored datetime to tz-aware UTC.

    Postgres returns tz-aware datetimes; sqlite (unit tests) drops the
    tzinfo on read. Coercing here keeps the window comparison correct on
    both backends.
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# -- New-question detection --------------------------------------------------


_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.\-' ]*?"
    r"(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|"
    r"lane|ln|crescent|cres|court|ct|place|pl|way|terrace|terr|"
    r"highway|hwy|circle|cir)\b",
    re.IGNORECASE,
)

# Strong phrases that signal the follow-up is about a *different* subject
# rather than a refinement of the existing answer. Kept conservative so
# legitimate reformat/clarify requests ("instead of bullets, use a
# table") are not misclassified.
_NEW_SUBJECT_PHRASES = (
    "different property",
    "another property",
    "different address",
    "another address",
    "new address",
    "different parcel",
    "another parcel",
    "different lot",
    "separate question",
    "different question",
    "a new question",
)


def _normalize_address(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _extract_addresses(text: str) -> list[str]:
    return [m.group(0) for m in _ADDRESS_RE.finditer(text or "")]


def is_new_question_programmatic(
    purchase: QuestionPurchase, follow_up: str
) -> bool:
    """Programmatic new-question signal: a civic address in the follow-up
    that differs from the purchased question's address, or a strong
    different-subject phrase. Deterministic and free."""
    original = _normalize_address(str(purchase.inputs_json.get("address", "")))
    for addr in _extract_addresses(follow_up):
        norm = _normalize_address(addr)
        if norm and norm != original:
            return True
    lowered = (follow_up or "").lower()
    return any(phrase in lowered for phrase in _NEW_SUBJECT_PHRASES)


_QUESTION_SHAPE_RE = re.compile(
    r"\?|\b(can i|could i|is it|are there|what about|how about|"
    r"would i be able|am i allowed|do i need)\b",
    re.IGNORECASE,
)


def _is_ambiguous_followup(message: str) -> bool:
    """True when a follow-up looks like a fresh question (interrogative
    shape) yet carries no detectable address — the case where the cheap
    programmatic check is inconclusive and the persona-gated LLM layer
    earns its keep."""
    return bool(_QUESTION_SHAPE_RE.search(message or ""))


async def llm_is_new_question(
    gateway: LLMGateway,
    purchase: QuestionPurchase,
    follow_up: str,
    *,
    model: str | None = None,
) -> bool:
    """Persona-gated LLM check: is this follow-up a new question?

    A small tool-less classification call. Shares intent with the richer
    intake logic in ABS-315/316; here it is the second layer behind the
    programmatic gate. Fails OPEN to "refinement" (returns False) on any
    parse/transport error so a transient classifier hiccup never wrongly
    blocks a paid customer's refinement.
    """
    import json  # noqa: PLC0415

    from advisor.llm import CompletionRequest  # noqa: PLC0415

    # Off-menu "Other" purchases (ABS-316) have no catalog entry; fall
    # back to a generic label and the free-form question as the subject.
    try:
        display_name = question_for(purchase.question_slug).display_name
    except KeyError:
        display_name = "your custom question"
    original_subject = purchase.inputs_json.get("address") or purchase.inputs_json.get(
        "question", "(unspecified)"
    )
    prompt = (
        f"{FOLLOWUP_CLASSIFY_MARKER}\n"
        "You are gating a paid answer's refinement window. The customer "
        f"bought: {display_name} about {original_subject!r}.\n"
        f"Their follow-up: {follow_up!r}\n\n"
        "Is this follow-up a REFINEMENT of the existing answer (reformat, "
        "clarify, expand a point already covered) or a materially NEW "
        "question (different property, use, or determination) that should "
        "be purchased separately? Respond with ONLY compact JSON: "
        '{"new_question": true} or {"new_question": false}.'
    )
    request = CompletionRequest(
        model=model or _default_model(),
        system="You classify follow-up questions. Output only JSON.",
        messages=[Message(role="user", content=prompt)],
    )
    try:
        response = await gateway.complete(request)
        text = "".join(
            b.text for b in response.content if isinstance(b, TextBlock)
        )
        verdict = json.loads(text)
        return bool(verdict.get("new_question", False))
    except Exception:  # noqa: BLE001 — fail open to "refinement"
        logger.warning(
            "new-question LLM gate failed to classify; defaulting to "
            "refinement",
            exc_info=True,
        )
        return False


def render_prompt(question: Question, inputs: dict) -> str:
    """Fill the question's prompt template from the collected inputs.

    Optional inputs absent from ``inputs`` are rendered as
    ``"(not provided)"`` so the template never leaves a raw placeholder
    or raises ``KeyError``.
    """
    values = {f.name: "(not provided)" for f in question.required_inputs}
    for key, value in (inputs or {}).items():
        text = str(value).strip()
        values[key] = text if text else "(not provided)"
    return question.prompt_template.format(**values)
