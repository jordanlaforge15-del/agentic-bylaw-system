from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from layer1.config import get_settings
from layer1.db.base import CrossReference, Document, PageBlock, SourceFragment, SourceTable, SourceTableCell
from layer1.models.enums import BlockType, FragmentType, ParseStatus, ResolutionStatus
from layer1.models.schemas import (
    DeterministicPageCheck,
    DocumentAuditReport,
    LlmAuditReview,
    PageAuditResult,
    PageAuditSnapshot,
)

NON_FRAGMENT_BLOCK_TYPES = {BlockType.TABLE_REGION, BlockType.HEADER, BlockType.FOOTER}

_NON_NORMATIVE_TYPES = {FragmentType.SCHEDULE, FragmentType.APPENDIX}

_AMENDMENT_HISTORY_RE = re.compile(
    r"amendment\s*hist|schedule\s*a\b.*amendment|amend.*record|history\s*of\s*amend",
    re.IGNORECASE,
)


def audit_document_pages(
    session: Session,
    document_id: int,
    *,
    page_numbers: list[int] | None = None,
    sample_size: int = 5,
    include_source_text: bool = True,
    use_llm: bool = False,
    llm_model: str | None = None,
) -> DocumentAuditReport:
    document = session.get(Document, document_id)
    if not document:
        raise ValueError(f"Document {document_id} not found")

    snapshots = collect_page_audit_snapshots(session, document_id, include_source_text=include_source_text)
    by_page = {snapshot.page_number: snapshot for snapshot in snapshots}
    selected_pages = page_numbers or select_audit_pages(snapshots, sample_size=sample_size)
    selected_snapshots = [by_page[page] for page in selected_pages if page in by_page]

    reviewer = ClaudeCodeLayer1Auditor(model=llm_model or get_settings().audit_llm_model) if use_llm else None
    page_results: list[PageAuditResult] = []
    for snapshot in selected_snapshots:
        llm_review = reviewer.review(snapshot) if reviewer else None
        page_results.append(
            PageAuditResult(
                page_number=snapshot.page_number,
                risk_score=snapshot.risk_score,
                risk_reasons=snapshot.risk_reasons,
                deterministic_checks=snapshot.deterministic_checks,
                llm_review=llm_review,
            )
        )

    return DocumentAuditReport(
        document_id=document_id,
        sampled_pages=[snapshot.page_number for snapshot in selected_snapshots],
        audit_mode="deterministic+llm" if reviewer else "deterministic",
        llm_model=reviewer.model if reviewer else None,
        page_results=page_results,
    )


def collect_page_audit_snapshots(
    session: Session,
    document_id: int,
    *,
    include_source_text: bool = True,
) -> list[PageAuditSnapshot]:
    document = session.get(Document, document_id)
    if not document:
        raise ValueError(f"Document {document_id} not found")

    settings = get_settings()
    blocks = (
        session.query(PageBlock)
        .filter_by(document_id=document_id)
        .order_by(PageBlock.page_number, PageBlock.reading_order)
        .all()
    )
    fragments = (
        session.query(SourceFragment)
        .filter_by(document_id=document_id)
        .order_by(SourceFragment.page_start, SourceFragment.reading_order_start, SourceFragment.id)
        .all()
    )
    tables = (
        session.query(SourceTable)
        .filter_by(document_id=document_id)
        .order_by(SourceTable.page_start, SourceTable.id)
        .all()
    )
    refs = (
        session.query(CrossReference)
        .join(SourceFragment, SourceFragment.id == CrossReference.source_fragment_id)
        .filter(CrossReference.document_id == document_id)
        .order_by(SourceFragment.page_start, CrossReference.id)
        .all()
    )

    refs_by_fragment_id = defaultdict(list)
    for ref in refs:
        refs_by_fragment_id[ref.source_fragment_id].append(ref)

    cells_by_table_id = defaultdict(list)
    table_ids = [table.id for table in tables]
    if table_ids:
        for cell in (
            session.query(SourceTableCell)
            .filter(SourceTableCell.table_id.in_(table_ids))
            .order_by(SourceTableCell.table_id, SourceTableCell.row_index, SourceTableCell.col_index)
            .all()
        ):
            cells_by_table_id[cell.table_id].append(cell)

    blocks_by_page = defaultdict(list)
    for block in blocks:
        blocks_by_page[block.page_number].append(block)

    fragments_by_page = defaultdict(list)
    for fragment in fragments:
        for page_number in range(fragment.page_start, fragment.page_end + 1):
            fragments_by_page[page_number].append(fragment)
    fragments_by_id = {fragment.id: fragment for fragment in fragments}

    tables_by_page = defaultdict(list)
    for table in tables:
        for page_number in range(table.page_start, table.page_end + 1):
            tables_by_page[page_number].append(table)

    refs_by_page = defaultdict(list)
    for fragment in fragments:
        for ref in refs_by_fragment_id.get(fragment.id, []):
            refs_by_page[fragment.page_start].append(ref)

    page_numbers = sorted(set(blocks_by_page) | set(fragments_by_page) | set(tables_by_page) | set(refs_by_page))
    source_text_by_page = (
        load_source_page_text(document.source_path, page_numbers) if include_source_text else {}
    )

    snapshots: list[PageAuditSnapshot] = []
    for page_number in page_numbers:
        page_blocks = blocks_by_page[page_number]
        page_fragments = fragments_by_page[page_number]
        page_tables = tables_by_page[page_number]
        page_refs = refs_by_page[page_number]
        risk_score, risk_reasons, deterministic_checks = score_page_risk(
            page_blocks=page_blocks,
            page_fragments=page_fragments,
            page_tables=page_tables,
            page_cross_references=page_refs,
        )
        snapshots.append(
            PageAuditSnapshot(
                page_number=page_number,
                source_page_text=source_text_by_page.get(page_number),
                page_block_count=len(page_blocks),
                fragment_count=len(page_fragments),
                table_count=len(page_tables),
                cross_reference_count=len(page_refs),
                risk_score=risk_score,
                risk_reasons=risk_reasons,
                deterministic_checks=deterministic_checks,
                page_blocks=[_block_dict(block) for block in page_blocks[: settings.audit_max_blocks_per_page]],
                fragments=[
                    _fragment_dict(
                        fragment,
                        parent_fragment=fragments_by_id.get(fragment.parent_fragment_id) if fragment.parent_fragment_id else None,
                        fragments_by_id=fragments_by_id,
                        page_number=page_number,
                    )
                    for fragment in page_fragments[: settings.audit_max_fragments_per_page]
                ],
                tables=[_table_dict(table, cells_by_table_id[table.id]) for table in page_tables],
                cross_references=[_crossref_dict(ref) for ref in page_refs],
            )
        )
    return snapshots


def select_audit_pages(snapshots: list[PageAuditSnapshot], *, sample_size: int) -> list[int]:
    ranked = sorted(snapshots, key=lambda item: (-item.risk_score, item.page_number))
    return [snapshot.page_number for snapshot in ranked[:sample_size]]


def score_page_risk(
    *,
    page_blocks: list[PageBlock],
    page_fragments: list[SourceFragment],
    page_tables: list[SourceTable],
    page_cross_references: list[CrossReference],
) -> tuple[int, list[str], list[DeterministicPageCheck]]:
    score = 0
    reasons: list[str] = []
    checks: list[DeterministicPageCheck] = []

    uncertain = [fragment for fragment in page_fragments if fragment.parse_status == ParseStatus.UNCERTAIN]
    if uncertain:
        score += len(uncertain) * 3
        reasons.append(f"{len(uncertain)} uncertain fragments")
        checks.append(
            DeterministicPageCheck(
                name="uncertain_fragments",
                severity="warning",
                detail=f"Page contains {len(uncertain)} fragments marked uncertain.",
            )
        )

    unusual_roots = [
        fragment
        for fragment in page_fragments
        if fragment.parent_fragment_id is None and fragment.fragment_type.value not in {"part", "section", "schedule", "appendix", "heading", "prose"}
    ]
    if unusual_roots:
        score += len(unusual_roots) * 2
        reasons.append(f"{len(unusual_roots)} unusual root fragments")
        checks.append(
            DeterministicPageCheck(
                name="unusual_root_fragments",
                severity="warning",
                detail=f"Page contains {len(unusual_roots)} root fragments with low-level types.",
            )
        )

    duplicate_citation_fragments = [
        fragment for fragment in page_fragments if (fragment.metadata_json or {}).get("duplicate_citation_path")
    ]
    if duplicate_citation_fragments:
        score += len(duplicate_citation_fragments) * 4
        reasons.append(f"{len(duplicate_citation_fragments)} duplicate citation path downgrades")
        checks.append(
            DeterministicPageCheck(
                name="duplicate_citation_downgrade",
                severity="warning",
                detail="One or more fragments had duplicate derived citation paths and were downgraded to uncertain.",
            )
        )

    if page_tables:
        score += len(page_tables) * 2
        reasons.append(f"{len(page_tables)} tables present")
        checks.append(
            DeterministicPageCheck(
                name="tables_present",
                severity="info",
                detail=f"Page contains {len(page_tables)} table regions or extracted tables.",
            )
        )

    unresolved_refs = [ref for ref in page_cross_references if ref.resolution_status != ResolutionStatus.RESOLVED]
    if unresolved_refs:
        score += len(unresolved_refs)
        reasons.append(f"{len(unresolved_refs)} unresolved cross-references")
        checks.append(
            DeterministicPageCheck(
                name="unresolved_cross_references",
                severity="info",
                detail=f"Page contains {len(unresolved_refs)} unresolved cross-references.",
            )
        )

    fallback_blocks = [
        block for block in page_blocks if "fallback" in (block.parser_source or "") or block.block_type in {BlockType.FOOTNOTE, BlockType.TABLE_REGION}
    ]
    if fallback_blocks:
        score += max(1, len(fallback_blocks) // 2)
        reasons.append(f"{len(fallback_blocks)} fallback-sensitive blocks")
        checks.append(
            DeterministicPageCheck(
                name="fallback_sensitive_blocks",
                severity="info",
                detail=f"Page contains {len(fallback_blocks)} blocks that often require manual review.",
            )
        )

    accounted_block_ids = set()
    for fragment in page_fragments:
        accounted_block_ids.update(fragment.source_block_ids_json or [])
    for table in page_tables:
        source_id = (table.metadata_json or {}).get("source_block_id")
        if source_id:
            accounted_block_ids.add(source_id)
        source_ids = (table.metadata_json or {}).get("source_block_ids")
        if isinstance(source_ids, list):
            accounted_block_ids.update(source_block_id for source_block_id in source_ids if source_block_id)
    unaccounted = [
        block
        for block in page_blocks
        if not block.is_boilerplate and block.block_type not in NON_FRAGMENT_BLOCK_TYPES and block.id not in accounted_block_ids
    ]
    if unaccounted:
        score += len(unaccounted) * 5
        reasons.append(f"{len(unaccounted)} unaccounted non-boilerplate blocks")
        checks.append(
            DeterministicPageCheck(
                name="unaccounted_blocks",
                severity="error",
                detail=f"{len(unaccounted)} non-boilerplate blocks on the page are not linked to a fragment.",
            )
        )

    if _is_non_normative_page(page_fragments):
        score = max(score // 10, 1) if score > 0 else 0
        reasons.append("non-normative (amendment-history/schedule appendix) — score discounted")
        checks.append(
            DeterministicPageCheck(
                name="non_normative_discount",
                severity="info",
                detail="Page detected as non-normative content (amendment history / schedule appendix); risk score heavily discounted.",
            )
        )

    if not reasons:
        reasons.append("low structural risk")

    return score, reasons, checks


def _is_non_normative_page(page_fragments: list[SourceFragment]) -> bool:
    """Detect pages dominated by non-normative content (amendment-history tables,
    schedule appendix metadata) that inflate risk scores without audit value.

    A page is non-normative if ALL fragments on it are rooted in a schedule or
    appendix AND any of the following hold:
      - A root fragment's text or citation matches amendment-history keywords
      - The root fragment type is schedule/appendix with no normative sub-structure
        (i.e. no section/clause children on this page)
    """
    if not page_fragments:
        return False

    has_normative_root = False
    has_amendment_signal = False

    for fragment in page_fragments:
        if fragment.parent_fragment_id is None:
            if fragment.fragment_type not in _NON_NORMATIVE_TYPES:
                has_normative_root = True
                break
            text_to_check = " ".join(
                filter(None, [fragment.text, fragment.citation_label, fragment.citation_path])
            )
            if _AMENDMENT_HISTORY_RE.search(text_to_check):
                has_amendment_signal = True

    if has_normative_root:
        return False

    if has_amendment_signal:
        return True

    # All fragments parented under schedule/appendix — check if the page has
    # only leaf-level fragments (list_item, prose, heading) with no normative
    # structure (section, clause, subsection). Table-dominated schedule pages
    # that aren't amendment history still get partial discount if they lack
    # normative descendants.
    normative_child_types = {
        FragmentType.SECTION,
        FragmentType.SUBSECTION,
        FragmentType.CLAUSE,
        FragmentType.SUBCLAUSE,
    }
    for fragment in page_fragments:
        if fragment.fragment_type in normative_child_types:
            return False

    # All roots are schedule/appendix, no normative children — non-normative page
    return True


def load_source_page_text(source_path: str, page_numbers: list[int]) -> dict[int, str]:
    path = Path(source_path)
    if not path.exists():
        return {}
    if path.suffix.lower() == ".pdf":
        try:
            import fitz
        except ImportError:
            return {}
        page_text: dict[int, str] = {}
        with fitz.open(path) as doc:
            for page_number in page_numbers:
                if 1 <= page_number <= doc.page_count:
                    page_text[page_number] = " ".join(doc[page_number - 1].get_text().split())
        return page_text

    raw = path.read_text(encoding="utf-8")
    pages = raw.split("\f")
    return {
        page_number: " ".join(pages[page_number - 1].split())
        for page_number in page_numbers
        if 1 <= page_number <= len(pages)
    }


_AUDIT_REVIEW_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string"},
        # NB: OpenAI's json_schema accepts ["number","null"] for nullable;
        # Anthropic's parser is stricter and dislikes some union shapes.
        # Use ["number","null"] for now; the retry/repair loop in
        # call_claude_p_with_schema covers transient validation drift.
        "confidence": {"type": ["number", "null"]},
        "summary": {"type": "string"},
        "suspected_issues": {"type": "array", "items": {"type": "string"}},
        "recommended_human_review": {"type": "boolean"},
    },
    "required": [
        "verdict",
        "confidence",
        "summary",
        "suspected_issues",
        "recommended_human_review",
    ],
}


class ClaudeCodeLayer1Auditor:
    """Page-level Layer 1 extraction reviewer that routes through Claude Code
    headless mode (`claude -p --json-schema`) instead of the OpenAI API.

    Under a Claude Max subscription this is billed against the subscription
    pool rather than per-token via OPENAI_API_KEY. Same prompt, same JSON
    schema, same :class:`LlmAuditReview` output shape — only the provider
    plumbing differs (per the user's instruction on ABS-109: "other than
    API plumbing, it shouldn't matter which LLM provider is called").

    ``model`` is currently advisory only — ``claude -p`` picks the model
    from the user's Claude Code config and routes Haiku/Opus internally.
    The argument is preserved for backward compatibility with the
    AUDIT_LLM_MODEL env var and the --model CLI flag, and reported on
    the produced ``DocumentAuditReport.llm_model`` field.
    """

    def __init__(self, model: str):
        self.model = model

    def review(self, snapshot: PageAuditSnapshot) -> LlmAuditReview:
        # Lazy import keeps the audit module importable even when the
        # `claude` CLI isn't installed — failure is deferred until the
        # user actually invokes `audit-page --llm`.
        from layer1._claude_code_client import call_claude_p_with_schema

        prompt = _build_audit_prompt(snapshot)
        structured = call_claude_p_with_schema(prompt, _AUDIT_REVIEW_JSON_SCHEMA)
        return LlmAuditReview.model_validate(structured)


# Backward-compat alias — any external script that imported the previous
# name still works. New code should use ClaudeCodeLayer1Auditor.
OpenAILayer1Auditor = ClaudeCodeLayer1Auditor


def _build_audit_prompt(snapshot: PageAuditSnapshot) -> str:
    payload = {
        "page_number": snapshot.page_number,
        "risk_score": snapshot.risk_score,
        "risk_reasons": snapshot.risk_reasons,
        "deterministic_checks": [check.model_dump() for check in snapshot.deterministic_checks],
        "source_page_text": snapshot.source_page_text,
        "page_blocks": snapshot.page_blocks,
        "fragments": snapshot.fragments,
        "tables": snapshot.tables,
        "cross_references": snapshot.cross_references,
    }
    return (
        "You are reviewing Layer 1 land-use bylaw extraction fidelity for one page. "
        "Compare the source page text against extracted blocks, fragments, tables, and cross-references. "
        "Focus on missing text, bad ordering, bad hierarchy attachment, heading/list/footnote mistakes, "
        "and table flattening risks. Off-page parent or ancestor context can be legitimate when the current page "
        "is a continuation of a definition list, clause sequence, or section started on a prior page; do not treat "
        "continuation ancestry alone as a failure if the visible page text is otherwise faithful. Return a strict JSON verdict.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _block_dict(block: PageBlock) -> dict[str, Any]:
    return {
        "id": block.id,
        "block_type": block.block_type.value,
        "reading_order": block.reading_order,
        "is_boilerplate": block.is_boilerplate,
        "parser_source": block.parser_source,
        "raw_text": block.raw_text,
    }


def _fragment_dict(
    fragment: SourceFragment,
    *,
    parent_fragment: SourceFragment | None = None,
    fragments_by_id: dict[int, SourceFragment] | None = None,
    page_number: int | None = None,
) -> dict[str, Any]:
    payload = {
        "id": fragment.id,
        "fragment_type": fragment.fragment_type.value,
        "citation_label": fragment.citation_label,
        "citation_path": fragment.citation_path,
        "parent_fragment_id": fragment.parent_fragment_id,
        "parse_status": fragment.parse_status.value,
        "text": fragment.text,
        "metadata_json": fragment.metadata_json,
    }
    if parent_fragment is not None:
        payload["parent_fragment_context"] = {
            "id": parent_fragment.id,
            "fragment_type": parent_fragment.fragment_type.value,
            "citation_label": parent_fragment.citation_label,
            "citation_path": parent_fragment.citation_path,
            "page_start": parent_fragment.page_start,
            "page_end": parent_fragment.page_end,
            "text": parent_fragment.text,
            "visible_on_current_page": (
                page_number is not None and parent_fragment.page_start <= page_number <= parent_fragment.page_end
            ),
        }
        if fragments_by_id:
            payload["ancestor_chain"] = _ancestor_chain(parent_fragment, fragments_by_id, page_number=page_number)
        payload["continuation_from_prior_page"] = bool(
            page_number is not None and parent_fragment.page_end < page_number
        )
    return payload


def _ancestor_chain(
    fragment: SourceFragment,
    fragments_by_id: dict[int, SourceFragment],
    *,
    page_number: int | None = None,
    max_depth: int = 6,
) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current = fragment
    depth = 0
    while current is not None and depth < max_depth:
        chain.append(
            {
                "id": current.id,
                "fragment_type": current.fragment_type.value,
                "citation_label": current.citation_label,
                "citation_path": current.citation_path,
                "page_start": current.page_start,
                "page_end": current.page_end,
                "text": current.text,
                "visible_on_current_page": (
                    page_number is not None and current.page_start <= page_number <= current.page_end
                ),
            }
        )
        if not current.parent_fragment_id:
            break
        current = fragments_by_id.get(current.parent_fragment_id)
        depth += 1
    return chain


def _table_dict(table: SourceTable, cells: list[SourceTableCell]) -> dict[str, Any]:
    return {
        "id": table.id,
        "caption": table.caption,
        "parse_status": table.parse_status.value,
        "cells": [
            {
                "row_index": cell.row_index,
                "col_index": cell.col_index,
                "text": cell.text,
            }
            for cell in cells
        ],
    }


def _crossref_dict(ref: CrossReference) -> dict[str, Any]:
    return {
        "id": ref.id,
        "raw_reference_text": ref.raw_reference_text,
        "target_citation_guess": ref.target_citation_guess,
        "resolution_status": ref.resolution_status.value,
    }
