"""Return one e2e document to the state production was in (ABS-526).

The ABS-520 defect cannot be reproduced by seeding alone: enrichment densifies
a permission matrix as it classifies it, so any corpus this suite ingests is
already repaired by the time a spec can look at it. Production's corpus was
*not* — it was ingested and profiled before the repair existed, and no re-ingest
was ever going to touch it. That is the state the migration has to fix, and this
script is how a spec reproduces it: delete the materialized blanks, keep every
cell the parser actually stored, leave the semantic profile and axis bindings
alone.

Scoped to one document by file hash on purpose. Playwright runs spec files in
parallel, and the corpus-wide reversal
(:func:`layer1.semantic.permission_grid.strip_corpus_grid_fills`, which the
migration's own downgrade uses) would strip a sibling spec's fixture out from
under it.

Only cells labelled ``metadata_json.grid_fill='absent_cell'`` are removed —
nothing else writes that key, so this cannot take extracted content.

Usage::

    python scripts/e2e_strip_permission_grid_fills.py --file-hash <hash>
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import argparse
import sys

from sqlalchemy import select

from layer1.db.base import Document, SourceTable, SourceTableCell
from layer1.db.session import session_scope
from layer1.semantic.permission_grid import is_grid_filled


def strip_document_grid_fills(session, file_hash: str) -> int:
    document = (
        session.execute(select(Document).where(Document.file_hash == file_hash))
        .scalars()
        .first()
    )
    if document is None:
        raise SystemExit(f"no document with file_hash={file_hash!r}")

    table_ids = list(
        session.execute(
            select(SourceTable.id).where(SourceTable.document_id == document.id)
        )
        .scalars()
        .all()
    )
    if not table_ids:
        return 0

    removed = 0
    for cell in (
        session.execute(
            select(SourceTableCell).where(SourceTableCell.table_id.in_(table_ids))
        )
        .scalars()
        .all()
    ):
        if is_grid_filled(cell):
            session.delete(cell)
            removed += 1
    session.flush()
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file-hash", required=True)
    args = parser.parse_args()

    with session_scope() as session:
        removed = strip_document_grid_fills(session, args.file_hash)
    print(f"e2e_strip_permission_grid_fills: removed={removed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
