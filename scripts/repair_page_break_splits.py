"""Repair mid-token page-break splits in an already-ingested corpus (ABS-461).

Runs :func:`layer1.pipeline.page_break_repair.repair_page_break_splits` against
an existing database: splices fragments a PDF page break cut in half on a
hyphen, deletes the phantom sections their tails were mistaken for, and rehomes
the clauses that reparented under those phantoms.

The parser guard that lands alongside this only helps documents ingested after
it. This script is how a corpus already in a database gets corrected -- on dev,
and on production, without a 457-page re-ingest that would churn every fragment
id (and every foreign key pointing at one).

Usage:

    # Review what would change (no writes)
    python scripts/repair_page_break_splits.py --document-id 4 --dry-run

    # Apply (writes a revert sidecar into the CWD by default)
    python scripts/repair_page_break_splits.py --document-id 4

    # Roll back a previous apply
    python scripts/repair_page_break_splits.py --revert <sidecar.json>

Omit ``--document-id`` to sweep every document in the database.

Fragments whose text changed have their ``fragment_embedding`` rows dropped, so
re-run the embedding pass afterwards if the corpus is embedded (dev/prod
document 4 currently is not). The script reports the count either way.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from layer1.db.session import session_scope
from layer1.pipeline.page_break_repair import (
    RepairStats,
    repair_page_break_splits,
    revert_page_break_splits,
)

logger = logging.getLogger("repair_page_break_splits")


def _report(stats: RepairStats, *, dry_run: bool) -> None:
    verb = "would join" if dry_run else "joined"
    for split in stats.splits:
        logger.info(
            "doc %s: %s fragment %s + %s",
            split.document_id,
            verb,
            split.head_id,
            split.tail_id,
        )
        logger.info("    before: ...%s", split.head_text_before[-70:])
        logger.info("    after:  ...%s", split.head_text_after[-70:])
        if split.phantom_path is None:
            logger.info("    tail was unaddressed prose; no phantom to unwind")
            continue
        if split.unresolved:
            logger.warning(
                "    phantom %r has no preceding section to graft onto — "
                "text joined, paths LEFT AS-IS for manual review",
                split.phantom_path,
            )
            continue
        logger.info(
            "    phantom %r -> %r (%d path(s))",
            split.phantom_path,
            split.replacement_prefix,
            len(split.rewrites),
        )
        for rewrite in split.rewrites:
            logger.info("      %s: %s", rewrite.fragment_id, rewrite.new_path)
        for collision in split.skipped_collisions:
            logger.warning(
                "      %s: SKIPPED, %r already exists",
                collision.fragment_id,
                collision.new_path,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL.")
    parser.add_argument("--document-id", type=int, default=None, help="Scope to one document.")
    parser.add_argument(
        "--sidecar-dir",
        default=".",
        help="Directory for the revert sidecar written by an applied run.",
    )
    parser.add_argument(
        "--revert",
        default=None,
        metavar="SIDECAR",
        help="Restore the before-values recorded in a previous apply's sidecar.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    started = time.monotonic()

    if args.revert:
        payload = json.loads(Path(args.revert).read_text())
        with session_scope(args.database_url) as session:
            restored = revert_page_break_splits(session, payload["splits"])
        print(
            f"reverted {restored} row(s) from {args.revert} "
            f"elapsed_s={time.monotonic() - started:.1f}"
        )
        return 0

    with session_scope(args.database_url) as session:
        stats = repair_page_break_splits(
            session, document_id=args.document_id, dry_run=args.dry_run
        )
        _report(stats, dry_run=args.dry_run)
        if not args.dry_run and stats.revert_payload:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            sidecar = Path(args.sidecar_dir) / f"page_break_repair_sidecar_{stamp}.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "created": stamp,
                        "document_id": args.document_id,
                        "splits": stats.revert_payload,
                    },
                    indent=1,
                )
            )
            logger.info("revert sidecar written to %s", sidecar)

    print(f"{stats.summary_line()} elapsed_s={time.monotonic() - started:.1f}")
    return 1 if stats.unresolved and not args.dry_run else 0


if __name__ == "__main__":
    raise SystemExit(main())
