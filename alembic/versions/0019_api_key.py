"""Add advisor_api_key table (ABS-59).

Stores hashed API keys for machine-to-machine access to the ABS
integration endpoints — initially used by the Speckle Automate function
to push IFC submissions without going through Clerk JWT auth.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_api_key"
down_revision = "0018_general_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advisor_api_key",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_advisor_api_key_key_hash", "advisor_api_key", ["key_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_advisor_api_key_key_hash", table_name="advisor_api_key")
    op.drop_table("advisor_api_key")
