"""General feedback table for non-message-specific user feedback.

Stores free-form feedback submitted from the app shell's feedback
form — UX issues, feature requests, general satisfaction. Each row
is tied to a user but not to a specific chat session or message.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_general_feedback"
down_revision = "0014_compliance_schema"
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
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_advisor_general_feedback_user_id",
        "advisor_general_feedback",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_advisor_general_feedback_user_id",
        table_name="advisor_general_feedback",
    )
    op.drop_table("advisor_general_feedback")
