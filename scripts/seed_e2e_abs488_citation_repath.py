#!/usr/bin/env python
"""Seed the citation-repath probe corpus (ABS-488).

Fixture for ``web/e2e/functional/abs488-citation-repath.spec.ts``.

Like the ABS-480 seed it does not hand-write ``SourceFragment`` rows — it runs
the real ``reconstruct_hierarchy`` over synthetic page blocks and persists
whatever the parser produces. The behaviour under test is what the *parser*
decides a clause's address is, so a seed that wrote the answer down would pin
nothing.

The blocks reproduce the two shapes that made 720 labelled provisions of
document 4 uncitable:

*Two clause groups under one section.* Section 9 has clauses (a) and (b); an
unnumbered stem then opens a second group with its own (a) and (b). Before
ABS-488 all four computed ``Part I > 9 > [Development Permit Exemptions] > (x)``
from the sticky heading above them, collided, and had their paths blanked. Now
the first group cites the section and the second cites the stem that scopes it.

*Part chapters.* Three Part headings, two of which name a chapter. All three
used to compute the bare ``Part I``. Now each is distinct — and the section
beneath them still cites the chapter-free ``Part I``, which is what keeps the
thousands of already-stored section paths valid.

The document is its own bylaw, so the spec scopes every lookup to it by
``document_id`` and no other seeded corpus can contribute matches.

Idempotent get-or-create keyed on the document's ``file_hash``.

Usage::

    DATABASE_URL=... python scripts/seed_e2e_abs488_citation_repath.py
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
from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.pipeline.hierarchy import reconstruct_hierarchy

DOCUMENT_FILE_HASH = "e2e-abs488-citation-repath-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Citation Repath Probe Bylaw (ABS-488 E2E)"

# Arbitrary but stable/unique among the e2e seeds ("abs488-repath").
ADVISORY_LOCK_KEY = 4880488

HERITAGE_STEM = (
    "On a registered heritage property, a development permit shall be required for:"
)
SECTION_PATH = "Part I > 9"
FIRST_GROUP_A = "Part I > 9 > (a)"
FIRST_GROUP_B = "Part I > 9 > (b)"
SECOND_GROUP_A = f"Part I > 9 > [{HERITAGE_STEM.rstrip(':')}] > (a)"
SECOND_GROUP_B = f"Part I > 9 > [{HERITAGE_STEM.rstrip(':')}] > (b)"
CHAPTER_ONE_PATH = "Part I, Chapter 1"
CHAPTER_TWO_PATH = "Part I, Chapter 2"

_BLOCK_TEXTS = (
    ("Part I: Administration", BlockType.HEADING),
    ("Part I, Chapter 1: General Administration", BlockType.HEADING),
    ("Part I, Chapter 2: Development Permit", BlockType.HEADING),
    ("Development Permit Exemptions", BlockType.HEADING),
    ("9 No development permit is required for the following:", BlockType.HEADING),
    ("(a) accessory structures of 20.0 square metres of floor area or less;", BlockType.LIST_ITEM),
    ("(b) kiosks of 20.0 square metres of floor area or less.", BlockType.LIST_ITEM),
    (HERITAGE_STEM, BlockType.LIST_ITEM),
    ("(a) uncovered structures less than 0.6 metre in height;", BlockType.LIST_ITEM),
    ("(b) fences.", BlockType.LIST_ITEM),
)


def _blocks() -> list[PageBlockData]:
    return [
        PageBlockData(
            page_number=1,
            block_type=block_type,
            reading_order=order,
            raw_text=text,
            normalized_text=text,
            parser_source="e2e-seed",
        )
        for order, (text, block_type) in enumerate(_BLOCK_TEXTS)
    ]


def _get_or_create_document(session) -> Document:
    document = session.execute(
        select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
    ).scalars().first()
    if document is not None:
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/abs488_citation_repath.txt",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="text/plain",
        page_count=1,
        parser_version="e2e-seed",
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_fragments(session, document_id: int) -> int:
    existing = session.execute(
        select(SourceFragment).where(SourceFragment.document_id == document_id)
    ).scalars().first()
    if existing is not None:
        return 0
    persisted: list[SourceFragment] = []
    for data in reconstruct_hierarchy(_blocks()):
        fragment = SourceFragment(
            document_id=document_id,
            fragment_type=data.fragment_type,
            citation_label=data.citation_label,
            citation_path=data.citation_path,
            parent_fragment_id=(
                persisted[data.parent_index].id if data.parent_index is not None else None
            ),
            page_start=data.page_start,
            page_end=data.page_end,
            reading_order_start=data.reading_order_start,
            reading_order_end=data.reading_order_end,
            text=data.text,
            parse_status=data.parse_status,
            confidence=data.confidence,
            source_block_ids_json=[],
            metadata_json=data.metadata,
        )
        session.add(fragment)
        session.flush()
        persisted.append(fragment)
    return len(persisted)


def main() -> int:
    with session_scope() as session:
        if session.bind.dialect.name == "postgresql":
            # ABS-207: serialise against the other Playwright viewport
            # workers, which all run this spec's beforeAll concurrently.
            session.execute(
                sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=ADVISORY_LOCK_KEY)
            )
        document = _get_or_create_document(session)
        created = _ensure_fragments(session, document.id)
        session.flush()
        payload = {
            "document_id": document.id,
            "bylaw_name": DOCUMENT_BYLAW_NAME,
            "fragments_created": created,
            "first_group": [FIRST_GROUP_A, FIRST_GROUP_B],
            "second_group": [SECOND_GROUP_A, SECOND_GROUP_B],
            "chapters": [CHAPTER_ONE_PATH, CHAPTER_TWO_PATH],
        }
    print(f"seed_e2e_abs488_citation_repath: document={payload['document_id']}")
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
