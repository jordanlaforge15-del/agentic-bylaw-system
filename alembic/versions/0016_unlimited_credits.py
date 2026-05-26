"""Add unlimited_credits flag to advisor_user (ABS-12).

Test / QA accounts with ``unlimited_credits = TRUE`` bypass the credit
gate in ``_claim_available_credit``: when no available credit exists the
system auto-mints one with ``source='test'`` so continuous QA doesn't
require repeated admin grants. The flag defaults to FALSE and can only
be toggled via the admin API.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_unlimited_credits"
down_revision = "0015_enforce_one_active_credit_per_case"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "advisor_user",
        sa.Column(
            "unlimited_credits",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("advisor_user", "unlimited_credits")
