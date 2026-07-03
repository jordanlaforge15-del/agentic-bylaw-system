"""FastAPI router for the billing endpoints — case-credit model.

Five endpoints replace the v1 subscription-style trio:

* ``GET /v1/billing/catalog`` — auth-required. Returns the 12-SKU
  matrix (tier × pack) with prices and which SKUs have a Stripe Price
  ID configured. The pricing page renders this.
* ``POST /v1/billing/checkout/pack`` — auth-required. Creates a Stripe
  Checkout session for one (tier, pack) combination and returns its
  URL.
* ``POST /v1/billing/webhook`` — no auth; verified via
  ``Stripe-Signature``. Applies the event to the database (inserts
  per-credit rows on ``checkout.session.completed``).
* ``GET /v1/billing/me`` — auth-required. Returns the user's credit
  balance grouped by tier, plus their stripe_customer_id and the
  enabled flag.
* ``GET /v1/billing/purchases`` — auth-required. Returns the user's
  purchase history newest-first.

Every endpoint short-circuits to HTTP 503 when ``settings.enabled`` is
False — same dormant-by-default safety as v1.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from advisor.billing import answers as answer_flow
from advisor.billing.answers import (
    FreeQuestionsExhaustedError,
    MissingRequiredInputsError,
    NewQuestionError,
    PurchaseNotAuthorizedError,
    QuestionPriceNotConfiguredError,
    RefinementNotAvailableError,
    UnknownQuestionError,
    WindowExhaustedError,
)
from advisor.billing.report import build_report
from advisor.billing.checkout import (
    PriceNotConfiguredError,
    UnknownOfferError,
    start_pack_checkout,
)
from advisor.billing.client import StripeClient
from advisor.billing.intake import detect_intake
from advisor.billing.packs import all_offers
from advisor.billing.pricing import get_pricing_settings
from advisor.billing.questions import all_questions, question_for
from advisor.billing.quote import EmptyQuestionError, quote_question
from advisor.billing.settings import AdvisorBillingSettings
from advisor.billing.webhooks import handle_event
# ABS-324: this Answers-product router must NOT import the Conversation /
# Case ledger helpers (``open_case_free`` / ``reserve_credit_for_session``).
# The free-question "buy an answer" path consumes ONLY its own
# entitlement, via ``advisor.billing.answers.start_question_free``. Only the
# read-only ``credit_balance_for`` (for the shared ``GET /me`` balance view)
# is borrowed. The import-boundary guard test pins this.
from advisor.db.cases import credit_balance_for
from advisor.db.models import CasePurchase, QuestionPurchase, User

logger = logging.getLogger(__name__)


# -- Request / response models ---------------------------------------------


class CheckoutPackRequest(BaseModel):
    """Body of ``POST /v1/billing/checkout/pack``."""

    tier: str = Field(
        ..., description="Case tier identifier (quick / standard / complex)."
    )
    pack_sku: str = Field(
        ...,
        description="Pack identifier (payg / starter / pro / enterprise).",
    )


class CheckoutResponse(BaseModel):
    url: str = Field(..., description="Stripe Checkout redirect URL.")


class CatalogOffer(BaseModel):
    """One (tier, pack) offer for the public pricing page."""

    tier: str
    tier_display_name: str
    tier_token_budget: int
    pack_sku: str
    pack_display_name: str
    quantity: int
    discount_bps: int
    list_price_cents: int
    amount_due_cents: int
    currency: str = "CAD"
    available: bool = Field(
        ...,
        description=(
            "True iff the Stripe Price ID for this offer is configured. "
            "Disabled offers render as 'coming soon' on the pricing page."
        ),
    )


class CatalogResponse(BaseModel):
    """Body of ``GET /v1/billing/catalog``."""

    enabled: bool
    currency: str = "CAD"
    cad_per_usd: float = Field(
        ...,
        description=(
            "FX rate for displaying USD equivalents on marketing pages "
            "targeted at US audiences. CAD is authoritative."
        ),
    )
    offers: list[CatalogOffer]


class QuestionInputField(BaseModel):
    """One required-or-optional input for a catalog question."""

    name: str
    label: str
    required: bool
    description: str


class QuestionMenuItem(BaseModel):
    """One priced question on the launch menu."""

    slug: str
    display_name: str
    price_cents: int
    currency: str = "CAD"
    summary: str
    backing_calls: list[str]
    required_inputs: list[QuestionInputField]
    catalog_anchor: str
    available: bool = Field(
        ...,
        description=(
            "True iff the Stripe Price ID for this question is configured "
            "and billing is enabled. Disabled questions render as 'coming "
            "soon' on the menu."
        ),
    )


class QuestionMenuResponse(BaseModel):
    """Body of ``GET /v1/billing/questions`` — the priced-question menu."""

    enabled: bool
    payments_enabled: bool = Field(
        default=False,
        description=(
            "ABS-322: when False the buy-an-answer flow consumes a "
            "free-question credit (no Stripe); when True it runs the "
            "Stripe authorize→capture path. The entry flow uses this to "
            "decide between a free-trial CTA and a paid checkout."
        ),
    )
    conversation_enabled: bool = Field(
        default=False,
        description=(
            "ABS-324 launch posture: when False the in-app /cases/new entry "
            "surfaces ONLY the Answers question menu. The Conversation "
            "product (turn-based /app chat reached by continuing an existing "
            "case) stays intact in the codebase but is hidden from this "
            "primary entry until ADVISOR_CONVERSATION_ENTRY_ENABLED is "
            "flipped true. The frontend uses this to gate the "
            "continue-existing-case CTA."
        ),
    )
    currency: str = "CAD"
    cad_per_usd: float = Field(
        ...,
        description=(
            "FX rate for displaying USD equivalents on marketing pages. "
            "CAD is authoritative."
        ),
    )
    questions: list[QuestionMenuItem]


class CheckoutQuestionRequest(BaseModel):
    """Body of ``POST /v1/billing/checkout/question``."""

    question_slug: str = Field(
        ..., description="Catalog question slug (see /v1/billing/questions)."
    )
    inputs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Collected required-input values keyed by input name "
            "(address, proposed_use, …)."
        ),
    )


class QuestionCheckoutResponse(BaseModel):
    url: str | None = Field(
        default=None,
        description=(
            "Stripe Checkout redirect URL. NULL in payments-off mode "
            "(ABS-322): no checkout session is created — the purchase is "
            "already authorized via a free-question credit and the client "
            "goes straight to POST .../answer."
        ),
    )
    purchase_id: int = Field(
        ..., description="Id of the pending QuestionPurchase row."
    )
    status: str = Field(
        default="authorizing",
        description=(
            "Purchase status. 'authorizing' awaits the Stripe webhook; "
            "'authorized' (payments-off) is immediately runnable."
        ),
    )
    payments_enabled: bool = Field(
        default=True,
        description="False when this purchase was unlocked by a free credit.",
    )


class QuoteRequest(BaseModel):
    """Body of ``POST /v1/billing/questions/quote`` (ABS-316)."""

    question: str = Field(
        ...,
        min_length=1,
        description="The free-form, off-menu question to price.",
    )


class QuoteResponse(BaseModel):
    """A FREE off-menu price quote (ABS-316).

    Producing this never charges the customer. ``price_cents`` is the
    anchored price they would pay to buy the answer; ``difficulty`` /
    ``rationale`` explain the price; the ``band_*`` fields give the
    launch-menu envelope for context ("within the $79–$299 band").
    """

    question: str
    difficulty: str
    difficulty_display_name: str
    price_cents: int
    currency: str
    rationale: str
    band_low_cents: int
    band_high_cents: int


class IntakeRequest(BaseModel):
    """Body of ``POST /v1/billing/questions/intake`` (ABS-315).

    The consultant-style intake step that runs BEFORE checkout. Carries
    the selected catalog question, whatever the user has said so far
    (free-form), and any inputs already collected in earlier intake turns.
    """

    question_slug: str = Field(
        ..., description="Catalog question slug (see /v1/billing/questions)."
    )
    conversation: str = Field(
        default="",
        description=(
            "The user's free-form description so far — the LLM extracts "
            "the question's required inputs from this."
        ),
    )
    inputs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Inputs already confirmed in earlier intake turns, keyed by "
            "input name. These take precedence over freshly-extracted "
            "values."
        ),
    )


class IntakeResponse(BaseModel):
    """Result of one consultant-style intake pass (ABS-315).

    Producing this is FREE (a single tools-less LLM extraction; no charge,
    no Stripe object). When ``complete`` is True the merged ``inputs`` are
    ready to hand to ``POST /v1/billing/checkout/question``; otherwise
    ``prompt`` is the consultant's follow-up asking for ``missing_required``.
    """

    question_slug: str
    complete: bool
    inputs: dict[str, str]
    missing_required: list[str]
    missing_optional: list[str]
    prompt: str


class CheckoutOtherRequest(BaseModel):
    """Body of ``POST /v1/billing/checkout/other`` (ABS-316)."""

    question: str = Field(
        ...,
        min_length=1,
        description="The free-form, off-menu question to buy an answer to.",
    )


class OtherCheckoutResponse(BaseModel):
    """Checkout for an off-menu question: URL + the server-bound quote."""

    url: str = Field(..., description="Stripe Checkout redirect URL.")
    purchase_id: int = Field(
        ..., description="Id of the pending QuestionPurchase row."
    )
    price_cents: int = Field(
        ..., description="The server-quoted price the card will hold."
    )
    currency: str = "CAD"
    difficulty: str
    rationale: str


class RefineRequest(BaseModel):
    """Body of ``POST /v1/billing/questions/purchases/{id}/refine``."""

    message: str = Field(
        ..., min_length=1, description="The refinement follow-up text."
    )


class ReportVerdict(BaseModel):
    """Determination band — ``status`` drives the client's ``statusInfo()``
    color/tag (``pass`` → accent; ``fail``/``conditional``/``attention`` →
    brick); ``label`` is the verdict headline."""

    status: str
    label: str


class ReportContent(BaseModel):
    """Structured report deliverable (ABS-342).

    The typed schema the ``ReportDocument`` client template renders: a
    letterhead + title + meta grid + determination band + summary + a typed
    ``blocks`` array + a verification footer. ``blocks`` is an
    intentionally-loose list of block dicts (``keyvals | uses | finding |
    table | flags | prose``) discriminated by each block's ``type`` field
    and rendered by one shared switch — no per-report layout code. See
    ``advisor.billing.report`` for the mapper.
    """

    ref: str
    report_type: str
    address: str
    zone_subtitle: str
    issued: str
    prepared_for: str
    bylaw_version: str
    price_cents: int
    currency: str
    verdict: ReportVerdict
    summary: str
    blocks: list[dict]
    footer: str


class QuestionPurchaseResponse(BaseModel):
    """State of a priced-question purchase + its answer.

    ``answer`` is the raw engine markdown (retained for back-compat and as
    the client's defensive fallback). ``report`` is the ABS-342 structured
    deliverable — present only on a ``captured`` purchase — that the
    ``ReportDocument`` template renders in place of the raw markdown."""

    id: int
    question_slug: str
    status: str
    price_cents: int
    currency: str
    answer: str | None = None
    report: ReportContent | None = None
    failure_reason: str | None = None
    refinement_count: int = 0
    refinements_remaining: int = 0
    window_expires_at: str | None = None


class ReportSummary(BaseModel):
    """ABS-345: one priced "report" (Answers purchase) row for the sidebar.

    The product sidebar merges these report rows with conversation
    (chat-session) rows into a single case-aware list, so a report and a
    conversation about the same property read side-by-side. Only the
    fields the sidebar renders are projected — the full answer text and
    transcript stay on the dedicated ``/app/answers/{id}`` view.

    ``address`` / ``zone`` are pulled from the purchase inputs (an Answers
    purchase carries its subject in ``inputs``, not a Case anchor); ``zone``
    is present only for questions that collected it, so the sidebar omits
    the caption when it is ``None``. ``answer_ready`` is True once the
    engine has captured a grounded answer — the sidebar uses it to decide
    whether opening the row lands on a ready answer.
    """

    id: int
    question_slug: str
    title: str
    status: str
    address: str | None = None
    zone: str | None = None
    answer_ready: bool = False
    updated_at: str | None = None


class ReportListResponse(BaseModel):
    reports: list[ReportSummary]


class TierBalance(BaseModel):
    tier: str
    available: int
    reserved: int
    consumed: int


class BillingMeResponse(BaseModel):
    enabled: bool
    payments_enabled: bool = Field(
        default=False,
        description=(
            "ABS-322: False when answers are unlocked by free-question "
            "credits (no Stripe). The entry flow combines this with "
            "free_questions_remaining to show the 'free trial used — paid "
            "answers coming soon' exhaustion state instead of a checkout "
            "button."
        ),
    )
    stripe_customer_id: str | None
    tier_balances: list[TierBalance]
    total_available_credits: int
    free_questions_remaining: int = 0


class PurchaseSummary(BaseModel):
    """One row in the purchase-history list."""

    id: int
    tier: str
    pack_sku: str
    quantity: int
    amount_paid_cents: int
    currency: str
    created_at: str


class PurchaseHistoryResponse(BaseModel):
    purchases: list[PurchaseSummary]


class FreeStartRequest(BaseModel):
    """Body of ``POST /v1/billing/questions/free-start``.

    The payments-off (ABS-322) "buy an answer" entry, decoupled by ABS-324:
    it consumes one free-question entitlement and opens a ``QuestionPurchase``
    in the **Answers** product — it does NOT open a Case or touch the
    Conversation CaseCredit ledger. The browser routes to the dedicated
    answer/refine view (``/app/answers/{purchase_id}``).

    ``anchor_label`` / ``anchor_kind`` are accepted for backward
    compatibility (the form still posts them) but are no longer used: an
    Answers purchase carries its subject in ``inputs``, not a Case anchor.
    """

    question_slug: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    anchor_label: str | None = None
    anchor_kind: str = "address"


class FreeStartResponse(BaseModel):
    """ABS-324: the Answers free-start lands a runnable ``QuestionPurchase``,
    not a Case. ``purchase_id`` is the answer-view target."""

    purchase_id: int
    status: str
    free_questions_remaining: int


# -- Shared builders --------------------------------------------------------


def _build_question_menu(
    *,
    settings: AdvisorBillingSettings | None,
    enabled: bool,
    currency: str,
    payments_enabled: bool = False,
) -> list[QuestionMenuItem]:
    """Render the priced-question catalog into menu items.

    ``available`` means "this question can be unlocked right now":

    * **payments-off (ABS-322):** True whenever billing is enabled —
      every question is answerable by consuming a free-question credit,
      independent of any Stripe Price ID. (Per-user exhaustion is a
      separate, authenticated signal — ``GET /me``'s
      ``free_questions_remaining`` — since this menu is public.)
    * **payments-on:** True only when billing is enabled AND the
      question's Stripe Price ID is configured on ``settings``.

    When ``settings`` is None (minimal dormant setups), there are no
    Price IDs, so payments-on questions render unavailable.
    """
    items: list[QuestionMenuItem] = []
    for question in all_questions():
        price_id = (
            getattr(
                settings, question.stripe_price_env_var.lower(), None
            )
            if settings is not None
            else None
        )
        available = enabled and (
            True if not payments_enabled else bool(price_id)
        )
        items.append(
            QuestionMenuItem(
                slug=question.slug,
                display_name=question.display_name,
                price_cents=question.price_cents,
                currency=currency,
                summary=question.summary,
                backing_calls=list(question.backing_calls),
                required_inputs=[
                    QuestionInputField(
                        name=f.name,
                        label=f.label,
                        required=f.required,
                        description=f.description,
                    )
                    for f in question.required_inputs
                ],
                catalog_anchor=question.catalog_anchor,
                available=available,
            )
        )
    return items


def _question_purchase_response(
    purchase: QuestionPurchase,
) -> QuestionPurchaseResponse:
    return QuestionPurchaseResponse(
        id=purchase.id,
        question_slug=purchase.question_slug,
        status=purchase.status,
        price_cents=purchase.price_cents,
        currency=purchase.currency,
        answer=purchase.answer_text,
        report=build_report(purchase),
        failure_reason=purchase.failure_reason,
        refinement_count=purchase.refinement_count,
        refinements_remaining=answer_flow.refinements_remaining(purchase),
        window_expires_at=(
            purchase.window_expires_at.isoformat()
            if purchase.window_expires_at is not None
            else None
        ),
    )


def _report_summary(purchase: QuestionPurchase) -> ReportSummary:
    """Project a ``QuestionPurchase`` into a sidebar ``ReportSummary``.

    The display title is the catalog question's ``display_name`` when the
    slug is known; off-menu ("other", ABS-316) purchases fall back to the
    verbatim free-form question text captured in ``inputs['question']``,
    then to the raw slug. Address / zone come from the collected inputs.
    """
    inputs = purchase.inputs_json or {}
    try:
        title = question_for(purchase.question_slug).display_name
    except KeyError:
        title = str(inputs.get("question") or purchase.question_slug)
    address = inputs.get("address")
    zone = inputs.get("zone")
    return ReportSummary(
        id=purchase.id,
        question_slug=purchase.question_slug,
        title=title,
        status=purchase.status,
        address=str(address) if isinstance(address, str) and address else None,
        zone=str(zone) if isinstance(zone, str) and zone else None,
        answer_ready=bool(purchase.answer_text),
        updated_at=(
            purchase.updated_at.isoformat()
            if purchase.updated_at is not None
            else None
        ),
    )


def _conversation_entry_enabled() -> bool:
    """ABS-324: is the Conversation product exposed in the in-app entry?

    Launch posture is Answers-only — ``/cases/new`` surfaces only the
    question menu. The turn-based ``/app`` chat (Conversation) stays in the
    codebase but its entry (continuing an existing case) is hidden until
    ``ADVISOR_CONVERSATION_ENTRY_ENABLED`` is flipped true. Read from the
    environment so the toggle is a config flip with no redeploy of logic.
    """
    return os.environ.get(
        "ADVISOR_CONVERSATION_ENTRY_ENABLED", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def _load_owned_purchase(db: Any, purchase_id: int, user: User) -> QuestionPurchase:
    """Fetch a purchase that belongs to ``user`` or raise HTTP 404."""
    purchase = db.get(QuestionPurchase, purchase_id)
    if purchase is None or purchase.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "purchase_not_found",
                "message": f"no question purchase {purchase_id}",
            },
        )
    return purchase


def _mount_answer_delivery_routes(
    router: APIRouter,
    *,
    user_dependency: Callable[..., Any],
    user_resolver: Callable[[Any, Session], User],
    open_db: Callable[[], Any],
    commit: Callable[[Any], None],
    answer_gateway: Any | None,
    answer_persona: str | None,
    answer_retrieval_factory: Callable[[], Any] | None,
    client_factory: Callable[[], StripeClient] | None = None,
    payments_enabled: bool = False,
    require_enabled: Callable[[], None] | None = None,
) -> None:
    """Mount the **Answers** delivery surface (ABS-321/312/317).

    These three endpoints — run-answer, refine, get-purchase — are the
    dedicated answer/refine view's backend. They are *payments-agnostic*:
    a free-credit (payments-off) purchase runs with ``client_factory=None``
    and never authorizes a card, while the Stripe path captures/voids the
    hold. ABS-324 mounts this on BOTH the live and dormant routers so the
    payments-off launch terminates the Answers flow in its own view instead
    of routing through the Conversation ``/app`` chat.

    Crucially, this surface consumes ONLY the Answers entitlement
    (``QuestionPurchase`` + free-question credit) — it never reserves or
    commits a CaseCredit. The import-boundary guard test enforces that.
    """

    def _require_runner() -> None:
        if (
            answer_gateway is None
            or answer_persona is None
            or answer_retrieval_factory is None
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "answer_runner_unavailable",
                    "message": (
                        "The answer engine is not wired into the billing "
                        "router on this deployment."
                    ),
                },
            )

    def _guard() -> None:
        if require_enabled is not None:
            require_enabled()
        _require_runner()

    def _require_gateway() -> None:
        if answer_gateway is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "answer_runner_unavailable",
                    "message": (
                        "The intake engine is not wired into the billing "
                        "router on this deployment."
                    ),
                },
            )

    @router.post("/questions/intake", response_model=IntakeResponse)
    async def post_question_intake(
        body: IntakeRequest,
        auth_session: Any = Depends(user_dependency),  # noqa: ARG001
    ) -> IntakeResponse:
        """Detect missing inputs for a question and ask for them (ABS-315).

        The consultant-style step BEFORE the purchase: an LLM reads the
        conversation, extracts whatever inputs it can, and the server
        decides completeness against the question's required-input schema.
        ALWAYS FREE — a single tools-less extraction, no charge, no Stripe
        object. Part of the Answers product, so it is mounted in payments-off
        (dormant) mode too: it only needs the LLM gateway, which is wired
        regardless of whether Stripe is configured.
        """
        if require_enabled is not None:
            require_enabled()
        _require_gateway()
        try:
            question = question_for(body.question_slug)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "unknown_question",
                    "message": f"unknown question {body.question_slug!r}",
                },
            ) from exc
        result = await detect_intake(
            answer_gateway,
            question,
            conversation=body.conversation,
            provided_inputs=body.inputs,
        )
        return IntakeResponse(
            question_slug=question.slug,
            complete=result.complete,
            inputs=result.inputs,
            missing_required=result.missing_required,
            missing_optional=result.missing_optional,
            prompt=result.prompt,
        )

    async def _run_generation_job(purchase_id: int) -> None:
        """Settle a ``generating`` purchase off the request path (ABS-343).

        The answer engine turn is long (~tens of seconds) and must not be
        tied to the browser tab that started it. ``post_run_answer`` flips
        the purchase to ``generating`` and dispatches this as a background
        task, so the HTTP POST returns immediately and the run continues
        even if the user leaves — the answer saves to the case regardless.

        Idempotent-by-guard: it only runs a purchase still in
        ``generating`` (a settled row is skipped). ``run_answer`` itself
        catches engine failures and voids the hold → ``failed``; the outer
        ``except`` only guards infra-level errors (DB, Stripe) so a stuck
        row still resolves to ``failed`` and the client stops polling.
        """
        try:
            with open_db() as db:
                purchase = db.get(QuestionPurchase, purchase_id)
                if purchase is None or purchase.status != "generating":
                    return
                await answer_flow.run_answer(
                    db,
                    purchase,
                    gateway=answer_gateway,
                    persona=answer_persona,
                    retrieval_factory=answer_retrieval_factory,
                    client=client_factory() if client_factory else None,
                )
                commit(db)
        except Exception:  # noqa: BLE001 — never leave a row wedged in generating
            logger.exception(
                "generation job failed for purchase %s", purchase_id
            )
            try:
                with open_db() as db:
                    purchase = db.get(QuestionPurchase, purchase_id)
                    if purchase is not None and purchase.status == "generating":
                        purchase.status = "failed"
                        purchase.failure_reason = "internal_error"
                        commit(db)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "could not mark purchase %s failed after job error",
                    purchase_id,
                )

    @router.post(
        "/questions/purchases/{purchase_id}/answer",
        response_model=QuestionPurchaseResponse,
    )
    async def post_run_answer(
        purchase_id: int,
        background_tasks: BackgroundTasks,
        auth_session: Any = Depends(user_dependency),
    ) -> QuestionPurchaseResponse:
        _guard()
        # Payments-off (ABS-322) runs answers with no Stripe client; the
        # Stripe path still requires one.
        if payments_enabled and client_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_misconfigured",
                    "message": "no Stripe client factory wired",
                },
            )
        with open_db() as db:
            user = user_resolver(auth_session, db)
            purchase = _load_owned_purchase(db, purchase_id, user)
            # ABS-343: the engine runs as a background job. A settled OR
            # already-generating purchase is returned as-is so a repeat POST
            # (React StrictMode double-mount, a retry, a second tab) never
            # fires a second engine run.
            if purchase.status in {
                "captured",
                "voided",
                "failed",
                "generating",
            }:
                return _question_purchase_response(purchase)
            if purchase.status != "authorized":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "not_authorized",
                        "message": (
                            f"purchase {purchase.id} is "
                            f"{purchase.status!r}, not authorized"
                        ),
                    },
                )
            # Persist ``generating`` BEFORE returning so a poll / the sidebar
            # / a second tab observe the in-flight state, then hand the run
            # to a background task that outlives this request.
            purchase.status = "generating"
            response = _question_purchase_response(purchase)
            commit(db)
        background_tasks.add_task(_run_generation_job, purchase_id)
        return response

    @router.post(
        "/questions/purchases/{purchase_id}/refine",
        response_model=QuestionPurchaseResponse,
    )
    async def post_refine(
        purchase_id: int,
        body: RefineRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> QuestionPurchaseResponse:
        _guard()
        with open_db() as db:
            user = user_resolver(auth_session, db)
            purchase = _load_owned_purchase(db, purchase_id, user)
            try:
                await answer_flow.run_refinement(
                    db,
                    purchase,
                    message=body.message,
                    gateway=answer_gateway,
                    persona=answer_persona,
                    retrieval_factory=answer_retrieval_factory,
                )
            except NewQuestionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "new_question",
                        "message": (
                            "This is a different question — please purchase "
                            "a new answer."
                        ),
                        "suggested_slug": exc.suggested_slug,
                    },
                ) from exc
            except WindowExhaustedError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "window_exhausted",
                        "message": (
                            "The refinement window for this answer is "
                            "closed — please purchase a new answer."
                        ),
                        "reason": exc.reason,
                    },
                ) from exc
            except RefinementNotAvailableError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "refinement_unavailable",
                        "message": str(exc),
                    },
                ) from exc
            response = _question_purchase_response(purchase)
            commit(db)
            return response

    @router.get(
        "/questions/purchases",
        response_model=ReportListResponse,
    )
    def list_question_purchases(
        auth_session: Any = Depends(user_dependency),
    ) -> ReportListResponse:
        """ABS-345: list the caller's priced "report" purchases, newest-first.

        Feeds the product sidebar, which merges these report rows with the
        conversation (chat-session) rows into one case-aware list. Read-only
        and deliberately NOT gated by ``require_enabled``: payments-off
        deployments still issue free-question ``QuestionPurchase`` rows
        (ABS-322 free-start), and the user must be able to see them. Rows in
        the pre-answer ``authorizing`` state are excluded — they have no
        subject worth showing until checkout completes.
        """
        with open_db() as db:
            user = user_resolver(auth_session, db)
            rows = (
                db.execute(
                    select(QuestionPurchase)
                    .where(QuestionPurchase.user_id == user.id)
                    .where(QuestionPurchase.status != "authorizing")
                    .order_by(QuestionPurchase.updated_at.desc())
                )
                .scalars()
                .all()
            )
            return ReportListResponse(
                reports=[_report_summary(p) for p in rows]
            )

    @router.get(
        "/questions/purchases/{purchase_id}",
        response_model=QuestionPurchaseResponse,
    )
    def get_question_purchase(
        purchase_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> QuestionPurchaseResponse:
        if require_enabled is not None:
            require_enabled()
        with open_db() as db:
            user = user_resolver(auth_session, db)
            purchase = _load_owned_purchase(db, purchase_id, user)
            return _question_purchase_response(purchase)


# -- Router factory ---------------------------------------------------------


UserResolver = Callable[[Any, Session], User]


def build_billing_router(
    *,
    settings: AdvisorBillingSettings,
    client_factory: Callable[[], StripeClient] | None,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
    answer_gateway: Any | None = None,
    answer_persona: str | None = None,
    answer_retrieval_factory: Callable[[], Any] | None = None,
) -> APIRouter:
    """Assemble the billing router.

    ``answer_gateway`` / ``answer_persona`` / ``answer_retrieval_factory``
    wire the priced-question "buy an answer" run/refine endpoints
    (ABS-312). When any is missing those endpoints return 503 — the
    catalog/checkout/webhook endpoints still work, so the flow degrades
    to "purchasable but not yet runnable" rather than crashing.
    """
    router = APIRouter(prefix="/v1/billing", tags=["billing"])

    def _require_enabled() -> None:
        if not settings.enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_disabled",
                    "message": (
                        "Billing is not enabled on this deployment. Set "
                        "ADVISOR_BILLING_ENABLED=true and configure "
                        "STRIPE_* env vars to enable."
                    ),
                },
            )

    @contextmanager
    def _open_db() -> Any:
        result = db_session_factory()
        if hasattr(result, "__enter__"):
            with result as session:
                yield session
        else:
            try:
                yield result
            finally:
                close = getattr(result, "close", None)
                if callable(close):
                    close()

    def _commit(db: Any) -> None:
        commit = getattr(db, "commit", None)
        if callable(commit):
            commit()

    @router.get("/catalog", response_model=CatalogResponse)
    def get_catalog() -> CatalogResponse:
        """Return the full 12-SKU pack matrix.

        Unauth-accessible by design: the pricing page is public and
        wants to show prices to anonymous visitors. The ``enabled``
        flag tells the frontend whether checkout will actually work.
        """
        pricing = get_pricing_settings()
        offers = []
        for offer in all_offers():
            price_id = getattr(
                settings, offer.stripe_price_env_var.lower(), None
            )
            offers.append(
                CatalogOffer(
                    tier=offer.tier.name,
                    tier_display_name=offer.tier.display_name,
                    tier_token_budget=offer.tier.token_budget,
                    pack_sku=offer.pack.sku,
                    pack_display_name=offer.pack.display_name,
                    quantity=offer.pack.quantity,
                    discount_bps=offer.pack.discount_bps,
                    list_price_cents=offer.list_price_cents,
                    amount_due_cents=offer.amount_due_cents,
                    currency=pricing.display_currency,
                    available=bool(price_id) and settings.enabled,
                )
            )
        return CatalogResponse(
            enabled=settings.enabled,
            currency=pricing.display_currency,
            cad_per_usd=pricing.cad_per_usd,
            offers=offers,
        )

    @router.get("/questions", response_model=QuestionMenuResponse)
    def get_questions() -> QuestionMenuResponse:
        """Return the priced-question launch menu.

        Public by design — the question menu is shown to anonymous
        visitors. ``payments_enabled`` tells the frontend which unlock
        path is live (free credit vs Stripe), and the per-question
        ``available`` flag whether that path can run the question.
        """
        pricing = get_pricing_settings()
        return QuestionMenuResponse(
            enabled=settings.enabled,
            payments_enabled=settings.payments_enabled,
            conversation_enabled=_conversation_entry_enabled(),
            currency=pricing.display_currency,
            cad_per_usd=pricing.cad_per_usd,
            questions=_build_question_menu(
                settings=settings,
                enabled=settings.enabled,
                currency=pricing.display_currency,
                payments_enabled=settings.payments_enabled,
            ),
        )

    @router.post("/checkout/pack", response_model=CheckoutResponse)
    def post_checkout_pack(
        body: CheckoutPackRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> CheckoutResponse:
        _require_enabled()
        if client_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_misconfigured",
                    "message": "no Stripe client factory wired",
                },
            )
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            try:
                url = start_pack_checkout(
                    db,
                    user,
                    tier=body.tier,
                    pack_sku=body.pack_sku,
                    client=client_factory(),
                    settings=settings,
                )
            except UnknownOfferError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "unknown_offer",
                        "message": str(exc),
                    },
                ) from exc
            except PriceNotConfiguredError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "price_not_configured",
                        "message": str(exc),
                    },
                ) from exc
            return CheckoutResponse(url=url)

    # -- Priced-question "buy an answer" flow (ABS-312) --------------------

    def _require_other_question_enabled() -> None:
        # ABS-325: the off-menu "Other" free-form path is disabled at
        # launch. Keep the engine wired but reject the public quote /
        # checkout-other endpoints until ADVISOR_OTHER_QUESTION_ENABLED is
        # flipped true. Catalog questions are unaffected.
        if not settings.other_question_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "other_question_disabled",
                    "message": (
                        "The off-menu free-form question path is disabled "
                        "on this deployment. Set "
                        "ADVISOR_OTHER_QUESTION_ENABLED=true to enable it."
                    ),
                },
            )

    def _require_answer_gateway() -> None:
        # The off-menu quote (ABS-316) needs only the LLM gateway — no
        # retrieval/persona — so it gates on the gateway alone.
        if answer_gateway is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "answer_runner_unavailable",
                    "message": (
                        "The quote engine is not wired into the billing "
                        "router on this deployment."
                    ),
                },
            )

    @router.post(
        "/checkout/question", response_model=QuestionCheckoutResponse
    )
    def post_checkout_question(
        body: CheckoutQuestionRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> QuestionCheckoutResponse:
        """Open a buy-an-answer purchase for one catalog question.

        Branches on ``settings.payments_enabled`` (ABS-322):

        * **off (default):** consume a free-question credit and return an
          already-``authorized`` purchase with ``url=None`` — the client
          proceeds straight to ``.../answer``. No Stripe object created.
          A trial with no credits left → HTTP 402 (exhaustion).
        * **on:** the unchanged Stripe authorize→capture path — create a
          manual-capture Checkout session and return its URL.
        """
        _require_enabled()
        # Payments-off: free-credit path, no Stripe.
        if not settings.payments_enabled:
            with _open_db() as db:
                user = user_resolver(auth_session, db)
                try:
                    purchase = answer_flow.start_question_free(
                        db,
                        user,
                        question_slug=body.question_slug,
                        inputs=body.inputs,
                    )
                except UnknownQuestionError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "unknown_question",
                            "message": str(exc),
                        },
                    ) from exc
                except MissingRequiredInputsError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "missing_required_inputs",
                            "message": str(exc),
                            "missing": exc.missing,
                        },
                    ) from exc
                except FreeQuestionsExhaustedError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail={
                            "code": "free_questions_exhausted",
                            "message": (
                                "Your free trial questions are used up. "
                                "Paid answers are coming soon."
                            ),
                        },
                    ) from exc
                purchase_id = purchase.id
                purchase_status = purchase.status
                _commit(db)
                return QuestionCheckoutResponse(
                    url=None,
                    purchase_id=purchase_id,
                    status=purchase_status,
                    payments_enabled=False,
                )

        # Payments-on: Stripe authorize→capture path.
        if client_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_misconfigured",
                    "message": "no Stripe client factory wired",
                },
            )
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            try:
                purchase, url = answer_flow.start_question_checkout(
                    db,
                    user,
                    question_slug=body.question_slug,
                    inputs=body.inputs,
                    client=client_factory(),
                    settings=settings,
                )
            except UnknownQuestionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "unknown_question", "message": str(exc)},
                ) from exc
            except MissingRequiredInputsError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "missing_required_inputs",
                        "message": str(exc),
                        "missing": exc.missing,
                    },
                ) from exc
            except QuestionPriceNotConfiguredError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "price_not_configured",
                        "message": str(exc),
                    },
                ) from exc
            purchase_id = purchase.id
            purchase_status = purchase.status
            _commit(db)
            return QuestionCheckoutResponse(
                url=url,
                purchase_id=purchase_id,
                status=purchase_status,
                payments_enabled=True,
            )

    # -- Consultant-style intake detection (ABS-315) ----------------------

    # -- Off-menu "Other" question: free quote → buy (ABS-316) ------------

    @router.post(
        "/questions/quote", response_model=QuoteResponse
    )
    async def post_quote_question(
        body: QuoteRequest,
        auth_session: Any = Depends(user_dependency),  # noqa: ARG001
    ) -> QuoteResponse:
        """Quote a price for an off-menu question. ALWAYS FREE.

        Producing a quote never charges the customer and never creates a
        Stripe object — it is a single (free) LLM difficulty
        classification mapped to an anchored price. Auth-gated so it
        can't be scraped anonymously, but no money moves.
        """
        _require_enabled()
        _require_other_question_enabled()
        _require_answer_gateway()
        try:
            quote = await quote_question(answer_gateway, body.question)
        except EmptyQuestionError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "empty_question", "message": str(exc)},
            ) from exc
        return QuoteResponse(
            question=quote.question_text,
            difficulty=quote.difficulty,
            difficulty_display_name=quote.difficulty_display_name,
            price_cents=quote.price_cents,
            currency=quote.currency,
            rationale=quote.rationale,
            band_low_cents=quote.band_low_cents,
            band_high_cents=quote.band_high_cents,
        )

    @router.post(
        "/checkout/other", response_model=OtherCheckoutResponse
    )
    async def post_checkout_other(
        body: CheckoutOtherRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> OtherCheckoutResponse:
        """Buy an answer to an off-menu question at the quoted price.

        Re-quotes the question SERVER-SIDE (never trusting a
        client-supplied price), then authorizes a manual-capture
        PaymentIntent for that amount. The answer-run flow later captures
        on a grounded answer / voids on a failed one — same
        failed-question rule as catalog questions.
        """
        _require_enabled()
        _require_other_question_enabled()
        _require_answer_gateway()
        if client_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_misconfigured",
                    "message": "no Stripe client factory wired",
                },
            )
        try:
            quote = await quote_question(answer_gateway, body.question)
        except EmptyQuestionError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "empty_question", "message": str(exc)},
            ) from exc
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            purchase, url = answer_flow.start_other_checkout(
                db,
                user,
                quote=quote,
                client=client_factory(),
                settings=settings,
            )
            purchase_id = purchase.id
            _commit(db)
            return OtherCheckoutResponse(
                url=url,
                purchase_id=purchase_id,
                price_cents=quote.price_cents,
                currency=quote.currency,
                difficulty=quote.difficulty,
                rationale=quote.rationale,
            )

    # ABS-321/312/317: the Answers delivery surface (run / refine / get).
    # Shared with the dormant router so the payments-off launch terminates
    # in the dedicated answer view, not the Conversation /app chat.
    _mount_answer_delivery_routes(
        router,
        user_dependency=user_dependency,
        user_resolver=user_resolver,
        open_db=_open_db,
        commit=_commit,
        answer_gateway=answer_gateway,
        answer_persona=answer_persona,
        answer_retrieval_factory=answer_retrieval_factory,
        client_factory=client_factory,
        payments_enabled=settings.payments_enabled,
        require_enabled=_require_enabled,
    )

    @router.post("/webhook")
    async def post_webhook(
        request: Request,
        stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    ) -> dict[str, Any]:
        _require_enabled()
        if client_factory is None or not settings.stripe_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "billing_misconfigured",
                    "message": "webhook handler not configured",
                },
            )
        if not stripe_signature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "missing_signature",
                    "message": "Stripe-Signature header is required",
                },
            )
        payload = await request.body()
        client = client_factory()
        try:
            event = client.construct_webhook_event(
                payload=payload,
                sig_header=stripe_signature,
                secret=settings.stripe_webhook_secret,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — Stripe SDK raises various
            logger.warning(
                "stripe webhook signature verification failed: %s", exc
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_signature",
                    "message": "webhook signature verification failed",
                },
            ) from exc

        with _open_db() as db:
            result = handle_event(db, event, settings)
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()
        return {
            "handled": result.handled,
            "event_type": result.event_type,
            "event_id": result.event_id,
            "note": result.note,
        }

    @router.get("/me", response_model=BillingMeResponse)
    def get_me(
        auth_session: Any = Depends(user_dependency),
    ) -> BillingMeResponse:
        _require_enabled()
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            balances = credit_balance_for(db, user_id=user.id)
            tier_balances = [
                TierBalance(
                    tier=b.tier,
                    available=b.available,
                    reserved=b.reserved,
                    consumed=b.consumed,
                )
                for b in balances
            ]
            return BillingMeResponse(
                enabled=settings.enabled,
                payments_enabled=settings.payments_enabled,
                stripe_customer_id=user.stripe_customer_id,
                tier_balances=tier_balances,
                total_available_credits=sum(
                    b.available for b in tier_balances
                ),
                free_questions_remaining=user.free_questions_remaining,
            )

    @router.get("/purchases", response_model=PurchaseHistoryResponse)
    def get_purchases(
        auth_session: Any = Depends(user_dependency),
    ) -> PurchaseHistoryResponse:
        _require_enabled()
        with _open_db() as db:
            user = user_resolver(auth_session, db)
            rows = (
                db.execute(
                    select(CasePurchase)
                    .where(CasePurchase.user_id == user.id)
                    .order_by(CasePurchase.created_at.desc())
                    .limit(100)
                )
                .scalars()
                .all()
            )
            return PurchaseHistoryResponse(
                purchases=[
                    PurchaseSummary(
                        id=r.id,
                        tier=r.tier,
                        pack_sku=r.pack_sku,
                        quantity=r.quantity,
                        amount_paid_cents=r.amount_paid_cents,
                        currency=r.currency,
                        created_at=r.created_at.isoformat(),
                    )
                    for r in rows
                ]
            )

    return router


def build_dormant_billing_router(
    *,
    db_session_factory: Callable[[], Any] | None = None,
    user_dependency: Callable[..., Any] | None = None,
    user_resolver: Callable[[Any, Any], Any] | None = None,
    answer_gateway: Any | None = None,
    answer_persona: str | None = None,
    answer_retrieval_factory: Callable[[], Any] | None = None,
) -> APIRouter:
    """Mount the dormant (Stripe-off) billing router.

    Conversation product (packs / Stripe checkout / webhook) is fully
    dormant here — those endpoints 503 until Stripe is wired up. But the
    **Answers** product is its own thing (ABS-324): when the answer engine
    is wired (``answer_gateway`` / ``answer_persona`` /
    ``answer_retrieval_factory``), the payments-off "buy an answer" path is
    live — ``POST /questions/free-start`` consumes a free-question credit and
    opens a ``QuestionPurchase`` (NOT a Case), and the answer/refine/get
    endpoints serve the dedicated answer view. This is the decoupling: the
    Answers free path no longer routes through the Conversation ``/app``
    chat or reserves a CaseCredit.

    ``GET /catalog`` always works (price list for the pricing page).
    ``GET /me`` returns real per-tier credit counts when ``db_session_factory``,
    ``user_dependency``, and ``user_resolver`` are all provided; otherwise
    it returns an empty-balance response so the page still renders without
    crashing.
    """
    router = APIRouter(prefix="/v1/billing", tags=["billing"])
    pricing = get_pricing_settings()

    checkout_detail = {
        "code": "billing_disabled",
        "message": (
            "Billing is not enabled on this deployment. Set "
            "ADVISOR_BILLING_ENABLED=true and configure STRIPE_* env vars."
        ),
    }

    @router.get("/catalog", response_model=CatalogResponse)
    def get_catalog_disabled() -> CatalogResponse:
        # The catalog can render without Stripe configured — every
        # offer's ``available`` flag is False so the frontend renders
        # the SKU but disables the "Buy" button.
        offers = [
            CatalogOffer(
                tier=offer.tier.name,
                tier_display_name=offer.tier.display_name,
                tier_token_budget=offer.tier.token_budget,
                pack_sku=offer.pack.sku,
                pack_display_name=offer.pack.display_name,
                quantity=offer.pack.quantity,
                discount_bps=offer.pack.discount_bps,
                list_price_cents=offer.list_price_cents,
                amount_due_cents=offer.amount_due_cents,
                currency=pricing.display_currency,
                available=False,
            )
            for offer in all_offers()
        ]
        return CatalogResponse(
            enabled=False,
            currency=pricing.display_currency,
            cad_per_usd=pricing.cad_per_usd,
            offers=offers,
        )

    @router.get("/questions", response_model=QuestionMenuResponse)
    def get_questions_disabled() -> QuestionMenuResponse:
        # The question menu renders without Stripe configured — every
        # question's ``available`` flag is False so the frontend shows
        # the menu but disables the "Buy" button.
        return QuestionMenuResponse(
            enabled=False,
            conversation_enabled=_conversation_entry_enabled(),
            currency=pricing.display_currency,
            cad_per_usd=pricing.cad_per_usd,
            questions=_build_question_menu(
                settings=None, enabled=False, currency=pricing.display_currency
            ),
        )

    @router.post("/checkout/pack")
    def post_checkout_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=checkout_detail,
        )

    @router.post("/webhook")
    def post_webhook_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=checkout_detail,
        )

    # ``GET /me`` — return real credit balances even when billing is
    # dormant so the billing page shows the accurate tier breakdown for
    # admin-granted credits. Two flavours depending on whether DB deps
    # are wired: with deps we read from Postgres; without deps we return
    # an empty response that the frontend renders as all-zero balances.
    if (
        db_session_factory is not None
        and user_dependency is not None
        and user_resolver is not None
    ):
        @contextmanager
        def _open_db_dormant() -> Any:
            result = db_session_factory()
            if hasattr(result, "__enter__"):
                with result as session:
                    yield session
            else:
                try:
                    yield result
                finally:
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()

        def _commit_dormant(db: Any) -> None:
            commit = getattr(db, "commit", None)
            if callable(commit):
                commit()

        @router.get("/me", response_model=BillingMeResponse)
        def get_me_dormant(
            auth_session: Any = Depends(user_dependency),
        ) -> BillingMeResponse:
            with _open_db_dormant() as db:
                user = user_resolver(auth_session, db)
                balances = credit_balance_for(db, user_id=user.id)
                tier_balances = [
                    TierBalance(
                        tier=b.tier,
                        available=b.available,
                        reserved=b.reserved,
                        consumed=b.consumed,
                    )
                    for b in balances
                ]
                return BillingMeResponse(
                    enabled=False,
                    stripe_customer_id=None,
                    tier_balances=tier_balances,
                    total_available_credits=sum(
                        b.available for b in tier_balances
                    ),
                    free_questions_remaining=user.free_questions_remaining,
                )

        @router.post("/questions/free-start", response_model=FreeStartResponse)
        def post_free_start(
            body: FreeStartRequest,
            auth_session: Any = Depends(user_dependency),
        ) -> FreeStartResponse:
            """Open a free-trial **Answers** purchase (ABS-324 decoupled).

            Called by the case-open form for the payments-off launch. It
            consumes one free-question credit and opens a
            ``QuestionPurchase`` straight in ``authorized`` state — ready
            for ``.../answer`` — via the Answers engine
            (``start_question_free``). It does NOT open a Case, does NOT
            touch the Conversation CaseCredit ledger, and returns a
            ``purchase_id`` so the browser routes to the dedicated answer
            view (``/app/answers/{purchase_id}``), never the ``/app`` chat.

            Order of operations honours the failed-question rule:
            unworkable inputs raise BEFORE a credit is reserved. Returns
            402 (``free_questions_exhausted``) when the trial is used up.
            """
            if body.question_slug is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "unknown_question",
                        "message": "question_slug is required",
                    },
                )
            with _open_db_dormant() as db:
                user = user_resolver(auth_session, db)
                try:
                    purchase = answer_flow.start_question_free(
                        db,
                        user,
                        question_slug=body.question_slug,
                        inputs=body.inputs,
                    )
                except UnknownQuestionError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"code": "unknown_question", "message": str(exc)},
                    ) from exc
                except MissingRequiredInputsError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "missing_required_inputs",
                            "message": str(exc),
                            "missing": exc.missing,
                        },
                    ) from exc
                except FreeQuestionsExhaustedError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail={
                            "code": "free_questions_exhausted",
                            "message": (
                                "Your free trial questions are used up. "
                                "Paid answers are coming soon."
                            ),
                        },
                    ) from exc
                purchase_id = purchase.id
                purchase_status = purchase.status
                # Read the post-decrement count inside the transaction —
                # the resolver may return a detached User instance, distinct
                # from the row start_question_free actually decremented.
                remaining = (
                    db.scalar(
                        select(User.free_questions_remaining).where(
                            User.id == user.id
                        )
                    )
                    or 0
                )
                db.commit()
                return FreeStartResponse(
                    purchase_id=purchase_id,
                    status=purchase_status,
                    free_questions_remaining=remaining,
                )

        # ABS-324: mount the Answers delivery surface (run / refine / get)
        # on the dormant router too, wired to the same engine the chat route
        # uses. This is what lets the payments-off free-trial answer land in
        # its own view (/app/answers/{id}) end-to-end without billing being
        # "enabled" in the Stripe sense.
        _mount_answer_delivery_routes(
            router,
            user_dependency=user_dependency,
            user_resolver=user_resolver,
            open_db=_open_db_dormant,
            commit=_commit_dormant,
            answer_gateway=answer_gateway,
            answer_persona=answer_persona,
            answer_retrieval_factory=answer_retrieval_factory,
            client_factory=None,
            payments_enabled=False,
            require_enabled=None,
        )

    else:
        @router.get("/me", response_model=BillingMeResponse)
        def get_me_dormant_no_db() -> BillingMeResponse:
            # No DB wired (minimal test setups). Return empty balances
            # rather than 503 so the frontend still renders gracefully.
            return BillingMeResponse(
                enabled=False,
                stripe_customer_id=None,
                tier_balances=[],
                total_available_credits=0,
            )

    @router.get("/purchases")
    def get_purchases_disabled() -> Any:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=checkout_detail,
        )

    return router
