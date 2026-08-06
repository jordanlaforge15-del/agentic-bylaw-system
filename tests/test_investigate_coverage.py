"""Unit tests for scripts/investigate_coverage.py.

Covers the pure logic — clustering, severity filtering, and markdown
rendering. The LLM-invocation path is not mocked here; smoke-testing it
end-to-end against the dev database is sufficient coverage for a
developer tool, and mocking subprocess transcripts would test the mock
more than the code.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    """Load scripts/investigate_coverage.py without putting scripts/ on sys.path."""
    project_root = Path(__file__).resolve().parent.parent
    src = project_root / "scripts" / "investigate_coverage.py"
    spec = importlib.util.spec_from_file_location("investigate_coverage", src)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["investigate_coverage"] = mod
    spec.loader.exec_module(mod)
    return mod


ic = _load_module()


# --------------------------------------------------------------------------- #
# filter_gaps                                                                 #
# --------------------------------------------------------------------------- #


def test_filter_gaps_keeps_requested_severities():
    gaps = [
        {"gap_type": "a", "severity": "high"},
        {"gap_type": "b", "severity": "medium"},
        {"gap_type": "c", "severity": "low"},
    ]
    kept = ic.filter_gaps(gaps, severities={"high", "medium"})
    assert [g["gap_type"] for g in kept] == ["a", "b"]


def test_filter_gaps_empty_when_no_match():
    gaps = [{"gap_type": "a", "severity": "low"}]
    assert ic.filter_gaps(gaps, severities={"high"}) == []


# --------------------------------------------------------------------------- #
# cluster_gaps                                                                #
# --------------------------------------------------------------------------- #


def test_cluster_gaps_groups_by_type():
    gaps = [
        {"gap_type": "empty_page", "severity": "high", "page": 5, "detail": "p5"},
        {"gap_type": "empty_page", "severity": "high", "page": 12, "detail": "p12"},
        {"gap_type": "uncertain_fragments", "severity": "medium", "page": 5, "detail": "u5"},
    ]
    clusters = ic.cluster_gaps(gaps, deep=False)
    assert len(clusters) == 2
    empties = next(c for c in clusters if c.gap_type == "empty_page")
    assert empties.pages == [5, 12]
    assert empties.count == 2
    assert empties.severity == "high"


def test_cluster_gaps_promotes_severity_to_highest_seen():
    gaps = [
        {"gap_type": "x", "severity": "low", "page": 1, "detail": "d"},
        {"gap_type": "x", "severity": "high", "page": 2, "detail": "d"},
        {"gap_type": "x", "severity": "medium", "page": 3, "detail": "d"},
    ]
    [cluster] = ic.cluster_gaps(gaps, deep=False)
    assert cluster.severity == "high"


def test_cluster_gaps_deep_mode_makes_one_cluster_per_gap():
    gaps = [
        {"gap_type": "empty_page", "severity": "high", "page": 5, "detail": "p5"},
        {"gap_type": "empty_page", "severity": "high", "page": 12, "detail": "p12"},
    ]
    clusters = ic.cluster_gaps(gaps, deep=True)
    assert len(clusters) == 2
    assert all(c.gap_type == "empty_page" for c in clusters)


def test_cluster_gaps_sorts_by_severity_then_count():
    gaps = [
        {"gap_type": "low_one", "severity": "low", "page": 1, "detail": "d"},
        {"gap_type": "high_one", "severity": "high", "page": 2, "detail": "d"},
        {"gap_type": "med_two_a", "severity": "medium", "page": 3, "detail": "d"},
        {"gap_type": "med_two_a", "severity": "medium", "page": 4, "detail": "d"},
    ]
    clusters = ic.cluster_gaps(gaps, deep=False)
    # Highest severity first; within the same severity, larger cluster first.
    assert [c.gap_type for c in clusters] == ["high_one", "med_two_a", "low_one"]


def test_cluster_gaps_caps_samples_and_details():
    gaps = [
        {
            "gap_type": "x",
            "severity": "medium",
            "page": p,
            "sample_text": f"sample {p}",
            "detail": f"detail {p}",
        }
        for p in range(1, 10)
    ]
    [cluster] = ic.cluster_gaps(gaps, deep=False)
    assert len(cluster.samples) == 3
    assert len(cluster.details) == 5
    assert cluster.count == 9  # count tracks page list, not capped lists


# --------------------------------------------------------------------------- #
# render_markdown                                                             #
# --------------------------------------------------------------------------- #


def _make_investigation(action: str, gap_type: str = "x") -> "ic.Investigation":
    cluster = ic.GapCluster(
        gap_type=gap_type,
        severity="high",
        pages=[1, 2, 3],
        details=[f"detail for {gap_type}"],
    )
    return ic.Investigation(
        cluster=cluster,
        hypothesis=f"hypothesis for {gap_type}",
        evidence="some evidence",
        suspected_files=["src/layer1/parsers/foo.py"],
        recommended_action=action,
        confidence="medium",
        rationale="why this action",
    )


def test_render_markdown_includes_summary_and_all_action_buckets():
    md = ic.render_markdown(
        doc_meta={"document_id": 5, "bylaw_name": "Test Bylaw", "municipality": "HRM"},
        coverage_summary={
            "grade": "B",
            "mean_page_overlap": 0.85,
            "page_count": 100,
            "pages_with_fragments": 99,
            "parsed_fragments": 500,
            "uncertain_fragments": 2,
            "total_tables": 10,
            "tables_without_cells": 0,
            "total_cross_references": 50,
            "resolved_cross_references": 48,
        },
        investigations=[
            _make_investigation("file-issue", "unaccounted_blocks"),
            _make_investigation("manual-review", "uncertain_fragments"),
            _make_investigation("accept-as-noise", "unresolved_cross_references"),
        ],
        skipped_clusters=[],
    )
    assert "Test Bylaw" in md
    assert "Grade:** B" in md
    assert "Worth filing" in md
    assert "Needs human review" in md
    assert "Acceptable noise" in md
    assert "unaccounted_blocks" in md
    assert "src/layer1/parsers/foo.py" in md


def test_render_markdown_shows_skipped_clusters():
    skipped = [ic.GapCluster(gap_type="z", severity="low", pages=[7])]
    md = ic.render_markdown(
        doc_meta={"document_id": 1},
        coverage_summary={"grade": "A"},
        investigations=[],
        skipped_clusters=skipped,
    )
    assert "Skipped" in md
    assert "`z`" in md


def test_render_markdown_renders_failed_investigations():
    cluster = ic.GapCluster(gap_type="x", severity="high", pages=[1])
    inv = ic.Investigation(cluster=cluster, error="claude timed out")
    md = ic.render_markdown(
        doc_meta={"document_id": 1},
        coverage_summary={"grade": "F"},
        investigations=[inv],
        skipped_clusters=[],
    )
    assert "Investigation failed" in md
    assert "claude timed out" in md


# --------------------------------------------------------------------------- #
# Linear promotion helpers                                                    #
# --------------------------------------------------------------------------- #


def test_build_linear_issue_title_is_stable_and_dedupable():
    cluster = ic.GapCluster(gap_type="empty_page", severity="high", pages=[1, 2, 3])
    inv = ic.Investigation(cluster=cluster)
    title = ic.build_linear_issue_title(inv, {"document_id": 7})
    # Stable prefix makes filtering trivial; doc id + gap type lets a
    # human spot duplicates in the Linear list view.
    assert title.startswith("[coverage] ")
    assert "empty_page" in title
    assert "doc 7" in title


def test_build_linear_issue_body_includes_hypothesis_evidence_files():
    cluster = ic.GapCluster(
        gap_type="missing_citation_path",
        severity="medium",
        pages=[],
        details=["531 parsed fragments lack a citation_path."],
    )
    inv = ic.Investigation(
        cluster=cluster,
        hypothesis="citation_path() silently returns None for label-less fragments.",
        evidence="hierarchy.py line 244 only demotes LOW_LEVEL types.",
        suspected_files=["src/layer1/pipeline/hierarchy.py"],
        recommended_action="file-issue",
        confidence="medium",
        rationale="Real logic hole; safe to file.",
    )
    body = ic.build_linear_issue_body(
        inv,
        doc_meta={"document_id": 5, "bylaw_name": "Test Bylaw"},
        coverage_summary={"grade": "A", "mean_page_overlap": 1.0},
    )
    assert "Test Bylaw" in body
    assert "hierarchy.py line 244" in body
    assert "src/layer1/pipeline/hierarchy.py" in body
    assert "531 parsed fragments" in body
    # Footer should make the auto-filed origin obvious.
    assert "investigate_coverage.py" in body


def test_render_markdown_includes_promotion_results_when_present():
    md = ic.render_markdown(
        doc_meta={"document_id": 5},
        coverage_summary={"grade": "A"},
        investigations=[],
        skipped_clusters=[],
        promotion_results=[
            {
                "gap_type": "missing_citation_path",
                "status": "created",
                "identifier": "ABS-999",
                "url": "https://linear.app/foo/issue/ABS-999",
            },
            {
                "gap_type": "orphan_fragments",
                "status": "skipped-low-confidence",
            },
        ],
    )
    assert "Linear promotion results" in md
    assert "ABS-999" in md
    assert "skipped-low-confidence" in md


def test_render_markdown_omits_promotion_section_when_empty():
    md = ic.render_markdown(
        doc_meta={"document_id": 5},
        coverage_summary={"grade": "A"},
        investigations=[],
        skipped_clusters=[],
        promotion_results=[],
    )
    assert "Linear promotion results" not in md


# --------------------------------------------------------------------------- #
# promote_to_linear's lazy LinearClient import (ABS-446)                       #
# --------------------------------------------------------------------------- #


def test_promote_to_linear_imports_linear_client():
    """The Linear client moved out of the retired night_manager package.

    `promote_to_linear` imports `scripts.linear_client.LinearClient` lazily,
    so a broken import path would only surface at runtime behind
    `--promote-to-linear`. Resolve the exact module the function names and
    assert the symbol is the class it expects.
    """
    import inspect

    source = inspect.getsource(ic.promote_to_linear)
    assert "from scripts.linear_client import LinearClient" in source
    assert "night_manager" not in source

    from scripts.linear_client import LinearClient

    assert inspect.isclass(LinearClient)
    # The two methods promote_to_linear actually calls.
    assert callable(LinearClient.get_team_id)
    assert callable(LinearClient.create_issue)
