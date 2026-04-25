from pathlib import Path

from layer1.db.base import PageBlock
from layer1.models.enums import BlockType
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.pipeline.audit import audit_document_pages, collect_page_audit_snapshots, score_page_risk, select_audit_pages
from layer1.pipeline.ingest import ingest_file


def test_select_audit_pages_prefers_high_risk(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    fixture = Path("tests/fixtures/synthetic_bylaw.txt")

    with session_scope(db_url) as session:
        document, _ = ingest_file(session, fixture, municipality="Sampleton", bylaw_name="Synthetic")
        snapshots = collect_page_audit_snapshots(session, document.id)

    selected = select_audit_pages(snapshots, sample_size=1)
    assert selected == [1]


def test_audit_document_pages_reports_deterministic_checks(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    fixture = Path("tests/fixtures/synthetic_bylaw.txt")

    with session_scope(db_url) as session:
        document, _ = ingest_file(session, fixture, municipality="Sampleton", bylaw_name="Synthetic")
        report = audit_document_pages(session, document.id, sample_size=2)

    assert report.audit_mode == "deterministic"
    assert report.sampled_pages
    first = report.page_results[0]
    assert first.deterministic_checks
    assert first.risk_score >= 0


def test_audit_ignores_header_footer_for_unaccounted_blocks():
    score, reasons, checks = score_page_risk(
        page_blocks=[
            PageBlock(
                id=1,
                document_id=1,
                page_number=1,
                block_type=BlockType.HEADER,
                bbox_json=None,
                reading_order=0,
                raw_text="Header",
                normalized_text="Header",
                is_boilerplate=False,
                parser_source="test",
                confidence=1.0,
                metadata_json={},
            ),
            PageBlock(
                id=2,
                document_id=1,
                page_number=1,
                block_type=BlockType.FOOTER,
                bbox_json=None,
                reading_order=1,
                raw_text="Footer",
                normalized_text="Footer",
                is_boilerplate=False,
                parser_source="test",
                confidence=1.0,
                metadata_json={},
            ),
        ],
        page_fragments=[],
        page_tables=[],
        page_cross_references=[],
    )
    assert score == 0
    assert "unaccounted non-boilerplate blocks" not in reasons
    assert not any(check.name == "unaccounted_blocks" for check in checks)
