#!/usr/bin/env python
"""Seed (or heal / delete) a normalized-name collision pair (ABS-434).

Playwright fixture for ``web/e2e/functional/corpus-coherence-audit.spec.ts``:
creates TWO retrieval-ENABLED ``document`` rows whose ``bylaw_name`` differ
only by casing ("...By-law..." vs "...By-Law...") under one synthetic
municipality — the exact doc-15/38 double-enable shape the ABS-434
enabled-name-collision audit exists to catch — so the spec can assert the
audit reports a violation naming both ids, heal it by disabling one, and
assert the report goes green.

The pair owns no fragments, so it is invisible to every other spec's
retrieval searches; the rows carry the standard e2e fingerprints
(``parser_version='e2e-seed'``, ``file_hash 'e2e-%'``). ``--slug`` must be
unique per caller (the spec embeds its Playwright worker index) so parallel
runs never share a municipality/group.

Usage::

    DATABASE_URL=... python scripts/seed_e2e_name_collision.py --slug w0abc
    DATABASE_URL=... python scripts/seed_e2e_name_collision.py --slug w0abc --disable-second
    DATABASE_URL=... python scripts/seed_e2e_name_collision.py --slug w0abc --delete
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
# (An explicit DATABASE_URL in the environment still wins — that is how
# the ABS-434 scratch-DB verification drives this script.)
import e2e_db_default  # noqa: F401  isort: skip

import argparse
import json
import re
import sys

from sqlalchemy import select, text as sa_text

from layer1.db.base import Document, utcnow
from layer1.db.session import session_scope

# Two spellings of one bylaw modulo casing — normalized-equal, literal-unequal.
BYLAW_NAME_A = "Name Collision Tripwire By-law (ABS-434 E2E)"
BYLAW_NAME_B = "Name Collision Tripwire By-Law (ABS-434 E2E)"


def _municipality(slug: str) -> str:
    return f"E2E Name Collision Municipality {slug}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--slug",
        required=True,
        help="Unique per-caller slug (letters/digits/hyphen, max 24 chars); "
        "namespaces the municipality and file hashes.",
    )
    parser.add_argument(
        "--disable-second",
        action="store_true",
        help="Heal the collision: set the second document retrieval_enabled=False.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete both synthetic rows instead of creating them.",
    )
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9-]{1,24}", args.slug):
        print("error: --slug must be 1-24 chars of [A-Za-z0-9-]", file=sys.stderr)
        return 1

    municipality = _municipality(args.slug)
    hashes = {
        "a": f"e2e-namecol-{args.slug}-a",
        "b": f"e2e-namecol-{args.slug}-b",
    }

    with session_scope() as session:
        if session.bind.dialect.name == "postgresql":
            # ABS-207 convention: serialise spec-callable seeds behind a
            # transaction-scoped advisory lock (unique constant per seed;
            # "abs434-namecol"). --slug uniqueness already prevents races,
            # the lock keeps the fleet-wide pattern intact.
            session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604604340))

        existing = {
            doc.file_hash: doc
            for doc in session.execute(
                select(Document).where(Document.file_hash.in_(list(hashes.values())))
            ).scalars()
        }

        if args.delete:
            for doc in existing.values():
                session.delete(doc)
            print(f"deleted {len(existing)} collision document(s) for slug {args.slug!r}")
            return 0

        if args.disable_second:
            doc_b = existing.get(hashes["b"])
            if doc_b is None:
                print(f"error: no second document for slug {args.slug!r}", file=sys.stderr)
                return 1
            doc_b.retrieval_enabled = False
            print(f"disabled document id={doc_b.id} ({doc_b.bylaw_name!r})")
            return 0

        created: dict[str, int] = {}
        for key, bylaw_name in (("a", BYLAW_NAME_A), ("b", BYLAW_NAME_B)):
            doc = existing.get(hashes[key])
            if doc is None:
                doc = Document(
                    municipality=municipality,
                    bylaw_name=bylaw_name,
                    source_path=f"e2e/name_collision_{args.slug}_{key}.pdf",
                    file_hash=hashes[key],
                    mime_type="application/pdf",
                    page_count=1,
                    parser_version="e2e-seed",
                    retrieval_enabled=True,
                    ingestion_timestamp=utcnow(),
                )
                session.add(doc)
                session.flush()
            else:
                doc.retrieval_enabled = True
            created[key] = doc.id
        # Machine-readable line the spec parses for the two ids.
        print("SEEDED " + json.dumps({"document_ids": [created["a"], created["b"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
