#!/usr/bin/env python
"""Seed the dimensional-retrieval probe corpus (ABS-500).

Fixture for ``web/e2e/functional/abs500-dimensional-retrieval.spec.ts``.

The corpus reproduces the two shapes a dimensional standard is written in, and
the two ways retrieval used to fail to reach either.

**A standard stated in a table.** The provision that introduces it says nothing
a reader's question contains::

    Part V > 94   "94 Every main building shall comply with the following
                   requirements:"
      [table, parented to 94]
          |            | Maximum Building Height | Maximum Lot Coverage |
          | HR-1 Zone  | 8.0 metres              | 45%                  |
          | HR-2 Zone  | 12.0 metres             | 60%                  |

The answer to "what is the maximum building height in the HR-2 zone" is the
cell reading ``12.0 metres``. Its text shares no term with the question, and the
zone that selects its row is stated only by ``table_axis_binding`` — the cell
says "HR-2" nowhere. Before ABS-500 the ranker scored fragments only, so the
cell was reachable exclusively if fragment 94 ranked on its own words, which on
this query it cannot.

**A standard stated in prose, under a chapter that declares the zone.** The
section never names the zone::

    Part VI, Chapter 9: Built Form and Siting Requirements within the
                        ER3, ER-2, and ER-1 Zones
      Part VI > 231   "231 (1) ... the maximum required lot coverage shall
                       be 45%."

Against it, the decoy — a clause that *does* say ER-1, in the shape the real
corpus produces them by the dozen::

    Part X > 425   "425 (1) Where a lot abuts another lot, any portion of
                    which, is zoned HR-2, HR-1, ER-3, ER-2 or ER-1, a
                    landscaped buffer shall be provided."

The decoy earned +4 for its own mention; the governed section earned +2 for the
chapter that scopes it. So the landscaping clause about abutting land outranked
the section that states the ER-1 standard, and the ``dimensional`` class sat at
Recall@10 = 0.056.

**A parentless table.** 63 of the 96 tables in the dev corpus carry no
``parent_fragment_id`` — every table in the Halifax Mainland by-law does — so
the loading-space table here is seeded with none, and its anchor has to be
recovered from the parser's block ordering.

The watercourse filler exists so the document-frequency cut has a corpus to
measure against: with a handful of fragments every query term looks rare, the
cut never fires and the spec would pass for the wrong reason.

The document is its own bylaw, so the spec scopes every search to it by
``bylaw_name`` and no other seeded corpus can contribute matches.

Idempotent get-or-create keyed on the document's ``file_hash``.

Usage::

    DATABASE_URL=... python scripts/seed_e2e_abs500_table_channel.py
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import json
import sys

from sqlalchemy import select, text as sa_text

from layer1.db.base import (
    Document,
    SemanticEntity,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

DOCUMENT_FILE_HASH = "e2e-abs500-table-channel-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Dimensional Retrieval Probe Bylaw (ABS-500 E2E)"

# Arbitrary but stable/unique among the e2e seeds ("abs500-table-channel").
ADVISORY_LOCK_KEY = 5000500

INTRO_CITATION_PATH = "Part V > 94"
LOADING_CITATION_PATH = "Part X > 203"
ZONE_CHAPTER_CITATION_PATH = "Part VI, Chapter 9"
GOVERNED_CITATION_PATH = "Part VI > 231"
MENTIONING_CITATION_PATH = "Part X > 425"

INTRO_TEXT = "94 Every main building shall comply with the following requirements:"
LOADING_TEXT = "203 Off-street loading spaces shall comply with the following:"
ZONE_CHAPTER_TEXT = (
    "Part VI, Chapter 9: Built Form and Siting Requirements within the ER3, "
    "ER-2, and ER-1 Zones"
)
GOVERNED_TEXT = (
    "231 (1) Subject to Subsections 231(2) and 231(3), the maximum required "
    "lot coverage shall be 45%."
)
MENTIONING_TEXT = (
    "425 (1) Where a lot abuts another lot, any portion of which, is zoned "
    "HR-2, HR-1, ER-3, ER-2 or ER-1, a landscaped buffer shall be provided."
)

#: The queries the spec issues. The first is answered by a cell; the second by
#: a section whose chapter, not whose text, names the zone.
TABLE_QUERY = "What is the maximum building height in the HR-2 zone?"
PROSE_QUERY = "What is the maximum required lot coverage in the ER-1 zone?"

#: The cell that answers TABLE_QUERY, and its sibling one row up.
ANSWER_CELL_TEXT = "12.0 metres"
SIBLING_CELL_TEXT = "8.0 metres"

_ZONE_CODES = ("HR-1", "HR-2", "ER-1", "ER-2", "ER-3", "CH-1")
_FILLER_COUNT = 40


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH))
        .scalars()
        .first()
    )
    if document is not None:
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/abs500_table_channel.txt",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="text/plain",
        page_count=2,
        parser_version="e2e-seed",
        # retrieval_enabled stays False, as it does for every probe seed: the
        # endpoint this spec drives builds an unscoped RetrievalService
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
    source_block_id: int,
    page: int = 1,
    parent: SourceFragment | None = None,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=citation_label,
        citation_path=citation_path,
        parent_fragment_id=parent.id if parent is not None else None,
        page_start=page,
        page_end=page,
        reading_order_start=reading_order,
        reading_order_end=reading_order,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[source_block_id],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment


def _add_table(
    session,
    document_id: int,
    *,
    grid: list[list[str]],
    source_block_id: int,
    page: int = 1,
    parent: SourceFragment | None = None,
    caption: str | None = None,
) -> SourceTable:
    table = SourceTable(
        document_id=document_id,
        parent_fragment_id=parent.id if parent is not None else None,
        caption=caption,
        page_start=page,
        page_end=page,
        parse_status=ParseStatus.PARSED,
        metadata_json={"source_block_id": source_block_id},
    )
    session.add(table)
    session.flush()
    for row_index, row in enumerate(grid):
        for col_index, cell_text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=None,
                    col_header_path=None,
                    text=cell_text,
                    bbox_json=None,
                    metadata_json={},
                )
            )
    session.flush()
    return table


def _zone_entities(session, document_id: int) -> dict[str, SemanticEntity]:
    entities: dict[str, SemanticEntity] = {}
    for code in _ZONE_CODES:
        entity = (
            session.execute(
                select(SemanticEntity)
                .where(SemanticEntity.document_id == document_id)
                .where(SemanticEntity.entity_type == "zone")
                .where(SemanticEntity.canonical_name == code)
            )
            .scalars()
            .first()
        )
        if entity is None:
            entity = SemanticEntity(
                document_id=document_id,
                entity_type="zone",
                canonical_name=code,
                aliases_json=[],
                confidence=1.0,
                metadata_json={},
            )
            session.add(entity)
            session.flush()
        entities[code] = entity
    return entities


def _bind_row(session, table: SourceTable, *, index: int, raw_label: str, entity) -> None:
    session.add(
        TableAxisBinding(
            table_id=table.id,
            axis="row",
            index=index,
            entity_id=entity.id,
            raw_label=raw_label,
            confidence=1.0,
            metadata_json={},
        )
    )
    session.flush()


def _ensure_corpus(session, document_id: int) -> None:
    existing = {
        fragment.citation_path
        for fragment in session.execute(
            select(SourceFragment).where(SourceFragment.document_id == document_id)
        ).scalars()
    }
    if INTRO_CITATION_PATH in existing:
        return

    zones = _zone_entities(session, document_id)

    intro = _add(
        session,
        document_id,
        fragment_type=FragmentType.SECTION,
        text=INTRO_TEXT,
        citation_label="94",
        citation_path=INTRO_CITATION_PATH,
        reading_order=1,
        source_block_id=10,
    )
    height_table = _add_table(
        session,
        document_id,
        grid=[
            ["", "Maximum Building Height", "Maximum Lot Coverage"],
            ["HR-1 Zone", SIBLING_CELL_TEXT, "45%"],
            ["HR-2 Zone", ANSWER_CELL_TEXT, "60%"],
        ],
        source_block_id=11,
        parent=intro,
    )
    session.add(
        TableSemanticProfile(
            table_id=height_table.id,
            profile_type="dimensional_matrix",
            row_axis_type="zone",
            column_axis_type="standard",
            value_type="numeric_or_text",
            metadata_json={},
        )
    )
    _bind_row(session, height_table, index=1, raw_label="HR-1 Zone", entity=zones["HR-1"])
    _bind_row(session, height_table, index=2, raw_label="HR-2 Zone", entity=zones["HR-2"])

    # Parentless table on page 2: the anchor has to be recovered from the
    # parser's block ordering, not from parent_fragment_id.
    _add(
        session,
        document_id,
        fragment_type=FragmentType.SECTION,
        text=LOADING_TEXT,
        citation_label="203",
        citation_path=LOADING_CITATION_PATH,
        reading_order=2,
        source_block_id=20,
        page=2,
    )
    loading_table = _add_table(
        session,
        document_id,
        grid=[
            ["", "Minimum Loading Space Length"],
            ["CH-1 Zone", "9.0 metres"],
        ],
        source_block_id=21,
        page=2,
        parent=None,
    )
    _bind_row(
        session, loading_table, index=1, raw_label="CH-1 Zone", entity=zones["CH-1"]
    )

    # The prose half: a zone-declaring chapter, the section it governs, and the
    # clause that merely mentions the zone.
    chapter = _add(
        session,
        document_id,
        fragment_type=FragmentType.PART,
        text=ZONE_CHAPTER_TEXT,
        citation_label="Part VI, Chapter 9",
        citation_path=ZONE_CHAPTER_CITATION_PATH,
        reading_order=3,
        source_block_id=30,
    )
    _add(
        session,
        document_id,
        fragment_type=FragmentType.SECTION,
        text=GOVERNED_TEXT,
        citation_label="231",
        citation_path=GOVERNED_CITATION_PATH,
        reading_order=4,
        source_block_id=31,
        parent=chapter,
    )
    _add(
        session,
        document_id,
        fragment_type=FragmentType.SECTION,
        text=MENTIONING_TEXT,
        citation_label="425",
        citation_path=MENTIONING_CITATION_PATH,
        reading_order=5,
        source_block_id=32,
    )

    for index in range(_FILLER_COUNT):
        _add(
            session,
            document_id,
            fragment_type=FragmentType.CLAUSE,
            text=(
                f"7{index:02d} No person shall deposit refuse, debris or fill upon "
                "a watercourse bank without a permit issued under this By-law."
            ),
            citation_label=f"7{index:02d}",
            citation_path=f"Part XI > 7{index:02d}",
            reading_order=6 + index,
            source_block_id=100 + index,
        )


def main() -> int:
    with session_scope() as session:
        if session.bind.dialect.name == "postgresql":
            # ABS-207: serialise against the other Playwright viewport workers,
            # which all run this spec's beforeAll concurrently.
            session.execute(
                sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(
                    k=ADVISORY_LOCK_KEY
                )
            )
        document = _get_or_create_document(session)
        _ensure_corpus(session, document.id)
        session.flush()
        payload = {
            "document_id": document.id,
            "bylaw_name": DOCUMENT_BYLAW_NAME,
            "table_query": TABLE_QUERY,
            "prose_query": PROSE_QUERY,
            "intro_citation_path": INTRO_CITATION_PATH,
            "loading_citation_path": LOADING_CITATION_PATH,
            "governed_citation_path": GOVERNED_CITATION_PATH,
            "mentioning_citation_path": MENTIONING_CITATION_PATH,
        }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
