"""Add free_questions_remaining to advisor_user (ABS-314).

Replaces the signup trial grant from 3 legacy CaseCredit rows to a
lightweight entitlement counter. New users receive FREE_QUESTION_GRANT
free questions consumed before any Stripe charge is placed.

Existing users default to 0 — pre-launch there are no real paid users
so the counter is only ever set by grant_starter_credits_if_needed at
first login.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_free_question_entitlement"
down_revision = "0020_user_case_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "advisor_user",
        sa.Column(
            "free_questions_remaining",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("advisor_user", "free_questions_remaining")
