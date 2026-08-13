#!/usr/bin/env python
"""Seed the citation-path-collision probe corpus (ABS-480).

Fixture for ``web/e2e/functional/abs480-citation-collision.spec.ts``.

Unlike most e2e seeds this one does not hand-write ``SourceFragment`` rows —
it runs the real ``reconstruct_hierarchy`` over five synthetic page blocks and
persists whatever the parser produces. That is the point: the behaviour under
test is what the *parser* decides about a collided provision, so a seed that
asserted the answer by writing it directly would pin nothing.

The five blocks produce:

* ``26``        section, path ``26``           — the ancestor.
* ``(g)`` x2    clauses that both derive ``26 > (g)`` — the collision. Path
                blanked, ``metadata_json.duplicate_citation_path`` recorded,
                and (ABS-480) ``parse_status`` left at ``parsed`` with the
                clause pattern's 0.85 confidence intact. Before the fix these
                came out ``uncertain`` at 0.6.
* ``(h)``       clause, path ``26 > (h)`` — the control. Same block type, same
                confidence, no collision, so any score difference between it
                and a ``(g)`` clause is the collision demotion and nothing
                else.
* a ``-`` bullet  an unlabelled list item the parser marks ``uncertain`` on its
                own merits. It must *stay* uncertain: the fix restores the
                provisions a collision demoted, it does not promote everything.

Every fragment contains the word "bicycle", so the query ``"bicycle"`` scores
identically against all of them on content and the only thing left to separate
their scores is the ``parse_status`` bonus in ``_score_fragment`` (+1.0 parsed
/ -2.0 otherwise).

The document is its own bylaw, so the spec scopes every search to it by
``bylaw_name`` and no other seeded corpus can contribute matches.

Idempotent get-or-create keyed on the document's ``file_hash``.

Usage::

    DATABASE_URL=... python scripts/seed_e2e_abs480_citation_collision.py
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

DOCUMENT_FILE_HASH = "e2e-abs480-citation-collision-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Citation Collision Probe Bylaw (ABS-480 E2E)"

# Arbitrary but stable/unique among the e2e seeds ("abs480-collision").
ADVISORY_LOCK_KEY = 4800480

COLLIDED_LABEL = "(g)"
COLLIDED_PATH = "26 > (g)"
CONTROL_PATH = "26 > (h)"
SECTION_PATH = "26"

_BLOCK_TEXTS = (
    ("26 Bicycle Parking", BlockType.HEADING),
    ("(g) Every bicycle parking space shall be located on the same lot.", BlockType.LIST_ITEM),
    ("(g) Every bicycle parking space shall be sheltered from precipitation.", BlockType.LIST_ITEM),
    ("(h) Every bicycle storage locker shall be secured.", BlockType.LIST_ITEM),
    ("- Bicycle racks shall be anchored to the ground.", BlockType.LIST_ITEM),
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
        source_path="e2e/abs480_citation_collision.txt",
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
            "collided_path": COLLIDED_PATH,
            "control_path": CONTROL_PATH,
        }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
