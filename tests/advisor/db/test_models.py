"""Persist + relationship behaviour for the advisor SQLAlchemy models.

sqlite doesn't enforce foreign keys by default; the cascade tests
flip ``PRAGMA foreign_keys = ON`` per-connection so the test exercises
the same ``ON DELETE CASCADE`` / ``ON DELETE SET NULL`` semantics that
production Postgres applies natively.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from advisor.db import ChatMessage, ChatSession, UsageEvent, User
from layer1.db.init_db import create_all
from layer1.db.session import make_session_factory, session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _fk_session_scope(db_url: str):
    """Like ``session_scope`` but with sqlite FK enforcement turned on.

    Layer 1's session helper builds a fresh engine each call, so
    attaching a connect listener to one engine wouldn't affect the
    next call. We build a single engine, install the listener, and
    return a context manager that yields a session bound to it.
    """
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(db_url, future=True)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _conn_rec):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def _scope():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return _scope


def _new_user(**overrides) -> User:
    base = dict(
        clerk_user_id="clerk_user_1",
        email="user@example.com",
        full_name="Example User",
        plan_tier="free",
        monthly_query_limit=100,
        monthly_queries_used=0,
        month_started_at=date(2026, 5, 1),
    )
    base.update(overrides)
    return User(**base)


def test_persist_each_model_assigns_ids(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)

    with session_scope(db_url) as s:
        user = _new_user()
        s.add(user)
        s.flush()
        assert user.id is not None

        session = ChatSession(user_id=user.id, title="hello")
        s.add(session)
        s.flush()
        assert session.id is not None

        message = ChatMessage(
            session_id=session.id,
            sequence=0,
            role="user",
            content_json="What is the height limit on Barrington?",
            tool_calls_json=[],
            tokens_input=10,
            tokens_output=0,
        )
        s.add(message)
        s.flush()
        assert message.id is not None

        event = UsageEvent(
            user_id=user.id,
            session_id=session.id,
            event_type="llm_call",
            provider="anthropic",
            model="claude-opus-4",
            tokens_input=10,
            tokens_output=42,
            cost_estimate_cents=3,
        )
        s.add(event)
        s.flush()
        assert event.id is not None


def test_user_delete_cascades_to_sessions_and_messages(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    scope = _fk_session_scope(db_url)

    with scope() as s:
        user = _new_user(clerk_user_id="clerk_cascade")
        s.add(user)
        s.flush()
        chat = ChatSession(user_id=user.id)
        s.add(chat)
        s.flush()
        s.add_all(
            [
                ChatMessage(
                    session_id=chat.id,
                    sequence=0,
                    role="user",
                    content_json="hi",
                ),
                ChatMessage(
                    session_id=chat.id,
                    sequence=1,
                    role="assistant",
                    content_json=[{"type": "text", "text": "hello"}],
                ),
            ]
        )
        user_id = user.id
        chat_id = chat.id

    with scope() as s:
        s.delete(s.get(User, user_id))

    with scope() as s:
        assert s.get(User, user_id) is None
        assert s.get(ChatSession, chat_id) is None
        assert s.query(ChatMessage).filter_by(session_id=chat_id).count() == 0


def test_session_delete_sets_usage_event_session_id_null(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    scope = _fk_session_scope(db_url)

    with scope() as s:
        user = _new_user(clerk_user_id="clerk_set_null")
        s.add(user)
        s.flush()
        chat = ChatSession(user_id=user.id)
        s.add(chat)
        s.flush()
        usage = UsageEvent(
            user_id=user.id,
            session_id=chat.id,
            event_type="llm_call",
        )
        s.add(usage)
        s.flush()
        chat_id = chat.id
        event_id = usage.id

    with scope() as s:
        s.delete(s.get(ChatSession, chat_id))

    with scope() as s:
        survivor = s.get(UsageEvent, event_id)
        assert survivor is not None
        assert survivor.session_id is None


def test_chat_message_session_sequence_unique(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)

    with session_scope(db_url) as s:
        user = _new_user(clerk_user_id="clerk_unique")
        s.add(user)
        s.flush()
        chat = ChatSession(user_id=user.id)
        s.add(chat)
        s.flush()
        s.add(
            ChatMessage(
                session_id=chat.id,
                sequence=0,
                role="user",
                content_json="first",
            )
        )

    with pytest.raises(IntegrityError):
        with session_scope(db_url) as s:
            chat = s.query(ChatSession).first()
            s.add(
                ChatMessage(
                    session_id=chat.id,
                    sequence=0,
                    role="user",
                    content_json="duplicate sequence",
                )
            )
