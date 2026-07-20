from pathlib import Path

from layer1.db.base import (
    CrossReference,
    Document,
    PageBlock,
    SourceFragment,
    SourceTable,
    SourceTableCell,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import IngestionStatus, ParseStatus
from layer1.pipeline.export import document_to_dict
from layer1.pipeline.ingest import _ensure_fragment_coverage
from layer1.pipeline.ingest import ingest_file
from layer1.models.enums import BlockType, FragmentType
from layer1.models.schemas import PageBlockData


def test_ingests_synthetic_bylaw(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    fixture = Path("tests/fixtures/synthetic_bylaw.txt")

    with session_scope(db_url) as session:
        document, run = ingest_file(
            session,
            fixture,
            municipality="Sampleton",
            bylaw_name="Synthetic Zoning Bylaw",
        )
        assert run.status in {IngestionStatus.COMPLETED, IngestionStatus.COMPLETED_WITH_WARNINGS}
        document_id = document.id

    with session_scope(db_url) as session:
        assert session.get(Document, document_id).page_count == 2
        assert session.query(PageBlock).filter_by(document_id=document_id).count() > 0
        assert session.query(SourceFragment).filter_by(document_id=document_id).count() > 0
        assert session.query(SourceTable).filter_by(document_id=document_id).count() == 1
        assert session.query(SourceTableCell).count() > 0
        assert session.query(CrossReference).filter_by(document_id=document_id).count() >= 3
        exported = document_to_dict(session, document_id)
        assert exported["document"]["municipality"] == "Sampleton"


def test_uncertain_fragments_are_persisted(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    fixture = tmp_path / "uncertain.txt"
    fixture.write_text("Loose opening sentence without heading.\n", encoding="utf-8")

    with session_scope(db_url) as session:
        document, run = ingest_file(session, fixture)
        assert run.status in {IngestionStatus.COMPLETED, IngestionStatus.COMPLETED_WITH_WARNINGS}
        uncertain = (
            session.query(SourceFragment)
            .filter_by(document_id=document.id, parse_status=ParseStatus.UNCERTAIN)
            .all()
        )
        assert len(uncertain) == 1
        assert uncertain[0].text == "Loose opening sentence without heading."


def test_unaccounted_blocks_are_preserved_as_uncertain_fragments():
    blocks = [
        PageBlockData(
            page_number=1,
            block_type=BlockType.TABLE_REGION,
            reading_order=0,
            raw_text="Unstructured amendment history row",
            normalized_text="Unstructured amendment history row",
            parser_source="test",
        )
    ]
    fragments = _ensure_fragment_coverage(blocks, [], [])
    assert len(fragments) == 1
    assert fragments[0].fragment_type == FragmentType.PROSE
    assert fragments[0].parse_status == ParseStatus.UNCERTAIN
    assert fragments[0].metadata["fallback_unaccounted_block"] is True


def test_ingest_links_table_captions_for_profiled_bylaw(tmp_path: Path):
    """ABS-409: ingest_file runs the caption-linking pass for profiles that
    declare a table_caption_re — the caption fragment becomes citation-
    addressable and claims its table."""
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    fixture = tmp_path / "captioned_bylaw.txt"
    fixture.write_text(
        "Town of Testville Bylaw\n"
        "Part 1 Uses\n"
        "1.1 General\n"
        "Uses are regulated by this section.\n"
        "Table 7A: Permitted uses by zone (COR and DD)\n"
        "Table 1 | Use | COR; Row 1 | Restaurant use | P\n",
        encoding="utf-8",
    )

    with session_scope(db_url) as session:
        document, run = ingest_file(session, fixture, profile="halifax")
        assert run.status in {IngestionStatus.COMPLETED, IngestionStatus.COMPLETED_WITH_WARNINGS}
        caption = (
            session.query(SourceFragment)
            .filter(SourceFragment.document_id == document.id)
            .filter(SourceFragment.text.like("Table 7A:%"))
            .one()
        )
        assert caption.citation_label == "Table 7A"
        assert caption.citation_path is not None
        assert caption.citation_path.endswith("[Table 7A]")
        table = (
            session.query(SourceTable)
            .filter_by(document_id=document.id)
            .one()
        )
        assert table.parent_fragment_id == caption.id
        assert table.caption.startswith("Table 7A:")


def test_ingest_default_profile_skips_caption_linking(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    fixture = tmp_path / "captioned_bylaw.txt"
    fixture.write_text(
        "Town of Testville Bylaw\n"
        "Part 1 Uses\n"
        "Table 7A: Permitted uses by zone (COR and DD)\n"
        "Table 1 | Use | COR; Row 1 | Restaurant use | P\n",
        encoding="utf-8",
    )
    with session_scope(db_url) as session:
        document, _run = ingest_file(session, fixture, profile="default")
        caption = (
            session.query(SourceFragment)
            .filter(SourceFragment.document_id == document.id)
            .filter(SourceFragment.text.like("Table 7A:%"))
            .one()
        )
        assert caption.citation_path is None
        table = session.query(SourceTable).filter_by(document_id=document.id).one()
        assert table.parent_fragment_id is None
