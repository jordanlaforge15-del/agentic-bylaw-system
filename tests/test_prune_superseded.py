"""Unit tests for layer1.pipeline.prune (prune-superseded command)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from layer1.db.base import (
    CrossReference,
    Document,
    IngestionRun,
    PageBlock,
    SourceFragment,
    SourceImage,
    SourceTable,
    SourceTableCell,
    utcnow,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import (
    BlockType,
    FragmentType,
    IngestionStatus,
    ParseStatus,
    ResolutionStatus,
)
from layer1.pipeline.prune import PruneEntry, PruneResult, prune_superseded_documents


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _ts(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _seed_doc(
    session,
    *,
    municipality: str = "TestCity",
    bylaw_name: str = "Test Bylaw",
    ingestion_timestamp: datetime | None = None,
    file_hash: str = "abc123",
) -> Document:
    doc = Document(
        municipality=municipality,
        bylaw_name=bylaw_name,
        source_path="/tmp/test.pdf",
        file_hash=file_hash,
        mime_type="application/pdf",
        page_count=10,
        parser_version="1.0",
        ingestion_timestamp=ingestion_timestamp or utcnow(),
    )
    session.add(doc)
    session.flush()
    return doc


def _seed_run(session, document_id: int) -> IngestionRun:
    run = IngestionRun(
        document_id=document_id,
        status=IngestionStatus.COMPLETED,
    )
    session.add(run)
    session.flush()
    return run


def _seed_block(session, document_id: int) -> PageBlock:
    block = PageBlock(
        document_id=document_id,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        reading_order=1,
        raw_text="Some text",
        is_boilerplate=False,
        parser_source="test",
    )
    session.add(block)
    session.flush()
    return block


def _seed_fragment(session, document_id: int, citation_suffix: str = "") -> SourceFragment:
    frag = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SECTION,
        citation_label="§1",
        citation_path=f"§1{citation_suffix}",
        page_start=1,
        page_end=1,
        text="Section text",
        parse_status=ParseStatus.PARSED,
    )
    session.add(frag)
    session.flush()
    return frag


def _seed_table(session, document_id: int) -> SourceTable:
    table = SourceTable(
        document_id=document_id,
        page_start=1,
        page_end=1,
        parse_status=ParseStatus.PARSED,
    )
    session.add(table)
    session.flush()
    return table


def _seed_xref(session, document_id: int, fragment_id: int) -> CrossReference:
    xref = CrossReference(
        document_id=document_id,
        source_fragment_id=fragment_id,
        raw_reference_text="§2",
        resolution_status=ResolutionStatus.UNRESOLVED,
    )
    session.add(xref)
    session.flush()
    return xref


def _seed_image(session, document_id: int) -> SourceImage:
    img = SourceImage(
        document_id=document_id,
        page_number=1,
        parse_status=ParseStatus.PARSED,
    )
    session.add(img)
    session.flush()
    return img


# ---------------------------------------------------------------------------
# Tests: grouping and keep-latest logic
# ---------------------------------------------------------------------------

def test_no_superseded_when_single_doc(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        _seed_doc(session)
        result = prune_superseded_documents(session, dry_run=True)
    assert result.entries == []
    assert result.deleted_count == 0


def test_no_superseded_when_exactly_keep_latest(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        _seed_doc(session, ingestion_timestamp=_ts(2024), file_hash="hash1")
        _seed_doc(session, ingestion_timestamp=_ts(2025), file_hash="hash2")
        result = prune_superseded_documents(session, keep_latest=2, dry_run=True)
    assert result.entries == []


def test_keep_latest_1_marks_older_as_superseded(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        old = _seed_doc(session, ingestion_timestamp=_ts(2023), file_hash="old_hash")
        new = _seed_doc(session, ingestion_timestamp=_ts(2025), file_hash="new_hash")
        result = prune_superseded_documents(session, keep_latest=1, dry_run=True)

    assert len(result.entries) == 1
    assert result.entries[0].id == old.id
    assert result.entries[0].file_hash == "old_hash"


def test_keep_latest_2_keeps_two_newest(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        oldest = _seed_doc(session, ingestion_timestamp=_ts(2021), file_hash="h1")
        mid = _seed_doc(session, ingestion_timestamp=_ts(2022), file_hash="h2")
        newer = _seed_doc(session, ingestion_timestamp=_ts(2023), file_hash="h3")
        newest = _seed_doc(session, ingestion_timestamp=_ts(2024), file_hash="h4")
        result = prune_superseded_documents(session, keep_latest=2, dry_run=True)

    superseded_ids = {e.id for e in result.entries}
    assert oldest.id in superseded_ids
    assert mid.id in superseded_ids
    assert newer.id not in superseded_ids
    assert newest.id not in superseded_ids


def test_groups_by_municipality_and_bylaw_name(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        # Two docs for bylaw A
        a_old = _seed_doc(session, municipality="HRM", bylaw_name="Bylaw A", ingestion_timestamp=_ts(2023), file_hash="a1")
        a_new = _seed_doc(session, municipality="HRM", bylaw_name="Bylaw A", ingestion_timestamp=_ts(2024), file_hash="a2")
        # One doc for bylaw B (should not be superseded)
        b = _seed_doc(session, municipality="HRM", bylaw_name="Bylaw B", ingestion_timestamp=_ts(2023), file_hash="b1")
        result = prune_superseded_documents(session, keep_latest=1, dry_run=True)

    superseded_ids = {e.id for e in result.entries}
    assert a_old.id in superseded_ids
    assert a_new.id not in superseded_ids
    assert b.id not in superseded_ids


def test_municipality_filter(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        hrm_old = _seed_doc(session, municipality="HRM", ingestion_timestamp=_ts(2023), file_hash="h1")
        hrm_new = _seed_doc(session, municipality="HRM", ingestion_timestamp=_ts(2024), file_hash="h2")
        other = _seed_doc(session, municipality="Other", ingestion_timestamp=_ts(2023), file_hash="o1")
        result = prune_superseded_documents(session, municipality="HRM", keep_latest=1, dry_run=True)

    superseded_ids = {e.id for e in result.entries}
    assert hrm_old.id in superseded_ids
    assert hrm_new.id not in superseded_ids
    assert other.id not in superseded_ids


def test_bylaw_name_filter(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        target_old = _seed_doc(session, bylaw_name="Target Bylaw", ingestion_timestamp=_ts(2023), file_hash="t1")
        target_new = _seed_doc(session, bylaw_name="Target Bylaw", ingestion_timestamp=_ts(2024), file_hash="t2")
        other = _seed_doc(session, bylaw_name="Other Bylaw", ingestion_timestamp=_ts(2023), file_hash="o1")
        result = prune_superseded_documents(session, bylaw_name="Target Bylaw", keep_latest=1, dry_run=True)

    superseded_ids = {e.id for e in result.entries}
    assert target_old.id in superseded_ids
    assert target_new.id not in superseded_ids
    assert other.id not in superseded_ids


# ---------------------------------------------------------------------------
# Tests: dry-run doesn't mutate
# ---------------------------------------------------------------------------

def test_dry_run_does_not_delete(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        _seed_doc(session, ingestion_timestamp=_ts(2023), file_hash="old")
        _seed_doc(session, ingestion_timestamp=_ts(2024), file_hash="new")
        result = prune_superseded_documents(session, keep_latest=1, dry_run=True)
        assert len(result.entries) == 1
        assert result.deleted_count == 0
        # Verify both docs still exist
        count = session.query(Document).count()
    assert count == 2


def test_dry_run_result_flag(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        _seed_doc(session, ingestion_timestamp=_ts(2023), file_hash="old")
        _seed_doc(session, ingestion_timestamp=_ts(2024), file_hash="new")
        result = prune_superseded_documents(session, keep_latest=1, dry_run=True)
    assert result.dry_run is True


# ---------------------------------------------------------------------------
# Tests: cascade removes children
# ---------------------------------------------------------------------------

def test_cascade_removes_all_children(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        # Superseded document with children
        old = _seed_doc(session, ingestion_timestamp=_ts(2023), file_hash="old")
        _seed_run(session, old.id)
        _seed_block(session, old.id)
        frag = _seed_fragment(session, old.id, citation_suffix="_old")
        _seed_table(session, old.id)
        _seed_xref(session, old.id, frag.id)
        _seed_image(session, old.id)

        # Keeper document (should be untouched)
        new = _seed_doc(session, ingestion_timestamp=_ts(2025), file_hash="new")
        _seed_run(session, new.id)
        _seed_block(session, new.id)
        _seed_fragment(session, new.id, citation_suffix="_new")

        old_id = old.id
        new_id = new.id

    with session_scope(db_url) as session:
        result = prune_superseded_documents(session, keep_latest=1, dry_run=False)
        assert result.deleted_count == 1

    # Verify children of the superseded doc are gone
    with session_scope(db_url) as session:
        assert session.get(Document, old_id) is None
        assert session.query(IngestionRun).filter_by(document_id=old_id).count() == 0
        assert session.query(PageBlock).filter_by(document_id=old_id).count() == 0
        assert session.query(SourceFragment).filter_by(document_id=old_id).count() == 0
        assert session.query(SourceTable).filter_by(document_id=old_id).count() == 0
        assert session.query(CrossReference).filter_by(document_id=old_id).count() == 0
        assert session.query(SourceImage).filter_by(document_id=old_id).count() == 0

    # Verify the keeper document and its children survive
    with session_scope(db_url) as session:
        assert session.get(Document, new_id) is not None
        assert session.query(IngestionRun).filter_by(document_id=new_id).count() == 1
        assert session.query(PageBlock).filter_by(document_id=new_id).count() == 1
        assert session.query(SourceFragment).filter_by(document_id=new_id).count() == 1


def test_cascade_counts_are_reported_in_entries(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        old = _seed_doc(session, ingestion_timestamp=_ts(2023), file_hash="old")
        _seed_run(session, old.id)
        _seed_run(session, old.id)
        _seed_block(session, old.id)
        frag = _seed_fragment(session, old.id)
        _seed_xref(session, old.id, frag.id)
        _seed_image(session, old.id)
        _seed_image(session, old.id)

        # Keeper
        _seed_doc(session, ingestion_timestamp=_ts(2025), file_hash="new")

        result = prune_superseded_documents(session, keep_latest=1, dry_run=True)

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.run_count == 2
    assert entry.block_count == 1
    assert entry.fragment_count == 1
    assert entry.cross_reference_count == 1
    assert entry.image_count == 2


def test_no_delete_when_nothing_superseded(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        _seed_doc(session, file_hash="only")
        result = prune_superseded_documents(session, keep_latest=1, dry_run=False)
    assert result.deleted_count == 0
    with session_scope(db_url) as session:
        assert session.query(Document).count() == 1
