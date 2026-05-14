"""Pydantic schemas: ORM-mode wiring and content-shape flexibility."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from advisor.db import (
    ChatMessage,
    ChatMessageOut,
    ChatSession,
    ChatSessionOut,
    UsageEvent,
    UsageEventOut,
    User,
    UserCreate,
    UserOut,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def test_user_create_strips_to_signup_fields() -> None:
    payload = UserCreate(
        clerk_user_id="clerk_123",
        email="hi@example.com",
        full_name="Hi There",
    )
    assert payload.clerk_user_id == "clerk_123"
    assert payload.full_name == "Hi There"
    # No quota fields on the create shape.
    assert not hasattr(payload, "monthly_query_limit")


def test_user_out_from_attributes(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)

    with session_scope(db_url) as s:
        user = User(
            clerk_user_id="clerk_attrs",
            email="attrs@example.com",
            full_name="Attr User",
        )
        s.add(user)
        s.flush()

        # Validate directly off the SQLAlchemy row.
        out = UserOut.model_validate(user)
        assert out.id == user.id
        assert out.clerk_user_id == "clerk_attrs"
        assert isinstance(out.created_at, datetime)


def test_chat_message_out_accepts_string_content(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)

    with session_scope(db_url) as s:
        user = User(
            clerk_user_id="clerk_msg_str",
            email="m@example.com",
        )
        s.add(user)
        s.flush()
        chat = ChatSession(user_id=user.id)
        s.add(chat)
        s.flush()
        msg = ChatMessage(
            session_id=chat.id,
            sequence=0,
            role="user",
            content_json="What is the side yard for R-2?",
        )
        s.add(msg)
        s.flush()
        out = ChatMessageOut.model_validate(msg)
        assert out.content_json == "What is the side yard for R-2?"
        assert out.role == "user"


def test_chat_message_out_accepts_block_list_content(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)

    with session_scope(db_url) as s:
        user = User(
            clerk_user_id="clerk_msg_blocks",
            email="m2@example.com",
        )
        s.add(user)
        s.flush()
        chat = ChatSession(user_id=user.id)
        s.add(chat)
        s.flush()
        msg = ChatMessage(
            session_id=chat.id,
            sequence=0,
            role="assistant",
            content_json=[
                {"type": "text", "text": "Looking that up..."},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "retrieve",
                    "input": {"q": "R-2 side yard"},
                },
            ],
            tool_calls_json=[{"tool_name": "retrieve", "tool_use_id": "tu_1"}],
        )
        s.add(msg)
        s.flush()
        out = ChatMessageOut.model_validate(msg)
        assert isinstance(out.content_json, list)
        assert out.content_json[0]["type"] == "text"
        assert out.tool_calls_json == [
            {"tool_name": "retrieve", "tool_use_id": "tu_1"}
        ]


def test_chat_session_out_from_attributes(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)

    with session_scope(db_url) as s:
        user = User(
            clerk_user_id="clerk_session_out",
            email="s@example.com",
        )
        s.add(user)
        s.flush()
        chat = ChatSession(user_id=user.id, title="A title")
        s.add(chat)
        s.flush()

        out = ChatSessionOut.model_validate(chat)
        assert out.id == chat.id
        assert out.title == "A title"
        assert out.user_id == user.id
        # Case fields default to None on a chat session not attached
        # to a case (legacy / non-billed path).
        assert out.case_id is None
        assert out.tier is None


def test_usage_event_out_from_attributes(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'advisor.db'}"
    create_all(db_url)

    with session_scope(db_url) as s:
        user = User(
            clerk_user_id="clerk_event_out",
            email="e@example.com",
        )
        s.add(user)
        s.flush()
        event = UsageEvent(
            user_id=user.id,
            event_type="llm_call",
            provider="anthropic",
            model="claude-opus-4",
            tokens_input=10,
            tokens_output=20,
            cost_estimate_cents=5,
        )
        s.add(event)
        s.flush()
        out = UsageEventOut.model_validate(event)
        assert out.event_type == "llm_call"
        assert out.cost_estimate_cents == 5
        assert out.session_id is None
        assert out.case_id is None
