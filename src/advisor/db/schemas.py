"""Pydantic v2 read/write schemas for the advisor user layer.

Kept deliberately separate from the SQLAlchemy models. The
``from_attributes=True`` config on the ``*Out`` schemas means a route
handler can do ``UserOut.model_validate(user_row)`` directly against a
SQLAlchemy row without an explicit dict shuffle.

The ``content_json`` field on ``ChatMessageOut`` is intentionally
``str | list | dict`` because the chat backend stores either a plain
string (for simple user turns) or the structured content-block list
(for assistant turns produced by ``advisor.llm``). The API layer is
the right place to enforce a tighter shape if a particular endpoint
needs one.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# -- User -------------------------------------------------------------


class UserCreate(BaseModel):
    """Fields the API accepts on first-touch user provisioning.

    Quota / plan fields aren't accepted on create — they default to the
    free tier and are mutated only by billing webhooks or admin
    endpoints, never by the user themselves.
    """

    clerk_user_id: str = Field(min_length=1, max_length=255)
    # Plain str rather than EmailStr to avoid the email-validator dep —
    # Clerk has already validated the address upstream, and the API
    # layer can re-validate at the route level if it cares.
    email: str = Field(min_length=3, max_length=320)
    full_name: str | None = Field(default=None, max_length=255)


class UserOut(BaseModel):
    """User shape returned to authenticated callers (themselves)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    clerk_user_id: str
    email: str
    full_name: str | None = None
    plan_tier: str
    monthly_query_limit: int
    monthly_queries_used: int
    created_at: datetime


# -- ChatSession ------------------------------------------------------


class ChatSessionCreate(BaseModel):
    """Caller may seed a title; usually omitted and generated lazily."""

    title: str | None = Field(default=None, max_length=500)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    metadata_json: dict[str, Any] = Field(default_factory=dict)


# -- ChatMessage ------------------------------------------------------


class ChatMessageCreate(BaseModel):
    """One turn that the chat backend wants to persist.

    ``sequence`` is caller-assigned — the chat backend tracks the
    next-sequence-per-session in memory and writes it through. Doing
    it server-side via a subquery would be a hot point of contention;
    the ``UniqueConstraint(session_id, sequence)`` is the
    correctness backstop.
    """

    sequence: int = Field(ge=0)
    role: str = Field(pattern=r"^(user|assistant|system)$")
    # Permissive: assistant turns are list[ContentBlock]-shaped dicts;
    # user turns may be plain strings; system turns may be either.
    content_json: str | list[Any] | dict[str, Any]
    tool_calls_json: list[Any] = Field(default_factory=list)
    tokens_input: int = Field(default=0, ge=0)
    tokens_output: int = Field(default=0, ge=0)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    sequence: int
    role: str
    content_json: str | list[Any] | dict[str, Any]
    tool_calls_json: list[Any] = Field(default_factory=list)
    tokens_input: int
    tokens_output: int
    created_at: datetime


# -- UsageEvent -------------------------------------------------------


class UsageEventCreate(BaseModel):
    """Telemetry record. Mostly written by ``advisor.db.quota`` and
    the chat backend; the API surface for creating these by hand is
    primarily for tests and admin tooling."""

    event_type: str = Field(max_length=64)
    session_id: int | None = None
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    tokens_input: int = Field(default=0, ge=0)
    tokens_output: int = Field(default=0, ge=0)
    cost_estimate_cents: int = Field(default=0, ge=0)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class UsageEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    session_id: int | None = None
    event_type: str
    provider: str | None = None
    model: str | None = None
    tokens_input: int
    tokens_output: int
    cost_estimate_cents: int
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# -- Quota ------------------------------------------------------------


class MonthlyQuota(BaseModel):
    """Snapshot of a user's monthly query allowance.

    ``window_start`` is the first day of the current monthly window —
    i.e. the value of ``user.month_started_at`` after any pending
    rollover has been applied.
    """

    limit: int
    used: int
    remaining: int
    window_start: date
