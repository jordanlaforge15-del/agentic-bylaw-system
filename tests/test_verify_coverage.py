"""Unit tests for layer1.pipeline.verify_coverage."""
from pathlib import Path

import pytest

from layer1.db.base import (
    CrossReference,
    Document,
    PageBlock,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    utcnow,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import (
    BlockType,
    FragmentType,
    ParseStatus,
    ResolutionStatus,
)
from layer1.pipeline.verify_coverage import (
    _normalize,
    _text_overlap_ratio,
    _token_set,
    verify_document_coverage,
)


def test_normalize_collapses_whitespace():
    assert _normalize("  hello   world\n\t") == "hello world"


def test_token_set_returns_lowered_words():
    assert _token_set("Hello World") == {"hello", "world"}


def test_text_overlap_ratio_full_match():
    assert _text_overlap_ratio("hello world", ["hello world"]) == 1.0


def test_text_overlap_ratio_partial_match():
    ratio = _text_overlap_ratio("hello world foo bar", ["hello world"])
    assert 0.4 <= ratio <= 0.6


def test_text_overlap_ratio_no_match():
    assert _text_overlap_ratio("hello world", ["completely different"]) == 0.0


def test_text_overlap_ratio_empty_source():
    assert _text_overlap_ratio("", ["some text"]) == 1.0


def test_text_overlap_ratio_empty_fragments():
    assert _text_overlap_ratio("some text", []) == 0.0


def test_text_overlap_ratio_multiple_fragments():
    ratio = _text_overlap_ratio("hello world foo bar", ["hello world", "foo bar"])
    assert ratio == 1.0


def _seed_document(session, source_path: str, page_count: int = 3) -> Document:
    doc = Document(
        municipality="TestMunicipality",
        bylaw_name="Test Bylaw",
        source_path=source_path,
        file_hash="test-hash-001",
        mime_type="text/plain",
        page_count=page_count,
        parser_version="test",
        ingestion_timestamp=utcnow(),
    )
    session.add(doc)
    session.flush()
    return doc


def _seed_block(session, document_id: int, page: int, order: int, text: str, **kwargs) -> PageBlock:
    block = PageBlock(
        document_id=document_id,
        page_number=page,
        block_type=kwargs.get("block_type", BlockType.PARAGRAPH),
        reading_order=order,
        raw_text=text,
        is_boilerplate=kwargs.get("is_boilerplate", False),
        parser_source="test",
    )
    session.add(block)
    session.flush()
    return block


def _seed_fragment(session, document_id: int, page: int, text: str, block_ids=None, **kwargs) -> SourceFragment:
    frag = SourceFragment(
        document_id=document_id,
        fragment_type=kwargs.get("fragment_type", FragmentType.SECTION),
        citation_label=kwargs.get("citation_label"),
        citation_path=kwargs.get("citation_path"),
        page_start=page,
        page_end=page,
        reading_order_start=kwargs.get("reading_order_start", 0),
        text=text,
        parse_status=kwargs.get("parse_status", ParseStatus.PARSED),
        confidence=kwargs.get("confidence", 0.9),
        source_block_ids_json=block_ids or [],
    )
    session.add(frag)
    session.flush()
    return frag


def test_verify_full_coverage(tmp_path: Path):
    source_file = tmp_path / "bylaw.txt"
    source_file.write_text("Page one content\fPage two content\fPage three content")
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, str(source_file), page_count=3)
        for page in range(1, 4):
            text = f"Page {'one two three'.split()[page-1]} content"
            block = _seed_block(session, doc.id, page, 0, text)
            _seed_fragment(
                session, doc.id, page, text,
                block_ids=[block.id],
                citation_label=f"s{page}",
                citation_path=f"s{page}",
            )
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert report.document_id == doc.id
    assert report.summary.page_count == 3
    assert report.summary.pages_with_fragments == 3
    assert report.summary.pages_without_fragments == []
    assert report.summary.total_fragments == 3
    assert report.summary.parsed_fragments == 3
    assert report.summary.uncertain_fragments == 0
    assert report.summary.mean_page_overlap > 0.5
    assert report.summary.grade in ("A", "B")


def test_verify_detects_empty_page(tmp_path: Path):
    source_file = tmp_path / "bylaw.txt"
    source_file.write_text("Page one content\fPage two has text but no fragments\fPage three content")
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, str(source_file), page_count=3)
        for page in [1, 3]:
            text = f"Page {'one _ three'.split()[page-1]} content"
            block = _seed_block(session, doc.id, page, 0, text)
            _seed_fragment(
                session, doc.id, page, text,
                block_ids=[block.id],
                citation_label=f"s{page}",
                citation_path=f"s{page}",
            )
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert 2 in report.summary.pages_without_fragments
    empty_gaps = [g for g in report.gaps if g.gap_type == "empty_page"]
    assert len(empty_gaps) == 1
    assert empty_gaps[0].page == 2


def test_verify_detects_uncertain_fragments(tmp_path: Path):
    source_file = tmp_path / "bylaw.txt"
    source_file.write_text("Page one with uncertain content")
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, str(source_file), page_count=1)
        block = _seed_block(session, doc.id, 1, 0, "Page one with uncertain content")
        _seed_fragment(
            session, doc.id, 1, "Page one with uncertain content",
            block_ids=[block.id],
            parse_status=ParseStatus.UNCERTAIN,
            confidence=0.4,
        )
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert report.summary.uncertain_fragments == 1
    uncertain_gaps = [g for g in report.gaps if g.gap_type == "uncertain_fragments"]
    assert len(uncertain_gaps) == 1


def test_verify_detects_unresolved_cross_references(tmp_path: Path):
    source_file = tmp_path / "bylaw.txt"
    source_file.write_text("Section referencing another section")
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, str(source_file), page_count=1)
        block = _seed_block(session, doc.id, 1, 0, "Section referencing another section")
        frag = _seed_fragment(
            session, doc.id, 1, "Section referencing another section",
            block_ids=[block.id],
            citation_label="8.1",
            citation_path="8.1",
        )
        ref = CrossReference(
            document_id=doc.id,
            source_fragment_id=frag.id,
            raw_reference_text="Section 4.2",
            target_citation_guess="4.2",
            resolution_status=ResolutionStatus.UNRESOLVED,
            confidence=0.7,
            metadata_json={},
        )
        session.add(ref)
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert report.summary.unresolved_cross_references == 1
    xref_gaps = [g for g in report.gaps if g.gap_type == "unresolved_cross_references"]
    assert len(xref_gaps) == 1


def test_verify_detects_tables_without_cells(tmp_path: Path):
    source_file = tmp_path / "bylaw.txt"
    source_file.write_text("Page with a table")
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, str(source_file), page_count=1)
        block = _seed_block(session, doc.id, 1, 0, "Page with a table",
                          block_type=BlockType.TABLE_REGION)
        table = SourceTable(
            document_id=doc.id,
            caption="Table 1",
            page_start=1,
            page_end=1,
            parse_status=ParseStatus.PARSED,
            metadata_json={"source_block_id": block.id},
        )
        session.add(table)
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert report.summary.tables_without_cells == 1
    table_gaps = [g for g in report.gaps if g.gap_type == "empty_tables"]
    assert len(table_gaps) == 1


def test_verify_grade_degrades_with_gaps(tmp_path: Path):
    source_file = tmp_path / "bylaw.txt"
    pages = ["Content " * 20] * 5
    source_file.write_text("\f".join(pages))
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, str(source_file), page_count=5)
        block = _seed_block(session, doc.id, 1, 0, "Content " * 20)
        _seed_fragment(
            session, doc.id, 1, "Content " * 20,
            block_ids=[block.id],
            citation_label="s1",
            citation_path="s1",
        )
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert report.summary.grade in ("D", "F")
    assert len(report.summary.pages_without_fragments) >= 3


def test_structural_fragments_without_citation_not_flagged(tmp_path: Path):
    """HEADING, PROSE, LIST_ITEM, FOOTNOTE fragments with no citation_path must
    not produce a missing_citation_path gap — they are non-citable by design."""
    source_file = tmp_path / "bylaw.txt"
    source_file.write_text("Heading text\fProse text\fList item text\fFootnote text")
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, str(source_file), page_count=4)
        structural_types = [
            (FragmentType.HEADING, 1, "Heading text"),
            (FragmentType.PROSE, 2, "Prose text"),
            (FragmentType.LIST_ITEM, 3, "List item text"),
            (FragmentType.FOOTNOTE, 4, "Footnote text"),
        ]
        for ftype, page, text in structural_types:
            block = _seed_block(session, doc.id, page, 0, text)
            _seed_fragment(
                session, doc.id, page, text,
                block_ids=[block.id],
                fragment_type=ftype,
                # citation_path intentionally omitted (None)
                parse_status=ParseStatus.PARSED,
            )
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert report.summary.fragments_without_citation == 0
    citation_gaps = [g for g in report.gaps if g.gap_type == "missing_citation_path"]
    assert citation_gaps == []


def test_citable_fragment_without_citation_is_flagged(tmp_path: Path):
    """A SECTION fragment with parse_status=PARSED but no citation_path IS a real gap."""
    source_file = tmp_path / "bylaw.txt"
    source_file.write_text("Section text")
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, str(source_file), page_count=1)
        block = _seed_block(session, doc.id, 1, 0, "Section text")
        _seed_fragment(
            session, doc.id, 1, "Section text",
            block_ids=[block.id],
            fragment_type=FragmentType.SECTION,
            # citation_path intentionally omitted to simulate a real gap
            parse_status=ParseStatus.PARSED,
        )
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert report.summary.fragments_without_citation == 1
    citation_gaps = [g for g in report.gaps if g.gap_type == "missing_citation_path"]
    assert len(citation_gaps) == 1


def test_verify_nonexistent_document_raises(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        with pytest.raises(ValueError, match="not found"):
            verify_document_coverage(session, 9999)


def test_verify_missing_source_file(tmp_path: Path):
    """When the source file is missing, overlap defaults to 1.0 (nothing to miss)."""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _seed_document(session, "/nonexistent/path.txt", page_count=2)
        block = _seed_block(session, doc.id, 1, 0, "some text")
        _seed_fragment(
            session, doc.id, 1, "some text",
            block_ids=[block.id],
            citation_label="s1",
            citation_path="s1",
        )
        session.flush()
        report = verify_document_coverage(session, doc.id)

    assert report.summary.page_count == 2
    assert report.summary.mean_page_overlap == 1.0
    assert len(report.gaps) == 0
