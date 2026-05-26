"""Enforce at most one unsessioned reservation per case (ABS-11).

The pre-ABS-9 code path could double-reserve credits on a case: ``open_case``
reserved one credit with ``session_id = NULL``, then the chat-session start
claimed a second instead of adopting the first.  The application-level fix
(idempotency guard in ``open_case`` + adoption path in
``reserve_credit_for_session``) landed earlier, but this migration adds the
DB-layer defence-in-depth constraint and cleans up any orphaned reservations
already present in production data.

Data fix
--------
1. Deduplicate: if a case somehow has two ``reserved`` credits with
   ``session_id IS NULL`` (pre-fix race), keep the oldest and refund the rest.
2. Orphan recovery: refund any ``reserved / session_id IS NULL`` credit on a
   case that already has a sessioned active credit (the classic ABS-11 leak).

Constraint
----------
Partial unique index ``uq_advisor_case_credit_one_unsessioned_reserve`` on
``(case_id) WHERE state = 'reserved' AND session_id IS NULL``.  After this,
the DB itself rejects a second unsessioned reservation on the same case.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_enforce_one_active_credit_per_case"
down_revision = "0014_compliance_schema"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_advisor_case_credit_one_unsessioned_reserve"


def upgrade() -> None:
    conn = op.get_bind()
    is_postgres = conn.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # Step 1 — deduplicate: keep oldest unsessioned reservation per case,
    #          refund any extras.
    # ------------------------------------------------------------------
    if is_postgres:
        conn.execute(
            sa.text("""
                UPDATE advisor_case_credit
                SET state      = 'available',
                    case_id    = NULL,
                    reserved_at = NULL
                WHERE id IN (
                    SELECT c.id
                    FROM advisor_case_credit c
                    JOIN (
                        SELECT case_id, MIN(id) AS keep_id
                        FROM advisor_case_credit
                        WHERE state = 'reserved'
                          AND session_id IS NULL
                          AND case_id IS NOT NULL
                        GROUP BY case_id
                        HAVING COUNT(*) > 1
                    ) dups ON c.case_id = dups.case_id
                    WHERE c.state = 'reserved'
                      AND c.session_id IS NULL
                      AND c.id != dups.keep_id
                )
            """)
        )

    # ------------------------------------------------------------------
    # Step 2 — orphan recovery: refund reserved/unsessioned credits on
    #          cases that already have a sessioned active credit.
    # ------------------------------------------------------------------
    if is_postgres:
        conn.execute(
            sa.text("""
                UPDATE advisor_case_credit
                SET state       = 'available',
                    case_id     = NULL,
                    reserved_at = NULL
                WHERE id IN (
                    SELECT orphan.id
                    FROM advisor_case_credit orphan
                    WHERE orphan.state = 'reserved'
                      AND orphan.session_id IS NULL
                      AND orphan.case_id IS NOT NULL
                      AND EXISTS (
                          SELECT 1
                          FROM advisor_case_credit sibling
                          WHERE sibling.case_id  = orphan.case_id
                            AND sibling.tier      = orphan.tier
                            AND sibling.id       != orphan.id
                            AND sibling.session_id IS NOT NULL
                            AND sibling.state IN ('reserved', 'consumed')
                      )
                )
            """)
        )

    # ------------------------------------------------------------------
    # Step 3 — add the partial unique index.
    # ------------------------------------------------------------------
    if is_postgres:
        op.create_index(
            INDEX_NAME,
            "advisor_case_credit",
            ["case_id"],
            unique=True,
            postgresql_where=sa.text(
                "state = 'reserved' AND session_id IS NULL"
            ),
        )
    else:
        op.execute(
            f"CREATE UNIQUE INDEX {INDEX_NAME} "
            "ON advisor_case_credit (case_id) "
            "WHERE state = 'reserved' AND session_id IS NULL"
        )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="advisor_case_credit")
