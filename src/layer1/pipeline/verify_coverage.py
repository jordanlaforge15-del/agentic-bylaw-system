"""Automated verification of bylaw ingest coverage.

Compares the source PDF text (page by page) against what was persisted
in the normalized database after ingestion.  Produces a structured
coverage report with per-page overlap ratios, gap classifications, and
an overall letter grade.

Usage (programmatic)::

    from layer1.pipeline.verify_coverage import verify_document_coverage
    report = verify_document_coverage(session, document_id)

Usage (CLI)::

    layer1 verify-coverage <document_id> [--out report.json]
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Sequence

from sqlalchemy.orm import Session

from layer1.db.base import (
    CrossReference,
    Document,
    PageBlock,
    SourceFragment,
    SourceTable,
    SourceTableCell,
)
from layer1.models.coverage import (
    ComparisonSection,
    CoverageGap,
    CoverageSummary,
    DocumentCoverageReport,
    GapDelta,
    PageCoverage,
)
from layer1.models.enums import BlockType, FragmentType, ParseStatus, ResolutionStatus
from layer1.pipeline.audit import load_source_page_text


_WHITESPACE_RE = re.compile(r"\s+")

# Fragment types that are individually citable and must have a citation_path.
# Structural types (HEADING, PROSE, LIST_ITEM, FOOTNOTE) intentionally have
# citation_path=None and must not be flagged as gaps.
_CITABLE_FRAGMENT_TYPES: frozenset[FragmentType] = frozenset({
    FragmentType.PART,
    FragmentType.SECTION,
    FragmentType.SUBSECTION,
    FragmentType.CLAUSE,
    FragmentType.SUBCLAUSE,
    FragmentType.SCHEDULE,
    FragmentType.APPENDIX,
})


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _token_set(text: str) -> set[str]:
    return set(_normalize(text).split())


def _text_overlap_ratio(source_text: str, fragment_texts: Sequence[str]) -> float:
    source_tokens = _token_set(source_text)
    if not source_tokens:
        return 1.0
    fragment_tokens: set[str] = set()
    for text in fragment_texts:
        fragment_tokens.update(_token_set(text))
    if not fragment_tokens:
        return 0.0
    return len(source_tokens & fragment_tokens) / len(source_tokens)


def verify_document_coverage(
    session: Session,
    document_id: int,
    *,
    low_coverage_threshold: float = 0.3,
) -> DocumentCoverageReport:
    document = session.get(Document, document_id)
    if not document:
        raise ValueError(f"Document {document_id} not found")

    page_count = document.page_count or 0
    all_pages = list(range(1, page_count + 1))

    fragments = (
        session.query(SourceFragment)
        .filter_by(document_id=document_id)
        .order_by(SourceFragment.page_start)
        .all()
    )
    blocks = (
        session.query(PageBlock)
        .filter_by(document_id=document_id)
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .all()
    )
    tables = (
        session.query(SourceTable)
        .filter_by(document_id=document_id)
        .order_by(SourceTable.page_start)
        .all()
    )
    table_ids = [t.id for t in tables]
    cells_by_table: dict[int, list[SourceTableCell]] = defaultdict(list)
    if table_ids:
        for cell in (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id.in_(table_ids))
            .all()
        ):
            cells_by_table[cell.table_id].append(cell)
    cross_refs = (
        session.query(CrossReference)
        .filter_by(document_id=document_id)
        .all()
    )

    source_text_by_page = load_source_page_text(document.source_path, all_pages)

    fragments_by_page: dict[int, list[SourceFragment]] = defaultdict(list)
    for f in fragments:
        for p in range(f.page_start, f.page_end + 1):
            fragments_by_page[p].append(f)

    blocks_by_page: dict[int, list[PageBlock]] = defaultdict(list)
    for b in blocks:
        blocks_by_page[b.page_number].append(b)

    tables_by_page: dict[int, list[SourceTable]] = defaultdict(list)
    for t in tables:
        for p in range(t.page_start, t.page_end + 1):
            tables_by_page[p].append(t)

    refs_by_page: dict[int, list[CrossReference]] = defaultdict(list)
    for f in fragments:
        for ref in cross_refs:
            if ref.source_fragment_id == f.id:
                refs_by_page[f.page_start].append(ref)

    page_details: list[PageCoverage] = []
    all_gaps: list[CoverageGap] = []
    overlap_values: list[float] = []
    pages_with_frags: set[int] = set()
    pages_without_frags: list[int] = []
    low_coverage_pages: list[int] = []

    for page_num in all_pages:
        page_frags = fragments_by_page.get(page_num, [])
        page_blocks = blocks_by_page.get(page_num, [])
        page_tables = tables_by_page.get(page_num, [])
        page_refs = refs_by_page.get(page_num, [])
        source_text = source_text_by_page.get(page_num, "")

        fragment_texts = [f.text for f in page_frags if f.text]
        overlap = _text_overlap_ratio(source_text, fragment_texts)
        overlap_values.append(overlap)

        uncertain_count = sum(
            1 for f in page_frags if f.parse_status == ParseStatus.UNCERTAIN
        )

        accounted_block_ids: set[int] = set()
        for f in page_frags:
            accounted_block_ids.update(f.source_block_ids_json or [])
        for t in page_tables:
            meta = t.metadata_json or {}
            src_id = meta.get("source_block_id")
            if src_id:
                accounted_block_ids.add(src_id)
            src_ids = meta.get("source_block_ids")
            if isinstance(src_ids, list):
                accounted_block_ids.update(i for i in src_ids if i)

        non_boilerplate_types = {BlockType.HEADER, BlockType.FOOTER, BlockType.TABLE_REGION}
        unaccounted = [
            b for b in page_blocks
            if not b.is_boilerplate
            and b.block_type not in non_boilerplate_types
            and b.id not in accounted_block_ids
        ]

        page_gaps: list[CoverageGap] = []

        if page_frags:
            pages_with_frags.add(page_num)
        elif source_text.strip():
            pages_without_frags.append(page_num)
            page_gaps.append(CoverageGap(
                gap_type="empty_page",
                severity="high",
                page=page_num,
                detail=f"Page {page_num} has source text but no fragments.",
                sample_text=source_text[:200] if source_text else None,
            ))

        if overlap < low_coverage_threshold and source_text.strip():
            low_coverage_pages.append(page_num)
            page_gaps.append(CoverageGap(
                gap_type="low_text_coverage",
                severity="high",
                page=page_num,
                detail=f"Page {page_num} text overlap is {overlap:.0%} (below {low_coverage_threshold:.0%} threshold).",
            ))

        if uncertain_count > 0:
            page_gaps.append(CoverageGap(
                gap_type="uncertain_fragments",
                severity="medium",
                page=page_num,
                detail=f"Page {page_num} has {uncertain_count} uncertain fragment(s).",
            ))

        if unaccounted:
            samples = [b.raw_text[:120] for b in unaccounted[:3]]
            page_gaps.append(CoverageGap(
                gap_type="unaccounted_blocks",
                severity="high",
                page=page_num,
                detail=f"Page {page_num} has {len(unaccounted)} unaccounted non-boilerplate block(s).",
                sample_text=" | ".join(samples),
            ))

        page_details.append(PageCoverage(
            page_number=page_num,
            source_char_count=len(source_text),
            fragment_char_count=sum(len(t) for t in fragment_texts),
            overlap_ratio=round(overlap, 4),
            fragment_count=len(page_frags),
            table_count=len(page_tables),
            cross_reference_count=len(page_refs),
            uncertain_fragment_count=uncertain_count,
            unaccounted_block_count=len(unaccounted),
            gaps=page_gaps,
        ))
        all_gaps.extend(page_gaps)

    # Document-level gap analysis
    # Only flag citable fragment types — structural types (HEADING, PROSE,
    # LIST_ITEM, FOOTNOTE) intentionally have citation_path=None by design.
    fragments_no_citation = [
        f for f in fragments
        if not f.citation_path
        and f.parse_status == ParseStatus.PARSED
        and f.fragment_type in _CITABLE_FRAGMENT_TYPES
    ]
    if fragments_no_citation:
        all_gaps.append(CoverageGap(
            gap_type="missing_citation_path",
            severity="medium",
            detail=f"{len(fragments_no_citation)} parsed fragment(s) lack a citation_path.",
        ))

    orphan_normal_types = {"part", "section", "schedule", "appendix", "heading", "prose"}
    orphan_fragments = [
        f for f in fragments
        if f.parent_fragment_id is None
        and f.fragment_type.value not in orphan_normal_types
        and f.parse_status != ParseStatus.UNCERTAIN
    ]
    if orphan_fragments:
        all_gaps.append(CoverageGap(
            gap_type="orphan_fragments",
            severity="medium",
            detail=f"{len(orphan_fragments)} non-root fragment(s) have no parent.",
        ))

    tables_no_cells = [t for t in tables if not cells_by_table.get(t.id)]
    if tables_no_cells:
        all_gaps.append(CoverageGap(
            gap_type="empty_tables",
            severity="medium",
            detail=f"{len(tables_no_cells)} table(s) have no extracted cells.",
        ))

    unresolved_refs = [
        r for r in cross_refs
        if r.resolution_status != ResolutionStatus.RESOLVED
    ]
    if unresolved_refs:
        all_gaps.append(CoverageGap(
            gap_type="unresolved_cross_references",
            severity="low",
            detail=f"{len(unresolved_refs)} of {len(cross_refs)} cross-reference(s) are unresolved.",
        ))

    mean_overlap = (
        sum(overlap_values) / len(overlap_values) if overlap_values else 0.0
    )

    summary = CoverageSummary(
        page_count=page_count,
        pages_with_fragments=len(pages_with_frags),
        pages_without_fragments=pages_without_frags,
        total_fragments=len(fragments),
        parsed_fragments=sum(1 for f in fragments if f.parse_status == ParseStatus.PARSED),
        uncertain_fragments=sum(1 for f in fragments if f.parse_status == ParseStatus.UNCERTAIN),
        fragments_without_citation=len(fragments_no_citation),
        orphan_fragments=len(orphan_fragments),
        total_tables=len(tables),
        tables_without_cells=len(tables_no_cells),
        total_cross_references=len(cross_refs),
        resolved_cross_references=sum(
            1 for r in cross_refs if r.resolution_status == ResolutionStatus.RESOLVED
        ),
        unresolved_cross_references=len(unresolved_refs),
        mean_page_overlap=round(mean_overlap, 4),
        low_coverage_pages=low_coverage_pages,
        grade=_compute_grade(
            mean_overlap=mean_overlap,
            page_count=page_count,
            pages_without_fragments=len(pages_without_frags),
            uncertain_ratio=(
                sum(1 for f in fragments if f.parse_status == ParseStatus.UNCERTAIN)
                / len(fragments) if fragments else 0.0
            ),
        ),
    )

    return DocumentCoverageReport(
        document_id=document_id,
        municipality=document.municipality,
        bylaw_name=document.bylaw_name,
        summary=summary,
        page_details=page_details,
        gaps=all_gaps,
    )


def _compute_grade(
    *,
    mean_overlap: float,
    page_count: int,
    pages_without_fragments: int,
    uncertain_ratio: float,
) -> str:
    score = 0.0
    score += mean_overlap * 50

    if page_count > 0:
        coverage_ratio = 1.0 - (pages_without_fragments / page_count)
        score += coverage_ratio * 30
    else:
        score += 30

    score += (1.0 - uncertain_ratio) * 20

    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 65:
        return "C"
    if score >= 50:
        return "D"
    return "F"


def compare_coverage_reports(
    old: DocumentCoverageReport,
    new: DocumentCoverageReport,
) -> ComparisonSection:
    """Compare two coverage reports for the same bylaw re-ingested after a parser fix.

    Gaps are matched by (gap_type, page) key.  A gap is resolved when that key
    exists only in *old*; introduced when it exists only in *new*; unchanged when
    it appears in both (possibly with a different count).
    """
    from collections import defaultdict

    def _count_by_type(gaps: list[CoverageGap]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for g in gaps:
            counts[g.gap_type] += 1
        return counts

    old_counts = _count_by_type(old.gaps)
    new_counts = _count_by_type(new.gaps)

    all_types = sorted(set(old_counts) | set(new_counts))

    gaps_resolved: list[GapDelta] = []
    gaps_introduced: list[GapDelta] = []
    gaps_unchanged: list[GapDelta] = []

    for gap_type in all_types:
        old_n = old_counts.get(gap_type, 0)
        new_n = new_counts.get(gap_type, 0)
        delta = GapDelta(gap_type=gap_type, old_count=old_n, new_count=new_n)
        if old_n > 0 and new_n == 0:
            gaps_resolved.append(delta)
        elif old_n == 0 and new_n > 0:
            gaps_introduced.append(delta)
        else:
            gaps_unchanged.append(delta)

    def _fmt_float(new_val: float, old_val: float) -> str:
        d = new_val - old_val
        return f"+{d:.2f}" if d >= 0 else f"{d:.2f}"

    def _fmt_int(new_val: int, old_val: int) -> str:
        d = new_val - old_val
        return f"+{d}" if d >= 0 else str(d)

    summary_deltas = {
        "mean_page_overlap": _fmt_float(new.summary.mean_page_overlap, old.summary.mean_page_overlap),
        "pages_with_fragments": _fmt_int(new.summary.pages_with_fragments, old.summary.pages_with_fragments),
        "parsed_fragments": _fmt_int(new.summary.parsed_fragments, old.summary.parsed_fragments),
        "uncertain_fragments": _fmt_int(new.summary.uncertain_fragments, old.summary.uncertain_fragments),
    }

    return ComparisonSection(
        old_document_id=old.document_id,
        old_grade=old.summary.grade,
        new_grade=new.summary.grade,
        gaps_resolved=gaps_resolved,
        gaps_introduced=gaps_introduced,
        gaps_unchanged=gaps_unchanged,
        summary_deltas=summary_deltas,
    )
