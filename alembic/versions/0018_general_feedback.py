"""Add general_feedback table (ABS-129).

Stores non-message-specific feedback submitted from the app shell:
UX issues, feature requests, general satisfaction, and other categories.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_general_feedback"
down_revision = "0017_chat_message_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advisor_general_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("advisor_general_feedback")
