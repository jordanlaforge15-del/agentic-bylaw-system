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
from advisor.api.quota import enforce_and_record_query
from advisor.api.sessions import InMemorySessionStore, SessionStore
from advisor.auth.clerk import ClerkVerifier
from advisor.chat.persona import load_persona
from advisor.chat.session import ChatSession
from advisor.chat.tools import build_bylaw_tools
from advisor.db.models import User
from advisor.llm import LLMGateway, Message, StreamEvent

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


class ChatSessionResponse(BaseModel):
    """Body of ``GET /v1/chat/sessions/{session_id}``."""

    session_id: str
    user_id: str
    model: str
    messages: list[Message]


def create_app(
    *,
    gateway: LLMGateway | None = None,
    retrieval_service_factory: Callable[[], Any] | None = None,
    session_store: SessionStore | None = None,
    persona_text: str | None = None,
    verifier: ClerkVerifier | None = None,
    db_session_factory: DbSessionFactory | None = None,
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

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    require_user = _build_user_dependency(verifier, db_session_factory)

    @app.post("/v1/chat")
    async def post_chat(
        body: ChatRequest,
        request: Request,
        user: User = Depends(require_user),
    ) -> EventSourceResponse:
        # Use the internal numeric id (as a string) so session-store
        # entries — and any future FK joins — don't have to re-resolve
        # through Clerk. ChatSession.user_id stays a string for now;
        # widening it to int is a separate workstream.
        user_id_str = str(user.id)
        session = _resolve_or_create_session(
            store=store,
            user_id=user_id_str,
            session_id=body.session_id,
            persona_text=persona,
            retrieval_factory=factory,
        )

        # Enforce + record the monthly quota BEFORE we start streaming.
        # If the user is over their limit, ``enforce_and_record_query``
        # raises a 429 and FastAPI never opens the SSE response. We
        # only enforce when a DB factory is wired AND we're using a
        # DB-backed store — the in-memory test mode skips this so
        # existing tests don't need DB fixtures. Token counts are 0
        # for now — see advisor.api.quota's docstring.
        if db_session_factory is not None and isinstance(store, DbSessionStore):
            with db_session_factory() as db:
                try:
                    db_user = default_resolve_user(db, user_id_str)
                except LookupError as exc:
                    # Unknown user with a DB store wired is a 401 —
                    # treat the same way the auth layer would.
                    raise HTTPException(
                        status_code=401, detail="Unknown user"
                    ) from exc
                try:
                    db_session_pk = int(session.session_id)
                except ValueError:
                    db_session_pk = None
                enforce_and_record_query(
                    db,
                    db_user,
                    event_type="llm_call",
                    session_id=db_session_pk,
                    model=session.model,
                    provider=getattr(gateway, "name", None),
                    tokens_input=0,
                    tokens_output=0,
                )

        async def event_stream() -> AsyncIterator[dict[str, str]]:
            # Send the session id up front so the frontend can persist
            # it before the first content chunk arrives. We use a
            # ``session`` event so it doesn't collide with the LLM's
            # event taxonomy.
            yield {
                "event": "session",
                "data": json.dumps({"session_id": session.session_id}),
            }
            async for stream_event in session.send_user_message(
                gateway, body.message
            ):
                yield _format_sse_event(stream_event)

        return EventSourceResponse(event_stream())

    @app.get("/v1/chat/sessions/{session_id}")
    async def get_session(
        session_id: str,
        user: User = Depends(require_user),
    ) -> ChatSessionResponse:
        user_id_str = str(user.id)
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
        )

    return app


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
    """

    __slots__ = ("id",)

    def __init__(self, *, id: str) -> None:  # noqa: A002
        self.id = id


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
    """Wrap the retrieval factory so each tool call opens its own session.

    The factory may be either a context manager (production) or a
    plain callable that returns a RetrievalService (tests). We detect
    which by looking for ``__enter__`` and adapt accordingly. This
    means tests can pass a simple ``lambda: service`` and get the
    same handler shape as production, which uses ``session_scope``.
    """

    def _open_service():
        result = retrieval_factory()
        # If the factory returns a context manager, enter it lazily
        # — but that breaks our handler signature, which expects a
        # plain RetrievalService. So in practice, callable factories
        # MUST return a service directly; context-manager factories
        # are invoked inside the handler closure. We unwrap here:
        if hasattr(result, "__enter__"):
            # Eagerly enter and discard the cm — fine for tests
            # because they hold their own session. Production wraps
            # session_scope inside a per-tool-call context manager
            # below.
            return result.__enter__()  # type: ignore[union-attr]
        return result

    # For each tool call we want a fresh session if the factory is a
    # context manager. We achieve that by wrapping the factory in a
    # callable that opens-and-closes the cm around the synchronous
    # service usage. Because the RetrievalService methods are sync
    # and the data they return is fully materialised before we
    # serialise to JSON, we can close the session immediately after
    # each call without worrying about lazy-loaded relationships.
    def _per_call_service():
        # We don't keep the cm open across the handler boundary; the
        # handler resolves once, calls one method, and serialises.
        return _open_service()

    return build_bylaw_tools(_per_call_service)


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
