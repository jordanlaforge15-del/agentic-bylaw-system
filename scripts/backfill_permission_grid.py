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

Running it is no longer the only way a corpus gets repaired: the
``0027_permission_grid_backfill`` Alembic migration calls the identical
:func:`layer1.semantic.permission_grid.densify_corpus` on every ``alembic
upgrade``, so an environment converges on deploy whether or not an operator
remembers this script (ABS-526 — production spent a release cycle ragged
because only dev had ever been backfilled by hand). This script remains the
tool for a dry run, for a per-zone blast radius, and for repairing a corpus
ingested after the migration already ran.

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
    # Rehearse — writes, measures the undetermined-use blast radius, rolls back:
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

from sqlalchemy.orm import Session

from layer1.db.migration_fence import fence_or_abort
from layer1.db.session import session_scope
from layer1.semantic.enrichment import enumerate_permission_column
from layer1.semantic.permission_grid import (
    CorpusGridFillStats,
    densify_corpus,
    permission_matrix_tables,
    table_cells,
)
from layer1.semantic.permission_markers import UNKNOWN

logger = logging.getLogger("backfill_permission_grid")

# The corpus walk lives in the package so the Alembic migration can call it too
# (ABS-526). Re-exported here because the guard script and the tests address it
# through this module.
__all__ = [
    "GridBackfillStats",
    "backfill",
    "permission_matrix_tables",
    "table_cells",
    "undetermined_counts",
]

GridBackfillStats = CorpusGridFillStats


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
    return densify_corpus(session, apply=not dry_run)


def rehearse(
    session: Session, zones: list[str]
) -> tuple[GridBackfillStats, dict[str, int], dict[str, int]]:
    """Apply the backfill, measure it, then undo it.

    ``--dry-run`` used to pass ``apply=False``, which left the per-zone
    before/after line trivially identical — the one number an operator reads to
    decide whether to run for real proved nothing (ABS-526). So the rehearsal
    writes the cells, reads the blast radius off the repaired corpus, and rolls
    the whole transaction back. Callers must not commit afterwards; the
    surrounding :func:`session_scope` commits an empty transaction.
    """
    before = undetermined_counts(session, zones) if zones else {}
    stats = backfill(session, dry_run=False)
    after = undetermined_counts(session, zones) if zones else {}
    session.rollback()
    return stats, before, after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Rehearse: write the cells, report the real blast radius, then "
            "roll back. Leaves the corpus untouched."
        ),
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
        if args.dry_run:
            stats, before, after = rehearse(session, zones)
        else:
            before = undetermined_counts(session, zones) if zones else {}
            stats = backfill(session, dry_run=False)
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
