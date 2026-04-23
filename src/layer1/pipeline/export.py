from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from layer1.db.base import CrossReference, Document, PageBlock, SourceFragment, SourceTable, SourceTableCell


def document_to_dict(session: Session, document_id: int) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if not document:
        raise ValueError(f"Document {document_id} not found")
    blocks = session.query(PageBlock).filter_by(document_id=document_id).order_by(PageBlock.reading_order).all()
    fragments = session.query(SourceFragment).filter_by(document_id=document_id).order_by(SourceFragment.id).all()
    tables = session.query(SourceTable).filter_by(document_id=document_id).order_by(SourceTable.id).all()
    table_ids = [table.id for table in tables]
    cells = (
        session.query(SourceTableCell).filter(SourceTableCell.table_id.in_(table_ids)).order_by(SourceTableCell.table_id, SourceTableCell.row_index, SourceTableCell.col_index).all()
        if table_ids
        else []
    )
    refs = session.query(CrossReference).filter_by(document_id=document_id).order_by(CrossReference.id).all()
    return {
        "document": _model_dict(document),
        "page_blocks": [_model_dict(block) for block in blocks],
        "source_fragments": [_model_dict(fragment) for fragment in fragments],
        "source_tables": [_model_dict(table) for table in tables],
        "source_table_cells": [_model_dict(cell) for cell in cells],
        "cross_references": [_model_dict(ref) for ref in refs],
    }


def export_document_json(session: Session, document_id: int, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document_to_dict(session, document_id), indent=2, default=str), encoding="utf-8")


def _model_dict(obj) -> dict[str, Any]:
    data = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.name)
        if hasattr(value, "value"):
            value = value.value
        data[column.name] = value
    return data
