#!/usr/bin/env python3
"""ABS-520: guard the Table 1A / Table 1B permission-matrix cell grid.

The defect this guards against is silent by construction. The PDF parser drops
a cell that holds no glyph, so a by-law's "blank means not permitted" arrives as
a *missing* cell; retrieval reports ``unknown``; ``get_zone_profile`` files the
use under ``undetermined``; and the user is told the permission "could not be
extracted" where the by-law says no. Nothing errors, nothing is empty, and no
existing test notices. A re-ingest with a different parser build, or a table
that was never densified, puts it straight back.

Three guards, run against the live corpus:

G1 — no fillable gap survives
    Every permission-matrix intersection whose geometry vouches for it must be
    occupied. A non-zero count means the corpus was ingested or re-parsed
    without :func:`layer1.semantic.permission_grid.densify_permission_matrix`,
    and prohibitions are being served as "undetermined". Run
    ``scripts/backfill_permission_grid.py``.

G2 — the named cells resolve
    A short list of cells whose answer is attested outside this codebase. The
    anchor is ABS-520's own case: Table 1B, ER-2 column, Townhouse dwelling row
    — the by-law prohibits it, and the golden case TC-026 grades an advisor
    that says "undetermined" as a failure.

G3 — the refused residue is reported, not hidden
    Intersections the geometry would not vouch for stay missing on purpose (a
    dropped row, a skewed grid). They are printed with their reason so the
    remaining extraction debt is on the record rather than mistaken for
    coverage. G3 never fails the run; it is the blast-radius report.

Usage::

    python scripts/verify_permission_grid_integrity.py
    python scripts/verify_permission_grid_integrity.py --zone ER-2 --zone HR-1

Exits non-zero when G1 or G2 fails. Exits 0 with a SKIP when no permission
matrix is present (CI, and any e2e worktree without the Halifax ingest).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _path in (REPO_ROOT / "src", REPO_ROOT / "mcp", REPO_ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy.orm import Session  # noqa: E402

from backfill_permission_grid import (  # noqa: E402
    permission_matrix_tables,
    table_cells,
    undetermined_counts,
)
from layer1.db.session import session_scope  # noqa: E402
from layer1.semantic.enrichment import resolve_permission_cell  # noqa: E402
from layer1.semantic.permission_grid import audit_permission_grid  # noqa: E402


@dataclass(frozen=True)
class NamedCell:
    """A (use, zone) pair whose permission is attested outside this codebase."""

    use: str
    zone: str
    expected: str
    source: str


# Keep this list short and attested. Every entry needs a citation a reviewer can
# check against the by-law, not a value copied out of the database.
NAMED_CELLS: tuple[NamedCell, ...] = (
    NamedCell(
        use="Townhouse dwelling use",
        zone="ER-2",
        expected="not_permitted",
        source="Table 1B (golden case TC-026, founder-attested)",
    ),
    NamedCell(
        use="Single-unit dwelling use",
        zone="CH-1",
        expected="permitted",
        source="Table 1B, page 48 — solid ● in the CH-1 column",
    ),
    NamedCell(
        use="Townhouse dwelling use",
        zone="ER-3",
        expected="conditional",
        source="Table 1B, page 48 — footnote ⑮ in the ER-3 column",
    ),
)


def check_fillable_gaps(session: Session) -> tuple[int, list[str], dict[str, int]]:
    """G1 + G3: remaining fillable gaps, and the refused residue by reason."""
    lines: list[str] = []
    refused_reasons: dict[str, int] = {}
    fillable = 0
    for table in permission_matrix_tables(session):
        audit = audit_permission_grid(
            table_cells(session, table.id), table_id=table.id
        )
        fillable += len(audit.gaps)
        for reason, count in audit.reason_counts().items():
            refused_reasons[reason] = refused_reasons.get(reason, 0) + count
        if audit.gaps:
            lines.append(
                f"table={table.id} doc={table.document_id} page={table.page_start}: "
                f"{len(audit.gaps)} cells the geometry vouches for are still missing "
                f"(first: {audit.gaps[:3]})"
            )
        elif audit.refused:
            lines.append(
                f"table={table.id} doc={table.document_id} page={table.page_start}: "
                f"{len(audit.refused)} intersections left unfilled on purpose "
                f"({audit.table_reason or audit.reason_counts()})"
            )
    return fillable, lines, refused_reasons


def check_named_cells(session: Session) -> list[tuple[NamedCell, str, bool]]:
    """G2: resolve each attested cell through the real matrix resolver."""
    tables = permission_matrix_tables(session)
    results: list[tuple[NamedCell, str, bool]] = []
    for named in NAMED_CELLS:
        actual = "no_matrix_addressed_the_pair"
        for table in tables:
            resolved = resolve_permission_cell(
                session, table_id=table.id, use_name=named.use, zone=named.zone
            )
            if resolved is None:
                continue
            marker = resolved.get("permission_marker")
            if marker is None:
                continue
            actual = marker
            if marker == named.expected:
                break
        results.append((named, actual, actual == named.expected))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--zone",
        action="append",
        default=None,
        metavar="ZONE",
        help="Also report the zone's undetermined-use count. Repeatable.",
    )
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    failed = False
    with session_scope(args.database_url) as session:
        if not permission_matrix_tables(session):
            print("SKIP: no permission-matrix table in this corpus.")
            return 0

        fillable, lines, refused_reasons = check_fillable_gaps(session)

        print("G1 — every intersection the geometry vouches for is materialized:")
        if fillable:
            failed = True
            print(
                f"  FAIL {fillable} blank cells are still missing from the grid. "
                "Prohibitions are being served as 'undetermined'; run "
                "scripts/backfill_permission_grid.py."
            )
        else:
            print("  PASS no fillable gap remains.")

        print("\nG2 — attested cells resolve to the permission the by-law prints:")
        for named, actual, ok in check_named_cells(session):
            status = "PASS" if ok else "FAIL"
            if not ok:
                failed = True
            print(
                f"  {status} ({named.use!r}, {named.zone}) = {actual} "
                f"(expected {named.expected}; {named.source})"
            )

        print("\nG3 — extraction debt left unfilled on purpose (advisory):")
        if refused_reasons:
            for reason, count in sorted(refused_reasons.items()):
                print(f"  {reason}: {count} rows/tables")
        else:
            print("  none.")
        for line in lines:
            print(f"  {line}")

        if args.zone:
            print("\nUndetermined uses per zone:")
            for zone, count in undetermined_counts(session, args.zone).items():
                print(f"  {zone}: {count}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
