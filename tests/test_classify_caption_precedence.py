"""Classifier + binding-inheritance coverage for ABS-409's real-corpus fixes.

Three defects surfaced by running the caption backfill against a clone of the
real Regional Centre corpus (none reproducible on synthetic fixtures):

1. The amendment-table disqualifier (ABS-283) fires on the REAL Table 1A —
   its cells carry amendment annotations ("RC-Dec 10/19…") — so any document
   re-enrichment demoted every permission matrix to ``unknown``. An explicit
   "permitted uses by zone" caption must outrank that veto.
2. The parking signal fires on Table 1A page 3 — "Parking structure use" is a
   USE ROW there, not a parking-requirements table. The caption must outrank
   the row-label veto too (a parking-CAPTIONED table still classifies
   parking_matrix).
3. Table 1D's continuation slice (1067) has no repeated header row, so its
   zone column could never bind — caption-linked siblings now donate their
   column bindings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from layer1.db.base import (
    Document,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.semantic.enrichment import (
    enrich_document_semantics,
    enumerate_permission_column,
)
from layer1.semantic.extractors import extract_zones

DOT = "\uf098"  # symbol-font dot: permitted as-of-right


def _add_document(session) -> Document:
    doc = Document(
        municipality="Halifax",
        bylaw_name="Regional Centre Land Use By-law",
        source_path="rc.pdf",
        file_hash="abs409-classifier",
        mime_type="application/pdf",
        ingestion_timestamp=datetime.now(timezone.utc),
        parser_version="test",
    )
    session.add(doc)
    session.flush()
    return doc


def _add_table(session, document_id, *, page, grid, caption=None, parent_fragment_id=None):
    table = SourceTable(
        document_id=document_id,
        caption=caption,
        parent_fragment_id=parent_fragment_id,
        page_start=page,
        page_end=page,
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
    return table


def _profile_type(session, table_id):
    row = session.query(TableSemanticProfile).filter_by(table_id=table_id).first()
    return row.profile_type if row else None


def test_caption_overrides_amendment_and_parking_row_vetoes(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'veto.db'}"
    create_all(url)
    with session_scope(url) as session:
        doc = _add_document(session)
        # Amendment-annotated + parking-named USE rows, as the real Table 1A
        # pages carry them.
        grid = [
            ["Residential", "DD", "DH"],
            ["Temporary use (RCCC-Oct 26/22;E-Nov 11/22)", DOT, DOT],
            ["Parking structure use", DOT, ""],
            ["Multi-unit dwelling use (RC-Jul 27/21;E-289)", DOT, DOT],
        ]
        table = _add_table(
            session, doc.id, page=45, grid=grid,
            caption="Table 1A: Permitted uses by zone (DD and DH)",
        )
        # A parking-CAPTIONED table keeps demoting even with zone headers.
        parking = _add_table(
            session, doc.id, page=60,
            grid=[["Use", "DD", "DH"], ["Restaurant use", "Not required", "1 space"]],
            caption="Table 15: Required number of motor vehicle parking spaces",
        )
        enrich_document_semantics(session, document_id=doc.id)
        assert _profile_type(session, table.id) == "permission_matrix"
        assert _profile_type(session, parking.id) == "parking_matrix"
        # The parking-named USE row is enumerable like any other row.
        rows = enumerate_permission_column(session, table_id=table.id, zone="DD")
        by_use = {r["use_label"]: r["permission"] for r in rows}
        assert by_use.get("Parking structure use") == "permitted"


def test_headerless_sibling_inherits_column_bindings(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'sibling.db'}"
    create_all(url)
    with session_scope(url) as session:
        doc = _add_document(session)
        caption = SourceFragment(
            document_id=doc.id,
            fragment_type=FragmentType.PROSE,
            citation_label="Table 1D",
            citation_path="Part I > [Table 1D]",
            page_start=54,
            page_end=54,
            reading_order_start=1,
            reading_order_end=1,
            text="Table 1D: Permitted uses by zone (HCD-SV)",
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
        )
        session.add(caption)
        session.flush()
        headered = _add_table(
            session, doc.id, page=54,
            grid=[["Residential", "HCD-SV"], ["Single-unit dwelling use", DOT]],
            caption=caption.text, parent_fragment_id=caption.id,
        )
        # Continuation slice: NO header row — row 0 is already a use row.
        headerless = _add_table(
            session, doc.id, page=56,
            grid=[["Transportation facility use", DOT], ["Museum use", ""]],
            caption=caption.text, parent_fragment_id=caption.id,
        )
        enrich_document_semantics(session, document_id=doc.id)

        inherited = (
            session.query(TableAxisBinding)
            .filter_by(table_id=headerless.id, axis="column")
            .all()
        )
        assert inherited, "headerless slice must inherit column bindings"
        assert inherited[0].metadata_json.get("inherited_from_table_id") == headered.id
        rows = enumerate_permission_column(
            session, table_id=headerless.id, zone="HCD-SV"
        )
        assert rows is not None
        assert {r["use_label"]: r["permission"] for r in rows}.get("Museum use") == "not_permitted"


def test_hcd_sv_is_a_known_zone_code():
    assert extract_zones("HCD-SV") == ["HCD-SV"]
    assert "HCD-SV" in extract_zones("Table 1D: Permitted uses by zone (HCD-SV)")
