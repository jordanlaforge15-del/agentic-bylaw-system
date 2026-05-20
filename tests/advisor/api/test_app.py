"""End-to-end FastAPI tests using TestClient and a MockGateway.

We exercise the full chat path: header auth, session creation /
resumption, SSE event streaming, and the debug session-history
endpoint. Each test injects mocks via ``create_app`` arguments so no
external deps (Anthropic, sqlite, etc.) are touched.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from advisor.api import InMemorySessionStore, create_app
from advisor.llm.mock import MockGateway, text_response


# ----- helpers ---------------------------------------------------------------


def _make_app(*, gateway: MockGateway | None = None, store: InMemorySessionStore | None = None):
    """Build an app with stub dependencies. The retrieval factory is
    a no-op lambda — we only test paths that don't actually call
    retrieval, because the mock gateway's scripted responses don't
    request tools."""
    return create_app(
        gateway=gateway or MockGateway(scripted=[text_response("hello back")]),
        retrieval_service_factory=lambda: None,
        session_store=store or InMemorySessionStore(),
        persona_text="You are a senior urban planner.",
    )


def _parse_sse_events(text: str) -> list[dict[str, str]]:
    """Parse an SSE response body into a list of {event, data} dicts.

    The wire format is:

        event: <name>
        data: <payload>
        \\n

    sse_starlette also sends ``ping`` keepalive frames; we keep them
    in the parsed result and let the caller filter — the v1 frontend
    will do the same.
    """
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if line == "":
            if current:
                events.append(current)
                current = {}
            continue
        if ":" in line:
            field, _, value = line.partition(":")
            # SSE lines start with optional whitespace after the
            # colon — strip just the leading space, not internal
            # whitespace.
            current[field.strip()] = value.lstrip()
    if current:
        events.append(current)
    return events


# ----- tests ------------------------------------------------------------------


def test_healthz_returns_ok():
    """Liveness check — no auth required, returns 200."""
    app = _make_app()
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_without_user_id_returns_401():
    """v1 placeholder auth: an absent X-User-Id is unauthorised.
    Workstream 3's bearer-token auth will replace this; the 401
    semantic is the same so the frontend doesn't need to change."""
    app = _make_app()
    with TestClient(app) as client:
        response = client.post("/v1/chat", json={"message": "hi"})
    assert response.status_code == 401


def test_chat_with_empty_user_id_returns_401():
    """Empty / whitespace user-id is treated as missing — otherwise
    a buggy frontend that forwards an empty header could create
    orphan sessions in the store."""
    app = _make_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat", json={"message": "hi"}, headers={"X-Test-User-Id": "  "}
        )
    assert response.status_code == 401


def test_chat_streams_sse_events():
    """A successful chat returns text/event-stream with at least
    one message_start and one message_stop event. Body parses as
    valid SSE with JSON-encoded data payloads."""
    gateway = MockGateway(scripted=[text_response("the answer is 42")])
    app = _make_app(gateway=gateway)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={"message": "what is the answer"},
            headers={"X-Test-User-Id": "user_alice"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_events(response.text)
    event_names = [e.get("event") for e in events]
    assert "session" in event_names
    assert "message_start" in event_names
    assert "message_stop" in event_names

    # The session event carries the new session id and parses as JSON.
    session_event = next(e for e in events if e.get("event") == "session")
    payload = json.loads(session_event["data"])
    assert "session_id" in payload
    assert payload["session_id"]


def test_chat_creates_new_session_when_no_id_provided():
    """Omitting session_id mints a new session. The store should
    reflect that exactly one session exists for the user after the
    call."""
    store = InMemorySessionStore()
    gateway = MockGateway(scripted=[text_response("x")])
    app = _make_app(gateway=gateway, store=store)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={"message": "hi"},
            headers={"X-Test-User-Id": "user_bob"},
        )
    assert response.status_code == 200
    sessions = store.list_for_user("user_bob")
    assert len(sessions) == 1


def test_chat_resumes_session_across_two_requests():
    """Two requests with the same session_id should append to the
    same conversation. After both calls the session's message list
    contains both user messages plus both assistant responses."""
    store = InMemorySessionStore()
    gateway = MockGateway(
        scripted=[text_response("first"), text_response("second")]
    )
    app = _make_app(gateway=gateway, store=store)
    with TestClient(app) as client:
        first = client.post(
            "/v1/chat",
            json={"message": "first turn"},
            headers={"X-Test-User-Id": "user_carol"},
        )
        assert first.status_code == 200
        first_events = _parse_sse_events(first.text)
        session_id = json.loads(
            next(e for e in first_events if e.get("event") == "session")["data"]
        )["session_id"]

        second = client.post(
            "/v1/chat",
            json={"message": "second turn", "session_id": session_id},
            headers={"X-Test-User-Id": "user_carol"},
        )
        assert second.status_code == 200

    # The second call should NOT have created a new session.
    sessions = store.list_for_user("user_carol")
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == session_id
    # Two user turns + two assistant turns = 4 messages in history.
    assert len(session.messages) == 4


def test_chat_different_user_cannot_resume_session():
    """A session id from user A used by user B must not resume A's
    session — that would leak conversation history. v1's behaviour
    is to silently mint a new session for B; we verify A's session
    is unchanged."""
    store = InMemorySessionStore()
    gateway = MockGateway(scripted=[text_response("a"), text_response("b")])
    app = _make_app(gateway=gateway, store=store)
    with TestClient(app) as client:
        first = client.post(
            "/v1/chat",
            json={"message": "alice's question"},
            headers={"X-Test-User-Id": "alice"},
        )
        alice_session_id = json.loads(
            next(
                e
                for e in _parse_sse_events(first.text)
                if e.get("event") == "session"
            )["data"]
        )["session_id"]

        # Mallory tries to resume alice's session:
        second = client.post(
            "/v1/chat",
            json={"message": "stealing", "session_id": alice_session_id},
            headers={"X-Test-User-Id": "mallory"},
        )
        mallory_session_id = json.loads(
            next(
                e
                for e in _parse_sse_events(second.text)
                if e.get("event") == "session"
            )["data"]
        )["session_id"]

    # Different ids — mallory got a fresh session:
    assert mallory_session_id != alice_session_id
    # Alice's session is untouched (still 2 messages: her q + reply):
    alice = store.get(alice_session_id)
    assert alice is not None
    assert len(alice.messages) == 2


def test_get_session_returns_history():
    """The debug endpoint returns the raw message list for the
    session. v1 only — the frontend has its own state and won't
    poll this in production."""
    store = InMemorySessionStore()
    gateway = MockGateway(scripted=[text_response("answer")])
    app = _make_app(gateway=gateway, store=store)
    with TestClient(app) as client:
        post = client.post(
            "/v1/chat",
            json={"message": "ask"},
            headers={"X-Test-User-Id": "user_dan"},
        )
        session_id = json.loads(
            next(
                e
                for e in _parse_sse_events(post.text)
                if e.get("event") == "session"
            )["data"]
        )["session_id"]
        get = client.get(
            f"/v1/chat/sessions/{session_id}",
            headers={"X-Test-User-Id": "user_dan"},
        )
    assert get.status_code == 200
    body = get.json()
    assert body["session_id"] == session_id
    assert body["user_id"] == "user_dan"
    assert len(body["messages"]) == 2


def test_get_session_404_for_other_user():
    """Looking up another user's session must 404, not 403, to
    avoid leaking session-id existence."""
    store = InMemorySessionStore()
    gateway = MockGateway(scripted=[text_response("x")])
    app = _make_app(gateway=gateway, store=store)
    with TestClient(app) as client:
        post = client.post(
            "/v1/chat",
            json={"message": "ask"},
            headers={"X-Test-User-Id": "owner"},
        )
        session_id = json.loads(
            next(
                e
                for e in _parse_sse_events(post.text)
                if e.get("event") == "session"
            )["data"]
        )["session_id"]
        # An unrelated user pokes the session:
        get = client.get(
            f"/v1/chat/sessions/{session_id}",
            headers={"X-Test-User-Id": "stranger"},
        )
    assert get.status_code == 404


def test_create_app_requires_gateway():
    """Forgetting to inject a gateway must be a loud error rather
    than a 500 at first request — that's the easiest configuration
    bug to introduce when wiring this into a deployment."""
    with pytest.raises(ValueError, match="gateway"):
        create_app(persona_text="x")


# ----- Sidebar title composition ----------------------------------------------


def test_compose_title_combines_anchor_and_question():
    """ABS-22: sidebar title summarises both the address and the question.

    The case-open flow attaches an anchor (typically an address) when
    the case is created, and the first user message carries the
    question. The sidebar should read like "1234 Main St · Can I…".
    Either piece alone is acceptable as a fallback, but "New reading"
    is only correct when both are absent.
    """
    from advisor.api.app import _compose_session_title

    assert (
        _compose_session_title(
            "1234 Main St, Halifax", "Can I build a 6-storey on this lot?"
        )
        == "1234 Main St, Halifax · Can I build a 6-storey on this lot?"
    )
    assert _compose_session_title("1234 Main St", None) == "1234 Main St"
    assert (
        _compose_session_title(None, "Standalone question")
        == "Standalone question"
    )
    assert _compose_session_title(None, None) == "New reading"
    # Whitespace-only inputs collapse to "absent" so a stray space
    # doesn't keep the placeholder out of the fallback branch.
    assert _compose_session_title("   ", None) == "New reading"


def test_compose_title_truncates_long_inputs():
    """Per-piece caps stop a runaway anchor / question from blowing past
    the sidebar's two-line clamp. Truncation is suffixed with an ellipsis
    so the user knows there's more content behind the row."""
    from advisor.api.app import _compose_session_title

    long_anchor = "1234 Some Very Extremely Long Street Name, Halifax NS"
    long_question = (
        "I have a question about the rezoning of this lot under the "
        "Regional Centre Land Use Bylaw and whether the proposed setback…"
    )
    title = _compose_session_title(long_anchor, long_question)
    anchor_part, _sep, question_part = title.partition(" · ")
    assert anchor_part.endswith("…")
    assert question_part.endswith("…")
    assert len(anchor_part) <= 41  # 40 chars + ellipsis
    assert len(question_part) <= 61


# ----- Clerk auth integration -------------------------------------------------


class TestClerkAuthIntegration:
    """Real-auth path: Clerk verification + DB-backed user resolution.

    These tests construct a ``ClerkVerifier`` whose JWKS is the public
    half of an in-test RSA keypair (fixtures live in
    ``tests/advisor/conftest.py``). The DB is a single shared sqlite
    in-memory engine so the same rows are visible across the auth
    dependency, the route handler, and the assertion phase.

    Each test gets its own ``create_app`` instance so cross-test state
    can't leak through the session store or the user table.
    """

    @staticmethod
    def _build_app_and_factory(make_keypair, make_jwks, fake_http_client_cls,
                               fake_response_cls, jwks_url):
        """Wire up an app with real Clerk auth + a sqlite-backed user DB.

        Returns ``(app, keypair, db_session_factory)`` so individual
        tests can sign tokens, mount the TestClient, and inspect the
        ``advisor_user`` table directly.
        """
        from contextlib import contextmanager

        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session, sessionmaker
        from sqlalchemy.pool import StaticPool

        from advisor.auth import ClerkVerifier, JWKSClient
        from layer1.db.base import Base

        keypair = make_keypair()
        # cache_ttl_s well over the test runtime so we never refetch.
        http = fake_http_client_cls(
            response_factory=lambda _url: fake_response_cls(make_jwks(keypair))
        )
        jwks = JWKSClient(jwks_url, http_client=http, cache_ttl_s=3600.0)
        verifier = ClerkVerifier(jwks_client=jwks)

        # StaticPool + ``check_same_thread=False`` is the canonical way
        # to share one in-memory sqlite db across multiple sessions in
        # a test process — see SQLAlchemy docs.
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )

        @contextmanager
        def db_session_factory():
            session: Session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        # Use a callable so the gateway never runs out of responses no
        # matter how many times an individual test calls /v1/chat.
        gateway = MockGateway(callable_=lambda _req: text_response("ok"))
        app = create_app(
            gateway=gateway,
            retrieval_service_factory=lambda: None,
            session_store=InMemorySessionStore(),
            persona_text="persona",
            verifier=verifier,
            db_session_factory=db_session_factory,
        )
        return app, keypair, db_session_factory

    def test_chat_requires_authorization_header(
        self, make_keypair, make_jwks, fake_http_client_cls,
        fake_response_cls, jwks_url,
    ):
        """No Authorization header → 401 with the structured Clerk
        error code so SPAs can branch on it."""
        app, _kp, _factory = self._build_app_and_factory(
            make_keypair, make_jwks, fake_http_client_cls,
            fake_response_cls, jwks_url,
        )
        with TestClient(app) as client:
            response = client.post("/v1/chat", json={"message": "hi"})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "missing_authorization_header"

    def test_chat_rejects_bad_token(
        self, make_keypair, make_jwks, fake_http_client_cls,
        fake_response_cls, jwks_url,
    ):
        """A bearer token whose signature doesn't match our JWKS
        must 401 — Clerk's verifier raises AuthError, the FastAPI
        layer maps it to 401, and the chat route never runs."""
        app, _kp, _factory = self._build_app_and_factory(
            make_keypair, make_jwks, fake_http_client_cls,
            fake_response_cls, jwks_url,
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat",
                json={"message": "hi"},
                headers={"Authorization": "Bearer not-a-real-jwt"},
            )
        assert response.status_code == 401

    def test_chat_creates_user_on_first_contact(
        self, make_keypair, make_jwks, sign_token, fake_http_client_cls,
        fake_response_cls, jwks_url,
    ):
        """A valid token whose sub has never been seen creates a new
        ``advisor_user`` row before the route runs. After the request
        completes the DB has exactly one user with that ``clerk_user_id``
        and the email from the JWT."""
        from advisor.db import User

        app, kp, factory = self._build_app_and_factory(
            make_keypair, make_jwks, fake_http_client_cls,
            fake_response_cls, jwks_url,
        )
        token = sign_token(
            kp, sub="user_2new", email="new@example.com",
            extra_claims={"name": "New User"},
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat",
                json={"message": "hi"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        with factory() as db:
            users = db.query(User).filter(
                User.clerk_user_id == "user_2new"
            ).all()
        assert len(users) == 1
        assert users[0].email == "new@example.com"
        assert users[0].full_name == "New User"

    def test_chat_reuses_existing_user(
        self, make_keypair, make_jwks, sign_token, fake_http_client_cls,
        fake_response_cls, jwks_url,
    ):
        """Two requests with tokens for the same Clerk user must NOT
        insert two rows — the dependency looks up by clerk_user_id
        and returns the existing row."""
        from advisor.db import User

        app, kp, factory = self._build_app_and_factory(
            make_keypair, make_jwks, fake_http_client_cls,
            fake_response_cls, jwks_url,
        )
        token = sign_token(kp, sub="user_2dup", email="dup@example.com")
        with TestClient(app) as client:
            first = client.post(
                "/v1/chat",
                json={"message": "first"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert first.status_code == 200
            second = client.post(
                "/v1/chat",
                json={"message": "second"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert second.status_code == 200
        with factory() as db:
            users = db.query(User).filter(
                User.clerk_user_id == "user_2dup"
            ).all()
        assert len(users) == 1

    def test_chat_updates_email_when_changed(
        self, make_keypair, make_jwks, sign_token, fake_http_client_cls,
        fake_response_cls, jwks_url,
    ):
        """If a returning Clerk user's profile email has changed,
        the existing row's email is updated in place — Clerk is the
        source of truth for that field."""
        from advisor.db import User

        app, kp, factory = self._build_app_and_factory(
            make_keypair, make_jwks, fake_http_client_cls,
            fake_response_cls, jwks_url,
        )
        first_token = sign_token(
            kp, sub="user_2change", email="old@example.com"
        )
        second_token = sign_token(
            kp, sub="user_2change", email="new@example.com"
        )
        with TestClient(app) as client:
            r1 = client.post(
                "/v1/chat",
                json={"message": "1"},
                headers={"Authorization": f"Bearer {first_token}"},
            )
            assert r1.status_code == 200
            r2 = client.post(
                "/v1/chat",
                json={"message": "2"},
                headers={"Authorization": f"Bearer {second_token}"},
            )
            assert r2.status_code == 200
        with factory() as db:
            users = db.query(User).filter(
                User.clerk_user_id == "user_2change"
            ).all()
        assert len(users) == 1
        assert users[0].email == "new@example.com"
