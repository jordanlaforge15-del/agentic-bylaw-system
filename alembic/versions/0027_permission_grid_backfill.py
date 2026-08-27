"""Carry the ABS-520 permission-grid repair to every environment (ABS-526).

ABS-520 shipped as code *plus* a data migration, and only the code half had a
delivery mechanism. ``scripts/backfill_permission_grid.py`` was run by hand
against dev; production kept its ragged grid through the deploy that shipped the
fix, so the advisor went on answering "the permission could not be extracted"
where the by-law prints a blank cell — which in the Regional Centre LUB *is* the
prohibition. Nothing was broken enough to notice: the code was merged, the
tests were green, and the corpus nobody re-ran the script against was the one
users were asking.

This migration is that missing mechanism. ``alembic upgrade head`` already runs
on every deploy (docs/DEPLOYMENT.md) and in every e2e stack boot, so putting the
repair here makes an environment converge because it was deployed, not because
an operator remembered.

What it does
------------
Calls :func:`layer1.semantic.permission_grid.densify_corpus` — the identical
function enrichment runs at ingest and the backfill script runs by hand. Only
tables carrying a ``permission_matrix`` semantic profile are touched, and within
them only intersections whose geometry shows the row lost nothing. Refused
intersections stay missing and stay ``unknown``: the ABS-483 distinction between
"we could not read this" and "the by-law prohibits this" survives, and the
residue is logged rather than hidden.

Idempotent — a second pass finds the intersections occupied and creates nothing
— and a no-op on any database with no permission matrix, which is CI, a fresh
deploy before its first ingest, and every e2e worktree without the Halifax
ingest.

Reversible. ``downgrade`` deletes exactly the cells this created, identified by
their own ``metadata_json.grid_fill='absent_cell'`` label. Nothing else writes
that key, so the reversal cannot take a cell the parser stored.

Why a data migration is allowed to import application code
----------------------------------------------------------
The usual objection — a migration frozen in the chain calls code that later
drifts — is answered by the no-op: this migration reads the corpus, and any
corpus it cannot make sense of yields zero gaps rather than an error. It writes
no DDL and depends on no model shape beyond ``source_table_cell``'s columns,
which 0001 created and nothing since has changed.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from alembic import op

# A child of alembic's own logger, so the summary lands in the deploy log that
# `alembic upgrade head` is already writing — the refused-intersection residue
# is the record of what extraction debt remains.
logger = logging.getLogger("alembic.permission_grid_backfill")

revision = "0027_permission_grid_backfill"
down_revision = "0026_drop_parcel_zone_code"
branch_labels = None
depends_on = None


def _skipped_offline(direction: str) -> bool:
    """``alembic upgrade head --sql`` cannot read a corpus it is not connected to.

    DEPLOYMENT.md previews every migration in offline mode before running it.
    A data migration has no SQL to emit there — the statements depend on what
    the geometry says — so say so plainly instead of failing on a mock bind.
    """
    if not op.get_context().as_sql:
        return False
    logger.warning(
        "offline mode: the permission-grid %s reads the corpus and cannot be "
        "rendered as SQL; it runs against the live database only",
        direction,
    )
    return True


def upgrade() -> None:
    from layer1.semantic.permission_grid import densify_corpus

    if _skipped_offline("backfill"):
        return
    session = Session(bind=op.get_bind())
    stats = densify_corpus(session, apply=True)
    session.flush()
    if stats.tables_scanned == 0:
        logger.info("no permission-matrix table in this corpus; nothing to repair")
        return
    logger.info("permission grid densified: %s", stats.summary_line())


def downgrade() -> None:
    from layer1.semantic.permission_grid import strip_corpus_grid_fills

    if _skipped_offline("reversal"):
        return
    session = Session(bind=op.get_bind())
    removed = strip_corpus_grid_fills(session)
    session.flush()
    logger.info("removed %d materialized blank permission cells", removed)
