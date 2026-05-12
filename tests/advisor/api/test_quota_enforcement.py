"""Monthly quota enforcement on the ``/v1/chat`` endpoint.

Stands up a sqlite DB, seeds a user, builds the FastAPI app with both
a ``db_session_factory`` and the default DB-backed store, and drives
the chat route via TestClient. Each test owns its DB so monkey-patches
of ``advisor.db.quota._utc_today`` don't leak across tests.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from advisor.api import create_app
from advisor.db.models import UsageEvent, User
from advisor.db import quota as quota_module
from advisor.llm import TokenUsage
from advisor.llm.budget import default_token_budget
from advisor.llm.mock import MockGateway, text_response
from layer1.db.init_db import create_all


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _build_factory(db_url: str):
    engine = create_engine(db_url, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def db_session_factory() -> Iterator[Session]:
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return db_session_factory, factory


def _seed_user(
    factory,
    *,
    clerk_user_id: str = "clerk_q",
    monthly_query_limit: int = 100,
    monthly_queries_used: int = 0,
    month_started_at: date = date(2026, 5, 1),
) -> int:
    s = factory()
    try:
        user = User(
            clerk_user_id=clerk_user_id,
            email="q@example.com",
            full_name="Q User",
            plan_tier="free",
            monthly_query_limit=monthly_query_limit,
            monthly_queries_used=monthly_queries_used,
            month_started_at=month_started_at,
        )
        s.add(user)
        s.commit()
        return user.id
    finally:
        s.close()


def _make_app(*, gateway, db_session_factory):
    """App with the DB-backed store wired (via auto-default)."""
    return create_app(
        gateway=gateway,
        retrieval_service_factory=lambda: None,
        persona_text="You are a senior urban planner.",
        db_session_factory=db_session_factory,
    )


def _parse_sse_event_names(text: str) -> list[str]:
    names: list[str] = []
    for line in text.splitlines():
        if line.startswith("event:"):
            names.append(line.split(":", 1)[1].strip())
    return names


def test_chat_succeeds_when_under_quota(tmp_path: Path, monkeypatch) -> None:
    """Happy path: a fresh user with quota free can chat normally."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_under")

    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 5, 15)
    )

    gateway = MockGateway(scripted=[text_response("hello back")])
    app = _make_app(gateway=gateway, db_session_factory=db_session_factory)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={"message": "hi"},
            headers={"X-Test-User-Id": str(user_id)},
        )
    assert response.status_code == 200
    assert "message_stop" in _parse_sse_event_names(response.text)


def test_chat_returns_429_when_quota_exceeded(
    tmp_path: Path, monkeypatch
) -> None:
    """A user at their limit gets a structured 429."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(
        factory,
        clerk_user_id="clerk_over",
        monthly_query_limit=2,
        monthly_queries_used=2,
    )
    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 5, 15)
    )

    gateway = MockGateway(scripted=[text_response("would not be sent")])
    app = _make_app(gateway=gateway, db_session_factory=db_session_factory)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={"message": "hi"},
            headers={"X-Test-User-Id": str(user_id)},
        )
    assert response.status_code == 429
    body = response.json()
    detail = body["detail"]
    assert detail["code"] == "monthly_quota_exceeded"
    assert detail["limit"] == 2
    assert detail["used"] == 2
    assert "message" in detail


def test_quota_increments_on_successful_chat(
    tmp_path: Path, monkeypatch
) -> None:
    """A successful chat bumps ``monthly_queries_used`` by 1."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(
        factory,
        clerk_user_id="clerk_inc",
        monthly_query_limit=10,
        monthly_queries_used=3,
    )
    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 5, 15)
    )

    gateway = MockGateway(
        scripted=[text_response("a"), text_response("b")]
    )
    app = _make_app(gateway=gateway, db_session_factory=db_session_factory)

    with TestClient(app) as client:
        r1 = client.post(
            "/v1/chat",
            json={"message": "first"},
            headers={"X-Test-User-Id": str(user_id)},
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/v1/chat",
            json={"message": "second"},
            headers={"X-Test-User-Id": str(user_id)},
        )
        assert r2.status_code == 200

    s = factory()
    try:
        user = s.get(User, user_id)
        assert user is not None
        # Was 3, +2 successful chats => 5.
        assert user.monthly_queries_used == 5
    finally:
        s.close()


def test_usage_event_tokens_patched_after_stream(
    tmp_path: Path, monkeypatch
) -> None:
    """After the SSE stream completes the chat route patches the
    up-front ``UsageEvent`` row with the aggregate token counts the
    LLM reported. Quota counters are unaffected — only the audit row
    learns the real numbers."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_tokens")

    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 5, 15)
    )

    gateway = MockGateway(
        scripted=[text_response("answer")],
        default_usage=TokenUsage(input_tokens=77, output_tokens=88),
    )
    app = _make_app(gateway=gateway, db_session_factory=db_session_factory)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={"message": "hi"},
            headers={"X-Test-User-Id": str(user_id)},
        )
    assert response.status_code == 200

    s = factory()
    try:
        events = (
            s.query(UsageEvent)
            .filter(UsageEvent.user_id == user_id)
            .filter(UsageEvent.event_type == "llm_call")
            .all()
        )
        assert len(events) == 1
        assert events[0].tokens_input == 77
        assert events[0].tokens_output == 88
    finally:
        s.close()


def test_cost_circuit_trip_recorded_in_usage_event_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    """When the per-turn input-token budget is exceeded, the chat
    route patches the up-front ``llm_call`` UsageEvent's
    ``metadata_json`` with the trip details (estimated tokens,
    budget, iteration). Analytics filters on
    ``metadata_json->>cost_circuit_trip`` to count how often the
    breaker saves us.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_trip")

    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 5, 15)
    )

    # Pin a tiny budget via env var so the breaker fires on the first
    # iteration of a normal-sized prompt. cache_clear() ensures the
    # newly-constructed ChatSession reads the override rather than a
    # value cached from an earlier test run.
    monkeypatch.setenv("ADVISOR_TURN_INPUT_TOKEN_BUDGET", "5")
    default_token_budget.cache_clear()

    gateway = MockGateway(
        scripted=[text_response("bounded synthesis answer")],
        default_usage=TokenUsage(input_tokens=11, output_tokens=22),
    )
    app = _make_app(gateway=gateway, db_session_factory=db_session_factory)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={"message": "hi"},
            headers={"X-Test-User-Id": str(user_id)},
        )
    assert response.status_code == 200

    s = factory()
    try:
        events = (
            s.query(UsageEvent)
            .filter(UsageEvent.user_id == user_id)
            .filter(UsageEvent.event_type == "llm_call")
            .all()
        )
        assert len(events) == 1
        meta = events[0].metadata_json or {}
        assert meta.get("cost_circuit_trip") is True
        assert meta.get("turn_input_token_budget") == 5
        assert meta.get("estimated_input_tokens", 0) > 5
        assert meta.get("trip_iteration", 0) >= 1
        # Tokens still patched alongside the trip metadata:
        assert events[0].tokens_input == 11
        assert events[0].tokens_output == 22
    finally:
        s.close()
        default_token_budget.cache_clear()


def test_quota_resets_at_month_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    """A user maxed out in May can chat in June after the rollover."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(
        factory,
        clerk_user_id="clerk_rollover",
        monthly_query_limit=3,
        monthly_queries_used=3,
        month_started_at=date(2026, 5, 1),
    )

    # Pretend "today" is June 7 — record_query should reset the
    # counter before evaluating the limit.
    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 6, 7)
    )

    gateway = MockGateway(scripted=[text_response("welcome back")])
    app = _make_app(gateway=gateway, db_session_factory=db_session_factory)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={"message": "first of june"},
            headers={"X-Test-User-Id": str(user_id)},
        )
    assert response.status_code == 200

    s = factory()
    try:
        user = s.get(User, user_id)
        assert user is not None
        # Reset to 0 then incremented by 1 by this call.
        assert user.monthly_queries_used == 1
        assert user.month_started_at == date(2026, 6, 1)
    finally:
        s.close()
