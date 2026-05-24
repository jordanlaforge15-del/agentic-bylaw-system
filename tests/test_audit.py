from pathlib import Path

from layer1.db.base import PageBlock
from layer1.db.base import Document, SourceFragment
from layer1.models.enums import BlockType, FragmentType, ParseStatus
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


def test_audit_includes_parent_fragment_context(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Sampleton",
            bylaw_name="Synthetic",
            source_path=str(Path("tests/fixtures/synthetic_bylaw.txt")),
            file_hash="x",
            mime_type="text/plain",
        )
        session.add(document)
        session.flush()
        parent = SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.PROSE,
            page_start=1,
            page_end=1,
            text='"View Plane" means any one of the following:',
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[1],
            metadata_json={},
        )
        child = SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            page_start=2,
            page_end=2,
            text="(c) View Plane 3 means a protected sightline.",
            parse_status=ParseStatus.UNCERTAIN,
            parent_fragment_id=1,
            source_block_ids_json=[2],
            metadata_json={},
        )
        session.add(parent)
        session.flush()
        child.parent_fragment_id = parent.id
        session.add(child)
        session.flush()
        snapshots = collect_page_audit_snapshots(session, document.id, include_source_text=False)
    page2 = next(snapshot for snapshot in snapshots if snapshot.page_number == 2)
    fragment = page2.fragments[0]
    assert fragment["parent_fragment_context"]["id"] == 1
    assert fragment["parent_fragment_context"]["visible_on_current_page"] is False
    assert fragment["continuation_from_prior_page"] is True
    assert fragment["ancestor_chain"][0]["id"] == 1


# --------------------------------------------------------------------- ABS-109
# The --llm audit path now routes through Claude Code headless mode
# (`claude -p --json-schema`) instead of OpenAI's responses API. Mock the
# subprocess and verify the auditor produces a valid LlmAuditReview.

def test_abs109_claude_code_auditor_review_returns_valid_review():
    """Smoke test the in-process plumbing — auditor builds prompt, calls
    call_claude_p_with_schema (mocked), validates the result."""
    import json
    from unittest.mock import MagicMock, patch
    from layer1.pipeline.audit import ClaudeCodeLayer1Auditor
    from layer1.models.schemas import (
        DeterministicPageCheck,
        LlmAuditReview,
        PageAuditSnapshot,
    )

    snapshot = PageAuditSnapshot(
        page_number=3,
        risk_score=2,
        risk_reasons=["unaccounted_blocks"],
        deterministic_checks=[
            DeterministicPageCheck(
                name="block_coverage", severity="warn", detail="2 blocks unmapped",
            ),
        ],
        page_block_count=0,
        fragment_count=0,
        table_count=0,
        cross_reference_count=0,
        source_page_text="Section 7. Use restrictions...",
        page_blocks=[],
        fragments=[],
        tables=[],
        cross_references=[],
    )

    fake_response = json.dumps({
        "type": "result", "is_error": False, "result": "",
        "structured_output": {
            "verdict": "ok_with_concerns",
            "confidence": 0.8,
            "summary": "Two paragraph blocks not mapped to fragments.",
            "suspected_issues": ["unmapped_blocks"],
            "recommended_human_review": True,
        },
    })
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_response, stderr="")
        auditor = ClaudeCodeLayer1Auditor(model="advisory-only")
        review = auditor.review(snapshot)

    assert isinstance(review, LlmAuditReview)
    assert review.verdict == "ok_with_concerns"
    assert review.confidence == 0.8
    assert review.recommended_human_review is True

    # The CLI invocation should carry the audit JSON schema as --json-schema.
    cmd = mock_run.call_args.args[0]
    assert "--json-schema" in cmd
    schema = json.loads(cmd[cmd.index("--json-schema") + 1])
    assert "verdict" in schema["properties"]
    assert "recommended_human_review" in schema["required"]


def test_abs109_openai_auditor_name_still_imports_for_back_compat():
    """Any external script that imported OpenAILayer1Auditor should still
    resolve to the renamed Claude Code class — same instance behavior."""
    from layer1.pipeline import audit as audit_mod
    assert audit_mod.OpenAILayer1Auditor is audit_mod.ClaudeCodeLayer1Auditor
