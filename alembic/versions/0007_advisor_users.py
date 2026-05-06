"""Advisor SaaS user/session/usage tables.

Adds the ``advisor_*`` tables that back the chat product onto the same
Postgres instance that already holds Layer 1's bylaw data. They share
the migration chain because the deploy story is "one Postgres
instance, two logical schemas". Splitting them later is a rename pass
on this file's table names plus a fresh migration.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_advisor_users"
down_revision = "0006_source_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "advisor_user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clerk_user_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_tier", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("monthly_query_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("monthly_queries_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("month_started_at", sa.Date(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.UniqueConstraint("clerk_user_id", name="uq_advisor_user_clerk_user_id"),
    )
    op.create_index(
        "ix_advisor_user_clerk_user_id", "advisor_user", ["clerk_user_id"]
    )
    op.create_index(
        "ix_advisor_user_stripe_customer_id",
        "advisor_user",
        ["stripe_customer_id"],
    )

    op.create_table(
        "advisor_chat_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    op.create_index(
        "ix_advisor_chat_session_user_id", "advisor_chat_session", ["user_id"]
    )

    op.create_table(
        "advisor_chat_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("advisor_chat_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content_json", json_type, nullable=False),
        sa.Column("tool_calls_json", json_type, nullable=False),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_advisor_chat_message_session_sequence",
        ),
    )
    op.create_index(
        "ix_advisor_chat_message_session_id",
        "advisor_chat_message",
        ["session_id"],
    )

    op.create_table(
        "advisor_usage_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("advisor_chat_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cost_estimate_cents", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_advisor_usage_event_user_id", "advisor_usage_event", ["user_id"]
    )
    op.create_index(
        "ix_advisor_usage_event_created_at",
        "advisor_usage_event",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_advisor_usage_event_created_at", table_name="advisor_usage_event"
    )
    op.drop_index(
        "ix_advisor_usage_event_user_id", table_name="advisor_usage_event"
    )
    op.drop_table("advisor_usage_event")

    op.drop_index(
        "ix_advisor_chat_message_session_id", table_name="advisor_chat_message"
    )
    op.drop_table("advisor_chat_message")

    op.drop_index(
        "ix_advisor_chat_session_user_id", table_name="advisor_chat_session"
    )
    op.drop_table("advisor_chat_session")

    op.drop_index(
        "ix_advisor_user_stripe_customer_id", table_name="advisor_user"
    )
    op.drop_index("ix_advisor_user_clerk_user_id", table_name="advisor_user")
    op.drop_table("advisor_user")
