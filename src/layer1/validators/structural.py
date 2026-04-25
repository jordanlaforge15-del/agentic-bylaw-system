from __future__ import annotations

from collections import Counter

from layer1.db.base import CrossReference, PageBlock, SourceFragment, SourceTable, SourceTableCell
from layer1.models.enums import BlockType, ParseStatus, ResolutionStatus
from layer1.models.schemas import ValidationReport


VALID_ROOT_TYPES = {"part", "schedule", "appendix", "section", "heading", "prose"}
NON_FRAGMENT_BLOCK_TYPES = {BlockType.TABLE_REGION, BlockType.HEADER, BlockType.FOOTER}


def validate_document_objects(
    *,
    page_count: int | None,
    blocks: list[PageBlock],
    fragments: list[SourceFragment],
    tables: list[SourceTable],
    table_cells: list[SourceTableCell],
    cross_references: list[CrossReference],
) -> ValidationReport:
    report = ValidationReport()
    accounted = set()
    for fragment in fragments:
        accounted.update(fragment.source_block_ids_json or [])
    for table in tables:
        source_idx = (table.metadata_json or {}).get("source_block_id")
        if source_idx:
            accounted.add(source_idx)
        source_ids = (table.metadata_json or {}).get("source_block_ids")
        if isinstance(source_ids, list):
            accounted.update(source_id for source_id in source_ids if source_id)

    for block in blocks:
        if block.is_boilerplate or block.block_type in NON_FRAGMENT_BLOCK_TYPES:
            continue
        if block.id not in accounted:
            report.errors.append(f"Page block {block.id} is not accounted for by a fragment or table")

    fragment_ids = {fragment.id for fragment in fragments}
    for fragment in fragments:
        if fragment.parent_fragment_id is None:
            if fragment.fragment_type.value not in VALID_ROOT_TYPES:
                report.warnings.append(f"Fragment {fragment.id} is an unusual root ({fragment.fragment_type.value})")
        elif fragment.parent_fragment_id not in fragment_ids:
            report.errors.append(f"Fragment {fragment.id} has missing parent {fragment.parent_fragment_id}")
        if fragment.page_start > fragment.page_end:
            report.errors.append(f"Fragment {fragment.id} has invalid page range")
        if page_count and (fragment.page_start < 1 or fragment.page_end > page_count):
            report.errors.append(f"Fragment {fragment.id} page range exceeds document page count")

    citations = [fragment.citation_path for fragment in fragments if fragment.citation_path]
    for citation, count in Counter(citations).items():
        if count > 1:
            report.errors.append(f"Duplicate citation path: {citation}")

    _validate_acyclic(fragments, report)

    table_ids = {table.id for table in tables}
    for cell in table_cells:
        if cell.table_id not in table_ids:
            report.errors.append(f"Table cell {cell.id} has missing table {cell.table_id}")
    for table in tables:
        if not any(cell.table_id == table.id for cell in table_cells):
            report.warnings.append(f"Table {table.id} has no cells")

    for ref in cross_references:
        if not ref.raw_reference_text.strip():
            report.errors.append(f"Cross-reference {ref.id} has empty raw text")
        if ref.resolution_status == ResolutionStatus.RESOLVED and ref.target_fragment_id is None:
            report.errors.append(f"Cross-reference {ref.id} marked resolved without a target")

    uncertain = [fragment.id for fragment in fragments if fragment.parse_status == ParseStatus.UNCERTAIN]
    report.stats = {
        "blocks": len(blocks),
        "fragments": len(fragments),
        "tables": len(tables),
        "table_cells": len(table_cells),
        "cross_references": len(cross_references),
        "uncertain_fragments": len(uncertain),
    }
    return report


def _validate_acyclic(fragments: list[SourceFragment], report: ValidationReport) -> None:
    parents = {fragment.id: fragment.parent_fragment_id for fragment in fragments}
    for fragment in fragments:
        seen = set()
        current = fragment.id
        while current is not None:
            if current in seen:
                report.errors.append(f"Fragment tree cycle detected at fragment {fragment.id}")
                break
            seen.add(current)
            current = parents.get(current)
