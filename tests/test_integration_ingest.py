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
from layer1.pipeline.ingest import ingest_file


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
