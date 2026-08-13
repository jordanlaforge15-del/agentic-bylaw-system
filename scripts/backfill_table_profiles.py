"""Backfill ``table_semantic_profile`` + ``table_axis_binding`` for permission matrices.

ABS-278 — Phase 2. Phase-0 inspection found ``table_semantic_profile`` coverage
is inconsistent: doc 3's permission matrix (table 1010) carries a
``profile_type=permission_matrix`` profile with bound zone/use axes, but doc 4's
identical table (1057) has *no profile row at all*. A permission marker (Phase 1)
is useless unless you also know which zone a column means and which use a row
means — that binding is what this backfill creates.

What it does
------------
Finds every document that owns at least one table with **no**
``table_semantic_profile`` (i.e. enrichment never ran on it), then runs full
semantic enrichment for each such document. Enrichment is idempotent — it clears
and rebuilds the document's semantic layer — so it both creates the missing
profiles/bindings (including any ``permission_matrix`` the structural classifier
detects) and refreshes stale ones (including the ABS-278 header-bleed
correction). Documents whose tables are all profiled are skipped unless
``--force`` is given.

ABS-281: candidate detection keys off profile *coverage*, not table captions.
The earlier version searched for permission-matrix tables by the caption
pattern ``Table 1%Permitted uses by zone%`` — but the real corpus stores no
captions, so it found zero candidates and the coverage gap (doc 4, doc 2 with
0 profiles) never closed. Detecting "this table has no profile yet" is both
caption-independent and the actual condition we want to repair.

Usage
-----
    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1 \
        .venv/bin/python scripts/backfill_table_profiles.py

    # Re-enrich every document with a permission matrix, even already-profiled:
    .venv/bin/python scripts/backfill_table_profiles.py --force

    # Report what would run, don't write:
    .venv/bin/python scripts/backfill_table_profiles.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import SourceTable, TableSemanticProfile
from layer1.db.migration_fence import fence_or_abort
from layer1.db.session import session_scope
from layer1.semantic.enrichment import enrich_document_semantics

logger = logging.getLogger("backfill_table_profiles")


@dataclass
class BackfillStats:
    documents_scanned: int = 0
    documents_enriched: int = 0
    profiles_created: int = 0
    axis_bindings_created: int = 0

    def summary_line(self) -> str:
        return (
            f"documents_scanned={self.documents_scanned} "
            f"documents_enriched={self.documents_enriched} "
            f"profiles={self.profiles_created} "
            f"axis_bindings={self.axis_bindings_created}"
        )


def _documents_needing_profiles(session: Session, *, force: bool) -> list[int]:
    """Document ids that own at least one table lacking a semantic profile.

    With ``force`` every document that owns any table is returned, profiled or
    not. Detection is caption-independent (ABS-281): a table is "needs
    profiling" iff no ``table_semantic_profile`` row references it.
    """
    tables = (
        session.execute(
            select(SourceTable.id, SourceTable.document_id)
        )
        .all()
    )
    profiled_table_ids = {
        row[0]
        for row in session.execute(select(TableSemanticProfile.table_id)).all()
    }
    needing: list[int] = []
    seen: set[int] = set()
    for table_id, document_id in tables:
        if document_id in seen:
            continue
        if force or table_id not in profiled_table_ids:
            # A single unprofiled table is enough to re-enrich the whole
            # document (enrichment is document-scoped and idempotent).
            needing.append(document_id)
            seen.add(document_id)
    return needing


def backfill(session: Session, *, dry_run: bool = False, force: bool = False) -> BackfillStats:
    """Profile + bind axes for permission matrices that lack a profile.

    Caller owns the transaction. In dry-run mode the candidate documents are
    counted and logged but enrichment is not run.
    """
    stats = BackfillStats()
    document_ids = _documents_needing_profiles(session, force=force)
    stats.documents_scanned = len(document_ids)
    logger.info("%d document(s) need permission-matrix profiling", len(document_ids))

    for document_id in document_ids:
        if dry_run:
            logger.info("[dry-run] would enrich document %d", document_id)
            continue
        report = enrich_document_semantics(session, document_id=document_id)
        stats.documents_enriched += 1
        stats.profiles_created += report.table_profiles
        stats.axis_bindings_created += report.axis_bindings
        logger.info(
            "enriched document %d: profiles=%d axis_bindings=%d warnings=%d",
            document_id,
            report.table_profiles,
            report.axis_bindings,
            len(report.warnings),
        )

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report which documents would be enriched, but don't write.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-enrich every document with a permission matrix, even profiled ones.",
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
        fence_or_abort("backfill-table-profiles", database_url=args.database_url)

    started = time.monotonic()
    with session_scope(args.database_url) as session:
        stats = backfill(session, dry_run=args.dry_run, force=args.force)
    elapsed_s = time.monotonic() - started

    print(
        f"backfill_table_profiles: {stats.summary_line()} "
        f"elapsed_s={elapsed_s:.2f} dry_run={args.dry_run} force={args.force}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
