#!/usr/bin/env python3
"""Census the permission-matrix cells that carry more than one footnote marker.

ABS-523. Enrichment used to keep the first circled marker in a cell and discard
the rest, so a cell reading ``⑮ ㉒`` reported only the Halifax Grain Elevator
carve-out and dropped the footnote authorising more than 8 units in ER-3. The
ticket's blast-radius claim — 16 cells, and the whole ER
residential-intensification family — comes from this script, so a later reader
can re-derive it rather than take a number on faith.

It reads only; it writes nothing. Run it against the dev corpus::

    python scripts/audit_permission_footnotes.py
    python scripts/audit_permission_footnotes.py --json
    python scripts/audit_permission_footnotes.py --database-url postgresql+psycopg://…

Exit code is 0 whatever it finds: this is a measurement, not a gate. The gate is
``tests/test_multi_footnote_permission_cells.py``, which pins the census as
data so a classifier that starts dropping ordinals again fails ``make test``
without needing a database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from sqlalchemy import select

from layer1.db.base import Document, SourceTable, SourceTableCell
from layer1.db.session import session_scope
from layer1.semantic.permission_markers import (
    PERMISSION_MATRIX_PROFILE,
    classify_permission_marker,
)


def _cell_grid(session, table_id: int) -> dict[tuple[int, int], SourceTableCell]:
    return {
        (cell.row_index, cell.col_index): cell
        for cell in session.execute(
            select(SourceTableCell).where(SourceTableCell.table_id == table_id)
        )
        .scalars()
        .all()
    }


def _label(grid: dict[tuple[int, int], SourceTableCell], row: int, col: int) -> tuple[str, str]:
    """(use label, zone label) for a cell, read off the grid's own headers.

    ``row_header_path`` / ``col_header_path`` are null throughout the Regional
    Centre corpus, so the headers are read positionally: column 0 carries the
    use, row 0 the zone. That is the same convention ``annotate_value_cells``
    uses to decide which cells are value cells at all.
    """
    use = grid.get((row, 0))
    zone = grid.get((0, col))
    return (
        (use.text or "").strip() if use is not None else "",
        (zone.text or "").strip() if zone is not None else "",
    )


def audit(session) -> dict:
    from layer1.db.base import TableSemanticProfile

    tables = (
        session.execute(
            select(SourceTable)
            .join(TableSemanticProfile, TableSemanticProfile.table_id == SourceTable.id)
            .where(TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE)
            .order_by(SourceTable.document_id, SourceTable.page_start, SourceTable.id)
        )
        .scalars()
        .all()
    )

    findings: list[dict] = []
    conditional_cells = 0
    dropped_ordinals: Counter[int] = Counter()
    for table in tables:
        grid = _cell_grid(session, table.id)
        document = session.get(Document, table.document_id)
        for (row, col), cell in sorted(grid.items()):
            if row == 0 or col == 0:
                continue
            result = classify_permission_marker(cell.text)
            ordinals = result.get("footnotes") or []
            if ordinals:
                conditional_cells += 1
            if len(ordinals) < 2:
                continue
            use, zone = _label(grid, row, col)
            for ordinal in ordinals[1:]:
                dropped_ordinals[ordinal] += 1
            findings.append(
                {
                    "document_id": table.document_id,
                    "bylaw_name": document.bylaw_name if document else None,
                    "table_id": table.id,
                    "page": table.page_start,
                    "row_index": row,
                    "col_index": col,
                    "use": use,
                    "zone": zone,
                    "markers": ordinals,
                    "kept_before_abs523": ordinals[0],
                    "dropped_before_abs523": ordinals[1:],
                }
            )

    return {
        "permission_matrix_tables": len(tables),
        "conditional_cells": conditional_cells,
        "multi_marker_cells": len(findings),
        "ordinals_dropped_before_abs523": dict(sorted(dropped_ordinals.items())),
        "cells": findings,
    }


def _render(report: dict) -> str:
    lines = [
        f"permission-matrix tables:        {report['permission_matrix_tables']}",
        f"conditional cells:               {report['conditional_cells']}",
        f"cells with >1 marker:            {report['multi_marker_cells']}",
        "",
        "Each of these stated one condition to the reader and silently dropped",
        "the rest. The dropped ordinal is usually the one that grants something.",
        "",
    ]
    for cell in report["cells"]:
        markers = " ".join(f"{ordinal}" for ordinal in cell["markers"])
        lines.append(
            f"  doc {cell['document_id']} table {cell['table_id']} p{cell['page']} "
            f"[{cell['row_index']},{cell['col_index']}] "
            f"{cell['zone'] or '?'} / {cell['use'] or '?'}: markers=[{markers}] "
            f"kept={cell['kept_before_abs523']} "
            f"DROPPED={cell['dropped_before_abs523']}"
        )
    counts = report["ordinals_dropped_before_abs523"]
    if counts:
        lines.append("")
        lines.append("dropped ordinals, by how many cells lost them:")
        for ordinal, count in counts.items():
            lines.append(f"  ㉒-style ordinal {ordinal}: {count} cell(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="DSN of the corpus to audit; defaults to $DATABASE_URL.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    with session_scope(args.database_url) as session:
        report = audit(session)

    print(json.dumps(report, indent=2) if args.json else _render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
