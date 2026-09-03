"""Repath an already-ingested corpus onto the ABS-488 citation shape.

Clause paths gain the container that scopes them, Part headings gain their
chapter, and the labelled provisions the collision rule had blanked become
citable again. On the dev corpus this takes document 4's citable-but-missing
count from 720 to 0.

The parser fix only helps documents ingested after it; this is how a corpus
already in a database gets the same shape -- on dev, and on production, without
a 457-page re-ingest that would churn every fragment id and orphan every
citation the Layer 2 answer tables have recorded. Rationale and the production
runbook: ``docs/data-gaps/citation-path-repath.md``.

Usage:

    # Review what would change (no writes)
    python scripts/repath_citation_paths.py --document-id 4 --dry-run

    # Apply (writes a revert sidecar into the CWD by default)
    python scripts/repath_citation_paths.py --document-id 4

    # Roll back a previous apply
    python scripts/repath_citation_paths.py --revert <sidecar.json>

Omit ``--document-id`` to sweep every document in the database.

Embeddings are untouched: ``layer2.pipeline.service`` embeds fragment text
alone, and no text changes here.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from layer1.db.migration_fence import fence_or_abort
from layer1.db.session import session_scope
from layer1.pipeline.corpus_repath import (
    DocumentRepath,
    RepathStats,
    repath_corpus,
    revert_corpus_repath,
)

logger = logging.getLogger("repath_citation_paths")


def _report(document: DocumentRepath, *, dry_run: bool, examples: int) -> None:
    verb = "would rewrite" if dry_run else "rewrote"
    logger.info(
        "doc %s: %s fragments, %s %s row(s); citable-but-missing %s -> %s",
        document.document_id,
        document.fragments,
        verb,
        len(document.changes),
        document.citable_before,
        document.citable_after,
    )
    for change in document.changes[:examples]:
        logger.info("    %s: %r -> %r", change.fragment_id, change.old_path, change.new_path)
    remaining = len(document.changes) - examples
    if remaining > 0:
        logger.info("    ... and %s more", remaining)
    if document.collided_paths:
        logger.warning(
            "    %s path(s) still collide after the repath and stay blanked:",
            len(document.collided_paths),
        )
        for path, count in document.collided_paths.most_common(examples):
            logger.warning("      %sx %s", count, path)


def _summary(stats: RepathStats) -> str:
    return stats.summary_line()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL.")
    parser.add_argument("--document-id", type=int, default=None, help="Scope to one document.")
    parser.add_argument("--examples", type=int, default=10, help="Rewrites to print per document.")
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
        # ABS-499: a revert is a corpus-wide write like any other.
        fence_or_abort("repath-citation-paths-revert", database_url=args.database_url, log=logger)
        payload = json.loads(Path(args.revert).read_text())
        with session_scope(args.database_url) as session:
            restored = revert_corpus_repath(session, payload["fragments"])
        print(
            f"reverted {restored} row(s) from {args.revert} "
            f"elapsed_s={time.monotonic() - started:.1f}"
        )
        return 0

    if not args.dry_run:
        # ABS-499: no corpus-wide repath without a labelled pre-change snapshot.
        fence_or_abort("repath-citation-paths", database_url=args.database_url, log=logger)

    with session_scope(args.database_url) as session:
        stats = repath_corpus(session, document_id=args.document_id, dry_run=args.dry_run)
        for document in stats.documents:
            _report(document, dry_run=args.dry_run, examples=args.examples)
        if not args.dry_run and stats.revert_payload:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            sidecar = Path(args.sidecar_dir) / f"citation_repath_sidecar_{stamp}.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "created": stamp,
                        "document_id": args.document_id,
                        "fragments": stats.revert_payload,
                    },
                    indent=1,
                )
            )
            logger.info("revert sidecar written to %s", sidecar)

    print(f"{_summary(stats)} elapsed_s={time.monotonic() - started:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
