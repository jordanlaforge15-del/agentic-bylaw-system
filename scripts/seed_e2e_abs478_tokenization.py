#!/usr/bin/env python
"""Seed the two-fragment zone-tokenization probe corpus (ABS-478).

Fixture for ``web/e2e/functional/abs478-zone-tokenization.spec.ts``.

The corpus is deliberately minimal and adversarial: one fragment that a
zone-scoped query must NOT match, and one it must. Before ABS-478 the
retrieval scorer split ``HR-2`` into ``hr`` + ``2`` and tested each token
with ``token in haystack``, so the lookalike fragment scored +8 on pure
substring artifacts:

* ``hr`` is a substring of "th**r**ough"  -> wrong, "hr" is not in "through"
  as a word; it matches because "through" contains the letters h-r
  consecutively ("t-h-r-o-u-g-h").
* ``2`` is a substring of "2nd".

Neither fragment mentions a setback in a way the other does, so the query
``"HR-2 setback"`` cleanly separates them:

* ``lookalike`` — contains "through" and "2nd", no zone code, no setback.
  Scores 9.0 under the old scorer (two substring hits + the parsed bonus),
  which clears ``_TEXT_CHANNEL_THRESHOLD`` and surfaces it as a match.
  Scores 1.0 (parsed bonus only) after the fix, so it drops out entirely.
* ``genuine`` — names ``HR-2`` and writes "setbacks" (plural, to also pin
  the inflectional-suffix rule that keeps "setback" matching "setbacks").

The document is its own bylaw, so the spec scopes every search to it by
``bylaw_name`` and no other seeded corpus can contribute matches.

Idempotent get-or-create keyed on the document's ``file_hash``.

Usage::

    DATABASE_URL=... python scripts/seed_e2e_abs478_tokenization.py
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import json
import sys

from sqlalchemy import select, text as sa_text

from layer1.db.base import Document, SourceFragment, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

DOCUMENT_FILE_HASH = "e2e-abs478-tokenization-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Zone Tokenization Probe Bylaw (ABS-478 E2E)"

# Arbitrary but stable/unique among the e2e seeds ("abs478-tokenization").
ADVISORY_LOCK_KEY = 4780478

LOOKALIKE_CITATION_PATH = "Part I > 1"
GENUINE_CITATION_PATH = "Part I > 2"

_FRAGMENTS = (
    {
        "citation_label": "Section 1",
        "citation_path": LOOKALIKE_CITATION_PATH,
        # "through" carries the "hr" substring; "2nd" carries the "2".
        # Nothing here is a whole-word hit for any token of "HR-2 setback".
        "text": (
            "No portion of a building shall project through the required "
            "yard above the 2nd storey."
        ),
        "reading_order_start": 1,
    },
    {
        "citation_label": "HR-2",
        "citation_path": GENUINE_CITATION_PATH,
        # "setbacks" is plural on purpose: the query says "setback".
        "text": (
            "In the HR-2 zone the minimum front and rear yard setbacks "
            "are 3.0 metres."
        ),
        "reading_order_start": 2,
    },
)


def _get_or_create_document(session) -> Document:
    document = session.execute(
        select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
    ).scalars().first()
    if document is not None:
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/abs478_tokenization.txt",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="text/plain",
        page_count=1,
        parser_version="e2e-seed",
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_fragments(session, document_id: int) -> None:
    existing = {
        fragment.citation_path
        for fragment in session.execute(
            select(SourceFragment).where(SourceFragment.document_id == document_id)
        ).scalars()
    }
    for spec in _FRAGMENTS:
        if spec["citation_path"] in existing:
            continue
        session.add(
            SourceFragment(
                document_id=document_id,
                fragment_type=FragmentType.CLAUSE,
                citation_label=spec["citation_label"],
                citation_path=spec["citation_path"],
                parent_fragment_id=None,
                page_start=1,
                page_end=1,
                reading_order_start=spec["reading_order_start"],
                reading_order_end=spec["reading_order_start"],
                text=spec["text"],
                parse_status=ParseStatus.PARSED,
                confidence=1.0,
                source_block_ids_json=[],
                metadata_json={},
            )
        )


def main() -> int:
    with session_scope() as session:
        if session.bind.dialect.name == "postgresql":
            # ABS-207: serialise against the other Playwright viewport
            # workers, which all run this spec's beforeAll concurrently.
            session.execute(
                sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=ADVISORY_LOCK_KEY)
            )
        document = _get_or_create_document(session)
        _ensure_fragments(session, document.id)
        session.flush()
        payload = {
            "document_id": document.id,
            "bylaw_name": DOCUMENT_BYLAW_NAME,
            "lookalike_citation_path": LOOKALIKE_CITATION_PATH,
            "genuine_citation_path": GENUINE_CITATION_PATH,
        }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
