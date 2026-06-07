"""Unit + integration coverage for permission-matrix axis binding (ABS-278).

Phase 2 binds a permission matrix's columns to **zone** entities and its rows to
**use** entities, corrects header-bleed (zone codes that spilled into the data
region), and lets a (use, zone) pair resolve to an addressed cell. These tests
exercise each acceptance criterion against an in-memory SQLite DB.
"""
from __future__ import annotations

import importlib.util as _importlib_util
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from layer1.db.base import (
    Document,
    SemanticEntity,
    SemanticFact,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.semantic.enrichment import (
    AXIS_REVIEW_CONFIDENCE_THRESHOLD,
    REVIEW_NEEDS,
    enrich_document_semantics,
    resolve_permission_cell,
)

# scripts/ isn't a package; load the profile-backfill module via importlib.
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "backfill_table_profiles.py"
)
_spec = _importlib_util.spec_from_file_location("backfill_table_profiles", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_backfill_mod = _importlib_util.module_from_spec(_spec)
_sys.modules[_spec.name] = _backfill_mod
_spec.loader.exec_module(_backfill_mod)
backfill_table_profiles = _backfill_mod.backfill


CLEAN_CAPTION = "Table 1A: Permitted uses by zone — Residential"
BLEED_CAPTION = "Table 1C: Permitted uses by zone — Mixed"

# Clean matrix: header row of zone codes, two use rows. Drives AC1, AC2, AC5.
CLEAN_CELLS = [
    ["Use", "DD", "DH", "COR"],
    ["Restaurant use", "●", "●", "●"],
    ["Office use", "●", "●", "●"],
]

# Header-bleed matrix: col 3's header is a positional index ("3", no zone), and
# the zone code "COR" leaks into a data cell of that column (row 2). A genuinely
# unmapped row label ("General provisions") + the positional header exercise the
# FR5 logging path. Drives AC3 + FR5.
BLEED_CELLS = [
    ["Use", "DD", "DH", "3"],
    ["Restaurant use", "●", "●", "●"],
    ["Office use", "●", "●", "COR"],
    ["General provisions", "", "", ""],
]


def _add_table(session, document_id: int, caption: str, rows: list[list[str]]) -> int:
    table = SourceTable(
        document_id=document_id,
        caption=caption,
        page_start=10,
        page_end=10,
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add(table)
    session.flush()
    for row_index, row in enumerate(rows):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=row[0] if row_index else None,
                    col_header_path=rows[0][col_index] if row_index and col_index < len(rows[0]) else None,
                    text=text,
                    metadata_json={},
                )
            )
    session.flush()
    return table.id


@pytest.fixture()
def axis_db(tmp_path: Path) -> dict:
    db_url = f"sqlite:///{tmp_path / 'axis.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="regional.pdf",
            file_hash="axis-binding",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        clean_id = _add_table(session, document.id, CLEAN_CAPTION, CLEAN_CELLS)
        bleed_id = _add_table(session, document.id, BLEED_CAPTION, BLEED_CELLS)
        document_id = document.id
    return {
        "db_url": db_url,
        "document_id": document_id,
        "clean_table_id": clean_id,
        "bleed_table_id": bleed_id,
    }


def test_ac1_every_zone_header_column_produces_a_zone_binding(axis_db):
    """AC1: each populated col_index binds to a zone matching its header token."""
    with session_scope(axis_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=axis_db["document_id"])
        bindings = (
            session.query(TableAxisBinding, SemanticEntity)
            .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
            .filter(
                TableAxisBinding.table_id == axis_db["clean_table_id"],
                TableAxisBinding.axis == "column",
            )
            .all()
        )
        resolved = {b.index: e for b, e in bindings}
        # Columns 1..3 carry zone codes DD / DH / COR.
        assert {1, 2, 3} <= set(resolved)
        assert resolved[1].entity_type == "zone" and resolved[1].canonical_name == "DD"
        assert resolved[2].canonical_name == "DH"
        assert resolved[3].canonical_name == "COR"
        for binding, _ in bindings:
            assert binding.raw_label  # raw_label preserved
            assert binding.confidence is not None


def test_ac2_use_phrase_rows_produce_use_bindings(axis_db):
    """AC2: each row whose col-0 text is a use phrase binds to a use entity."""
    with session_scope(axis_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=axis_db["document_id"])
        rows = (
            session.query(TableAxisBinding, SemanticEntity)
            .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
            .filter(
                TableAxisBinding.table_id == axis_db["clean_table_id"],
                TableAxisBinding.axis == "row",
            )
            .all()
        )
        by_index = {b.index: (b, e) for b, e in rows}
        assert by_index[1][1].entity_type == "use"
        assert by_index[1][1].canonical_name == "restaurant use"
        assert by_index[1][0].raw_label == "Restaurant use"
        assert by_index[2][1].canonical_name == "office use"


def test_ac3_header_bleed_zone_is_rebound_and_cell_value_cleared(axis_db):
    """AC3: a zone code in a data cell is re-attributed to the column axis and
    the cell no longer reports the zone code as its value."""
    with session_scope(axis_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=axis_db["document_id"])

        # The bled "COR" is bound to column 3 (whose header was positional).
        col3 = (
            session.query(TableAxisBinding, SemanticEntity)
            .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
            .filter(
                TableAxisBinding.table_id == axis_db["bleed_table_id"],
                TableAxisBinding.axis == "column",
                TableAxisBinding.index == 3,
            )
            .one()
        )
        binding, entity = col3
        assert entity.entity_type == "zone"
        assert entity.canonical_name == "COR"
        # Recovered binding is low-confidence and flagged for review.
        assert binding.confidence <= AXIS_REVIEW_CONFIDENCE_THRESHOLD
        assert (binding.metadata_json or {}).get("review") == REVIEW_NEEDS

        # The offending cell is flagged and no longer treated as a value.
        cell = (
            session.query(SourceTableCell)
            .filter_by(table_id=axis_db["bleed_table_id"], row_index=2, col_index=3)
            .one()
        )
        assert (cell.metadata_json or {}).get("header_bleed") is True
        assert (cell.metadata_json or {}).get("recovered_zone") == "COR"

        # No permission fact treats the bled zone code as a marker value.
        assert (
            session.query(SemanticFact)
            .filter(
                SemanticFact.document_id == axis_db["document_id"],
                SemanticFact.relation_type == "permission",
                SemanticFact.value_text == "COR",
            )
            .count()
            == 0
        )


def test_fr5_unmapped_axis_labels_are_logged(axis_db):
    """FR5: unmapped column headers + row labels are logged, not dropped."""
    with session_scope(axis_db["db_url"]) as session:
        report = enrich_document_semantics(session, document_id=axis_db["document_id"])
        assert any("unmapped row label" in w for w in report.warnings)
        assert any("unmapped column header" in w for w in report.warnings)
        assert any("header-bleed" in w for w in report.warnings)


def test_ac5_resolve_use_zone_pair_addresses_the_correct_cell(axis_db):
    """AC5: a known (use, zone) pair resolves to the right (row, col) cell."""
    with session_scope(axis_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=axis_db["document_id"])
        resolved = resolve_permission_cell(
            session,
            table_id=axis_db["clean_table_id"],
            use_name="Restaurant use",
            zone="DH",
        )
        assert resolved is not None
        assert resolved["row_index"] == 1
        assert resolved["col_index"] == 2
        # And the addressed cell really is the (Restaurant use, DH) intersection.
        cell = (
            session.query(SourceTableCell)
            .filter_by(
                table_id=axis_db["clean_table_id"],
                row_index=resolved["row_index"],
                col_index=resolved["col_index"],
            )
            .one()
        )
        assert cell.row_header_path == "Restaurant use"
        assert cell.col_header_path == "DH"

        # An unbound zone resolves to None rather than guessing.
        assert (
            resolve_permission_cell(
                session,
                table_id=axis_db["clean_table_id"],
                use_name="Restaurant use",
                zone="ZZ-9",
            )
            is None
        )


def test_abs282_bare_use_form_resolves_same_cell_as_suffixed(axis_db):
    """ABS-282: a query missing the trailing ' use' still resolves.

    The bound use entity is "office use" (from the row label "Office use"), but
    ``normalize_use("office")`` -> "office" (no alias appends " use"). Before the
    fix that bare form missed the row binding entirely. Both phrasings must now
    address the same (Office use, DH) cell.
    """
    with session_scope(axis_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=axis_db["document_id"])
        suffixed = resolve_permission_cell(
            session, table_id=axis_db["clean_table_id"], use_name="Office use", zone="DH"
        )
        bare = resolve_permission_cell(
            session, table_id=axis_db["clean_table_id"], use_name="office", zone="DH"
        )
        assert suffixed is not None
        assert bare is not None
        assert (bare["row_index"], bare["col_index"]) == (
            suffixed["row_index"],
            suffixed["col_index"],
        )
        assert (bare["row_index"], bare["col_index"]) == (2, 2)


def test_ac4_backfill_creates_missing_permission_matrix_profile(tmp_path: Path):
    """AC4: after backfill, a permission table that had no profile gets one
    (parity with an already-profiled identical table)."""
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Doc 4",
            source_path="doc4.pdf",
            file_hash="doc4",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        table_id = _add_table(session, document.id, CLEAN_CAPTION, CLEAN_CELLS)
        document_id = document.id

    # Precondition: no profile exists yet (mirrors table 1057 in doc 4).
    with session_scope(db_url) as session:
        assert (
            session.query(TableSemanticProfile)
            .filter(TableSemanticProfile.table_id == table_id)
            .count()
            == 0
        )

    with session_scope(db_url) as session:
        stats = backfill_table_profiles(session)
        assert stats.documents_enriched == 1
        assert stats.profiles_created >= 1

    with session_scope(db_url) as session:
        profile = (
            session.query(TableSemanticProfile)
            .filter(TableSemanticProfile.table_id == table_id)
            .one()
        )
        assert profile.profile_type == "permission_matrix"
        assert profile.row_axis_type == "use"
        assert profile.column_axis_type == "zone"
        assert profile.value_type == "permission_marker"

    # Idempotent: a second run skips the now-profiled document.
    with session_scope(db_url) as session:
        stats2 = backfill_table_profiles(session)
        assert stats2.documents_enriched == 0
