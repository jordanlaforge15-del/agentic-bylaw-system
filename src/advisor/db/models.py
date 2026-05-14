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

Cost model
----------
This module defines the case-based pricing schema:

* ``Case`` — the inquiry anchor (a property address / project ref / DA
  number). One case spans every chat session opened against that anchor
  within the 30-day reopen window.
* ``CasePurchase`` — one row per Stripe checkout success; groups the
  individual credits issued by a single payment.
* ``CaseCredit`` — **one row per individual credit** (a Pro pack of 20
  → 20 rows). Per-credit storage is mandatory: it's how we run atomic
  tier-upgrade swaps and per-tier analytics without an aggregate-balance
  hack. See ``advisor.billing.packs`` for the pack catalog.
* ``CaseEvent`` — append-only audit narrative of every state transition,
  feeds the admin analytics dashboard.

The legacy subscription fields (``plan_tier``, ``monthly_*``,
``stripe_subscription_id``, etc.) have been removed from ``User`` —
billing is now per-credit, not per-month-of-access.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from layer1.db.base import Base, json_type, utcnow


class User(Base):
    """An end-user of the advisor SaaS.

    ``clerk_user_id`` is the authoritative identifier from the auth
    provider (Clerk). The internal numeric ``id`` is what foreign keys
    reference, so re-issuing Clerk credentials doesn't cascade through
    every related row.

    Quota / plan fields have been removed in favour of the case-credit
    model — see ``CaseCredit`` and ``Case``. The remaining fields are:

    * Identity: ``clerk_user_id``, ``email``, ``full_name``.
    * Stripe linkage: ``stripe_customer_id`` (still needed because
      one-time pack checkouts also produce a customer record we want to
      reuse on the next purchase).
    * Abuse cap: ``requests_per_minute_limit``. This is orthogonal to
      credit gating — a malicious script with one active credit can
      still spam ``/v1/chat`` with empty messages and burn our compute.
      Cheap to enforce against the existing usage-event log.
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
    requests_per_minute_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), index=True
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
    cases: Mapped[list["Case"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    case_credits: Mapped[list["CaseCredit"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Case(Base):
    """An inquiry anchored to a specific property / project / DA file.

    A case is the atomic unit of billing. Same anchor (normalised) by
    the same user within the 30-day window resolves to the same case —
    so reopening a research thread doesn't re-charge the user.

    ``anchor_label`` is the verbatim text the user typed (preserved for
    display); ``anchor_key`` is the normalised form used for the 30-day
    match. Normalisation rules live in ``advisor.db.cases``.

    ``current_tier`` mirrors the tier of the credit currently servicing
    the case so chat-time reads don't need to join through credits.
    Updated atomically with the credit swap on tier upgrades.

    ``tokens_consumed`` is the running total (input + output) across
    every session in the case. Layer 1 (``advisor.llm.budget``) reads
    this to enforce the per-tier token hard cap.
    """

    __tablename__ = "advisor_case"
    __table_args__ = (
        Index(
            "ix_advisor_case_user_key_status",
            "user_id",
            "anchor_key",
            "status",
        ),
        Index("ix_advisor_case_user_last_activity", "user_id", "last_activity_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    anchor_label: Mapped[str] = mapped_column(String(500), nullable=False)
    anchor_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # 'address' | 'project_ref' | 'development_application'.
    anchor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 'open' | 'closed' | 'archived'. Closed cases stay reopenable for
    # 30 days from last_activity_at; archived is admin-only.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )
    # Mirrored from the active credit so chat-time reads skip a join.
    current_tier: Mapped[str | None] = mapped_column(String(16))
    # Cumulative input+output tokens across all sessions in this case.
    tokens_consumed: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type()), nullable=False, default=dict
    )

    user: Mapped[User] = relationship(back_populates="cases")
    sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="case",
        # SET NULL on delete — a deleted case shouldn't cascade-delete
        # the chat history, since we may still want it for support.
        passive_deletes=True,
    )
    credits: Mapped[list["CaseCredit"]] = relationship(
        back_populates="case",
        passive_deletes=True,
        primaryjoin="CaseCredit.case_id == Case.id",
        foreign_keys="CaseCredit.case_id",
    )
    events: Mapped[list["CaseEvent"]] = relationship(
        back_populates="case", passive_deletes=True
    )


class CasePurchase(Base):
    """One row per Stripe checkout success — a batch of credits.

    Groups the N individual ``CaseCredit`` rows that a single pack
    purchase produced, so the billing page can show "Pro pack of 20
    Standard, $1,105.00 paid 2026-04-12" rather than 20 anonymous
    credit rows.

    ``stripe_checkout_session_id`` is unique because that's our
    second idempotency layer beneath the existing
    ``stripe_event_id`` dedupe in ``advisor_usage_event`` — even if
    the event-level dedupe fails, the unique index here prevents
    double-credit issuance.

    ``pack_sku`` carries either a real Stripe-purchased SKU (``payg`` /
    ``starter`` / ``pro`` / ``enterprise``) or the synthetic
    ``admin_grant`` value used when admins gift credits without
    a Stripe transaction.
    """

    __tablename__ = "advisor_case_purchase"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pack_sku: Mapped[str] = mapped_column(String(32), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    list_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_bps: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    amount_paid_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="CAD"
    )
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(
        String(255), unique=True
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String(255), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    credits: Mapped[list["CaseCredit"]] = relationship(
        back_populates="purchase"
    )


class CaseCredit(Base):
    """One row per purchased / granted credit.

    The brief mandates per-credit storage rather than an aggregate
    balance: a Pro pack of 20 → 20 rows, each individually trackable
    through the lifecycle. Reasons:

    1. Atomic tier upgrade — burn this credit, swap to a higher-tier
       credit, all under one ``FOR UPDATE`` lock.
    2. Tier integrity — credits are tier-typed at purchase. A user
       cannot conjure 1 Standard out of 2 Quick.
    3. Source attribution — refunds and revenue analytics need to
       know which pack each credit came from.
    4. Per-tier analytics — count credits by ``(tier, source, state)``
       directly without an aggregate-balance reconciliation.

    Lifecycle:
      ``available`` → ``reserved`` (case-open) → ``consumed`` (first
      qualifying tool output).
      ``reserved`` → ``available`` (refund on abandon).
      ``reserved | consumed`` → ``upgraded_out`` (tier upgrade accepted;
      ``upgraded_to_credit_id`` points at the replacement).
      ``available`` → ``expired`` (admin / pack-expiry policy).

    Partial unique index on ``(session_id) WHERE state IN
    ('reserved','consumed')`` enforces "at most one live credit per
    session" at the DB layer — the upgrade transaction relies on this.
    """

    __tablename__ = "advisor_case_credit"
    __table_args__ = (
        Index(
            "ix_advisor_case_credit_user_tier_state",
            "user_id",
            "tier",
            "state",
        ),
        Index(
            "uq_advisor_case_credit_active_session",
            "session_id",
            unique=True,
            postgresql_where=text("state IN ('reserved', 'consumed')"),
            sqlite_where=text("state IN ('reserved', 'consumed')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    purchase_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_case_purchase.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    # 'payg' | 'starter' | 'pro' | 'enterprise' | 'admin_grant' |
    # 'upgrade'. ``upgrade`` is set on the new credit produced by an
    # in-flight tier swap so analytics can distinguish purchased upgrades
    # from organic pack purchases.
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # 'available' | 'reserved' | 'consumed' | 'refunded' |
    # 'upgraded_out' | 'expired'.
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="available"
    )
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("advisor_case.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("advisor_chat_session.id", ondelete="SET NULL"),
        nullable=True,
    )
    upgraded_from_tier: Mapped[str | None] = mapped_column(String(16))
    upgraded_to_credit_id: Mapped[int | None] = mapped_column(
        ForeignKey("advisor_case_credit.id", ondelete="SET NULL"),
        nullable=True,
    )
    purchased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type()), nullable=False, default=dict
    )

    user: Mapped[User] = relationship(back_populates="case_credits")
    purchase: Mapped[CasePurchase] = relationship(back_populates="credits")
    case: Mapped["Case | None"] = relationship(
        back_populates="credits",
        foreign_keys=[case_id],
    )


class CaseEvent(Base):
    """Append-only audit narrative for the case-credit lifecycle.

    Powers two things the operational data alone can't:

    1. The admin analytics dashboard (recommendation-vs-actual
       confusion matrix, upgrade conversion rate, abandon rate). Every
       interesting state transition writes a row here, so all of those
       are aggregate queries against this single table.
    2. Support diagnostics — when a user asks "why was my case
       charged?" the support agent can replay the event timeline.

    ``event_type`` values used by the case service:
      - ``opened`` / ``reopened`` / ``closed``
      - ``credit_reserved`` / ``credit_consumed`` / ``credit_refunded``
      - ``tier_recommended`` (Layer 2 classifier output)
      - ``upgrade_offered`` (Layer 3 agent or Layer 2 classifier)
      - ``upgrade_accepted`` / ``upgrade_declined``
      - ``budget_warning`` (Layer 1 nearing exhaustion)
      - ``admin_credit_grant`` / ``admin_credit_expire``
    """

    __tablename__ = "advisor_case_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("advisor_case.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credit_id: Mapped[int | None] = mapped_column(
        ForeignKey("advisor_case_credit.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type()), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    case: Mapped["Case | None"] = relationship(back_populates="events")


class ChatSession(Base):
    """One conversation thread for a user.

    ``title`` is left nullable because it's typically generated lazily
    from the first user message — newly created sessions are unnamed.
    Cascading delete from ``User`` is intentional: if a user is purged,
    their conversation history goes with them (GDPR-style erasure).

    Case linkage:
      - ``case_id`` — every chat session that bills a credit must be
        attached to a case. Nullable for legacy sessions and tests
        that don't exercise billing.
      - ``tier`` — denormalised from the active ``CaseCredit`` so the
        chat hot-path can read tier without a join.
      - ``token_budget_remaining`` — running per-case ledger; chat
        layer decrements after each turn and surfaces an upgrade
        prompt when it hits the warn threshold.
    """

    __tablename__ = "advisor_chat_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("advisor_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("advisor_case.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tier: Mapped[str | None] = mapped_column(String(16))
    token_budget_remaining: Mapped[int | None] = mapped_column(BigInteger)
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
    case: Mapped["Case | None"] = relationship(back_populates="sessions")
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
    (Stripe webhook receipts, credit-state transitions, classifier
    invocations), and a single message may produce several events (one
    ``llm_call`` plus several ``tool_call`` rows). Append-only by
    convention; aggregation lives in queries, not in column updates.

    ``session_id`` and ``case_id`` use ``ON DELETE SET NULL`` rather
    than CASCADE: if a user purges a chat session or a case is closed,
    the audit trail of cost / usage stays intact, just with the
    pointers cleared. ``user_id`` does cascade because deleting a user
    is a hard erasure.

    ``case_id`` is the per-case cost ledger join: a SUM of
    ``cost_estimate_cents`` grouped by ``case_id`` is the raw cost
    against the price the user paid for the case.
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
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("advisor_case.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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


class InviteRequest(Base):
    """A pending / decided invite request to the private-beta product.

    Replaces the old web/data/invites.jsonl file. The web frontend's
    /api/invite route creates rows with status='pending';
    /admin/invites flips them to approved / rejected.

    When approved, the admin handler ALSO calls Clerk's Backend API
    to add the email to the allowlist (so Clerk's own sign-up flow
    will accept that email). The returned allowlist-identifier id is
    stored in clerk_allowlist_id; we need it to delete the
    allowlist entry when an invite expires unredeemed.

    On first sign-in, ``granted_starter_credits`` rows of type
    ``granted_starter_tier`` are issued to the user as
    ``CaseCredit`` rows with ``source='admin_grant'``. Default is zero
    credits — admins choose whether to gift trial credits per-invite.

    Expiry: when admin approves, expires_at is set to
    decided_at + 14 days. A daily sweep finds approved-but-
    unredeemed-and-expired rows, removes them from Clerk's allowlist,
    and flips status to 'expired'. Redeemed invites (redeemed_at
    set) survive the sweep — once the user has signed in, they keep
    their access regardless of the invite expiry.
    """

    __tablename__ = "invite_request"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str | None] = mapped_column(String(200))
    project: Mapped[str | None] = mapped_column(Text())
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(320))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clerk_allowlist_id: Mapped[str | None] = mapped_column(String(64))
    # Number of starter credits to issue on first sign-in (0 = none).
    granted_starter_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Tier of those credits when ``granted_starter_credits > 0``. Stored
    # rather than hardcoded so an invite could grant Standard credits
    # while the default elsewhere is Quick.
    granted_starter_tier: Mapped[str | None] = mapped_column(String(16))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text())
