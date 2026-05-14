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

Quota fields previously living on ``UserOut`` (``plan_tier``,
``monthly_query_limit``, ``monthly_queries_used``) have been removed;
billing is now per-case-credit, not per-month-of-access. The
``CaseCredit*`` and ``Case*`` schemas below are the new shape that the
billing / cases endpoints return.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# -- User -------------------------------------------------------------


class UserCreate(BaseModel):
    """Fields the API accepts on first-touch user provisioning."""

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
    created_at: datetime


# -- ChatSession ------------------------------------------------------


class ChatSessionCreate(BaseModel):
    """Caller may seed a title; usually omitted and generated lazily."""

    title: str | None = Field(default=None, max_length=500)
    case_id: int | None = Field(default=None)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    case_id: int | None = None
    tier: str | None = None
    token_budget_remaining: int | None = None
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    metadata_json: dict[str, Any] = Field(default_factory=dict)


# -- ChatMessage ------------------------------------------------------


class ChatMessageCreate(BaseModel):
    """One turn that the chat backend wants to persist."""

    sequence: int = Field(ge=0)
    role: str = Field(pattern=r"^(user|assistant|system)$")
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
    """Telemetry record. Mostly written by ``advisor.db.cases`` and
    the chat backend; the API surface for creating these by hand is
    primarily for tests and admin tooling."""

    event_type: str = Field(max_length=64)
    session_id: int | None = None
    case_id: int | None = None
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
    case_id: int | None = None
    event_type: str
    provider: str | None = None
    model: str | None = None
    tokens_input: int
    tokens_output: int
    cost_estimate_cents: int
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# -- Case -------------------------------------------------------------


class CaseOut(BaseModel):
    """Shape returned for a single case (case browser, header)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    anchor_label: str
    anchor_kind: str
    status: str
    current_tier: str | None = None
    tokens_consumed: int
    opened_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None = None


class CaseCreditOut(BaseModel):
    """Shape returned for a single credit (admin tooling, billing page)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    purchase_id: int
    tier: str
    source: str
    state: str
    case_id: int | None = None
    session_id: int | None = None
    upgraded_from_tier: str | None = None
    purchased_at: datetime
    consumed_at: datetime | None = None


class CreditBalanceEntry(BaseModel):
    """Aggregated credit balance for a single (tier, state) combination."""

    tier: str
    state: str
    count: int


class CreditBalanceSummary(BaseModel):
    """Per-tier breakdown returned by ``GET /v1/billing/me``."""

    tier: str
    available: int
    reserved: int
    consumed: int
