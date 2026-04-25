from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from layer1.db.base import (
    CrossReference,
    Document,
    IngestionRun,
    PageBlock,
    SourceFragment,
    SourceTable,
    SourceTableCell,
)
from layer1.models.enums import BlockType, FragmentType, IngestionStatus, ParseStatus
from layer1.models.schemas import FragmentData, PageBlockData, TableData
from layer1.parsers.factory import parse_source
from layer1.pipeline.crossrefs import detect_cross_references
from layer1.pipeline.hierarchy import reconstruct_hierarchy
from layer1.profiles import ParsingProfile, get_parsing_profile
from layer1.utils.files import detect_mime_type, sha256_file
from layer1.validators.structural import validate_document_objects


def ingest_file(
    session: Session,
    path: Path,
    *,
    municipality: str | None = None,
    bylaw_name: str | None = None,
    source_url: str | None = None,
    ocr: bool = False,
    debug: bool = False,
    camelot: bool = False,
    profile: ParsingProfile | str | None = None,
) -> tuple[Document, IngestionRun]:
    profile = get_parsing_profile(profile)
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    document = Document(
        municipality=municipality or "Unknown",
        bylaw_name=bylaw_name or path.stem,
        source_path=str(path),
        source_url=source_url,
        file_hash=sha256_file(path),
        mime_type=detect_mime_type(path),
        ingestion_timestamp=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()

    run = IngestionRun(document_id=document.id, status=IngestionStatus.RUNNING, warnings_json=[], errors_json=[])
    session.add(run)
    session.flush()

    try:
        parsed = parse_source(path, ocr=ocr, debug=debug, camelot=camelot, profile=profile)
        document.page_count = parsed.page_count
        document.parser_version = f"{parsed.parser_version}:{profile.name}" if parsed.parser_version else profile.name

        db_blocks = _persist_blocks(session, document.id, parsed.page_blocks)
        fragments_data = reconstruct_hierarchy(parsed.page_blocks, profile=profile)
        fragments_data = _ensure_fragment_coverage(parsed.page_blocks, fragments_data, parsed.tables)
        db_fragments = _persist_fragments(session, document.id, fragments_data, db_blocks)
        db_tables = _persist_tables(session, document.id, parsed.tables, db_fragments, db_blocks)
        refs_data = detect_cross_references(fragments_data)
        db_refs = _persist_crossrefs(session, document.id, refs_data, db_fragments)

        session.flush()
        table_cells = [cell for table in db_tables for cell in table.cells]
        report = validate_document_objects(
            page_count=document.page_count,
            blocks=db_blocks,
            fragments=db_fragments,
            tables=db_tables,
            table_cells=table_cells,
            cross_references=db_refs,
        )
        warnings = parsed.warnings + report.warnings
        errors = report.errors
        run.warnings_json = warnings
        run.errors_json = errors
        run.status = (
            IngestionStatus.FAILED
            if errors
            else IngestionStatus.COMPLETED_WITH_WARNINGS
            if warnings
            else IngestionStatus.COMPLETED
        )
        run.completed_at = datetime.now(timezone.utc)
        return document, run
    except Exception as exc:
        run.status = IngestionStatus.FAILED
        run.errors_json = [str(exc)]
        run.completed_at = datetime.now(timezone.utc)
        raise


def _persist_blocks(session: Session, document_id: int, blocks_data) -> list[PageBlock]:
    blocks = []
    for block in blocks_data:
        db_block = PageBlock(
            document_id=document_id,
            page_number=block.page_number,
            block_type=block.block_type,
            bbox_json=block.bbox.model_dump() if block.bbox else None,
            reading_order=block.reading_order,
            raw_text=block.raw_text,
            normalized_text=block.normalized_text,
            is_boilerplate=block.is_boilerplate,
            parser_source=block.parser_source,
            confidence=block.confidence,
            metadata_json=block.metadata,
        )
        session.add(db_block)
        blocks.append(db_block)
    session.flush()
    return blocks


def _persist_fragments(session: Session, document_id: int, fragments_data, blocks: list[PageBlock]) -> list[SourceFragment]:
    fragments: list[SourceFragment] = []
    for data in fragments_data:
        source_ids = [blocks[idx].id for idx in data.source_block_indices if idx < len(blocks)]
        db_fragment = SourceFragment(
            document_id=document_id,
            fragment_type=data.fragment_type,
            citation_label=data.citation_label,
            citation_path=data.citation_path,
            parent_fragment_id=fragments[data.parent_index].id if data.parent_index is not None else None,
            page_start=data.page_start,
            page_end=data.page_end,
            reading_order_start=data.reading_order_start,
            reading_order_end=data.reading_order_end,
            text=data.text,
            parse_status=data.parse_status,
            confidence=data.confidence,
            source_block_ids_json=source_ids,
            metadata_json=data.metadata,
        )
        session.add(db_fragment)
        session.flush()
        fragments.append(db_fragment)
    return fragments


def _persist_tables(session: Session, document_id: int, tables_data, fragments: list[SourceFragment], blocks: list[PageBlock]) -> list[SourceTable]:
    tables: list[SourceTable] = []
    for table_data in tables_data:
        metadata = dict(table_data.metadata)
        source_idx = metadata.pop("source_block_index", None)
        if source_idx is not None and source_idx < len(blocks):
            metadata["source_block_id"] = blocks[source_idx].id
        source_indices = metadata.pop("source_block_indices", None)
        if isinstance(source_indices, list):
            metadata["source_block_ids"] = [
                blocks[idx].id for idx in source_indices if isinstance(idx, int) and idx < len(blocks)
            ]
        db_table = SourceTable(
            document_id=document_id,
            parent_fragment_id=fragments[table_data.parent_fragment_index].id if table_data.parent_fragment_index is not None and table_data.parent_fragment_index < len(fragments) else None,
            caption=table_data.caption,
            page_start=table_data.page_start,
            page_end=table_data.page_end,
            parse_status=table_data.parse_status,
            metadata_json=metadata,
        )
        session.add(db_table)
        session.flush()
        for cell_data in table_data.cells:
            db_table.cells.append(
                SourceTableCell(
                    table_id=db_table.id,
                    row_index=cell_data.row_index,
                    col_index=cell_data.col_index,
                    row_header_path=cell_data.row_header_path,
                    col_header_path=cell_data.col_header_path,
                    text=cell_data.text,
                    bbox_json=cell_data.bbox.model_dump() if cell_data.bbox else None,
                    metadata_json=cell_data.metadata,
                )
            )
        tables.append(db_table)
    session.flush()
    return tables


def _persist_crossrefs(session: Session, document_id: int, refs_data, fragments: list[SourceFragment]) -> list[CrossReference]:
    refs = []
    for data in refs_data:
        db_ref = CrossReference(
            document_id=document_id,
            source_fragment_id=fragments[data.source_fragment_index].id,
            raw_reference_text=data.raw_reference_text,
            target_citation_guess=data.target_citation_guess,
            target_fragment_id=fragments[data.target_fragment_index].id if data.target_fragment_index is not None else None,
            resolution_status=data.resolution_status,
            confidence=data.confidence,
            metadata_json=data.metadata,
        )
        session.add(db_ref)
        refs.append(db_ref)
    session.flush()
    return refs


def _ensure_fragment_coverage(
    blocks: list[PageBlockData],
    fragments: list[FragmentData],
    tables: list[TableData],
) -> list[FragmentData]:
    accounted_block_indices = set()
    for fragment in fragments:
        accounted_block_indices.update(fragment.source_block_indices)
    for table in tables:
        source_idx = table.metadata.get("source_block_index")
        if isinstance(source_idx, int):
            accounted_block_indices.add(source_idx)
        source_indices = table.metadata.get("source_block_indices")
        if isinstance(source_indices, list):
            accounted_block_indices.update(idx for idx in source_indices if isinstance(idx, int))

    for block_index, block in enumerate(blocks):
        if block_index in accounted_block_indices:
            continue
        if block.is_boilerplate or block.block_type in {BlockType.HEADER, BlockType.FOOTER}:
            continue
        text = block.normalized_text or " ".join(block.raw_text.split())
        if not text:
            continue
        fragments.append(
            FragmentData(
                fragment_type=_fallback_fragment_type(block.block_type),
                citation_label=None,
                citation_path=None,
                parent_index=None,
                page_start=block.page_number,
                page_end=block.page_number,
                reading_order_start=block.reading_order,
                reading_order_end=block.reading_order,
                text=text,
                parse_status=ParseStatus.UNCERTAIN,
                confidence=0.4,
                source_block_indices=[block_index],
                metadata={
                    "block_type": block.block_type.value,
                    "fallback_unaccounted_block": True,
                },
            )
        )
    return fragments


def _fallback_fragment_type(block_type: BlockType) -> FragmentType:
    if block_type == BlockType.HEADING:
        return FragmentType.HEADING
    if block_type == BlockType.LIST_ITEM:
        return FragmentType.LIST_ITEM
    if block_type == BlockType.FOOTNOTE:
        return FragmentType.FOOTNOTE
    return FragmentType.PROSE
