"""Add user_case_number to advisor_case (ABS-186).

Postgres sequences are non-transactional: a ``db.flush()`` allocates the
next sequence value even when the surrounding transaction later rolls
back. This means ``advisor_case.id`` can jump non-contiguously (e.g.
ids 1, 5, 7 if transactions for ids 2-4 rolled back before commit).

``user_case_number`` is a per-user sequential counter (1, 2, 3 …)
assigned as an ordinary integer column inside the same transaction that
creates the case row. Because it is not a sequence, it rolls back with
the case row on failure — the user always sees contiguous "Case #N"
labels regardless of gaps in the global primary key.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_user_case_number"
down_revision = "0019_api_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "advisor_case",
        sa.Column("user_case_number", sa.Integer(), nullable=True),
    )
    # Backfill existing rows: assign 1-based sequential numbers per
    # user ordered by (opened_at, id) so the numbering matches
    # creation order.
    op.execute(
        """
        WITH numbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY user_id ORDER BY opened_at, id
                   ) AS rn
            FROM advisor_case
        )
        UPDATE advisor_case
        SET user_case_number = numbered.rn
        FROM numbered
        WHERE advisor_case.id = numbered.id
        """
    )
    op.alter_column("advisor_case", "user_case_number", nullable=False)
    op.create_index(
        "ix_advisor_case_user_case_number",
        "advisor_case",
        ["user_id", "user_case_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_advisor_case_user_case_number", table_name="advisor_case")
    op.drop_column("advisor_case", "user_case_number")
