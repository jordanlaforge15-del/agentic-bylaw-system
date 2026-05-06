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
            "/v1/chat", json={"message": "hi"}, headers={"X-User-Id": "  "}
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
            headers={"X-User-Id": "user_alice"},
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
            headers={"X-User-Id": "user_bob"},
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
            headers={"X-User-Id": "user_carol"},
        )
        assert first.status_code == 200
        first_events = _parse_sse_events(first.text)
        session_id = json.loads(
            next(e for e in first_events if e.get("event") == "session")["data"]
        )["session_id"]

        second = client.post(
            "/v1/chat",
            json={"message": "second turn", "session_id": session_id},
            headers={"X-User-Id": "user_carol"},
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
            headers={"X-User-Id": "alice"},
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
            headers={"X-User-Id": "mallory"},
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
            headers={"X-User-Id": "user_dan"},
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
            headers={"X-User-Id": "user_dan"},
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
            headers={"X-User-Id": "owner"},
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
            headers={"X-User-Id": "stranger"},
        )
    assert get.status_code == 404


def test_create_app_requires_gateway():
    """Forgetting to inject a gateway must be a loud error rather
    than a 500 at first request — that's the easiest configuration
    bug to introduce when wiring this into a deployment."""
    with pytest.raises(ValueError, match="gateway"):
        create_app(persona_text="x")
