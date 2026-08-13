#!/usr/bin/env python
"""Seed the provision-in-context probe corpus (ABS-492).

Fixture for ``web/e2e/functional/abs492-provision-in-context.spec.ts``.

The corpus reproduces, in five load-bearing fragments plus filler, the shape
the Regional Centre by-law states its side-setback rule in — and the ranking
inversion that shape used to produce.

The tree the rule is written as::

    Part V, Chapter 9: Built Form and Siting Requirements within the ER-3 Zone
      Part V > 229   "229 (1) The minimum required side setback for a main
                      building is 2.5 metres."
        Part V > 229 > (f)   "(f) 2.5 metres elsewhere."

Only the chapter names the zone. Only the section names the dimension. The
list item — the fragment that states the number a reader actually wants —
names neither, which is why "ER-3 side setback" could not reach it: nothing
about its containers was indexed or scored.

Against that, the decoy::

    Part V > 135 > [The maximum required side setback for any main building
    shall be] > (a)   "(a) on lots located within the Downtown Halifax Central
                       Blocks Special Area."

Its own text is about a special area, not a setback. ABS-488 repathed clauses
onto the container that scopes them, which folded the container's sentence into
this fragment's ``citation_path`` — and before ABS-492 that sentence scored at
path weight, so the decoy banked +35 for the phrase and +12 for each of "side"
and "setback" and beat the section that states the standard. It is the fragment
that topped the real corpus's ranking for this query.

The control twin (``Part IX > 631``) repeats the section's text word for word
with no container at all. Anything the section scores above the twin is scope,
which is what lets the spec separate "inherited context" from "longer text".

The parking filler exists so the document-frequency cut has a corpus to
measure against: with a handful of fragments every query token looks rare and
the cut never fires, which would make the spec pass for the wrong reason.

The document is its own bylaw, so the spec scopes every search to it by
``bylaw_name`` and no other seeded corpus can contribute matches.

Idempotent get-or-create keyed on the document's ``file_hash``.

Usage::

    DATABASE_URL=... python scripts/seed_e2e_abs492_provision_context.py
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

DOCUMENT_FILE_HASH = "e2e-abs492-provision-context-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Provision Context Probe Bylaw (ABS-492 E2E)"

# Arbitrary but stable/unique among the e2e seeds ("abs492-provision-context").
ADVISORY_LOCK_KEY = 4920492

CHAPTER_CITATION_PATH = "Part V, Chapter 9"
SECTION_CITATION_PATH = "Part V > 229"
LIST_ITEM_CITATION_PATH = "Part V > 229 > (f)"
DECOY_CITATION_PATH = (
    "Part V > 135 > [The maximum required side setback for any main building "
    "shall be] > (a)"
)
TWIN_CITATION_PATH = "Part IX > 631"
PARKING_CHAPTER_CITATION_PATH = "Part VII, Chapter 2"

SECTION_TEXT = (
    "229 (1) The minimum required side setback for a main building is 2.5 metres."
)
LIST_ITEM_TEXT = "(f) 2.5 metres elsewhere."

#: The query the spec issues. Every term in it comes from a container of the
#: list item and none of it from the list item itself.
PROBE_QUERY = "ER-3 side setback"

_FILLER_COUNT = 20


def _get_or_create_document(session) -> Document:
    document = session.execute(
        select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
    ).scalars().first()
    if document is not None:
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/abs492_provision_context.txt",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="text/plain",
        page_count=1,
        parser_version="e2e-seed",
        # retrieval_enabled stays False, as it does for every probe seed: the
        # endpoints this spec drives build an unscoped RetrievalService
        # (ABS-413), and publishing the probe would put it inside the
        # enabled-document set that other specs assert over.
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _add(
    session,
    document_id: int,
    *,
    fragment_type: FragmentType,
    text: str,
    citation_label: str | None,
    citation_path: str,
    reading_order: int,
    parent: SourceFragment | None = None,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=citation_label,
        citation_path=citation_path,
        parent_fragment_id=parent.id if parent is not None else None,
        page_start=1,
        page_end=1,
        reading_order_start=reading_order,
        reading_order_end=reading_order,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment


def _ensure_fragments(session, document_id: int) -> None:
    existing = {
        fragment.citation_path
        for fragment in session.execute(
            select(SourceFragment).where(SourceFragment.document_id == document_id)
        ).scalars()
    }
    if CHAPTER_CITATION_PATH in existing:
        return

    chapter = _add(
        session,
        document_id,
        fragment_type=FragmentType.PART,
        text=(
            "Part V, Chapter 9: Built Form and Siting Requirements within the "
            "ER-3 Zone"
        ),
        citation_label="Part V, Chapter 9",
        citation_path=CHAPTER_CITATION_PATH,
        reading_order=1,
    )
    section = _add(
        session,
        document_id,
        fragment_type=FragmentType.SECTION,
        text=SECTION_TEXT,
        citation_label="229",
        citation_path=SECTION_CITATION_PATH,
        reading_order=2,
        parent=chapter,
    )
    _add(
        session,
        document_id,
        fragment_type=FragmentType.LIST_ITEM,
        text=LIST_ITEM_TEXT,
        citation_label=None,
        citation_path=LIST_ITEM_CITATION_PATH,
        reading_order=3,
        parent=section,
    )
    _add(
        session,
        document_id,
        fragment_type=FragmentType.CLAUSE,
        text=(
            "(a) on lots located within the Downtown Halifax Central Blocks "
            "Special Area."
        ),
        citation_label=None,
        citation_path=DECOY_CITATION_PATH,
        reading_order=4,
    )
    _add(
        session,
        document_id,
        fragment_type=FragmentType.SECTION,
        text=SECTION_TEXT,
        citation_label="631",
        citation_path=TWIN_CITATION_PATH,
        reading_order=5,
    )

    parking = _add(
        session,
        document_id,
        fragment_type=FragmentType.PART,
        text="Part VII, Chapter 2: Parking and Loading Requirements",
        citation_label="Part VII, Chapter 2",
        citation_path=PARKING_CHAPTER_CITATION_PATH,
        reading_order=6,
    )
    for index in range(_FILLER_COUNT):
        _add(
            session,
            document_id,
            fragment_type=FragmentType.CLAUSE,
            text=(
                f"({chr(ord('a') + index % 26)}) A parking space for a vehicle "
                "shall be at least 2.6 metres wide."
            ),
            citation_label=None,
            citation_path=f"Part VII > {700 + index}",
            reading_order=7 + index,
            parent=parking,
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
            "query": PROBE_QUERY,
            "chapter_citation_path": CHAPTER_CITATION_PATH,
            "section_citation_path": SECTION_CITATION_PATH,
            "list_item_citation_path": LIST_ITEM_CITATION_PATH,
            "decoy_citation_path": DECOY_CITATION_PATH,
            "twin_citation_path": TWIN_CITATION_PATH,
        }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
