"""Unit coverage for ``enumerate_permission_column`` (ABS-409, T5).

Two fixture styles:

* an enrichment-bound matrix (same shape as tests/advisor/
  test_lookup_permitted_use.py) proving marker classification end to end —
  ● permitted, ③ conditional with ordinal, blank not_permitted, and the
  ``None`` contract for an unbound zone;
* hand-built axis bindings proving the corpus-hardening paths — whitespace
  raw labels ("CEN-1  "), grouped comma-token columns ("DD, DH, CEN-2"),
  ``section:`` placeholder rows skipped, duplicate labels deduped.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import (
    Document,
    SemanticEntity,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.semantic.enrichment import (
    enrich_document_semantics,
    enumerate_permission_column,
    resolve_permission_cell,
)

DOT = ""  # symbol-font ● "permitted as-of-right"

MATRIX = [
    ["Use", "DD", "DH", "COR"],
    ["Restaurant use", DOT, "③", ""],
    ["Office use", DOT, DOT, DOT],
    ["Multi-unit dwelling use", DOT, DOT, ""],
]


def _add_document(session) -> Document:
    doc = Document(
        municipality="Halifax",
        bylaw_name="Regional Centre Land Use By-law",
        source_path="rc.pdf",
        file_hash="abs409-enumerate",
        mime_type="application/pdf",
        ingestion_timestamp=datetime.now(timezone.utc),
        parser_version="test",
    )
    session.add(doc)
    session.flush()
    return doc


def _add_table(session, document_id: int, grid: list[list[str]], caption: str | None = None) -> int:
    table = SourceTable(
        document_id=document_id,
        caption=caption,
        page_start=45,
        page_end=45,
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add(table)
    session.flush()
    for row_index, row in enumerate(grid):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=row[0] if row_index else None,
                    col_header_path=grid[0][col_index] if row_index else None,
                    text=text,
                    metadata_json={},
                )
            )
    session.flush()
    return table.id


@pytest.fixture()
def bound_matrix(tmp_path: Path) -> dict:
    db_url = f"sqlite:///{tmp_path / 'enumerate.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        doc = _add_document(session)
        # Caption carries the permitted-uses phrase so the classifier profiles
        # the table as a permission matrix and enrichment binds the axes.
        table_id = _add_table(
            session, doc.id, MATRIX, caption="Table 1A: Permitted uses by zone (DD, DH, COR)"
        )
        enrich_document_semantics(session, document_id=doc.id)
        return {"db_url": db_url, "table_id": table_id}


def test_enumerates_markers_for_bound_zone(bound_matrix):
    with session_scope(bound_matrix["db_url"]) as session:
        rows = enumerate_permission_column(
            session, table_id=bound_matrix["table_id"], zone="DH"
        )
        assert rows is not None
        by_use = {row["use_label"]: row for row in rows}
        assert by_use["Restaurant use"]["permission"] == "conditional"
        assert by_use["Restaurant use"]["footnote_ordinal"] == 3
        assert by_use["Office use"]["permission"] == "permitted"
        assert by_use["Multi-unit dwelling use"]["permission"] == "permitted"


def test_blank_cell_is_not_permitted(bound_matrix):
    with session_scope(bound_matrix["db_url"]) as session:
        rows = enumerate_permission_column(
            session, table_id=bound_matrix["table_id"], zone="COR"
        )
        by_use = {row["use_label"]: row for row in rows}
        assert by_use["Restaurant use"]["permission"] == "not_permitted"
        assert by_use["Multi-unit dwelling use"]["permission"] == "not_permitted"
        assert by_use["Office use"]["permission"] == "permitted"


def test_abs483_missing_cell_is_unknown_not_not_permitted(tmp_path: Path):
    """A bound use row with NO cell in the zone's column is an extraction gap.

    The parser dropped the cell; the bylaw said nothing. Reporting
    ``not_permitted`` there would invent a prohibition, so the row enumerates
    as ``unknown`` — while the present-but-blank cell one row away still
    enumerates as ``not_permitted`` (the two cases must stay distinguishable).
    """
    db_url = f"sqlite:///{tmp_path / 'missing_cell.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        doc = _add_document(session)
        grid = [
            ["Use", "DD", "DH"],
            ["Restaurant use", DOT, ""],  # blank DH cell -> not_permitted
            ["Office use", DOT, ""],  # DH cell deleted below -> unknown
        ]
        table_id = _add_table(
            session, doc.id, grid, caption="Table 1A: Permitted uses by zone (DD, DH)"
        )
        enrich_document_semantics(session, document_id=doc.id)
        # Drop the (Office use, DH) cell entirely — the shape a table parser
        # leaves behind when it loses a cell from the grid.
        session.query(SourceTableCell).filter(
            SourceTableCell.table_id == table_id,
            SourceTableCell.row_index == 2,
            SourceTableCell.col_index == 2,
        ).delete(synchronize_session=False)
        session.flush()

        rows = enumerate_permission_column(session, table_id=table_id, zone="DH")
        assert rows is not None
        by_use = {row["use_label"]: row["permission"] for row in rows}
        assert by_use["Office use"] == "unknown"
        assert by_use["Restaurant use"] == "not_permitted"


def test_abs483_resolve_permission_cell_reports_unknown_for_a_missing_cell(
    tmp_path: Path,
):
    """The per-cell resolver propagates the same distinction as the column
    enumerator: both axes bind, but nothing sits at their intersection."""
    db_url = f"sqlite:///{tmp_path / 'missing_cell_resolve.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        doc = _add_document(session)
        grid = [
            ["Use", "DD", "DH"],
            ["Restaurant use", DOT, ""],
            ["Office use", DOT, ""],
        ]
        table_id = _add_table(
            session, doc.id, grid, caption="Table 1A: Permitted uses by zone (DD, DH)"
        )
        enrich_document_semantics(session, document_id=doc.id)
        session.query(SourceTableCell).filter(
            SourceTableCell.table_id == table_id,
            SourceTableCell.row_index == 2,
            SourceTableCell.col_index == 2,
        ).delete(synchronize_session=False)
        session.flush()

        missing = resolve_permission_cell(
            session, table_id=table_id, use_name="Office use", zone="DH"
        )
        assert missing is not None
        assert missing["permission_marker"] == "unknown"
        assert missing["cell_text"] is None

        blank = resolve_permission_cell(
            session, table_id=table_id, use_name="Restaurant use", zone="DH"
        )
        assert blank is not None
        assert blank["permission_marker"] == "not_permitted"


def test_unbound_zone_returns_none(bound_matrix):
    with session_scope(bound_matrix["db_url"]) as session:
        assert (
            enumerate_permission_column(
                session, table_id=bound_matrix["table_id"], zone="HCD-SV"
            )
            is None
        )


def _bind(session, table_id: int, document_id: int, *, axis: str, index: int,
          entity_type: str, canonical: str, raw_label: str) -> None:
    entity = (
        session.query(SemanticEntity)
        .filter_by(document_id=document_id, entity_type=entity_type, canonical_name=canonical)
        .one_or_none()
    )
    if entity is None:
        entity = SemanticEntity(
            document_id=document_id,
            entity_type=entity_type,
            canonical_name=canonical,
            source_text=raw_label,
            confidence=0.9,
            metadata_json={},
        )
        session.add(entity)
        session.flush()
    session.add(
        TableAxisBinding(
            table_id=table_id,
            axis=axis,
            index=index,
            entity_id=entity.id,
            raw_label=raw_label,
            confidence=0.9,
            metadata_json={},
        )
    )
    session.flush()


def test_hardened_matching_grouped_whitespace_section_and_dedupe(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'hardened.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        doc = _add_document(session)
        grid = [
            ["Use", "DD, DH, CEN-2", "CEN-1  "],
            ["Restaurant use", DOT, ""],
            ["38(2)", DOT, DOT],
            ["Office use", "", DOT],
            ["Office use ", DOT, ""],  # duplicate label after strip -> deduped
        ]
        table_id = _add_table(session, doc.id, grid)
        # Grouped column binds ONE canonical (CEN-2) for the comma label —
        # mirrors prod table 1083. Whitespace label mirrors table 1057.
        _bind(session, table_id, doc.id, axis="column", index=1,
              entity_type="zone", canonical="CEN-2", raw_label="DD, DH, CEN-2")
        _bind(session, table_id, doc.id, axis="column", index=2,
              entity_type="zone", canonical="CEN-1", raw_label="CEN-1  ")
        _bind(session, table_id, doc.id, axis="row", index=1,
              entity_type="use", canonical="restaurant use", raw_label="Restaurant use")
        _bind(session, table_id, doc.id, axis="row", index=2,
              entity_type="use", canonical="section:38-2", raw_label="38(2)")
        _bind(session, table_id, doc.id, axis="row", index=3,
              entity_type="use", canonical="office use", raw_label="Office use")
        _bind(session, table_id, doc.id, axis="row", index=4,
              entity_type="use", canonical="office use", raw_label="Office use ")

        # DH only appears inside the grouped raw label — comma-token match.
        rows = enumerate_permission_column(session, table_id=table_id, zone="DH")
        assert rows is not None
        labels = [row["use_label"] for row in rows]
        assert labels == ["Restaurant use", "Office use"]  # section: skipped, dupe deduped
        by_use = {row["use_label"]: row for row in rows}
        assert by_use["Restaurant use"]["permission"] == "permitted"

        # Whitespace-padded raw label still resolves via canonical name.
        rows = enumerate_permission_column(session, table_id=table_id, zone="CEN-1")
        assert rows is not None
        assert {r["use_label"]: r["permission"] for r in rows} == {
            "Restaurant use": "not_permitted",
            "Office use": "permitted",
        }
