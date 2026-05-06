"""SQLAlchemy-backed implementation of the ``SessionStore`` Protocol.

Replaces the v1 ``InMemorySessionStore`` for production deployments.
Sessions and messages persist to ``advisor_chat_session`` /
``advisor_chat_message``; tools and handlers do NOT persist (they're
rebuilt at restoration time from the same factory the route uses for
new sessions). Quota enforcement lives at the route edge — this store
is concerned only with persistence.

Design decisions
================

A. ``user_id`` resolution
-------------------------
``ChatSession.user_id`` is a ``str`` placeholder; the DB schema uses
an integer FK to ``advisor_user.id``. Treat ``external_user_id`` as
an internal numeric id when parseable, otherwise fall back to
``clerk_user_id`` — this lets the store be wired before/after the
auth integration without churn. Tests of the chat route inject opaque
strings ("user_alice") and need the clerk-fallback path; once auth is
live the route hands us ``str(User.id)`` and the int parse wins.

B. When messages are written
----------------------------
``ChatSession.send_user_message_blocking`` REPLACES ``self.messages``
wholesale at the end of each turn. The cleanest place to persist is
right after that replacement. Rather than reach into the chat route's
streaming generator (which has no "after stream" hook), we attach an
``on_turn_complete`` callback to the ChatSession at ``get`` / ``create``
time. The callback runs synchronously after the tool loop settles and
before the synthetic stream starts emitting events.

C. Snapshot semantics
---------------------
Per turn we INSERT only the new messages, never wipe-and-rewrite. The
``_last_persisted_sequence`` field on the in-memory ``ChatSession``
isn't part of the dataclass, so we shadow it with an internal
mapping keyed by ``session_id``. Diffing-by-sequence is correct
because the unique ``(session_id, sequence)`` constraint on
``advisor_chat_message`` would reject any duplicate-sequence write —
and because the chat session always extends the message list (never
rewrites prior turns).

D. Tools / handlers don't persist
---------------------------------
Tools rehydrate on ``get`` from a caller-supplied
``tool_defs_handler_factory``. The chat route already builds tools
for new sessions via ``_build_tools_with_factory``; we accept that
same factory here so resumed sessions land with the identical tool
shape.

Token-counting note
-------------------
``ChatMessage.tokens_input`` / ``tokens_output`` are written as 0 for
now. Real token accounting per turn is a follow-up — the underlying
``CompletionResponse`` has ``usage`` data, but the chat session
doesn't currently propagate it back to the persistence hook. When
that lands, this store will read from ``ChatSession`` (or a richer
``on_turn_complete`` arg) without a schema change.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session

from advisor.chat.session import ChatSession
from advisor.db.models import ChatMessage as DbChatMessage
from advisor.db.models import ChatSession as DbChatSession
from advisor.db.models import User
from advisor.llm import LLMRole, Message, ToolDefinition
from advisor.llm.tool_loop import ToolHandler

logger = logging.getLogger(__name__)


SessionFactory = Callable[[], AbstractContextManager[Session]]
ToolFactory = Callable[
    [], tuple[list[ToolDefinition], dict[str, ToolHandler]]
]
UserResolver = Callable[[Session, str], User]


def default_resolve_user(db: Session, external_user_id: str) -> User:
    """Resolve an opaque external user id to an ``advisor_user`` row.

    Treat ``external_user_id`` as an internal numeric id when
    parseable; fall back to ``clerk_user_id`` lookup otherwise. See
    decision (A) in the module docstring for why both paths matter.

    Raises ``LookupError`` if no user matches — callers can decide
    whether to translate that into a 401 / 404 / auto-provision.
    """
    user: User | None = None
    if external_user_id.isdigit():
        user = db.get(User, int(external_user_id))
    if user is None:
        user = (
            db.query(User)
            .filter(User.clerk_user_id == external_user_id)
            .one_or_none()
        )
    if user is None:
        raise LookupError(
            f"No advisor_user matches external id {external_user_id!r} "
            "(tried internal id then clerk_user_id)."
        )
    return user


class DbSessionStore:
    """SQLAlchemy-backed ``SessionStore`` implementation.

    Open a fresh DB session for each operation via ``db_session_factory``;
    we never hold a session across calls because chat turns can be
    minutes apart and idle DB connections are expensive.
    """

    def __init__(
        self,
        *,
        db_session_factory: SessionFactory,
        tool_defs_handler_factory: ToolFactory,
        resolve_user: UserResolver | None = None,
    ) -> None:
        """Build the store.

        Args:
            db_session_factory: Context manager that yields a
                ``sqlalchemy.orm.Session`` and commits on exit. In
                production this is layer1's ``session_scope``; tests
                pass a sqlite-backed equivalent.
            tool_defs_handler_factory: Zero-arg callable that returns
                ``(tool_defs, tool_handlers)``. Called every time a
                session is created OR resumed. Same factory as the
                chat route's ``_build_tools_with_factory`` — see
                decision (D).
            resolve_user: Maps ``external_user_id`` to a ``User`` row.
                Defaults to ``default_resolve_user`` (numeric-id then
                clerk-id fallback). Tests inject a stub when needed.
        """
        self._db_session_factory = db_session_factory
        self._tool_factory = tool_defs_handler_factory
        self._resolve_user = resolve_user or default_resolve_user
        # Track how far we've persisted per session so on_turn_complete
        # only inserts new rows. Keyed by ChatSession.session_id (the
        # int-as-string we hand back to callers).
        self._last_persisted_sequence: dict[str, int] = {}

    # -- SessionStore Protocol --------------------------------------------

    def get(self, session_id: str) -> ChatSession | None:
        """Load a session and its messages, or None if not found.

        Tools and handlers are rebuilt from the factory — they aren't
        persisted. The returned ChatSession has ``on_turn_complete``
        bound so the next turn's mutations land back in the DB.
        """
        try:
            db_session_pk = int(session_id)
        except ValueError:
            # Non-numeric session ids never match an integer PK; treat
            # as "not found" rather than raising. Lets callers feed
            # opaque strings safely.
            return None

        with self._db_session_factory() as db:
            row = db.get(DbChatSession, db_session_pk)
            if row is None:
                return None
            messages = [
                _row_to_message(m)
                for m in sorted(row.messages, key=lambda m: m.sequence)
            ]
            external_user_id = str(row.user_id)
            tool_defs, tool_handlers = self._tool_factory()

            chat_session = ChatSession(
                session_id=str(row.id),
                user_id=external_user_id,
                system_prompt="",
                messages=messages,
                tool_defs=list(tool_defs),
                tool_handlers=dict(tool_handlers),
            )

            # Track how many messages we've already persisted so the
            # turn-complete hook only writes the new ones.
            if messages:
                self._last_persisted_sequence[chat_session.session_id] = max(
                    m.sequence for m in row.messages
                )
            else:
                self._last_persisted_sequence[chat_session.session_id] = -1

            chat_session.on_turn_complete = self._make_persist_hook()
            return chat_session

    def create(
        self,
        *,
        user_id: str,
        system_prompt: str,
        tool_defs: list[ToolDefinition],
        tool_handlers: dict[str, ToolHandler],
        model: str | None = None,
    ) -> ChatSession:
        """Mint a new session for ``user_id`` and return it.

        ``tool_defs`` / ``tool_handlers`` from the caller are honoured
        as-is; the store doesn't second-guess them on create. The
        factory passed at construction time only matters on ``get``.
        """
        with self._db_session_factory() as db:
            user = self._resolve_user(db, user_id)
            row = DbChatSession(user_id=user.id)
            db.add(row)
            db.flush()
            new_id = row.id

        chat_session_id = str(new_id)
        kwargs: dict[str, Any] = dict(
            session_id=chat_session_id,
            user_id=user_id,
            system_prompt=system_prompt,
            tool_defs=list(tool_defs),
            tool_handlers=dict(tool_handlers),
        )
        if model is not None:
            kwargs["model"] = model

        chat_session = ChatSession(**kwargs)
        self._last_persisted_sequence[chat_session_id] = -1
        chat_session.on_turn_complete = self._make_persist_hook()
        return chat_session

    def list_for_user(self, user_id: str) -> list[ChatSession]:
        """All sessions belonging to ``user_id``, newest first.

        Returns lightweight ChatSession objects with empty tool sets
        — callers that want to actually drive a session should
        ``get`` it by id, which pays for the message load and tool
        rehydration.
        """
        with self._db_session_factory() as db:
            try:
                user = self._resolve_user(db, user_id)
            except LookupError:
                return []
            rows = (
                db.query(DbChatSession)
                .filter(DbChatSession.user_id == user.id)
                .order_by(DbChatSession.created_at.desc())
                .all()
            )
            return [
                ChatSession(
                    session_id=str(r.id),
                    user_id=user_id,
                    system_prompt="",
                )
                for r in rows
            ]

    # -- internals --------------------------------------------------------

    def _make_persist_hook(self) -> Callable[[ChatSession], None]:
        """Return a closure that persists newly-appended messages.

        The closure binds ``self`` so it can hit the DB factory and
        update ``_last_persisted_sequence``. We return a fresh
        closure per session so two concurrent sessions don't share
        any mutable state beyond the dict they each key into.
        """

        def _persist(chat_session: ChatSession) -> None:
            self._persist_new_messages(chat_session)

        return _persist

    def _persist_new_messages(self, chat_session: ChatSession) -> None:
        """Insert any messages whose sequence > the last persisted one.

        ChatSession.messages is the new-truth list; we diff it
        against ``_last_persisted_sequence`` and INSERT only the
        delta. Sequences are assigned by enumeration of the current
        message list — that matches what
        ``ChatSession.send_user_message_blocking`` produces (a flat,
        ordered conversation including tool round-trips).
        """
        try:
            db_session_pk = int(chat_session.session_id)
        except ValueError:
            logger.warning(
                "Skipping message persist: session_id %r is not numeric",
                chat_session.session_id,
            )
            return

        last_persisted = self._last_persisted_sequence.get(
            chat_session.session_id, -1
        )

        with self._db_session_factory() as db:
            new_rows: list[DbChatMessage] = []
            for sequence, message in enumerate(chat_session.messages):
                if sequence <= last_persisted:
                    continue
                new_rows.append(
                    DbChatMessage(
                        session_id=db_session_pk,
                        sequence=sequence,
                        role=str(message.role.value),
                        content_json=_message_content_to_json(message),
                        tool_calls_json=[],
                        # Token counting is wired but always 0 for now —
                        # see the module docstring's "Token-counting note".
                        tokens_input=0,
                        tokens_output=0,
                    )
                )
            if not new_rows:
                return
            db.add_all(new_rows)
            db.flush()

        self._last_persisted_sequence[chat_session.session_id] = (
            len(chat_session.messages) - 1
        )


# -- helpers --------------------------------------------------------------


def _message_content_to_json(message: Message) -> Any:
    """Serialise a ``Message.content`` for the ``content_json`` column.

    Plain strings stay as strings (matches the schema's permissive
    ``str | list | dict`` shape). Block lists are dumped to JSON-mode
    pydantic dicts so the round-trip through the DB is lossless.
    """
    if isinstance(message.content, str):
        return message.content
    return [block.model_dump(mode="json") for block in message.content]


def _row_to_message(row: DbChatMessage) -> Message:
    """Inverse of ``_message_content_to_json``: rebuild a ``Message``.

    Keeps roles in sync with ``LLMRole`` (the chat layer's enum). The
    schema's ``role`` column is a free-form string, so we narrow it
    here at the boundary.
    """
    content = row.content_json
    return Message(role=LLMRole(row.role), content=content)
