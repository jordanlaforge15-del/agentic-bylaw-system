"""Seed a synthetic bylaw document for the ABS-270 structured-query e2e spec.

Inserts fragments whose ``citation_path`` values match the canonical paths
produced by ``_lookup_via_structured``:

* ``ZoneAttributeQuery(zone="HR-2", attribute="max_height")``
  → path ``"HR-2 > max_height"``
* ``ScheduleRowQuery(schedule="Table 1A", row="HR-2")``
  → path ``"Table 1A > HR-2"``

A third fragment (``citation_path="HR-2 > max_lot_coverage"``) exists to give
the suggestion engine something to rank for near-miss queries, so the e2e spec
can verify that missed zone-attribute lookups return a non-empty suggestion list.

Idempotent — identified by the unique ``file_hash``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

from layer1.db.base import Document, PageBlock, SourceFragment, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import BlockType, FragmentType, ParseStatus


ABS270_FILE_HASH = "e2e-abs270-structured-query-v1"
ABS270_MUNICIPALITY = "HRM"
ABS270_BYLAW_NAME = "ABS-270 Structured Query E2E Bylaw"


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text  # noqa: PLC0415

        # Stable arbitrary 64-bit key, distinct from other seed scripts.
        # Serialises concurrent beforeAll invocations across Playwright
        # workers so the unique-key inserts below don't race.
        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2702700270))

    doc = _get_or_create_document(session)
    _ensure_blocks(session, doc.id)
    _ensure_fragments(session, doc.id)
    session.flush()
    return {"document_id": doc.id}


def _get_or_create_document(session) -> Document:
    doc = (
        session.execute(
            select(Document).where(Document.file_hash == ABS270_FILE_HASH)
        )
        .scalars()
        .first()
    )
    if doc is not None:
        # Converge the publish flag on re-seed: rows created before
        # ABS-413 (or left disabled by the migration backfill) must
        # still end up retrieval-enabled in the persistent e2e DB.
        doc.retrieval_enabled = True
        session.flush()
        return doc
    doc = Document(
        municipality=ABS270_MUNICIPALITY,
        bylaw_name=ABS270_BYLAW_NAME,
        source_path="/tmp/e2e_abs270_structured.txt",
        file_hash=ABS270_FILE_HASH,
        mime_type="text/plain",
        page_count=2,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(doc)
    session.flush()
    return doc


def _ensure_blocks(session, document_id: int) -> None:
    from sqlalchemy import select as sa_select
    existing = (
        session.execute(
            sa_select(PageBlock.id).where(PageBlock.document_id == document_id)
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return
    block = PageBlock(
        document_id=document_id,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        reading_order=0,
        raw_text="Zone HR-2 maximum height 12 metres",
        is_boilerplate=False,
        parser_source="e2e-seed",
    )
    session.add(block)
    session.flush()


def _ensure_fragments(session, document_id: int) -> None:
    from sqlalchemy import select as sa_select
    existing = (
        session.execute(
            sa_select(SourceFragment.id).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_path == "HR-2 > max_height",
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return

    specs = [
        dict(
            fragment_type=FragmentType.CLAUSE,
            citation_label="HR-2 > max_height",
            citation_path="HR-2 > max_height",
            page_start=1, page_end=1,
            reading_order_start=0,
            text=(
                "HR-2 Zone: Maximum building height shall be 12.0 metres "
                "or 4 storeys, whichever is less."
            ),
            parse_status=ParseStatus.PARSED,
            confidence=0.95,
        ),
        dict(
            fragment_type=FragmentType.CLAUSE,
            citation_label="HR-2 > max_lot_coverage",
            citation_path="HR-2 > max_lot_coverage",
            page_start=1, page_end=1,
            reading_order_start=1,
            text=(
                "HR-2 Zone: Maximum lot coverage shall be 40% of the total lot area."
            ),
            parse_status=ParseStatus.PARSED,
            confidence=0.93,
        ),
        dict(
            fragment_type=FragmentType.CLAUSE,
            citation_label="Table 1A > HR-2",
            citation_path="Table 1A > HR-2",
            page_start=2, page_end=2,
            reading_order_start=0,
            text=(
                "Table 1A — Zone Development Standards: "
                "HR-2 row — Height: 12 m; Coverage: 40%; FAR: 1.5."
            ),
            parse_status=ParseStatus.PARSED,
            confidence=0.92,
        ),
    ]
    for spec in specs:
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
            source_block_ids_json=[],
        ))
    session.flush()


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(f"seed_e2e_abs270_structured: document={ids['document_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
