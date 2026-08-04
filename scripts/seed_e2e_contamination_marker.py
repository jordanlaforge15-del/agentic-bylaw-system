#!/usr/bin/env python
"""Insert (or delete) one synthetic e2e-marker document row (ABS-432).

Playwright fixture for ``web/e2e/functional/corpus-coherence-audit.spec.ts``:
creates a minimal ``document`` row whose ``file_hash`` starts with ``e2e-``
and whose ``parser_version`` is ``e2e-seed`` — the exact fingerprints the
ABS-432 contamination tripwire sweeps for — so the spec can assert the sweep
names the row, then delete it and assert it is gone.

The row is retrieval-DISABLED and owns no fragments, so it is invisible to
every other spec's retrieval scope. ``file_hash`` must be unique per caller
(the spec embeds its Playwright worker index) because four viewport projects
run the suite in parallel against one database.

Usage::

    DATABASE_URL=... python scripts/seed_e2e_contamination_marker.py --file-hash e2e-tripwire-w0
    DATABASE_URL=... python scripts/seed_e2e_contamination_marker.py --file-hash e2e-tripwire-w0 --delete
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
# (An explicit DATABASE_URL in the environment still wins — that is how
# the ABS-432 scratch-DB verification drives this script.)
import e2e_db_default  # noqa: F401  isort: skip

import argparse
import sys

from sqlalchemy import select, text as sa_text

from layer1.db.base import Document, utcnow
from layer1.db.session import session_scope

MUNICIPALITY = "E2E Contamination Tripwire Municipality"
BYLAW_NAME = "Contamination Tripwire Synthetic Bylaw"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file-hash",
        required=True,
        help="Unique file_hash for the synthetic row; must start with 'e2e-' (max 64 chars).",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the synthetic row instead of creating it.",
    )
    args = parser.parse_args(argv)

    if not args.file_hash.startswith("e2e-") or len(args.file_hash) > 64:
        print("error: --file-hash must start with 'e2e-' and be at most 64 chars", file=sys.stderr)
        return 1

    with session_scope() as session:
        if session.bind.dialect.name == "postgresql":
            # ABS-207 convention: serialise spec-callable seeds behind a
            # transaction-scoped advisory lock. Each caller passes a unique
            # --file-hash so races are already impossible by construction,
            # but the lock keeps this script within the fleet-wide pattern
            # tests/test_e2e_seed_concurrency.py enforces. Constant is
            # arbitrary but stable/unique among seeds ("abs432-tripwire").
            session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604604320))

        existing = session.execute(
            select(Document).where(Document.file_hash == args.file_hash)
        ).scalars().first()

        if args.delete:
            if existing is None:
                print(f"marker document {args.file_hash!r} already absent")
                return 0
            session.delete(existing)
            print(f"deleted marker document id={existing.id} file_hash={args.file_hash!r}")
            return 0

        if existing is not None:
            print(f"marker document already present id={existing.id} file_hash={args.file_hash!r}")
            return 0

        document = Document(
            municipality=MUNICIPALITY,
            bylaw_name=BYLAW_NAME,
            source_path="e2e/contamination_tripwire.pdf",
            file_hash=args.file_hash,
            mime_type="application/pdf",
            page_count=1,
            parser_version="e2e-seed",
            retrieval_enabled=False,
            ingestion_timestamp=utcnow(),
        )
        session.add(document)
        session.flush()
        print(f"created marker document id={document.id} file_hash={args.file_hash!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
