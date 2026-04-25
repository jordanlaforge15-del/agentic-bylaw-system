from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from layer1.config import get_settings
from layer1.db.base import CrossReference, Document, PageBlock, SourceFragment, SourceTable, SourceTableCell
from layer1.models.enums import BlockType, ParseStatus, ResolutionStatus
from layer1.models.schemas import (
    DeterministicPageCheck,
    DocumentAuditReport,
    LlmAuditReview,
    PageAuditResult,
    PageAuditSnapshot,
)

NON_FRAGMENT_BLOCK_TYPES = {BlockType.TABLE_REGION, BlockType.HEADER, BlockType.FOOTER}


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

    reviewer = OpenAILayer1Auditor(model=llm_model or get_settings().audit_llm_model) if use_llm else None
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
                fragments=[_fragment_dict(fragment) for fragment in page_fragments[: settings.audit_max_fragments_per_page]],
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

    if not reasons:
        reasons.append("low structural risk")

    return score, reasons, checks


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


class OpenAILayer1Auditor:
    def __init__(self, model: str):
        self.model = model
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency/runtime edge
            raise RuntimeError("openai package is required for --llm audit mode") from exc
        self._client = OpenAI()

    def review(self, snapshot: PageAuditSnapshot) -> LlmAuditReview:
        prompt = _build_audit_prompt(snapshot)
        response = self._client.responses.create(
            model=self.model,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "layer1_page_audit_review",
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "verdict": {"type": "string"},
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
                    },
                }
            },
        )
        content = getattr(response, "output_text", None)
        if not content:
            raise RuntimeError("LLM audit returned no structured content")
        return LlmAuditReview.model_validate_json(content)


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
        "and table flattening risks. Return a strict JSON verdict.\n\n"
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


def _fragment_dict(fragment: SourceFragment) -> dict[str, Any]:
    return {
        "id": fragment.id,
        "fragment_type": fragment.fragment_type.value,
        "citation_label": fragment.citation_label,
        "citation_path": fragment.citation_path,
        "parent_fragment_id": fragment.parent_fragment_id,
        "parse_status": fragment.parse_status.value,
        "text": fragment.text,
        "metadata_json": fragment.metadata_json,
    }


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
