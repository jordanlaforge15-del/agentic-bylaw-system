"""Explicit retrieval publish flag on documents (ABS-413).

``document.retrieval_enabled`` replaces every recency-derived retrieval
scope (``latest_per_bylaw_resolver`` in production, ``--latest-only`` on
the dev MCP server). The retrieval corpus is now exactly the set of
documents an operator has enabled; freshly ingested documents default to
disabled (opt-in publish via the layer1 CLI).

Backfill: enabled = the documents ``latest_per_bylaw_resolver`` selected
at migration time — the newest ingest per ``(municipality, bylaw_name)``,
ties broken by highest id, mirroring that resolver's ordering exactly —
so retrieval behavior is unchanged the moment this migration lands.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_document_retrieval_enabled"
down_revision = "0023_token_wallet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document",
        sa.Column("retrieval_enabled", sa.Boolean(), nullable=True),
    )
    # Window ordering must stay in lockstep with the pre-ABS-413
    # latest_per_bylaw_resolver: ingestion_timestamp DESC, id DESC.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY municipality, bylaw_name
                       ORDER BY ingestion_timestamp DESC, id DESC
                   ) AS rn
            FROM document
        )
        UPDATE document
        SET retrieval_enabled = (
            document.id IN (SELECT id FROM ranked WHERE rn = 1)
        )
        """
    )
    op.alter_column(
        "document",
        "retrieval_enabled",
        nullable=False,
        server_default=sa.false(),
    )


def downgrade() -> None:
    op.drop_column("document", "retrieval_enabled")
