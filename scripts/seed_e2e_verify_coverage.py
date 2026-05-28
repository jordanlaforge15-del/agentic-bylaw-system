"""Seed a synthetic bylaw document for the verify-coverage e2e spec.

Creates a 5-page text-based document with known fragments and deliberate
gaps so the verify-coverage endpoint returns a predictable report shape.

Seeded content:

* Document ``HRM / Coverage E2E Bylaw`` (5 pages, text source).
* Page 1: one SECTION fragment with full text coverage.
* Page 2: one CLAUSE fragment, plus one UNCERTAIN prose fragment.
* Page 3: one table (SourceTable with 2 cells).
* Page 4: empty — no fragments at all (deliberate gap).
* Page 5: one fragment with a cross-reference (unresolved).

Expected verification gaps:
- Page 4: empty_page (high)
- Page 2: uncertain_fragments (medium)
- Page 5: unresolved_cross_references (low)

Idempotent — re-running drops prior rows by unique key and re-inserts.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from sqlalchemy import select

from layer1.db.base import (
    CrossReference,
    Document,
    PageBlock,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import (
    BlockType,
    FragmentType,
    ParseStatus,
    ResolutionStatus,
)


DOCUMENT_FILE_HASH = "e2e-verify-coverage-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Coverage E2E Bylaw"

PAGE_TEXTS = {
    1: "Part 1 General Provisions This part establishes the general provisions applicable to all zones within the municipality.",
    2: "Section 4.2 Setback Requirements The minimum front yard setback shall be 6.0 metres. Additional unstructured text that was not properly parsed.",
    3: "Table 1 Permitted Uses Zone R-1 Residential Zone C-1 Commercial",
    4: "Schedule A Amendment History This page records amendments to the bylaw as enacted by council.",
    5: "Section 8.1 Parking Requirements All developments shall comply with the parking standards set out in Section 4.2.",
}


def _write_source_file() -> str:
    content = "\f".join(PAGE_TEXTS[p] for p in sorted(PAGE_TEXTS))
    path = Path(tempfile.gettempdir()) / "e2e_verify_coverage_bylaw.txt"
    path.write_text(content, encoding="utf-8")
    return str(path)


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text
        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601178))

    source_path = _write_source_file()
    document = _get_or_create_document(session, source_path)
    _ensure_blocks(session, document.id)
    _ensure_fragments(session, document.id)
    _ensure_tables(session, document.id)
    _ensure_cross_references(session, document.id)
    session.flush()

    return {"document_id": document.id}


def _get_or_create_document(session, source_path: str) -> Document:
    doc = (
        session.execute(
            select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
        )
        .scalars()
        .first()
    )
    if doc is not None:
        doc.source_path = source_path
        session.flush()
        return doc
    doc = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path=source_path,
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="text/plain",
        page_count=5,
        parser_version="e2e-seed",
        ingestion_timestamp=utcnow(),
    )
    session.add(doc)
    session.flush()
    return doc


def _ensure_blocks(session, document_id: int) -> None:
    existing = session.execute(
        select(PageBlock.id).where(PageBlock.document_id == document_id)
    ).scalars().first()
    if existing is not None:
        return

    blocks = [
        dict(page_number=1, block_type=BlockType.HEADING, reading_order=0,
             raw_text="Part 1 General Provisions"),
        dict(page_number=1, block_type=BlockType.PARAGRAPH, reading_order=1,
             raw_text="This part establishes the general provisions applicable to all zones within the municipality."),
        dict(page_number=2, block_type=BlockType.HEADING, reading_order=0,
             raw_text="Section 4.2 Setback Requirements"),
        dict(page_number=2, block_type=BlockType.PARAGRAPH, reading_order=1,
             raw_text="The minimum front yard setback shall be 6.0 metres."),
        dict(page_number=2, block_type=BlockType.PARAGRAPH, reading_order=2,
             raw_text="Additional unstructured text that was not properly parsed."),
        dict(page_number=3, block_type=BlockType.TABLE_REGION, reading_order=0,
             raw_text="Table 1 Permitted Uses Zone R-1 Residential Zone C-1 Commercial"),
        dict(page_number=4, block_type=BlockType.PARAGRAPH, reading_order=0,
             raw_text="Schedule A Amendment History This page records amendments to the bylaw as enacted by council."),
        dict(page_number=5, block_type=BlockType.HEADING, reading_order=0,
             raw_text="Section 8.1 Parking Requirements"),
        dict(page_number=5, block_type=BlockType.PARAGRAPH, reading_order=1,
             raw_text="All developments shall comply with the parking standards set out in Section 4.2."),
    ]
    db_blocks = []
    for b in blocks:
        block = PageBlock(
            document_id=document_id,
            page_number=b["page_number"],
            block_type=b["block_type"],
            reading_order=b["reading_order"],
            raw_text=b["raw_text"],
            is_boilerplate=False,
            parser_source="e2e-seed",
        )
        session.add(block)
        db_blocks.append(block)
    session.flush()


def _ensure_fragments(session, document_id: int) -> None:
    existing = session.execute(
        select(SourceFragment.id).where(
            SourceFragment.document_id == document_id,
            SourceFragment.citation_path == "Part 1",
        )
    ).scalars().first()
    if existing is not None:
        return

    blocks = (
        session.query(PageBlock)
        .filter_by(document_id=document_id)
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .all()
    )
    block_ids_by_page: dict[int, list[int]] = {}
    for b in blocks:
        block_ids_by_page.setdefault(b.page_number, []).append(b.id)

    fragments_spec = [
        dict(
            fragment_type=FragmentType.SECTION,
            citation_label="Part 1",
            citation_path="Part 1",
            page_start=1, page_end=1,
            reading_order_start=0,
            text="Part 1 General Provisions This part establishes the general provisions applicable to all zones within the municipality.",
            parse_status=ParseStatus.PARSED,
            confidence=0.95,
            source_block_ids=block_ids_by_page.get(1, []),
        ),
        dict(
            fragment_type=FragmentType.CLAUSE,
            citation_label="4.2",
            citation_path="4.2",
            page_start=2, page_end=2,
            reading_order_start=0,
            text="Section 4.2 Setback Requirements The minimum front yard setback shall be 6.0 metres.",
            parse_status=ParseStatus.PARSED,
            confidence=0.9,
            source_block_ids=block_ids_by_page.get(2, [])[:2],
        ),
        dict(
            fragment_type=FragmentType.PROSE,
            citation_label=None,
            citation_path=None,
            page_start=2, page_end=2,
            reading_order_start=2,
            text="Additional unstructured text that was not properly parsed.",
            parse_status=ParseStatus.UNCERTAIN,
            confidence=0.4,
            source_block_ids=block_ids_by_page.get(2, [])[2:3],
        ),
        # PARSED structural fragment with no citation_path — intentional by design.
        # Must NOT trigger a missing_citation_path gap (ABS-205).
        dict(
            fragment_type=FragmentType.HEADING,
            citation_label=None,
            citation_path=None,
            page_start=1, page_end=1,
            reading_order_start=0,
            text="Part 1 General Provisions",
            parse_status=ParseStatus.PARSED,
            confidence=0.85,
            source_block_ids=block_ids_by_page.get(1, [])[:1],
        ),
        dict(
            fragment_type=FragmentType.SECTION,
            citation_label="8.1",
            citation_path="8.1",
            page_start=5, page_end=5,
            reading_order_start=0,
            text="Section 8.1 Parking Requirements All developments shall comply with the parking standards set out in Section 4.2.",
            parse_status=ParseStatus.PARSED,
            confidence=0.9,
            source_block_ids=block_ids_by_page.get(5, []),
        ),
    ]
    for spec in fragments_spec:
        session.add(SourceFragment(
            document_id=document_id,
            fragment_type=spec["fragment_type"],
            citation_label=spec["citation_label"],
            citation_path=spec["citation_path"],
            page_start=spec["page_start"],
            page_end=spec["page_end"],
            reading_order_start=spec["reading_order_start"],
            text=spec["text"],
            parse_status=spec["parse_status"],
            confidence=spec["confidence"],
            source_block_ids_json=spec["source_block_ids"],
        ))
    session.flush()


def _ensure_tables(session, document_id: int) -> None:
    existing = session.execute(
        select(SourceTable.id).where(
            SourceTable.document_id == document_id,
            SourceTable.caption == "Table 1 Permitted Uses",
        )
    ).scalars().first()
    if existing is not None:
        return

    table = SourceTable(
        document_id=document_id,
        caption="Table 1 Permitted Uses",
        page_start=3,
        page_end=3,
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add(table)
    session.flush()

    cells = [
        SourceTableCell(table_id=table.id, row_index=0, col_index=0, text="R-1", row_header_path="Zone", col_header_path="Zone Code"),
        SourceTableCell(table_id=table.id, row_index=0, col_index=1, text="Residential", row_header_path="Zone", col_header_path="Use"),
        SourceTableCell(table_id=table.id, row_index=1, col_index=0, text="C-1", row_header_path="Zone", col_header_path="Zone Code"),
        SourceTableCell(table_id=table.id, row_index=1, col_index=1, text="Commercial", row_header_path="Zone", col_header_path="Use"),
    ]
    for cell in cells:
        session.add(cell)
    session.flush()


def _ensure_cross_references(session, document_id: int) -> None:
    frags = (
        session.query(SourceFragment)
        .filter_by(document_id=document_id)
        .all()
    )
    frag_by_path = {f.citation_path: f for f in frags if f.citation_path}
    parking_frag = frag_by_path.get("8.1")
    if not parking_frag:
        return

    existing = session.execute(
        select(CrossReference.id).where(
            CrossReference.document_id == document_id,
            CrossReference.source_fragment_id == parking_frag.id,
        )
    ).scalars().first()
    if existing is not None:
        return

    session.add(CrossReference(
        document_id=document_id,
        source_fragment_id=parking_frag.id,
        raw_reference_text="Section 4.2",
        target_citation_guess="4.2",
        target_fragment_id=frag_by_path.get("4.2", parking_frag).id if "4.2" in frag_by_path else None,
        resolution_status=ResolutionStatus.UNRESOLVED,
        confidence=0.7,
        metadata_json={},
    ))
    session.flush()


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(f"seed_e2e_verify_coverage: document={ids['document_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
