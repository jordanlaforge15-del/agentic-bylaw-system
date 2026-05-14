"""Persistence behaviour for ``DbSessionStore``.

Each test stands up a fresh sqlite DB via ``layer1.db.init_db.create_all``
and a per-test session factory bound to that DB. We exercise the
public Protocol (``get`` / ``create`` / ``list_for_user``) plus the
``on_turn_complete`` hook contract — that hook is what keeps message
history in sync with the in-memory ChatSession across turns.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from advisor.api.db_session_store import DbSessionStore
from advisor.chat.session import ChatSession
from advisor.db.models import ChatMessage as DbChatMessage
from advisor.db.models import ChatSession as DbChatSession
from advisor.db.models import User
from advisor.llm import LLMRole, Message, TextBlock, TokenUsage
from layer1.db.init_db import create_all


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _build_factory(db_url: str):
    """Build an isolated session factory bound to one engine.

    Layer 1's ``session_scope`` builds a fresh engine per call which
    makes per-test isolation harder; for these tests we build the
    engine once, share it across the closures we hand to
    ``DbSessionStore``, and let pytest clean up via tmp_path.
    """
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
    clerk_user_id: str = "clerk_test",
    email: str = "test@example.com",
) -> int:
    s = factory()
    try:
        user = User(
            clerk_user_id=clerk_user_id,
            email=email,
            full_name="Test User",
        )
        s.add(user)
        s.commit()
        return user.id
    finally:
        s.close()


def _empty_tool_factory():
    return ([], {})


def test_create_persists_session(tmp_path: Path) -> None:
    """``create`` writes a row and returns it with an int-as-string id."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_create")

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

    # Returned object: int-id-as-string, in-memory user_id echoes input.
    assert chat_session.session_id.isdigit()
    assert chat_session.user_id == str(user_id)

    # And the row exists in the DB with the right user FK.
    s = factory()
    try:
        row = s.get(DbChatSession, int(chat_session.session_id))
        assert row is not None
        assert row.user_id == user_id
    finally:
        s.close()


def test_get_loads_messages_in_sequence_order(tmp_path: Path) -> None:
    """Messages seeded out of sequence-order come back ordered."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_order")

    s = factory()
    try:
        chat_row = DbChatSession(user_id=user_id)
        s.add(chat_row)
        s.flush()
        chat_pk = chat_row.id
        # Insert in a deliberately scrambled order: 2, 0, 1.
        for seq, role, content in (
            (2, "assistant", "third"),
            (0, "user", "first"),
            (1, "assistant", "second"),
        ):
            s.add(
                DbChatMessage(
                    session_id=chat_pk,
                    sequence=seq,
                    role=role,
                    content_json=content,
                )
            )
        s.commit()
    finally:
        s.close()

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    loaded = store.get(str(chat_pk))
    assert loaded is not None
    contents = [m.content for m in loaded.messages]
    assert contents == ["first", "second", "third"]


def test_get_hydrates_case_billing_context(tmp_path: Path) -> None:
    """``case_id`` / ``tier`` / ``token_budget_remaining`` ride back from the DB.

    The chat resume path reads ``session.case_id`` to skip re-asking the
    client for it; ``GET /v1/chat/sessions/{id}`` reads it to drive the
    frontend's composer gate. Both depend on this hydration step.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_case_hydrate")

    s = factory()
    try:
        chat_row = DbChatSession(
            user_id=user_id,
            case_id=None,
            tier="standard",
            token_budget_remaining=12_345,
        )
        s.add(chat_row)
        s.flush()
        chat_pk = chat_row.id
        # case_id is set via raw assignment since we don't have a Case
        # FK seeded here — sqlite doesn't enforce the FK at insert time
        # without PRAGMA, which keeps this test focused on hydration.
        chat_row.case_id = 42
        s.commit()
    finally:
        s.close()

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    loaded = store.get(str(chat_pk))
    assert loaded is not None
    assert loaded.case_id == 42
    assert loaded.tier == "standard"
    assert loaded.token_budget_remaining == 12_345


def test_get_hydrates_null_case_billing_context(tmp_path: Path) -> None:
    """Legacy sessions (pre-migration) hydrate with ``case_id is None``.

    Surfaces this so the chat handler can fall through to its
    ``case_id_required`` error and the frontend can show its
    legacy-session notice instead of letting users hit the 400.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_legacy")

    s = factory()
    try:
        chat_row = DbChatSession(user_id=user_id)
        s.add(chat_row)
        s.commit()
        chat_pk = chat_row.id
    finally:
        s.close()

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    loaded = store.get(str(chat_pk))
    assert loaded is not None
    assert loaded.case_id is None
    assert loaded.tier is None
    assert loaded.token_budget_remaining is None


def test_get_returns_none_for_unknown_session(tmp_path: Path) -> None:
    """A nonexistent session id yields None, not an exception."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, _factory = _build_factory(db_url)
    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    assert store.get("99999") is None
    # Non-numeric ids are also "not found", not crashes.
    assert store.get("not-an-int") is None


def test_on_turn_complete_persists_new_messages_only(tmp_path: Path) -> None:
    """The turn hook inserts only messages with sequence > last persisted.

    Seeded one message; simulate a turn that ends with four messages
    (the seed plus three new ones). The hook should INSERT exactly
    three rows.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_turn")

    # Seed one persisted message at sequence=0.
    s = factory()
    try:
        chat_row = DbChatSession(user_id=user_id)
        s.add(chat_row)
        s.flush()
        chat_pk = chat_row.id
        s.add(
            DbChatMessage(
                session_id=chat_pk,
                sequence=0,
                role="user",
                content_json="first user msg",
            )
        )
        s.commit()
    finally:
        s.close()

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    chat_session = store.get(str(chat_pk))
    assert chat_session is not None

    # Simulate a turn: the chat layer would replace messages with the
    # full post-turn list. We add three more.
    chat_session.messages = list(chat_session.messages) + [
        Message(role=LLMRole.ASSISTANT, content=[TextBlock(text="reply 1")]),
        Message(role=LLMRole.USER, content="follow-up"),
        Message(role=LLMRole.ASSISTANT, content=[TextBlock(text="reply 2")]),
    ]

    assert chat_session.on_turn_complete is not None
    chat_session.on_turn_complete(chat_session)

    # The DB tells the truth: 1 seeded + 3 new == 4 total.
    s = factory()
    try:
        count = s.execute(
            select(func.count(DbChatMessage.id)).where(
                DbChatMessage.session_id == chat_pk
            )
        ).scalar_one()
        assert count == 4

        # And running the hook again with no new messages is a no-op.
        chat_session.on_turn_complete(chat_session)
    finally:
        s.close()

    s = factory()
    try:
        count_again = s.execute(
            select(func.count(DbChatMessage.id)).where(
                DbChatMessage.session_id == chat_pk
            )
        ).scalar_one()
        assert count_again == 4
    finally:
        s.close()


def test_on_turn_complete_attributes_tokens_to_last_new_row(
    tmp_path: Path,
) -> None:
    """When ``last_turn_usage`` is set, the hook writes tokens onto the
    final new row (the assistant turn). Earlier rows in the same batch
    keep tokens at 0 because we don't have per-iteration breakdowns."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_tokens")

    s = factory()
    try:
        chat_row = DbChatSession(user_id=user_id)
        s.add(chat_row)
        s.commit()
        chat_pk = chat_row.id
    finally:
        s.close()

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    chat_session = store.get(str(chat_pk))
    assert chat_session is not None

    chat_session.messages = [
        Message(role=LLMRole.USER, content="how high?"),
        Message(role=LLMRole.ASSISTANT, content=[TextBlock(text="90m.")]),
    ]
    chat_session.last_turn_usage = TokenUsage(
        input_tokens=123, output_tokens=456
    )
    assert chat_session.on_turn_complete is not None
    chat_session.on_turn_complete(chat_session)

    s = factory()
    try:
        rows = (
            s.execute(
                select(DbChatMessage)
                .where(DbChatMessage.session_id == chat_pk)
                .order_by(DbChatMessage.sequence)
            )
            .scalars()
            .all()
        )
        assert [r.role for r in rows] == ["user", "assistant"]
        assert (rows[0].tokens_input, rows[0].tokens_output) == (0, 0)
        assert (rows[1].tokens_input, rows[1].tokens_output) == (123, 456)
    finally:
        s.close()


def test_user_id_resolves_to_clerk_user_id_when_not_numeric(
    tmp_path: Path,
) -> None:
    """Opaque external_user_id falls back to clerk_user_id lookup."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_alpha")

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    chat_session = store.create(
        user_id="clerk_alpha",
        system_prompt="x",
        tool_defs=[],
        tool_handlers={},
    )

    s = factory()
    try:
        row = s.get(DbChatSession, int(chat_session.session_id))
        assert row is not None
        assert row.user_id == user_id
    finally:
        s.close()


def test_create_and_get_return_same_user_id(tmp_path: Path) -> None:
    """``create`` and ``get`` must return the same ``user_id`` string.

    Regression test for a bug where ``create`` echoed the external
    clerk_user_id back to the caller but ``get`` returned the internal
    integer FK stringified. The route handler compared the two and
    404'd legitimate session-detail requests when the user-id formats
    happened to differ (always, in test-fallback mode where the
    clerk_user_id is the X-Test-User-Id header value, not a number).
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    _seed_user(factory, clerk_user_id="smoke-test-1")

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    created = store.create(
        user_id="smoke-test-1",
        system_prompt="x",
        tool_defs=[],
        tool_handlers={},
    )
    assert created.user_id == "smoke-test-1"

    fetched = store.get(created.session_id)
    assert fetched is not None
    assert fetched.user_id == created.user_id, (
        "get() must return the same user_id format as create() so route "
        "handlers can compare against str(user.clerk_user_id) without "
        "modality-specific normalization."
    )


def test_user_id_resolves_to_internal_id_when_numeric(tmp_path: Path) -> None:
    """A numeric external_user_id is parsed as ``User.id``."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    user_id = _seed_user(factory, clerk_user_id="clerk_42")

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    chat_session = store.create(
        user_id=str(user_id),
        system_prompt="x",
        tool_defs=[],
        tool_handlers={},
    )
    s = factory()
    try:
        row = s.get(DbChatSession, int(chat_session.session_id))
        assert row is not None
        assert row.user_id == user_id
    finally:
        s.close()


def test_list_for_user_returns_user_sessions_only(tmp_path: Path) -> None:
    """``list_for_user`` filters to the requested user."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    db_session_factory, factory = _build_factory(db_url)
    a_id = _seed_user(factory, clerk_user_id="clerk_a", email="a@x.com")
    b_id = _seed_user(factory, clerk_user_id="clerk_b", email="b@x.com")

    store = DbSessionStore(
        db_session_factory=db_session_factory,
        tool_defs_handler_factory=_empty_tool_factory,
    )
    a1 = store.create(
        user_id=str(a_id),
        system_prompt="x",
        tool_defs=[],
        tool_handlers={},
    )
    a2 = store.create(
        user_id=str(a_id),
        system_prompt="x",
        tool_defs=[],
        tool_handlers={},
    )
    store.create(
        user_id=str(b_id),
        system_prompt="x",
        tool_defs=[],
        tool_handlers={},
    )

    a_sessions = store.list_for_user(str(a_id))
    a_session_ids = {s.session_id for s in a_sessions}
    assert a_session_ids == {a1.session_id, a2.session_id}

    b_sessions = store.list_for_user(str(b_id))
    assert len(b_sessions) == 1
