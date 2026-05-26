"""Add chat_message_feedback table (ABS-121).

Stores per-user thumbs up/down ratings and flag/report data on
individual chat messages. Unique on (message_id, user_id) so
re-submitting overwrites rather than duplicates.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_chat_message_feedback"
down_revision = "0016_unlimited_credits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advisor_chat_message_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("advisor_chat_message.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.String(8), nullable=True),
        sa.Column("flag_reason", sa.String(32), nullable=True),
        sa.Column("flag_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_advisor_chat_message_feedback_msg_user",
        ),
    )


def downgrade() -> None:
    op.drop_table("advisor_chat_message_feedback")
