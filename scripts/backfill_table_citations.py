"""Backfill table-caption citations for an already-ingested corpus (ABS-409).

Runs :func:`layer1.pipeline.table_captions.link_table_captions` against an
existing database — giving "Table 1A:"-style caption fragments a citation
label/path and claiming their ``source_table`` rows (``parent_fragment_id`` +
``caption``) — then re-runs document-scoped semantic enrichment so the table
classifier re-profiles the matrices with captions in view (the caption phrase
"permitted uses by zone" / "parking" is the classifier's strongest signal;
see ``_classify_table``). The re-enrichment is what repairs the misprofiled
matrix continuation pages and rebuilds their axis bindings.

Usage:

    # Review what would change (no writes, prints the caption->tables mapping)
    python scripts/backfill_table_citations.py \
        --document-id 4 --profile halifax --dry-run

    # Apply (writes a revert sidecar next to the CWD by default)
    python scripts/backfill_table_citations.py --document-id 4 --profile halifax

    # Roll back a previous apply
    python scripts/backfill_table_citations.py --revert <sidecar.json>

``--profile`` names the bylaw's parsing profile; its ``table_caption_re``
declares the caption convention (a profile without one makes this a no-op).
``--document-id`` scopes the run to one document — always pass it on mixed
corpora.

Revert restores the caption/citation columns from the sidecar; enrichment is
rebuild-idempotent, so a full rollback is ``--revert`` followed by re-running
enrichment (``scripts/backfill_table_profiles.py``) if the enrichment step had
already run.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from layer1.db.session import session_scope
from layer1.pipeline.table_captions import (
    LinkStats,
    link_table_captions,
    revert_table_captions,
)
from layer1.profiles import ParsingProfile, get_parsing_profile
from layer1.semantic.enrichment import enrich_document_semantics

logger = logging.getLogger("backfill_table_citations")


def backfill(
    session: Session,
    *,
    document_id: int | None,
    profile: ParsingProfile | str,
    dry_run: bool = False,
    enrich: bool = True,
) -> LinkStats:
    """Link captions, then re-enrich the touched document(s).

    Caller owns the transaction (pass a ``session_scope()`` session for real
    runs, a fixture session in tests). Dry runs never write and never enrich.
    """
    resolved = get_parsing_profile(profile)
    stats = link_table_captions(
        session, document_id=document_id, profile=resolved, dry_run=dry_run
    )
    logger.info(stats.summary_line())
    for entry in stats.mapping:
        logger.info(
            "  doc %s p%s %-12s %-16s -> tables %s (%s)",
            entry["document_id"],
            entry["page"],
            entry["label"],
            entry["citation_path"] or "-",
            entry["table_ids"] or "-",
            entry["action"],
        )

    if enrich and not dry_run and stats.writes:
        touched_docs = sorted({entry["document_id"] for entry in stats.mapping})
        for doc_id in touched_docs:
            logger.info("re-enriching document %s (caption-aware reclassification)", doc_id)
            report = enrich_document_semantics(
                session, document_id=doc_id, profile=resolved
            )
            logger.info(
                "  document %s: %d table profile(s) rebuilt", doc_id, report.table_profiles
            )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL.")
    parser.add_argument("--document-id", type=int, default=None, help="Scope to one document.")
    parser.add_argument(
        "--profile",
        default=None,
        help="Parsing profile declaring the caption convention (e.g. 'halifax').",
    )
    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Skip the post-link document re-enrichment step.",
    )
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
            restored = revert_table_captions(session, payload["touched"])
        print(f"reverted {restored} row(s) from {args.revert} elapsed_s={time.monotonic() - started:.1f}")
        return 0

    if not args.profile:
        parser.error("--profile is required (or use --revert)")

    with session_scope(args.database_url) as session:
        stats = backfill(
            session,
            document_id=args.document_id,
            profile=args.profile,
            dry_run=args.dry_run,
            enrich=not args.skip_enrich,
        )
        if not args.dry_run and (stats.touched["fragments"] or stats.touched["tables"]):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            sidecar = Path(args.sidecar_dir) / f"table_citations_sidecar_{stamp}.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "created": stamp,
                        "document_id": args.document_id,
                        "profile": args.profile,
                        "touched": stats.touched,
                    },
                    indent=1,
                )
            )
            logger.info("revert sidecar written to %s", sidecar)

    print(f"{stats.summary_line()} elapsed_s={time.monotonic() - started:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
