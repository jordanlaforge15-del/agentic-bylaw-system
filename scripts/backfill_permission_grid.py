"""Materialize the blank permission-matrix cells the PDF parser dropped.

ABS-520. The table parser emits a ragged grid: a cell with no glyph produces no
``source_table_cell`` at all. In the Regional Centre LUB a blank cell is how the
by-law spells "not permitted", so every such prohibition reaches retrieval as a
*missing* cell — which ABS-483 correctly reports as ``unknown``. The user is
told "the permission could not be extracted" where the by-law says no.

This backfill repairs an already-ingested corpus without a re-parse. Enrichment
runs the same code on every fresh ingest
(:func:`layer1.semantic.permission_grid.densify_permission_matrix`), so a
re-enrich and a backfill produce identical grids.

Scope and safety
----------------
Only tables carrying a ``permission_matrix`` semantic profile are touched, and
within them only the intersections whose *geometry* shows the row lost nothing
(column drift, orphaned markers, dropped row labels and reprinted headers all
refuse the row). Refused intersections stay missing and stay ``unknown`` — the
ABS-483 distinction between "we could not read this" and "the by-law prohibits
this" is preserved, and the residue is reported rather than hidden.

Every cell created carries ``metadata_json.grid_fill='absent_cell'``, so the
materialization is greppable and reversible.

Idempotent
----------
Safe to re-run: a second pass finds the intersections occupied and creates
nothing.

Usage
-----
    # Report what would be filled, and the undetermined-use blast radius:
    .venv/bin/python scripts/backfill_permission_grid.py --dry-run \
        --zone ER-2 --zone ER-1 --zone CH-1

    # Apply:
    .venv/bin/python scripts/backfill_permission_grid.py
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
from layer1.db.migration_fence import fence_or_abort
from layer1.db.session import session_scope
from layer1.semantic.enrichment import enumerate_permission_column
from layer1.semantic.permission_grid import densify_permission_matrix
from layer1.semantic.permission_markers import PERMISSION_MATRIX_PROFILE, UNKNOWN

logger = logging.getLogger("backfill_permission_grid")


@dataclass
class GridBackfillStats:
    """What we did, suitable for a post-run issue update."""

    tables_scanned: int = 0
    tables_refused: int = 0
    cells_filled: int = 0
    cells_refused: int = 0
    reasons: Counter = field(default_factory=Counter)

    def summary_line(self) -> str:
        reasons = " ".join(
            f"{name}={count}" for name, count in sorted(self.reasons.items())
        )
        return (
            f"tables={self.tables_scanned} "
            f"tables_refused={self.tables_refused} "
            f"filled={self.cells_filled} "
            f"refused={self.cells_refused} "
            f"[{reasons}]"
        )


def permission_matrix_tables(session: Session) -> list[SourceTable]:
    return list(
        session.execute(
            select(SourceTable)
            .join(
                TableSemanticProfile,
                TableSemanticProfile.table_id == SourceTable.id,
            )
            .where(TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE)
            .order_by(SourceTable.document_id, SourceTable.page_start, SourceTable.id)
        )
        .scalars()
        .all()
    )


def table_cells(session: Session, table_id: int) -> list[SourceTableCell]:
    return list(
        session.execute(
            select(SourceTableCell)
            .where(SourceTableCell.table_id == table_id)
            .order_by(SourceTableCell.row_index, SourceTableCell.col_index)
        )
        .scalars()
        .all()
    )


def undetermined_counts(session: Session, zones: list[str]) -> dict[str, int]:
    """Uses a zone's matrix column cannot resolve, per zone.

    This is the number the ticket asks to see move: it is exactly what
    ``get_zone_profile`` reports under ``uses.undetermined`` before the prose
    fallback runs.
    """
    tables = permission_matrix_tables(session)
    counts: dict[str, int] = {}
    for zone in zones:
        seen: set[str] = set()
        undetermined: set[str] = set()
        for table in tables:
            rows = enumerate_permission_column(session, table_id=table.id, zone=zone)
            if rows is None:
                continue
            for row in rows:
                label = row["use_label"]
                if label in seen:
                    continue
                seen.add(label)
                if row["permission"] == UNKNOWN:
                    undetermined.add(label)
        counts[zone] = len(undetermined)
    return counts


def backfill(session: Session, *, dry_run: bool = False) -> GridBackfillStats:
    """Densify every permission matrix in the corpus.

    Caller owns the transaction. In dry-run mode the audit runs and is counted
    but no cell is created.
    """
    stats = GridBackfillStats()
    for table in permission_matrix_tables(session):
        stats.tables_scanned += 1
        cells = table_cells(session, table.id)
        audit = densify_permission_matrix(session, table, cells, apply=not dry_run)
        if audit.table_reason is not None:
            stats.tables_refused += 1
        stats.cells_filled += len(audit.gaps)
        stats.cells_refused += len(audit.refused)
        stats.reasons.update(audit.reason_counts())
        if not dry_run and audit.gaps:
            session.flush()
        logger.info(
            "table=%s doc=%s page=%s filled=%d refused=%d %s",
            table.id,
            table.document_id,
            table.page_start,
            len(audit.gaps),
            len(audit.refused),
            audit.table_reason or "",
        )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Audit and report, but create no cells.",
    )
    parser.add_argument(
        "--zone",
        action="append",
        default=None,
        metavar="ZONE",
        help=(
            "Report the count of undetermined uses for this zone before and "
            "after the run. Repeatable."
        ),
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

    if not args.dry_run:
        # ABS-499: no unfenced write to the dev corpus.
        fence_or_abort("backfill-permission-grid", database_url=args.database_url)

    zones = args.zone or []
    started = time.monotonic()
    with session_scope(args.database_url) as session:
        before = undetermined_counts(session, zones) if zones else {}
        stats = backfill(session, dry_run=args.dry_run)
        after = undetermined_counts(session, zones) if zones else {}
    elapsed_s = time.monotonic() - started

    print(
        f"backfill_permission_grid: {stats.summary_line()} "
        f"elapsed_s={elapsed_s:.2f} dry_run={args.dry_run}"
    )
    for zone in zones:
        print(
            f"  undetermined[{zone}]: before={before.get(zone)} "
            f"after={after.get(zone)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
