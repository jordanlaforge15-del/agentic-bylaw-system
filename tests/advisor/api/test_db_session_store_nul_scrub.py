"""ABS-333: the chat store must never persist a value Postgres rejects.

Same crash class as ABS-332 (which fixed it for the Answers product), now
on the Conversation ``/app`` chat path. Real bylaw evidence is extracted
from municipal PDFs that carry stray NUL (``0x00``) bytes; a NUL inside a
``tool_result`` string (or an assistant turn echoing one) rides untouched
through ``Message.model_dump`` — Pydantic does not scrub strings — into
``advisor_chat_message.content_json`` (a Postgres ``jsonb`` column). On a
real Postgres backend ``db.flush()`` then raises ``psycopg.DataError``
(``unsupported Unicode escape sequence … \\u0000 cannot be converted to
text``), surfacing as a raw HTTP 500 on a chat turn.

SQLite (these unit tests) accepts the byte, so we assert the *scrubbing*
happens at the serialisation boundary here; the matching Postgres-backed
proof that the 500 is gone lives in
``web/e2e/functional/abs333-chat-nul-bytes.spec.ts``.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from advisor.api.db_session_store import DbSessionStore, _message_content_to_json
from advisor.db.models import ChatMessage as DbChatMessage
from advisor.db.models import User
from advisor.llm import (
    LLMRole,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from layer1.db.init_db import create_all

NUL = "\x00"


# -- _message_content_to_json (the serialisation boundary) -------------------


def test_string_content_is_scrubbed() -> None:
    msg = Message(role=LLMRole.USER, content=f"permitted use{NUL} setback")
    out = _message_content_to_json(msg)
    assert out == "permitted use setback"
    assert NUL not in out


def test_string_content_without_nul_is_untouched() -> None:
    # The common case must stay a faithful passthrough — tabs/newlines are
    # legal in jsonb and must survive.
    body = "line one\n\tindented\r\nline two"
    assert _message_content_to_json(Message(role=LLMRole.USER, content=body)) == body


def test_tool_use_block_input_nul_is_scrubbed() -> None:
    # The assistant tool_use turn carries the model's query verbatim; a NUL
    # echoed from PDF-extracted evidence must not reach the jsonb column.
    msg = Message(
        role=LLMRole.ASSISTANT,
        content=[
            TextBlock(text=f"Searching the bylaw{NUL}."),
            ToolUseBlock(
                id="t1",
                name="search_bylaw_evidence",
                input={"query": f"setback{NUL}rule"},
            ),
        ],
    )
    blob = json.dumps(_message_content_to_json(msg))
    assert NUL not in blob
    assert "\\u0000" not in blob


def test_tool_result_block_content_nul_is_scrubbed() -> None:
    # The tool_result turn carries the raw retrieved evidence — the most
    # likely NUL carrier, since it comes straight from the PDF corpus.
    msg = Message(
        role=LLMRole.USER,
        content=[
            ToolResultBlock(
                tool_use_id="t1", content=f"Part II{NUL} setback is 7.5 m"
            )
        ],
    )
    blob = json.dumps(_message_content_to_json(msg))
    assert NUL not in blob
    assert "\\u0000" not in blob


# -- end-to-end through the persist hook -------------------------------------


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


def _seed_user(factory) -> int:
    s = factory()
    try:
        user = User(clerk_user_id="clerk_nul", email="nul@example.com")
        s.add(user)
        s.commit()
        return user.id
    finally:
        s.close()


def _empty_tool_factory():
    return ([], {})


def test_persist_hook_writes_nul_free_content_json(tmp_path: Path) -> None:
    """A turn carrying NUL bytes persists clean rows.

    Drives the real ``on_turn_complete`` path the chat route binds: every
    persisted ``content_json`` must be writable to a Postgres ``jsonb``
    column, i.e. carry no NUL. Mirrors the MOCK_NUL_BYTES turn shape (a
    tool_use input + tool_result + final answer text, each with a NUL).
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory)

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    chat_session = store.create(
        user_id=str(user_id),
        system_prompt="be helpful",
        tool_defs=[],
        tool_handlers={},
    )

    chat_session.messages = [
        Message(role=LLMRole.USER, content=f"can I build a duplex{NUL}?"),
        Message(
            role=LLMRole.ASSISTANT,
            content=[
                ToolUseBlock(
                    id="t1",
                    name="search_bylaw_evidence",
                    input={"query": f"permitted use{NUL} setback"},
                )
            ],
        ),
        Message(
            role=LLMRole.USER,
            content=[
                ToolResultBlock(
                    tool_use_id="t1", content=f"RC-LUB §15.4{NUL} applies"
                )
            ],
        ),
        Message(
            role=LLMRole.ASSISTANT,
            content=[TextBlock(text=f"The permitted use is confirmed.{NUL}")],
        ),
    ]

    assert chat_session.on_turn_complete is not None
    chat_session.on_turn_complete(chat_session)

    s = factory()
    try:
        rows = (
            s.execute(
                select(DbChatMessage)
                .where(DbChatMessage.session_id == int(chat_session.session_id))
                .order_by(DbChatMessage.sequence)
            )
            .scalars()
            .all()
        )
        assert len(rows) == 4
        for row in rows:
            blob = json.dumps(row.content_json)
            assert NUL not in blob, f"NUL persisted in seq={row.sequence}"
            assert "\\u0000" not in blob
        # The scrub left the surrounding text intact.
        assert rows[0].content_json == "can I build a duplex?"
    finally:
        s.close()
