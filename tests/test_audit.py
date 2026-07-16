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


def test_abs114_session_not_in_transaction_during_llm_loop(tmp_path: Path):
    """DB transaction is committed before the LLM review loop so that Postgres
    idle-in-transaction timeout cannot kill the connection mid-audit (ABS-114)."""
    from unittest.mock import patch
    from layer1.db.init_db import create_all
    from layer1.db.session import session_scope
    from layer1.models.schemas import LlmAuditReview
    from layer1.pipeline.audit import audit_document_pages, ClaudeCodeLayer1Auditor
    from layer1.pipeline.ingest import ingest_file

    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    fixture = Path("tests/fixtures/synthetic_bylaw.txt")

    fake_review = LlmAuditReview(
        verdict="ok",
        confidence=0.9,
        summary="All good",
        suspected_issues=[],
        recommended_human_review=False,
    )

    in_transaction_during_review: list[bool] = []

    with session_scope(db_url) as session:
        document, _ = ingest_file(session, fixture, municipality="Sampleton", bylaw_name="Synthetic")

        def stub_review(snapshot):
            in_transaction_during_review.append(session.in_transaction())
            return fake_review

        with patch.object(ClaudeCodeLayer1Auditor, "review", side_effect=stub_review):
            audit_document_pages(session, document.id, sample_size=2, use_llm=True)

    assert in_transaction_during_review, "reviewer was never called — sample should select ≥1 page"
    assert not any(in_transaction_during_review), (
        "session must not hold an active transaction while the LLM reviewer runs "
        "(Postgres idle-in-transaction timeout would kill it after ~60 s per page)"
    )


def test_abs109_openai_auditor_name_still_imports_for_back_compat():
    """Any external script that imported OpenAILayer1Auditor should still
    resolve to the renamed Claude Code class — same instance behavior."""
    from layer1.pipeline import audit as audit_mod
    assert audit_mod.OpenAILayer1Auditor is audit_mod.ClaudeCodeLayer1Auditor


# --------------------------------------------------------------------- ABS-115
# Non-normative amendment-history pages should not dominate the audit sample.

def test_abs115_amendment_history_page_gets_discounted_score():
    """An amendment-history page with many uncertain fragments should score
    far below a normative page with fewer uncertain fragments."""
    from layer1.models.schemas import PageAuditSnapshot

    # Build a high-signal amendment-history page: schedule root + amendment text + many uncertain fragments
    amendment_fragments = [
        SourceFragment(
            id=100 + i,
            document_id=1,
            fragment_type=FragmentType.SCHEDULE,
            citation_label="Schedule A",
            citation_path="Schedule A",
            parent_fragment_id=None,
            page_start=10,
            page_end=10,
            text="Schedule A — Amendment History" if i == 0 else f"(RC-May 9/{24 + i})",
            parse_status=ParseStatus.UNCERTAIN,
            source_block_ids_json=[100 + i],
            metadata_json={},
        )
        for i in range(30)
    ]

    # Normative page with modest uncertain fragments
    normative_fragments = [
        SourceFragment(
            id=200 + i,
            document_id=1,
            fragment_type=FragmentType.SECTION,
            citation_label=f"Section {i + 1}",
            citation_path=f"Part 1 > Section {i + 1}",
            parent_fragment_id=None,
            page_start=5,
            page_end=5,
            text=f"No person shall erect a building exceeding {10 + i} metres.",
            parse_status=ParseStatus.UNCERTAIN,
            source_block_ids_json=[200 + i],
            metadata_json={},
        )
        for i in range(5)
    ]

    amendment_score, amendment_reasons, amendment_checks = score_page_risk(
        page_blocks=[],
        page_fragments=amendment_fragments,
        page_tables=[],
        page_cross_references=[],
    )

    normative_score, normative_reasons, _ = score_page_risk(
        page_blocks=[],
        page_fragments=normative_fragments,
        page_tables=[],
        page_cross_references=[],
    )

    # The normative page (5 uncertain fragments) should outscore the
    # amendment-history page (30 uncertain fragments) after discount
    assert normative_score > amendment_score, (
        f"Normative score ({normative_score}) should exceed amendment score ({amendment_score})"
    )
    assert any("non-normative" in r for r in amendment_reasons)
    assert any(c.name == "non_normative_discount" for c in amendment_checks)


def test_abs115_select_audit_pages_prefers_normative_over_amendment():
    """select_audit_pages with a mix of amendment-history and normative snapshots
    should rank normative pages first."""
    from layer1.models.schemas import PageAuditSnapshot

    # Simulate: 3 amendment pages with high raw risk, 2 normative with moderate risk
    amendment_fragments_per_page = [
        [
            SourceFragment(
                id=1000 + page * 100 + i,
                document_id=1,
                fragment_type=FragmentType.APPENDIX,
                citation_label="Appendix A",
                citation_path="Appendix A",
                parent_fragment_id=None,
                page_start=200 + page,
                page_end=200 + page,
                text="Amendment History Record" if i == 0 else f"Case {1000 + i}",
                parse_status=ParseStatus.UNCERTAIN,
                source_block_ids_json=[1000 + page * 100 + i],
                metadata_json={},
            )
            for i in range(40)
        ]
        for page in range(3)
    ]

    normative_fragments_per_page = [
        [
            SourceFragment(
                id=5000 + page * 100 + i,
                document_id=1,
                fragment_type=FragmentType.SECTION,
                citation_label=f"Section {page * 10 + i}",
                citation_path=f"Part 2 > Section {page * 10 + i}",
                parent_fragment_id=None,
                page_start=50 + page,
                page_end=50 + page,
                text=f"The minimum setback shall be {3 + i} metres.",
                parse_status=ParseStatus.UNCERTAIN,
                source_block_ids_json=[5000 + page * 100 + i],
                metadata_json={},
            )
            for i in range(8)
        ]
        for page in range(2)
    ]

    snapshots = []
    for page_idx, frags in enumerate(amendment_fragments_per_page):
        risk_score, risk_reasons, checks = score_page_risk(
            page_blocks=[], page_fragments=frags, page_tables=[], page_cross_references=[],
        )
        snapshots.append(PageAuditSnapshot(
            page_number=200 + page_idx,
            risk_score=risk_score,
            risk_reasons=risk_reasons,
            deterministic_checks=checks,
            page_block_count=0,
            fragment_count=len(frags),
            table_count=0,
            cross_reference_count=0,
        ))

    for page_idx, frags in enumerate(normative_fragments_per_page):
        risk_score, risk_reasons, checks = score_page_risk(
            page_blocks=[], page_fragments=frags, page_tables=[], page_cross_references=[],
        )
        snapshots.append(PageAuditSnapshot(
            page_number=50 + page_idx,
            risk_score=risk_score,
            risk_reasons=risk_reasons,
            deterministic_checks=checks,
            page_block_count=0,
            fragment_count=len(frags),
            table_count=0,
            cross_reference_count=0,
        ))

    selected = select_audit_pages(snapshots, sample_size=5)

    # Both normative pages should appear before the amendment pages
    normative_pages = {50, 51}
    amendment_pages = {200, 201, 202}
    assert normative_pages.issubset(set(selected[:2])), (
        f"Top 2 should be normative pages {normative_pages}, got {selected}"
    )
    # With sample_size=3 (realistic budget), amendment pages should not dominate
    selected_3 = select_audit_pages(snapshots, sample_size=3)
    amendment_in_sample_3 = amendment_pages & set(selected_3)
    assert len(amendment_in_sample_3) <= 1, (
        f"At most 1 amendment page expected in sample of 3, got {amendment_in_sample_3} from {selected_3}"
    )


def test_abs115_normative_schedule_page_not_penalized():
    """A schedule page with normative sub-structure (sections/clauses) should
    NOT be discounted — only amendment-history content gets the penalty."""
    normative_schedule_fragments = [
        SourceFragment(
            id=300,
            document_id=1,
            fragment_type=FragmentType.SCHEDULE,
            citation_label="Schedule 15",
            citation_path="Schedule 15",
            parent_fragment_id=None,
            page_start=80,
            page_end=80,
            text="Schedule 15 — Maximum Building Heights",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[300],
            metadata_json={},
        ),
        SourceFragment(
            id=301,
            document_id=1,
            fragment_type=FragmentType.CLAUSE,
            citation_label="1",
            citation_path="Schedule 15 > 1",
            parent_fragment_id=300,
            page_start=80,
            page_end=80,
            text="The maximum building height in the R-1 zone is 10 metres.",
            parse_status=ParseStatus.UNCERTAIN,
            source_block_ids_json=[301],
            metadata_json={},
        ),
        SourceFragment(
            id=302,
            document_id=1,
            fragment_type=FragmentType.CLAUSE,
            citation_label="2",
            citation_path="Schedule 15 > 2",
            parent_fragment_id=300,
            page_start=80,
            page_end=80,
            text="The maximum building height in the R-2 zone is 12 metres.",
            parse_status=ParseStatus.UNCERTAIN,
            source_block_ids_json=[302],
            metadata_json={},
        ),
    ]

    score, reasons, checks = score_page_risk(
        page_blocks=[],
        page_fragments=normative_schedule_fragments,
        page_tables=[],
        page_cross_references=[],
    )

    # Should NOT be discounted — clauses make it normative
    assert not any("non-normative" in r for r in reasons)
    assert not any(c.name == "non_normative_discount" for c in checks)
    # 2 uncertain fragments * 3 = 6 base score, no discount
    assert score == 6
