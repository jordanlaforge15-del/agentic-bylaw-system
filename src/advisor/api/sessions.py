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
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from advisor.chat.session import ChatSession
from advisor.llm import LLMRole, ToolDefinition
from advisor.llm.tool_loop import ToolHandler


@dataclass(frozen=True)
class SessionListEntry:
    """Sidebar-shaped projection of one session for the listing endpoint.

    Carries the minimum data needed to compose a row in the sidebar
    without pulling a full ChatSession with every message round-trip.
    The store is responsible for deriving ``first_user_message`` and
    the counts from whatever it has on disk — the route doesn't peek
    into message content.
    """

    session_id: str
    model: str
    # First plain-string user message in the session, if any. ``None``
    # for sessions that exist but haven't received a user turn yet (or
    # only carry tool_result intermediates).
    first_user_message: str | None
    # ``advisor_case.anchor_label`` for the case this session is
    # attached to. ``None`` for legacy / un-cased sessions and for any
    # session store that doesn't track cases (the in-memory store).
    anchor_label: str | None
    # Count of plain-string user turns — same definition the sidebar
    # used to compute from in-memory ChatSession.messages.
    user_message_count: int
    # Count of assistant turns that produced at least one non-empty
    # text block. Excludes pure tool_use loops with no final answer.
    assistant_text_count: int
    updated_at: datetime | None


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

    def list_summaries_for_user(self, user_id: str) -> list[SessionListEntry]:
        """Sidebar-shaped projection: ``list_for_user`` + first message + anchor.

        Kept separate from ``list_for_user`` so the heavy listing path
        (joins through the case + scans messages) doesn't penalise the
        cheap "do you own this session" lookups callers use elsewhere.
        Newest-first ordering by ``updated_at`` is the contract.
        """
        ...


def summarise_messages(
    messages: list,
) -> tuple[str | None, int, int]:
    """Scan a ChatSession's in-memory ``messages`` list for sidebar bits.

    Returns ``(first_user_message, user_count, assistant_text_count)``.
    User messages whose content is a list (tool_result intermediates)
    are skipped — only plain-string user turns count. Assistant turns
    only count if they produced at least one non-empty text block.
    """
    first_user: str | None = None
    user_count = 0
    assistant_text_count = 0
    for m in messages:
        if m.role == LLMRole.USER:
            if isinstance(m.content, str):
                user_count += 1
                if first_user is None:
                    first_user = m.content
            # else: tool_result intermediate — skip
        elif m.role == LLMRole.ASSISTANT and isinstance(m.content, list):
            if any(
                getattr(b, "type", None) == "text"
                and getattr(b, "text", "").strip()
                for b in m.content
            ):
                assistant_text_count += 1
    return first_user, user_count, assistant_text_count


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

    def list_summaries_for_user(self, user_id: str) -> list[SessionListEntry]:
        """Project in-memory sessions into ``SessionListEntry`` rows.

        ``anchor_label`` is sourced from ``ChatSession.case_anchor_label``
        when the chat route has mirrored it onto the session (case-bound
        path); legacy / test sessions without a case leave it ``None``.
        Ordering matches ``list_for_user`` — the route does its own sort.
        """
        entries: list[SessionListEntry] = []
        for s in self._sessions.values():
            if s.user_id != user_id:
                continue
            first_user, user_count, assistant_text_count = summarise_messages(
                s.messages
            )
            entries.append(
                SessionListEntry(
                    session_id=s.session_id,
                    model=s.model,
                    first_user_message=first_user,
                    anchor_label=s.case_anchor_label,
                    user_message_count=user_count,
                    assistant_text_count=assistant_text_count,
                    updated_at=s.updated_at,
                )
            )
        return entries
