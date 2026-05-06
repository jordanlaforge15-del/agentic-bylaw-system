"""Session storage abstraction and in-memory v1 implementation.

Workstream 2 is delivering a SQLAlchemy-backed session table; this
``SessionStore`` interface exists so we can swap that in without
touching the FastAPI routes. Anything that mutates session state goes
through these methods.

The in-memory impl is process-local and intentionally minimal — it
loses sessions on restart, doesn't enforce per-user limits, and makes
no concurrency guarantees beyond Python's GIL. It's enough for v1
behaviour testing.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Protocol

from advisor.chat.session import ChatSession
from advisor.llm import ToolDefinition
from advisor.llm.tool_loop import ToolHandler


class SessionStore(Protocol):
    """Storage interface for chat sessions.

    All methods are synchronous because the v1 in-memory impl is
    trivially fast and the DB-backed impl can stay sync (SQLAlchemy
    sessions are sync). If we move to async DB later we'll wrap
    the methods in an async facade rather than changing this protocol
    out from under callers.
    """

    def get(self, session_id: str) -> ChatSession | None:
        """Return a session by id, or None if not found.

        Tests don't need this to be user-scoped; callers that care
        about authorisation should also check ``session.user_id``.
        """
        ...

    def create(
        self,
        *,
        user_id: str,
        system_prompt: str,
        tool_defs: list[ToolDefinition],
        tool_handlers: dict[str, ToolHandler],
        model: str | None = None,
    ) -> ChatSession:
        """Mint a new session for ``user_id`` and return it."""
        ...

    def list_for_user(self, user_id: str) -> list[ChatSession]:
        """All sessions belonging to a given user (newest first is fine)."""
        ...


class InMemorySessionStore:
    """Process-local dict-backed session store.

    Use this for tests and v1 deployments. It does not survive
    restarts, doesn't share state across worker processes, and has no
    per-user limits. The DB-backed replacement will provide all three.
    """

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        # Tests inject a deterministic id_factory; production uses
        # uuid4. We separate the two so test assertions on session_id
        # don't have to mock uuid globally.
        self._sessions: dict[str, ChatSession] = {}
        self._id_factory = id_factory

    def get(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    def create(
        self,
        *,
        user_id: str,
        system_prompt: str,
        tool_defs: list[ToolDefinition],
        tool_handlers: dict[str, ToolHandler],
        model: str | None = None,
    ) -> ChatSession:
        session_id = self._id_factory()
        # Build kwargs without ``model`` when the caller didn't pass one
        # so the dataclass default ("claude-opus-4-5") wins. Passing
        # ``model=None`` would override it with None, which would
        # break gateway.complete() at request time.
        kwargs = dict(
            session_id=session_id,
            user_id=user_id,
            system_prompt=system_prompt,
            tool_defs=list(tool_defs),
            tool_handlers=dict(tool_handlers),
        )
        if model is not None:
            kwargs["model"] = model
        session = ChatSession(**kwargs)
        self._sessions[session_id] = session
        return session

    def list_for_user(self, user_id: str) -> list[ChatSession]:
        return [s for s in self._sessions.values() if s.user_id == user_id]
