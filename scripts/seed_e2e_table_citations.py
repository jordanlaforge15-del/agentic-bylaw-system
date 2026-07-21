"""Seed the ORPHAN caption/table state for the ABS-409 e2e spec.

Creates a dedicated Document ("Table Citations Test By-law") mirroring the
Regional Centre corpus defect in miniature:

* an addressed SECTION fragment ("Part I > 39", p44) supplying the part
  prefix for caption paths;
* an UNADDRESSED caption PROSE fragment ("Table 1A: Permitted uses by zone
  …", p45, ``citation_path=NULL``);
* TWO orphan matrix tables (p45 + p46 continuation slice, ``caption=NULL``,
  ``parent_fragment_id=NULL``, no semantic profile) whose union carries the
  full use column — the continuation row ("Military use") only exists on the
  second slice;
* a FOOTNOTE fragment carrying the ③ legend the conditional cell joins to;
* a parking table (p60) WRONGLY pre-profiled ``permission_matrix`` with its
  own unaddressed "Table 15: … parking spaces" caption — the demotion case.

The spec then drives POST /v1/_test/link-table-captions (the backfill path)
and asserts lookup_citation + get_zone_profile against the healed state.

Re-runnable: if the document already exists (a prior run linked it), it is
reset to the orphan state first — the e2e database persists across runs.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from layer1.db.base import (
    Document,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableSemanticProfile,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.semantic.enrichment import _clear_existing_semantics

DOCUMENT_FILE_HASH = "e2e-table-citations-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Table Citations Test By-law"

CAPTION_USES = "Table 1A: Permitted uses by zone (DD, DH, and COR)"
CAPTION_PARKING = "Table 15: Required minimum or maximum number of motor vehicle parking spaces"
CONDITION_TEXT = (
    "③ Use is permitted subject to a maximum gross floor area of 100 square metres."
)

# Slice 1 (p45) and continuation slice 2 (p46) of the same logical table.
MATRIX_P45 = [
    ["Use", "DD", "DH", "COR"],
    ["Restaurant use", "●", "③", ""],
    ["Office use", "●", "●", "●"],
]
MATRIX_P46 = [
    ["Use", "DD", "DH", "COR"],
    ["Multi-unit dwelling use", "●", "●", ""],
    ["Military use", "", "●", ""],
]
PARKING_P60 = [
    ["Use", "DD", "COR"],
    ["Restaurant use", "Not required", "Maximum 1 space"],
    ["Office use", "Not required", "Not required"],
]


def _add_fragment(session, document_id, *, text, page, fragment_type,
                  citation_path=None, citation_label=None, order=1):
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=citation_label,
        citation_path=citation_path,
        page_start=page,
        page_end=page,
        reading_order_start=order,
        reading_order_end=order,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment


def _add_table(session, document_id, *, page, grid):
    table = SourceTable(
        document_id=document_id,
        page_start=page,
        page_end=page,
        parse_status=ParseStatus.PARSED,
        caption=None,
        metadata_json={},
    )
    session.add(table)
    session.flush()
    for row_index, row in enumerate(grid):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=row[0] if row_index else None,
                    col_header_path=grid[0][col_index] if row_index else None,
                    text=text,
                    metadata_json={},
                )
            )
    session.flush()
    return table


def _reset_existing(session, document: Document) -> None:
    """Restore the orphan state on a document a prior run already linked."""
    _clear_existing_semantics(session, document_id=document.id)
    for fragment in session.execute(
        select(SourceFragment).where(SourceFragment.document_id == document.id)
    ).scalars():
        if fragment.text and fragment.text.startswith("Table "):
            fragment.citation_path = None
            fragment.citation_label = None
    tables = list(
        session.execute(
            select(SourceTable).where(SourceTable.document_id == document.id)
        ).scalars()
    )
    for table in tables:
        table.caption = None
        table.parent_fragment_id = None
    # Re-stage the wrong parking profile (the demotion assertion's precondition).
    parking = max(tables, key=lambda t: t.page_start)
    session.add(
        TableSemanticProfile(
            table_id=parking.id,
            profile_type="permission_matrix",
            confidence=0.4,
            review_status="auto",
            metadata_json={},
        )
    )
    session.flush()


def main() -> int:
    with session_scope() as session:
        if session.bind.dialect.name == "postgresql":
            from sqlalchemy import text as sa_text

            # Playwright runs this seed once per worker concurrently; the
            # lock serialises the check-then-insert on DOCUMENT_FILE_HASH.
            session.execute(
                sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601409)
            )

        existing = session.execute(
            select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
        ).scalar_one_or_none()
        if existing is not None:
            _reset_existing(session, existing)
            print(f"reset document {existing.id} to orphan state")
            return 0

        document = Document(
            municipality=DOCUMENT_MUNICIPALITY,
            bylaw_name=DOCUMENT_BYLAW_NAME,
            source_path="e2e-table-citations.pdf",
            file_hash=DOCUMENT_FILE_HASH,
            mime_type="application/pdf",
            page_count=60,
            parser_version="e2e-seed",
            retrieval_enabled=True,
            ingestion_timestamp=utcnow(),
        )
        session.add(document)
        session.flush()

        _add_fragment(
            session, document.id,
            text="39 A general provision about uses.",
            page=44, fragment_type=FragmentType.SECTION,
            citation_path="Part I > 39", citation_label="39",
        )
        _add_fragment(
            session, document.id, text=CAPTION_USES, page=45,
            fragment_type=FragmentType.PROSE, order=2,
        )
        _add_table(session, document.id, page=45, grid=MATRIX_P45)
        _add_table(session, document.id, page=46, grid=MATRIX_P46)
        _add_fragment(
            session, document.id, text=CONDITION_TEXT, page=47,
            fragment_type=FragmentType.FOOTNOTE, order=3,
        )
        _add_fragment(
            session, document.id, text=CAPTION_PARKING, page=60,
            fragment_type=FragmentType.PROSE, order=4,
        )
        parking = _add_table(session, document.id, page=60, grid=PARKING_P60)
        session.add(
            TableSemanticProfile(
                table_id=parking.id,
                profile_type="permission_matrix",
                confidence=0.4,
                review_status="auto",
                metadata_json={},
            )
        )
        session.flush()
        print(f"seeded document {document.id} ({DOCUMENT_BYLAW_NAME})")
        return 0


if __name__ == "__main__":
    sys.exit(main())
