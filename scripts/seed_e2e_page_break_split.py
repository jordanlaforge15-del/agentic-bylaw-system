"""Seed the page-break-split bylaw for the ABS-461 e2e spec.

Reproduces the page 171/172 break in clause 198(1)(a) of the HRM Regional
Centre Land Use By-law, verbatim from ``page_block.raw_text`` on document 4:
the block on page 171 ends mid-token at "...is zoned ER-3, ER-" and the block
on page 172 opens "2, ER-1, CH-2, ...". The defective parser read that tail as
a new section and reparented seven clauses under a phantom "Part V > 2".

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


def _block_data() -> list[PageBlockData]:
    return [
        PageBlockData(
            page_number=page,
            block_type=block_type,
            reading_order=1750 + order,
            raw_text=text,
            normalized_text=" ".join(text.split()),
            parser_source="docling",
        )
        for order, (page, block_type, text) in enumerate(BLOCKS)
    ]


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH))
        .scalars()
        .first()
    )
    if document is not None:
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="/tmp/e2e_page_break_split.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=172,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text

        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=461461461))

    document = _get_or_create_document(session)

    # Rebuild from scratch so a re-seed always reflects the current parser.
    session.execute(delete(SourceFragment).where(SourceFragment.document_id == document.id))
    session.execute(delete(PageBlock).where(PageBlock.document_id == document.id))
    session.flush()

    block_data = _block_data()
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


def main() -> int:
    with session_scope() as session:
        result = seed(session)
    print(f"seeded document_id={result['document_id']} fragments={result['fragments']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
