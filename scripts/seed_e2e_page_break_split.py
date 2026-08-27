"""Seed the page-break-split bylaws for the ABS-461 e2e spec.

Reproduces the page 171/172 break in clause 198(1)(a) of the HRM Regional
Centre Land Use By-law, verbatim from ``page_block.raw_text`` on document 4:
the block on page 171 ends mid-token at "...is zoned ER-3, ER-" and the block
on page 172 opens "2, ER-1, CH-2, ...". The defective parser read that tail as
a new section and reparented seven clauses under a phantom "Part V > 2".

ABS-465 adds the *second* break the same bylaw carries, on pages 104/105 of
subsection 94.5. Production was found to have both; the dev eval case only
ever surfaced the 198 one, so only that one had coverage. The 104/105 tail
opens "3, ER-2, ER-1, ..." and forged a phantom "Part V > 3" that swallowed
the three permitted-encroachment clauses — the provisions a deck or balcony
question turns on. Each break is seeded as its own document so a regression
in one cannot mask the other.

The seed deliberately runs the blocks through the *real*
:func:`layer1.pipeline.hierarchy.reconstruct_hierarchy` rather than writing
fragment rows by hand. Hand-written rows would assert what the seed author
believes the parser does; running the parser asserts what it actually does, so
if the guard regresses the seed reproduces the phantom and the spec fails.

Idempotent — re-running drops the prior document's blocks and fragments and
rebuilds them.
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import sys

from sqlalchemy import delete, select

from layer1.db.base import Document, PageBlock, SourceFragment, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import BlockType
from layer1.models.schemas import PageBlockData
from layer1.pipeline.hierarchy import reconstruct_hierarchy

DOCUMENT_FILE_HASH = "e2e-page-break-split-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Page Break E2E Bylaw"

ENCROACHMENT_FILE_HASH = "e2e-page-break-split-2"
ENCROACHMENT_BYLAW_NAME = "Page Break E2E Bylaw (encroachments)"

LOCK_KEY = 461461461

# (page, block type, raw text) — pages 170-172, blocks 8458-8472 of document 4.
BLOCKS: list[tuple[int, BlockType, str]] = [
    (170, BlockType.HEADING, "Part V Land Use"),
    (171, BlockType.HEADING, "Side Setback Requirements"),
    (
        171,
        BlockType.LIST_ITEM,
        (
            "198 (1) Subject to Subsections 198(2) and 198(3), the minimum required "
            "side setback for any main building shall be:"
        ),
    ),
    (
        171,
        BlockType.LIST_ITEM,
        (
            "(a) subject to Clauses 198(1)(b) and 198(1)(c), where a lot line abuts a "
            "lot, any portion of which, is zoned ER-3, ER-"
        ),
    ),
    # ---- page break lands here, mid-token ----
    (172, BlockType.LIST_ITEM, "2, ER-1, CH-2, CH-1, PCF, or RPK zone: (RCCC-Sep 4/24;E-Apr 17/25)"),
    (
        172,
        BlockType.LIST_ITEM,
        (
            "(i) 3.0 metres from the side lot line abutting the lot for any low-rise "
            "building, or (RCCC-Sep 4/24;E-Apr 17/25)"
        ),
    ),
    (
        172,
        BlockType.LIST_ITEM,
        (
            "(ii) 6.0 metres from the side lot line abutting the lot for any mid-rise, "
            "tall mid-rise, or high-rise building; (RCCC-Sep 4/24;E-Apr 17/25)"
        ),
    ),
    (172, BlockType.LIST_ITEM, "(b) for a townhouse dwelling use:"),
    (172, BlockType.LIST_ITEM, "(i) 0.0 metre along a common wall between each unit, or"),
    (172, BlockType.LIST_ITEM, "(ii) 3.0 metres elsewhere;"),
    (
        172,
        BlockType.LIST_ITEM,
        "(c) for a semi-detached dwelling use or duplex apartment use: (RCCC-Sep 4/24;E-Apr 17/25)",
    ),
    (172, BlockType.LIST_ITEM, "(i) 0.0 metre along a common wall between each unit, or"),
    (172, BlockType.LIST_ITEM, "(ii) 3.0 metres elsewhere;"),
    (
        172,
        BlockType.LIST_ITEM,
        (
            "(d) where a lot line abuts a lot, any portion of which, is zoned DD, DH, "
            "CEN-2, CEN-1, or COR zone, 0.0 metre, except as provided in Clause "
            "198(1)(a); (RCCC-Sep 4/24;E-Apr 17/25)"
        ),
    ),
    (
        172,
        BlockType.LIST_ITEM,
        (
            "(e) where a lot line abuts lands governed by the Downtown Halifax Secondary "
            "Municipal Planning Strategy and the Downtown Halifax Land Use By-law, "
            "0.0 metre; or"
        ),
    ),
    (172, BlockType.LIST_ITEM, "(f) 2.5 metres elsewhere."),
]

# ABS-465: the second break, pages 103-105, blocks 7697-7718 of document 4.
# The page-104 block ends mid-token at "...abuts a lot containing an ER-" and
# page 105 opens "3, ER-2, ...", forging the phantom "Part V > 3" that the
# three permitted-encroachment clauses reparented under on production.
#
# The FOOTER block between the head and the encroachment clauses is kept
# because it is page furniture the rejoin has to step over (ABS-461); dropping
# it would seed an easier problem than the corpus actually poses.
ENCROACHMENT_BLOCKS: list[tuple[int, BlockType, str]] = [
    (103, BlockType.HEADING, "Part V Land Use"),
    (
        103,
        BlockType.HEADING,
        (
            "General Requirement: Permitted Encroachments into Setbacks, "
            "Stepbacks, or Separation Distances"
        ),
    ),
    (
        103,
        BlockType.LIST_ITEM,
        (
            "94.5 (1) All of the following structures may encroach into a required "
            "setback, stepback, or separation distance:"
        ),
    ),
    (
        103,
        BlockType.LIST_ITEM,
        (
            "(a) a patio that is less than 0.6 metres in height, access ramps, "
            "walkways, lifting devices, uncovered steps, and staircases;"
        ),
    ),
    (
        104,
        BlockType.LIST_ITEM,
        (
            "(b) a sill, eave, gutter, downspout, cornice, chimney, fireplace, stove "
            "bump out, railing system, canopy, awning, or another similar feature, if "
            "an encroachment is no more than 0.6 metres;"
        ),
    ),
    (
        104,
        BlockType.LIST_ITEM,
        (
            "Subject to Subsection 94.5(5) and Section 96, a balcony or unenclosed "
            "porch may encroach into a required setback, stepback, or separation "
            "distance by no more than"
        ),
    ),
    (
        104,
        BlockType.LIST_ITEM,
        (
            "(a) 1.5 metres at the ground floor, except for a balcony that does not "
            "have access to a street without going through a main dwelling; or"
        ),
    ),
    (104, BlockType.LIST_ITEM, "(b) 2.0 metres at the second storey or above."),
    (104, BlockType.FOOTER, "(RCCC-Sep 4/24;E-Apr 17/25)"),
    (
        104,
        BlockType.LIST_ITEM,
        (
            "Except as provided in Subsection 94.5(6), a balcony or unenclosed porch "
            "shall not encroach into a required setback or stepback, if it faces a lot "
            "line that abuts a lot containing an ER-"
        ),
    ),
    # ---- page break lands here, mid-token ----
    (105, BlockType.LIST_ITEM, "3, ER-2, ER-1, CH-2, CH-1, PCF, or RPK zone. (RCCC-Sep 4/24;E-Apr 17/25)"),
    (
        105,
        BlockType.LIST_ITEM,
        (
            "A balcony or unenclosed porch in Subsection 94.5(5) may encroach into a "
            "required stepback if a main building is setback from a lot line that abuts "
            "an ER-3, ER-2, ER-1, CH-2, CH-1, PCF, or RPK zone by at least"
        ),
    ),
    (105, BlockType.LIST_ITEM, "(a) 8.0 metres for a mid-rise building;"),
    (105, BlockType.LIST_ITEM, "(b) 12.5 metres for a tall mid-rise building; or"),
    (105, BlockType.LIST_ITEM, "(c) 12.5 metres for a high-rise building."),
]


def _block_data(
    blocks: list[tuple[int, BlockType, str]], *, reading_order_base: int
) -> list[PageBlockData]:
    return [
        PageBlockData(
            page_number=page,
            block_type=block_type,
            reading_order=reading_order_base + order,
            raw_text=text,
            normalized_text=" ".join(text.split()),
            parser_source="docling",
        )
        for order, (page, block_type, text) in enumerate(blocks)
    ]


def _get_or_create_document(
    session, *, file_hash: str, bylaw_name: str, page_count: int
) -> Document:
    document = (
        session.execute(select(Document).where(Document.file_hash == file_hash))
        .scalars()
        .first()
    )
    if document is not None:
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=bylaw_name,
        source_path=f"/tmp/{file_hash}.pdf",
        file_hash=file_hash,
        mime_type="application/pdf",
        page_count=page_count,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def seed(
    session,
    *,
    blocks_source: list[tuple[int, BlockType, str]] | None = None,
    file_hash: str = DOCUMENT_FILE_HASH,
    bylaw_name: str = DOCUMENT_BYLAW_NAME,
    page_count: int = 172,
    reading_order_base: int = 1750,
) -> dict[str, int]:
    blocks_source = BLOCKS if blocks_source is None else blocks_source
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        # One key for every document this script seeds, deliberately. Both
        # seeds run inside a single transaction, and an advisory *xact* lock
        # is held to commit — so a second key would let two Playwright workers
        # take them in opposite orders and deadlock. Re-taking the same key
        # within a transaction is free.
        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=LOCK_KEY))

    document = _get_or_create_document(
        session, file_hash=file_hash, bylaw_name=bylaw_name, page_count=page_count
    )

    # Rebuild from scratch so a re-seed always reflects the current parser.
    session.execute(delete(SourceFragment).where(SourceFragment.document_id == document.id))
    session.execute(delete(PageBlock).where(PageBlock.document_id == document.id))
    session.flush()

    block_data = _block_data(blocks_source, reading_order_base=reading_order_base)
    blocks = []
    for data in block_data:
        block = PageBlock(
            document_id=document.id,
            page_number=data.page_number,
            block_type=data.block_type,
            reading_order=data.reading_order,
            raw_text=data.raw_text,
            normalized_text=data.normalized_text,
            is_boilerplate=False,
            parser_source=data.parser_source,
        )
        session.add(block)
        blocks.append(block)
    session.flush()

    fragments: list[SourceFragment] = []
    for data in reconstruct_hierarchy(block_data):
        fragment = SourceFragment(
            document_id=document.id,
            fragment_type=data.fragment_type,
            citation_label=data.citation_label,
            citation_path=data.citation_path,
            parent_fragment_id=(
                fragments[data.parent_index].id if data.parent_index is not None else None
            ),
            page_start=data.page_start,
            page_end=data.page_end,
            reading_order_start=data.reading_order_start,
            reading_order_end=data.reading_order_end,
            text=data.text,
            parse_status=data.parse_status,
            confidence=data.confidence,
            source_block_ids_json=[
                blocks[index].id for index in data.source_block_indices if index < len(blocks)
            ],
            metadata_json=data.metadata,
        )
        session.add(fragment)
        session.flush()
        fragments.append(fragment)

    return {"document_id": document.id, "fragments": len(fragments)}


def seed_encroachment(session) -> dict[str, int]:
    """Seed the pages 104/105 break (ABS-465) as its own document."""
    return seed(
        session,
        blocks_source=ENCROACHMENT_BLOCKS,
        file_hash=ENCROACHMENT_FILE_HASH,
        bylaw_name=ENCROACHMENT_BYLAW_NAME,
        page_count=105,
        reading_order_base=980,
    )


def main() -> int:
    with session_scope() as session:
        result = seed(session)
        encroachment = seed_encroachment(session)
    # Two labelled lines: the spec reads each id by name rather than by
    # position, so adding a third seeded break later cannot silently
    # repoint an existing assertion.
    print(f"seeded document_id={result['document_id']} fragments={result['fragments']}")
    print(
        f"seeded encroachment_document_id={encroachment['document_id']} "
        f"fragments={encroachment['fragments']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
