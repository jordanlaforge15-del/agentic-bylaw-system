"""FastAPI application for the Halifax Bylaw Advisor chat backend.

The ``create_app`` factory takes optional dependencies so tests can
inject a ``MockGateway``, an in-memory session store, and a stub
retrieval factory without standing up a real database. Production
callers can pass nothing and pick up the default wiring (Anthropic
gateway, in-memory sessions, RetrievalService bound to the configured
DB URL).

Endpoints:
* ``GET /healthz`` — liveness check; no auth required.
* ``POST /v1/chat`` — send a message, get an SSE stream of events.
* ``GET /v1/chat/sessions/{session_id}`` — debug endpoint that
  returns the message history; will be removed once the frontend
  has its own state.

Auth: production callers pass a ``ClerkVerifier`` and a DB session
factory; routes are then protected by ``current_user_dependency`` which
verifies the Clerk JWT, resolves the caller to an
``advisor.db.models.User`` row (creating one on first contact), and
commits before the route handler runs. The session-store ``user_id``
field stores the **internal numeric id as a string** so future FK joins
don't have to re-resolve through Clerk.

For tests that don't care about auth there's a fallback: when neither
``verifier`` nor ``db_session_factory`` is provided, the routes accept
an ``X-Test-User-Id`` header instead. This keeps the existing chat
behaviour tests minimal while real auth tests use the proper
verifier-backed wiring.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from advisor.api.auth import current_user_dependency
from advisor.api.db_session_store import DbSessionStore, default_resolve_user
from advisor.api.quota import (
    add_case_tokens,
    commit_credit_for,
    enforce_request_rate,
    record_llm_call,
    refund_credit_for,
    reserve_credit_for_session,
    update_usage_event_tokens,
)
from advisor.api.sessions import InMemorySessionStore, SessionStore
from advisor.auth.clerk import ClerkVerifier
from advisor.chat.persona import load_persona
from advisor.chat.session import ChatSession
from advisor.chat.tools import build_bylaw_tools
from advisor.db.models import Case, User
from advisor.llm import LLMGateway, LLMRole, Message, StreamEvent

logger = logging.getLogger(__name__)


DbSessionFactory = Callable[[], AbstractContextManager[Session]]


# Production retrieval factory: opens a fresh session per request via
# layer1's session_scope. Imported lazily inside the factory function
# so tests don't pay the import cost (and the DB module isn't loaded
# at module-import time, which keeps unit tests fast).
def _default_retrieval_service_factory() -> Callable[[], Any]:
    """Build the default per-request retrieval factory.

    Mirrors the pattern in ``mcp/bylaw_retrieval/server.py``: every
    invocation opens a session_scope, creates a RetrievalService
    bound to it, and yields the service. The caller is responsible
    for keeping the context manager alive while the service is in
    use — see ``_open_retrieval_service`` for the wrapper that does
    this for in-process tool calls.
    """
    from bylaw_retrieval.retrieval import (  # noqa: PLC0415 — lazy import
        RetrievalService,
        latest_document_id_resolver,
    )
    from layer1.db.session import session_scope  # noqa: PLC0415

    @contextmanager
    def factory() -> Any:
        with session_scope() as session:
            yield RetrievalService(
                session,
                default_document_id_resolver=latest_document_id_resolver,
            )

    return factory


class ChatRequest(BaseModel):
    """Body of ``POST /v1/chat``."""

    message: str = Field(..., min_length=1, description="User's chat message.")
    session_id: str | None = Field(
        default=None,
        description="Resume this session; omit to create a new one.",
    )
    case_id: int | None = Field(
        default=None,
        description=(
            "Case to bill this turn against. Required for new sessions "
            "in the case-credit model; ignored when ``session_id`` is "
            "provided (the session is already attached to a case)."
        ),
    )


class ChatSessionResponse(BaseModel):
    """Body of ``GET /v1/chat/sessions/{session_id}``."""

    session_id: str
    user_id: str
    model: str
    messages: list[Message]
    # Case-billing context. ``None`` for legacy sessions that predate
    # the case-credit model — the frontend uses this to gate the
    # composer (a null ``case_id`` means the conversation can't be
    # resumed and the user must start a new case).
    case_id: int | None = None
    tier: str | None = None


class ChatSessionSummary(BaseModel):
    """One row in the response of ``GET /v1/chat/sessions``.

    ``title`` is the first user message in the session, truncated. It's
    cheap to derive at request time; persisting a stored title would
    just drift unless we also wired a "rename session" UX.
    ``message_count`` counts only user-facing turns (user input or
    assistant text reply), not the intermediate tool_use / tool_result
    rounds — that's what a sidebar wants to display.
    """

    session_id: str
    model: str
    title: str
    message_count: int
    # ISO 8601 timestamp of the most recent turn, or None for sessions
    # that exist but have never been written to. The frontend renders
    # this as a relative label ("2m ago"); the backend stays neutral.
    updated_at: str | None = None


class ChatSessionList(BaseModel):
    sessions: list[ChatSessionSummary]


def create_app(
    *,
    gateway: LLMGateway | None = None,
    retrieval_service_factory: Callable[[], Any] | None = None,
    session_store: SessionStore | None = None,
    persona_text: str | None = None,
    verifier: ClerkVerifier | None = None,
    db_session_factory: DbSessionFactory | None = None,
    clerk_webhook_secret: str | None = None,
    billing_settings: Any | None = None,
    stripe_client_factory: Callable[[], Any] | None = None,
    billing_db_session_factory: Callable[[], Any] | None = None,
    billing_user_dependency: Callable[..., Any] | None = None,
    billing_user_resolver: Callable[[Any, Any], Any] | None = None,
) -> FastAPI:
    """Build the FastAPI app with explicit, injectable dependencies.

    Injection points (all optional):

    * ``gateway`` — defaults to ``None`` (callers must pass one for
      v1; we don't auto-construct AnthropicGateway because it requires
      API-key wiring outside this module's scope).
    * ``retrieval_service_factory`` — defaults to a production
      session_scope-backed factory. Tests pass a stub callable that
      yields a service bound to an in-memory sqlite session.
    * ``session_store`` — defaults to ``InMemorySessionStore`` (or
      ``DbSessionStore`` when ``db_session_factory`` is supplied and
      ``session_store`` is omitted).
    * ``persona_text`` — defaults to the contents of
      ``docs/agent/persona.md``. Tests can pass a short string to
      avoid touching the filesystem.
    * ``verifier`` — when provided AND ``db_session_factory`` is also
      provided, the chat routes are guarded by
      ``current_user_dependency`` which verifies a Clerk JWT and
      resolves an ``advisor.db.models.User`` row. When the verifier is
      ``None`` the routes fall back to an ``X-Test-User-Id`` header
      (test-only convenience).
    * ``db_session_factory`` — when provided, enables (a) DB-backed
      session persistence (unless ``session_store`` is also passed, in
      which case the caller's choice wins) and (b) monthly quota
      enforcement on ``/v1/chat``. Independent from ``verifier``: a
      test can wire DB persistence without real auth (test-header
      fallback handles user identity), and production wires both.
    * ``clerk_webhook_secret`` — when provided alongside
      ``db_session_factory``, mounts ``POST /v1/webhooks/clerk`` which
      handles user lifecycle events from Clerk (created/updated/deleted)
      and keeps the ``advisor_user`` table in sync. The endpoint is
      signature-verified via svix using this secret; without it we
      skip mounting the route entirely rather than expose an unsigned
      remote-write hole.
    * Billing kwargs (``billing_settings``, ``stripe_client_factory``,
      ``billing_db_session_factory``, ``billing_user_dependency``,
      ``billing_user_resolver``) — wire the Stripe billing router.
      When all five are provided AND ``billing_settings.enabled`` is
      True, the live billing router is mounted. Otherwise a dormant
      stub router is mounted that returns 503 from every billing
      endpoint, so the frontend can probe ``/v1/billing/*`` without
      crashing during the pre-Stripe phase. Typed as ``Any`` so this
      module doesn't import ``advisor.billing`` eagerly — the import
      is lazy in the body for the same reason the Stripe SDK is.
    """
    if gateway is None:
        # We don't auto-build a gateway because Anthropic credentials
        # live outside this module. Forcing the caller to be explicit
        # makes it impossible to accidentally hit a real API in tests.
        raise ValueError(
            "create_app requires a gateway; pass MockGateway in tests "
            "or AnthropicGateway in production"
        )
    persona = persona_text if persona_text is not None else load_persona()
    factory = retrieval_service_factory or _default_retrieval_service_factory()

    # If the caller wired a DB factory and didn't override the store,
    # default to the DB-backed store. Otherwise fall back to the
    # in-memory store as before so existing tests stay green.
    store: SessionStore
    if session_store is not None:
        store = session_store
    elif db_session_factory is not None:
        store = DbSessionStore(
            db_session_factory=db_session_factory,
            tool_defs_handler_factory=lambda: _build_tools_with_factory(factory),
        )
    else:
        store = InMemorySessionStore()

    app = FastAPI(title="Halifax Bylaw Advisor", version="0.1.0")

    # Stash dependencies on app.state so route handlers can grab them
    # via ``request.app.state`` rather than closing over locals — that
    # keeps the routes inspectable and lets tests poke the store mid-
    # session.
    app.state.gateway = gateway
    app.state.session_store = store
    app.state.persona_text = persona
    app.state.retrieval_factory = factory
    app.state.db_session_factory = db_session_factory

    # Billing router. Mounted in two flavours:
    # * If ``billing_settings`` is provided AND ``enabled=True`` AND
    #   the wiring kwargs are present, the live router is mounted.
    # * Otherwise, a dormant stub router that 503s every endpoint —
    #   so the frontend can probe ``/v1/billing/me`` regardless and
    #   the operator can flip the flag without redeploying. This
    #   block is the ONLY billing-related edit to this file; all
    #   other billing logic lives in ``advisor.billing``.
    from advisor.billing.router import (  # noqa: PLC0415 — lazy import
        build_billing_router,
        build_dormant_billing_router,
    )

    if (
        billing_settings is not None
        and getattr(billing_settings, "enabled", False)
        and stripe_client_factory is not None
        and billing_db_session_factory is not None
        and billing_user_dependency is not None
        and billing_user_resolver is not None
    ):
        app.include_router(
            build_billing_router(
                settings=billing_settings,
                client_factory=stripe_client_factory,
                db_session_factory=billing_db_session_factory,
                user_dependency=billing_user_dependency,
                user_resolver=billing_user_resolver,
            )
        )
    else:
        app.include_router(build_dormant_billing_router())

    # Build the user-dependency callable here so the cases / admin
    # routers (mounted next) can share it. The same callable is
    # consumed by ``POST /v1/chat`` further down.
    require_user = _build_user_dependency(verifier, db_session_factory)

    # Cases router — case-credit lifecycle (open / match / classify /
    # upgrade / close). Mounted whenever a DB factory is wired; the
    # classifier endpoint degrades gracefully when no classifier
    # gateway factory is provided.
    if db_session_factory is not None and verifier is not None:
        from advisor.api.cases_router import build_cases_router  # noqa: PLC0415

        # Default classifier gateway = same Anthropic gateway as chat,
        # since the gateway is model-agnostic and selects per-call.
        from advisor.llm.registry import (  # noqa: PLC0415
            get_settings as get_llm_settings,
        )

        llm_settings = get_llm_settings()

        def _classifier_gateway_factory() -> LLMGateway:
            return gateway

        app.include_router(
            build_cases_router(
                classifier_gateway_factory=_classifier_gateway_factory,
                classifier_model=llm_settings.classifier_model,
                db_session_factory=db_session_factory,
                user_dependency=require_user,
                user_resolver=lambda u, _db: u,
            )
        )

        # Admin router — gated by ADVISOR_ADMIN_API_ENABLED + the Clerk
        # allowlist. Mounting is unconditional so the 403 stub can
        # respond, but the live endpoints check both flags at request
        # time.
        from advisor.admin.router import build_admin_router  # noqa: PLC0415

        app.include_router(
            build_admin_router(
                db_session_factory=db_session_factory,
                user_dependency=require_user,
                user_resolver=lambda u, _db: u,
            )
        )

    # Clerk webhook router. Only mounted when both the secret and a DB
    # factory are wired — the route needs a real DB to write user-row
    # changes against, and without the secret we have no way to verify
    # signatures (an unsigned endpoint here would be a remote-write
    # hole). Tests that don't care about webhooks simply omit the
    # secret and the route stays unmounted.
    if clerk_webhook_secret and db_session_factory is not None:
        from advisor.auth.router import (  # noqa: PLC0415 — lazy import
            build_clerk_webhook_router,
        )

        app.include_router(
            build_clerk_webhook_router(
                webhook_secret=clerk_webhook_secret,
                db_session_factory=db_session_factory,
            )
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/chat")
    async def post_chat(
        body: ChatRequest,
        request: Request,
        user: User = Depends(require_user),
    ) -> EventSourceResponse:
        # Use the external clerk_user_id (or the X-Test-User-Id
        # header value in the dev fallback). Both ``create`` and
        # ``get`` on the session store return this same string in
        # ``ChatSession.user_id``, so handler comparisons against it
        # round-trip cleanly. Using ``str(user.id)`` here previously
        # caused a session-vs-user-id mismatch in test mode and would
        # have caused the same mismatch in Clerk mode — sessions
        # would create fine but ``GET /v1/chat/sessions/{id}`` would
        # 404 because the comparison never matched.
        user_id_str = user.clerk_user_id
        session = _resolve_or_create_session(
            store=store,
            user_id=user_id_str,
            session_id=body.session_id,
            persona_text=persona,
            retrieval_factory=factory,
        )

        # Pre-flight (case-credit + RPM) BEFORE we start streaming.
        # In the case-credit model:
        #   1. Enforce the per-user RPM rate cap.
        #   2. Reserve a credit for this session if one isn't already
        #      attached. New sessions need ``body.case_id`` so we know
        #      which case to bill against; resumed sessions inherit the
        #      previously-reserved credit.
        #   3. Record an up-front ``llm_call`` audit row; tokens get
        #      patched after the stream.
        # Skip everything when no DB factory is wired (in-memory test
        # path) so existing tests don't need DB fixtures.
        usage_event_id: int | None = None
        case_id_for_session: int | None = None
        case_tier_for_session: str | None = None
        if db_session_factory is not None and isinstance(store, DbSessionStore):
            with db_session_factory() as db:
                try:
                    db_user = default_resolve_user(db, user_id_str)
                except LookupError as exc:
                    raise HTTPException(
                        status_code=401, detail="Unknown user"
                    ) from exc

                enforce_request_rate(db, db_user)

                try:
                    db_session_pk = int(session.session_id)
                except ValueError:
                    db_session_pk = None

                # Resolve / reserve the credit for this session. If the
                # session already has one attached (resume path), use
                # that. Otherwise the request must carry ``case_id``.
                from advisor.db.models import (  # noqa: PLC0415 — local import to avoid heavy import at module load
                    CaseCredit as _CaseCredit,
                    ChatSession as _DbChatSession,
                )

                db_chat_session = (
                    db.get(_DbChatSession, db_session_pk)
                    if db_session_pk is not None
                    else None
                )
                if db_chat_session is None:
                    raise HTTPException(
                        status_code=404,
                        detail={"code": "session_not_found"},
                    )

                existing_credit = (
                    db.query(_CaseCredit)
                    .filter(
                        _CaseCredit.session_id == db_chat_session.id,
                        _CaseCredit.state.in_(["reserved", "consumed"]),
                    )
                    .one_or_none()
                )
                if existing_credit is None:
                    # Resume-path safety net: if the session is already
                    # attached to a case in the DB but the live credit
                    # got refunded / expired (or the client just didn't
                    # bother sending case_id on a follow-up turn), fall
                    # back to the session's stored case_id rather than
                    # forcing the client to re-supply it.
                    effective_case_id = (
                        body.case_id
                        if body.case_id is not None
                        else db_chat_session.case_id
                    )
                    if effective_case_id is None:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "case_id_required",
                                "message": (
                                    "case_id is required for new sessions in "
                                    "the case-credit model. Open a case via "
                                    "POST /v1/cases first."
                                ),
                            },
                        )
                    case_row = db.get(Case, effective_case_id)
                    if case_row is None or case_row.user_id != db_user.id:
                        raise HTTPException(
                            status_code=404, detail={"code": "case_not_found"}
                        )
                    if case_row.current_tier is None:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "case_no_active_tier",
                                "message": (
                                    "Case has no active tier; reserve a "
                                    "credit by opening it through "
                                    "POST /v1/cases."
                                ),
                            },
                        )
                    db_chat_session.case_id = case_row.id
                    db_chat_session.tier = case_row.current_tier
                    # Initialise the per-case budget remaining from the
                    # tier's full budget minus what the case has burned
                    # in earlier sessions (reopen path).
                    from advisor.llm.budget import (  # noqa: PLC0415
                        case_budget_for,
                    )

                    db_chat_session.token_budget_remaining = max(
                        0,
                        case_budget_for(case_row.current_tier)
                        - (case_row.tokens_consumed or 0),
                    )
                    credit = reserve_credit_for_session(
                        db,
                        db_user,
                        session=db_chat_session,
                        case=case_row,
                        tier=case_row.current_tier,
                    )
                    case_id_for_session = case_row.id
                    case_tier_for_session = credit.tier
                else:
                    case_id_for_session = existing_credit.case_id
                    case_tier_for_session = existing_credit.tier

                usage_event = record_llm_call(
                    db,
                    db_user,
                    session_id=db_session_pk,
                    case_id=case_id_for_session,
                    model=session.model,
                    provider=getattr(gateway, "name", None),
                )
                usage_event_id = usage_event.id

                # Pull the case's persisted spatial facts so we can
                # inject them into the system prompt suffix. Read happens
                # inside the open transaction; the formatted block is
                # carried out via ``case_spatial_facts``.
                if case_id_for_session is not None:
                    case_for_facts = db.get(Case, case_id_for_session)
                    case_spatial_facts = (
                        (case_for_facts.metadata_json or {}).get("spatial_facts")
                        if case_for_facts is not None
                        else None
                    )
                else:
                    case_spatial_facts = None
        else:
            case_spatial_facts = None

        # Append the lot-facts preamble to the system prompt. We
        # recompute from the bare ``persona`` each turn (rather than
        # appending to ``session.system_prompt``) because the session
        # store persists the prompt — appending would compound the
        # block across resumes.
        if case_spatial_facts:
            from layer2.spatial.extractor import (  # noqa: PLC0415
                format_lot_facts_block,
            )

            facts_block = format_lot_facts_block(case_spatial_facts)
            if facts_block:
                session.system_prompt = persona + "\n\n" + facts_block

        # Mirror case context onto the in-memory ChatSession so
        # ``send_user_message_blocking`` can update the budget ledger
        # and surface the per-turn upgrade-request drain.
        if case_id_for_session is not None:
            session.case_id = case_id_for_session
            session.tier = case_tier_for_session
            if (
                session.token_budget_remaining is None
                and case_tier_for_session is not None
            ):
                from advisor.llm.budget import case_budget_for  # noqa: PLC0415

                session.token_budget_remaining = case_budget_for(
                    case_tier_for_session
                )

        async def event_stream() -> AsyncIterator[dict[str, str]]:
            # Send the session id up front so the frontend can persist
            # it before the first content chunk arrives.
            yield {
                "event": "session",
                "data": json.dumps(
                    {
                        "session_id": session.session_id,
                        "case_id": case_id_for_session,
                        "tier": case_tier_for_session,
                    }
                ),
            }
            stream_failed = False
            try:
                async for stream_event in session.send_user_message(
                    gateway, body.message
                ):
                    yield _format_sse_event(stream_event)
            except Exception as exc:  # noqa: BLE001 — surface to client
                stream_failed = True
                logger.exception("chat stream failed")
                yield {
                    "event": "chat_error",
                    "data": json.dumps(
                        {
                            "kind": type(exc).__name__,
                            "message": str(exc) or "Internal chat error.",
                        }
                    ),
                }
            finally:
                # Drain any tier-upgrade requests the agent fired via
                # ``request_tier_upgrade`` and emit them as
                # ``case_upgrade_offer`` SSE events. Done outside the
                # try-block so a stream failure still surfaces any
                # upgrade prompt that was already raised mid-turn.
                for offer in session.last_turn_upgrade_requests:
                    yield {
                        "event": "case_upgrade_offer",
                        "data": json.dumps(
                            {
                                "case_id": case_id_for_session,
                                "current_tier": case_tier_for_session,
                                "recommended_tier": offer.get(
                                    "recommended_tier"
                                ),
                                "reason": offer.get("reason"),
                            }
                        ),
                    }

                # Post-stream DB updates: patch tokens, commit / refund
                # the case credit, bump the per-case ledger, and emit a
                # budget warning if we're in the danger zone.
                _patch_usage_event_tokens(
                    db_session_factory=db_session_factory,
                    usage_event_id=usage_event_id,
                    chat_session=session,
                )
                budget_warning = _settle_case_credit(
                    db_session_factory=db_session_factory,
                    chat_session=session,
                    case_id=case_id_for_session,
                    tier=case_tier_for_session,
                    stream_failed=stream_failed,
                )
                if budget_warning is not None:
                    yield {
                        "event": "case_budget_warning",
                        "data": json.dumps(budget_warning),
                    }

        return EventSourceResponse(event_stream())

    @app.get("/v1/chat/sessions")
    async def list_sessions(
        user: User = Depends(require_user),
    ) -> ChatSessionList:
        """Return the current user's sessions, newest-first.

        Order key: ``updated_at`` when present (the DB store always
        populates it; the in-memory store populates it after the first
        turn). Sessions that have never been written to fall back to
        dict insertion order so a freshly minted empty session still
        sorts above older ones from the same render.
        """
        user_id_str = user.clerk_user_id
        sessions = store.list_for_user(user_id_str)
        ordered = sorted(
            enumerate(sessions),
            key=lambda pair: (
                pair[1].updated_at.timestamp()
                if pair[1].updated_at is not None
                else float(pair[0])
            ),
            reverse=True,
        )
        summaries = [_summarise_session(s) for _, s in ordered]
        return ChatSessionList(sessions=summaries)

    @app.get("/v1/chat/sessions/{session_id}")
    async def get_session(
        session_id: str,
        user: User = Depends(require_user),
    ) -> ChatSessionResponse:
        user_id_str = user.clerk_user_id
        session = store.get(session_id)
        if session is None or session.user_id != user_id_str:
            # 404, not 403, because leaking "this session exists but
            # isn't yours" would let an attacker enumerate session
            # ids. Treat unauth and missing as the same response.
            raise HTTPException(status_code=404, detail="Session not found")
        return ChatSessionResponse(
            session_id=session.session_id,
            user_id=session.user_id,
            model=session.model,
            messages=session.messages,
            case_id=session.case_id,
            tier=session.tier,
        )

    return app


def _summarise_session(session: ChatSession) -> ChatSessionSummary:
    """Project a full ``ChatSession`` into the lightweight sidebar shape.

    Title comes from the first user message in the conversation,
    truncated. Tool-result intermediate user messages (whose ``content``
    is a list of blocks rather than a string) are skipped. If no user
    message has ever been sent, fall back to a placeholder.

    ``message_count`` counts only the rounds a human would call a
    "turn" — a user input or an assistant text reply — so a session
    that ran a 3-tool-call loop and produced one final answer reads
    as 2 messages, not 8.
    """
    title = "New reading"
    user_count = 0
    assistant_text_count = 0
    for m in session.messages:
        if m.role == LLMRole.USER:
            if isinstance(m.content, str):
                user_count += 1
                if title == "New reading":
                    title = m.content[:80] + ("…" if len(m.content) > 80 else "")
            # else: tool_result intermediate, ignore
        elif m.role == LLMRole.ASSISTANT and isinstance(m.content, list):
            if any(
                getattr(b, "type", None) == "text"
                and getattr(b, "text", "").strip()
                for b in m.content
            ):
                assistant_text_count += 1
    return ChatSessionSummary(
        session_id=session.session_id,
        model=session.model,
        title=title,
        message_count=user_count + assistant_text_count,
        updated_at=(
            session.updated_at.isoformat()
            if session.updated_at is not None
            else None
        ),
    )


def _settle_case_credit(
    *,
    db_session_factory: DbSessionFactory | None,
    chat_session: ChatSession,
    case_id: int | None,
    tier: str | None,
    stream_failed: bool,
) -> dict | None:
    """Post-stream credit settlement and budget-warning derivation.

    Behaviour:

    * If the stream errored before any tool call landed → refund the
      reserved credit (the user got nothing).
    * If the turn produced a qualifying assistant message AND at least
      one prior tool_use block → commit the reserved credit (consumed).
      "Qualifying" is defined as: final assistant content includes a
      non-empty TextBlock AND the message history has at least one
      ToolUseBlock from this session.
    * Otherwise (empty assistant turn) → refund.
    * Bump ``advisor_case.tokens_consumed`` by the turn's input+output.
    * Compute the budget-warning payload when ``token_budget_remaining``
      drops below 25% of the tier's full budget.

    All DB work happens inside a single ``session_scope()`` — failing
    here would otherwise propagate out of the SSE generator and surface
    as a confusing 500 *after* the stream had already been delivered.
    Returns the warning payload dict (or ``None`` to suppress the
    warning event).
    """
    if db_session_factory is None or case_id is None:
        return None

    qualifying = _turn_was_qualifying(chat_session)
    usage = chat_session.last_turn_usage
    spent = (
        (usage.input_tokens + usage.output_tokens)
        if usage is not None
        else 0
    )

    try:
        with db_session_factory() as db:
            try:
                db_session_pk = int(chat_session.session_id)
            except ValueError:
                db_session_pk = None
            if db_session_pk is not None:
                if stream_failed or not qualifying:
                    refund_credit_for(
                        db,
                        session_id=db_session_pk,
                        reason=(
                            "stream_error"
                            if stream_failed
                            else "non_qualifying_turn"
                        ),
                    )
                else:
                    commit_credit_for(db, session_id=db_session_pk)
            if spent:
                add_case_tokens(
                    db,
                    case_id=case_id,
                    input_tokens=usage.input_tokens if usage else 0,
                    output_tokens=usage.output_tokens if usage else 0,
                )
    except Exception:  # noqa: BLE001 — last-mile settlement; don't crash the SSE
        logger.exception(
            "failed to settle case credit for session %s",
            chat_session.session_id,
        )
        return None

    if (
        tier is None
        or chat_session.token_budget_remaining is None
        or chat_session.token_budget_remaining <= 0
    ):
        return None
    from advisor.llm.budget import case_budget_for  # noqa: PLC0415

    full = case_budget_for(tier)
    if full <= 0:
        return None
    fraction_remaining = chat_session.token_budget_remaining / full
    if fraction_remaining > 0.25:
        return None
    return {
        "case_id": case_id,
        "tier": tier,
        "remaining_tokens": chat_session.token_budget_remaining,
        "tier_budget": full,
        "fraction_remaining": fraction_remaining,
    }


def _turn_was_qualifying(chat_session: ChatSession) -> bool:
    """Heuristic: did this turn produce billable output?

    True iff the conversation contains at least one ``ToolUseBlock``
    AND the final assistant message has a non-empty ``TextBlock``. The
    "abandoned" definition in the brief: empty assistant turn or stream
    error before any tool call → refund. Both conditions fail this
    check.
    """
    has_tool_use = False
    final_text_non_empty = False
    for message in chat_session.messages:
        if not isinstance(message.content, list):
            continue
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                has_tool_use = True
    last = next(
        (
            m
            for m in reversed(chat_session.messages)
            if m.role == LLMRole.ASSISTANT
        ),
        None,
    )
    if last is not None and isinstance(last.content, list):
        for block in last.content:
            if (
                getattr(block, "type", None) == "text"
                and getattr(block, "text", "").strip()
            ):
                final_text_non_empty = True
                break
    return has_tool_use and final_text_non_empty


def _patch_usage_event_tokens(
    *,
    db_session_factory: DbSessionFactory | None,
    usage_event_id: int | None,
    chat_session: ChatSession,
) -> None:
    """Patch the up-front ``UsageEvent`` with real aggregate token counts.

    No-op when the DB factory isn't wired or the up-front
    ``enforce_and_record_query`` was skipped (no event id to update).
    Catches and logs any DB exception — failing here would otherwise
    propagate out of the SSE generator and surface to the client as a
    confusing 500 *after* the stream had already been delivered.

    When the cost-circuit breaker fired on this turn,
    ``chat_session.last_turn_circuit_trip`` carries the estimate and
    budget; we attach those to ``metadata_json`` so the trip is
    visible alongside the call it happened on. Tracking it on the
    existing ``llm_call`` row (rather than a new ``cost_circuit_trip``
    event_type) keeps the audit shape flat — analytics queries are
    "events where metadata_json->>cost_circuit_trip = true".
    """
    if db_session_factory is None or usage_event_id is None:
        return
    usage = chat_session.last_turn_usage
    trip = chat_session.last_turn_circuit_trip
    if usage is None and trip is None:
        # Silent skip is invisible to the DB row (it stays at the up-front
        # zeros) so log it: this is the gateway-raised-before-usage path
        # (auth/credit error, 400, network drop) that leaves an
        # untraceable (0,0) UsageEvent. The next occurrence is then
        # diagnosable from logs alone.
        logger.info(
            "skipping UsageEvent token patch id=%s: "
            "last_turn_usage is None and no circuit trip; "
            "row stays at (0,0). Likely the LLM call raised before any "
            "iteration recorded usage (gateway error, auth failure, "
            "client disconnect mid-call).",
            usage_event_id,
        )
        return
    metadata: dict | None = None
    if trip is not None:
        metadata = {
            "cost_circuit_trip": True,
            "estimated_input_tokens": trip.estimated_input_tokens,
            "turn_input_token_budget": trip.budget,
            "trip_iteration": trip.iteration,
        }
    try:
        with db_session_factory() as db:
            update_usage_event_tokens(
                db,
                usage_event_id=usage_event_id,
                tokens_input=usage.input_tokens if usage else 0,
                tokens_output=usage.output_tokens if usage else 0,
                metadata=metadata,
            )
    except Exception:  # noqa: BLE001 — last-mile audit update; don't fail the request
        logger.exception(
            "failed to patch tokens on UsageEvent id=%s", usage_event_id
        )


def _build_user_dependency(
    verifier: ClerkVerifier | None,
    db_session_factory: Callable[[], AbstractContextManager[Session]] | None,
) -> Callable[..., User]:
    """Return a Depends-compatible callable that yields a ``User``.

    Production path: real Clerk verification + DB-backed user
    resolution. Test fallback: an ``X-Test-User-Id`` header that is
    wrapped in a synthetic ``User`` instance so route handlers see the
    same shape regardless of code path.
    """
    if verifier is not None and db_session_factory is not None:
        return current_user_dependency(verifier, db_session_factory)

    # test-only fallback — header-based auth so existing chat-behaviour
    # tests don't have to mint JWTs to exercise unrelated route logic.
    def _require_test_user_id(
        x_test_user_id: str | None = Header(default=None),
    ) -> User:
        if not x_test_user_id or not x_test_user_id.strip():
            raise HTTPException(
                status_code=401,
                detail="Missing X-Test-User-Id header (test-only fallback).",
            )
        cleaned = x_test_user_id.strip()
        # Build a transient ``User`` whose ``id`` echoes the supplied
        # value. We hash a stable integer when the header isn't already
        # numeric so existing tests can keep passing strings like
        # ``user_alice`` without surprise. The handler downstream only
        # uses ``str(user.id)``, so what matters is round-trip
        # stability per request, not numeric meaning.
        return _TestUser(id=cleaned)

    return _require_test_user_id


class _TestUser:
    """Stand-in for ``User`` used only by the test-only fallback path.

    We avoid instantiating the real SQLAlchemy ``User`` model here
    because doing so would attach a detached instance to the global
    Identity Map and confuse any real DB session opened later in the
    test process. A bare object with the attributes the routes read is
    enough.

    Both ``id`` and ``clerk_user_id`` are set to the same header value
    so route handlers can read ``user.clerk_user_id`` uniformly,
    matching the real ``User`` model's attribute shape.
    """

    __slots__ = ("id", "clerk_user_id")

    def __init__(self, *, id: str) -> None:  # noqa: A002
        self.id = id
        self.clerk_user_id = id


def _resolve_or_create_session(
    *,
    store: SessionStore,
    user_id: str,
    session_id: str | None,
    persona_text: str,
    retrieval_factory: Callable[[], Any],
) -> ChatSession:
    """Look up a session by id or mint a new one with bound tools.

    A resumed session keeps its existing tool defs/handlers — we don't
    re-bind them per request because that would stomp any handlers
    the test may have monkey-patched. A new session gets a fresh
    factory-bound tool set.
    """
    if session_id is not None:
        existing = store.get(session_id)
        if existing is not None and existing.user_id == user_id:
            return existing
        # Fall through and create a new session with the requested id
        # discarded. We could 404 here instead, but for v1 it's nicer
        # to silently create — the frontend can recover from a server
        # restart without surfacing an error to the user.

    tool_defs, tool_handlers = _build_tools_with_factory(retrieval_factory)
    return store.create(
        user_id=user_id,
        system_prompt=persona_text,
        tool_defs=tool_defs,
        tool_handlers=tool_handlers,
    )


def _build_tools_with_factory(
    retrieval_factory: Callable[[], Any],
) -> tuple[list, dict]:
    """Build retrieval tools bound to a per-request factory.

    ``retrieval_factory`` may be either a callable returning a
    ``RetrievalService`` (tests) or a callable returning a context
    manager that yields one (production's session_scope-backed
    factory). ``build_bylaw_tools`` handles both shapes inside each
    handler — it enters the cm before the synchronous service call
    and exits it after, so the underlying SQLAlchemy session always
    closes even if the LLM disconnects mid-stream and the chat
    coroutine is cancelled.
    """
    return build_bylaw_tools(retrieval_factory)


def _format_sse_event(event: StreamEvent) -> dict[str, str]:
    """Render a ``StreamEvent`` as an SSE event dict.

    sse_starlette's ``EventSourceResponse`` accepts dict messages with
    ``event`` and ``data`` keys and handles framing for us. We pass
    the unified event ``type`` as the SSE event name and the full
    serialized model as the data payload — that matches what real
    Anthropic streams send and gives the frontend a consistent shape
    regardless of whether streaming is real or synthetic.
    """
    return {
        "event": event.type,
        "data": json.dumps(event.model_dump(mode="json")),
    }
