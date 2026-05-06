"""SQLAlchemy models for the advisor SaaS layer.

These bind to the same ``Base`` as Layer 1's bylaw schema (see
``layer1.db.base.Base``) so a single Alembic chain manages both the
content tables (``document``, ``source_fragment``, ...) and the user /
session tables introduced here. The ``advisor_`` table prefix keeps the
two concerns visually separate and makes a future split into a
dedicated user database a rename rather than a redesign.

JSON columns reuse the same ``json_type()`` helper that resolves to
``JSONB`` on Postgres and ``JSON`` everywhere else (sqlite for tests),
so the same model definitions exercise both backends.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from layer1.db.base import Base, json_type, utcnow


def _today() -> date:
    """Today's date in UTC. The quota window is anchored to the first
    of the current month — see ``advisor.db.quota`` — so a
    UTC-stamped ``date.today()`` is precise enough."""
    return utcnow().date()


class User(Base):
    """An end-user of the advisor SaaS.

    ``clerk_user_id`` is the authoritative identifier from the auth
    provider (Clerk). The internal numeric ``id`` is what foreign keys
    reference, so re-issuing Clerk credentials doesn't cascade through
    every related row.

    Quota fields (``monthly_query_limit``, ``monthly_queries_used``,
    ``month_started_at``) are denormalised onto the user row for the
    common path: every chat message has to read the user's quota and
    most updates are increments. Joining against an aggregated usage
    view at chat time would multiply read load. A reconciliation job
    can recompute against ``advisor_usage_event`` when needed.
    """

    __tablename__ = "advisor_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clerk_user_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    plan_tier: Mapped[str] = mapped_column(
        String(32), nullable=False, default="free"
    )
    monthly_query_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )
    monthly_queries_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    month_started_at: Mapped[date] = mapped_column(
        Date, nullable=False, default=_today
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), index=True
    )
    # Subscription bookkeeping populated by the billing webhook.
    # Nullable because free-tier users have no subscription, and a
    # cancelled subscription clears these back to None until the
    # user upgrades again.
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), index=True
    )
    subscription_status: Mapped[str | None] = mapped_column(String(32))
    subscription_current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    metadata_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type()), nullable=False, default=dict
    )

    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    usage_events: Mapped[list["UsageEvent"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ChatSession(Base):
    """One conversation thread for a user.

    ``title`` is left nullable because it's typically generated lazily
    from the first user message — newly created sessions are unnamed.
    Cascading delete from ``User`` is intentional: if a user is purged,
    their conversation history goes with them (GDPR-style erasure).
    """

    __tablename__ = "advisor_chat_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    metadata_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type()), nullable=False, default=dict
    )

    user: Mapped[User] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.sequence",
    )


class ChatMessage(Base):
    """One turn of a chat session.

    ``sequence`` is set by the caller (start at 0) and uniquely
    identifies the message within the session. The
    ``(session_id, sequence)`` unique constraint is what lets the chat
    backend safely retry a failed write without producing duplicates.

    ``content_json`` stores the full structured ``Message`` payload
    from ``advisor.llm`` — for assistant turns that's a list of content
    blocks (text, tool_use, tool_result). For user turns a plain string
    is also valid. ``tool_calls_json`` is a denormalised summary of any
    ``ToolInvocation`` records produced by ``advisor.llm.tool_loop``,
    kept here for fast reads of "what tools did this turn invoke?"
    without having to re-scan ``content_json``.
    """

    __tablename__ = "advisor_chat_message"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_advisor_chat_message_session_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_chat_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # JSON payload may be a string, list, or dict depending on role —
    # leave the column type permissive and let the pydantic schema
    # enforce shape at the API boundary.
    content_json: Mapped[object] = mapped_column(
        json_type(), nullable=False
    )
    tool_calls_json: Mapped[list] = mapped_column(
        MutableList.as_mutable(json_type()), nullable=False, default=list
    )
    tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class UsageEvent(Base):
    """A single billable / observable event.

    Distinct from ``ChatMessage`` because not every event is a message
    (quota resets, quota exceedances, session creation), and a single
    message may produce several events (one ``llm_call`` plus several
    ``tool_call`` rows). Append-only by convention; aggregation lives
    in queries, not in column updates.

    ``session_id`` uses ``ON DELETE SET NULL`` rather than CASCADE: if
    a user purges a chat session the audit trail of cost / usage stays
    intact, just with the session pointer cleared. ``user_id`` does
    cascade because deleting a user is a hard erasure.
    """

    __tablename__ = "advisor_usage_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("advisor_chat_session.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    cost_estimate_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    metadata_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type()), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    user: Mapped[User] = relationship(back_populates="usage_events")
    # Intentionally NOT cascade — see class docstring.
    session: Mapped[ChatSession | None] = relationship()
