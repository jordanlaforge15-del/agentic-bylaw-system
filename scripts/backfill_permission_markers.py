"""Backfill ``permission_marker`` onto existing permission-matrix cells.

ABS-277 — Phase 1. The solid ● "permitted as-of-right" dot in Table 1A is
stored in ``source_table_cell.text`` as a symbol-font Private Use Area
codepoint (``U+F098``) that reads as blank to every downstream consumer. This
script normalizes each permission-matrix cell's raw text into a canonical
marker and writes it to ``metadata_json.permission_marker`` (and ``footnote``
for conditional uses) without touching the raw ``text``.

Scope
-----
Only cells belonging to tables that carry a ``permission_matrix`` semantic
profile (``table_semantic_profile.profile_type``, written by enrichment's
structural classifier) are touched — NOT a caption match, because real-corpus
captions are empty (ABS-281). Header cells (row 0 / column 0) are skipped —
only the value grid carries marker semantics. The normalization logic lives in
``layer1.semantic.permission_markers`` and is shared with the enrichment-time
step, so a re-enrich and a backfill produce identical results.

Idempotent
----------
Safe to re-run. For each cell the desired metadata is computed and written
only when it differs from what's already stored, so a second run reports
``updated=0`` and leaves values unchanged.

Usage
-----
    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1 \
        .venv/bin/python scripts/backfill_permission_markers.py

    # Dry-run (compute and report, don't write):
    .venv/bin/python scripts/backfill_permission_markers.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import SourceTable, SourceTableCell, TableSemanticProfile
from layer1.db.session import session_scope
from layer1.semantic.permission_markers import (
    PERMISSION_MATRIX_PROFILE,
    annotate_cell,
    classify_permission_marker,
)


logger = logging.getLogger("backfill_permission_markers")


@dataclass
class BackfillStats:
    """What we did, suitable for a post-run issue update."""

    tables_scanned: int = 0
    cells_scanned: int = 0
    cells_updated: int = 0
    marker_counts: Counter = field(default_factory=Counter)

    def summary_line(self) -> str:
        markers = " ".join(
            f"{name}={count}" for name, count in sorted(self.marker_counts.items())
        )
        return (
            f"tables={self.tables_scanned} "
            f"cells={self.cells_scanned} "
            f"updated={self.cells_updated} "
            f"[{markers}]"
        )


def backfill(session: Session, *, dry_run: bool = False) -> BackfillStats:
    """Normalize permission markers on all permission-matrix cells.

    Caller owns the transaction — pass ``session_scope()``'s session for real
    runs or a test-fixture session for unit tests. In dry-run mode the desired
    metadata is computed and counted but never written.
    """
    stats = BackfillStats()

    tables = (
        session.execute(
            select(SourceTable)
            .join(
                TableSemanticProfile,
                TableSemanticProfile.table_id == SourceTable.id,
            )
            .where(TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE)
        )
        .scalars()
        .all()
    )
    logger.info("found %d permission-matrix tables", len(tables))

    for table in tables:
        stats.tables_scanned += 1
        cells = (
            session.execute(
                select(SourceTableCell)
                .where(SourceTableCell.table_id == table.id)
                .order_by(SourceTableCell.row_index, SourceTableCell.col_index)
            )
            .scalars()
            .all()
        )
        for cell in cells:
            # Header row / row-label column carry no marker semantics.
            if cell.row_index == 0 or cell.col_index == 0:
                continue
            stats.cells_scanned += 1
            changed = annotate_cell(cell, apply=not dry_run)
            if changed:
                stats.cells_updated += 1
            # Count the resulting marker for reporting. In dry-run the cell's
            # metadata isn't written, so recompute the classification value.
            if dry_run:
                marker = classify_permission_marker(cell.text)["permission_marker"]
            else:
                marker = (cell.metadata_json or {}).get("permission_marker", "unknown")
            stats.marker_counts[marker] += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and report markers, but don't write metadata_json.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (otherwise read from the environment).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    started = time.monotonic()
    with session_scope(args.database_url) as session:
        stats = backfill(session, dry_run=args.dry_run)
    elapsed_s = time.monotonic() - started

    print(
        f"backfill_permission_markers: {stats.summary_line()} "
        f"elapsed_s={elapsed_s:.2f} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
