from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import (
    Document,
    SemanticEntity,
    SemanticFact,
    SemanticProvenance,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableSemanticProfile,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics, validate_document_semantics
from layer1.semantic.extractors import extract_zones
from layer2.db.init_db import create_all
from layer2.retrieval.planner import normalize_zone_code


@pytest.fixture()
def semantic_db(tmp_path: Path) -> dict:
    db_url = f"sqlite:///{tmp_path / 'semantic.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="regional.pdf",
            file_hash="semantic",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        table = SourceTable(
            document_id=document.id,
            caption="Table 1A: Permitted uses by zone (DD, DH, CEN-2, CEN-1, COR, HR-2, and HR-1)",
            page_start=45,
            page_end=47,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(table)
        session.flush()
        _add_table_rows(
            session,
            table.id,
            [
                ["Residential", "DD", "DH", "CEN-2", "CEN-1", "COR", "HR-2", "HR-1"],
                ["Restaurant use", "●", "●", "●", "●", "●", "③", "② ③"],
                ["Cluster housing use", "", "", "", "", "㉑", "㉑", "㉑"],
                ["Blank example use", "", "", "", "", "", "", ""],
            ],
        )
        dimensional = SourceTable(
            document_id=document.id,
            caption="Table 2: Dimensional standards by zone",
            page_start=60,
            page_end=60,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(dimensional)
        session.flush()
        _add_table_rows(
            session,
            dimensional.id,
            [
                ["Zone", "Minimum Lot Area", "Building Height"],
                ["R1", "400 m2", "11 metres"],
            ],
        )
        for text in [
            "② Use is permitted in HR-1 only when located in a local commercial area.",
            "③ Use is permitted subject to commercial use conditions.",
            "㉑ Use is permitted subject to cluster housing conditions.",
        ]:
            session.add(
                SourceFragment(
                    document_id=document.id,
                    fragment_type=FragmentType.FOOTNOTE,
                    page_start=47,
                    page_end=47,
                    text=text,
                    parse_status=ParseStatus.PARSED,
                    source_block_ids_json=[],
                    metadata_json={},
                )
            )
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id}


def test_semantic_enrichment_profiles_tables_and_emits_source_grounded_facts(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        report = enrich_document_semantics(session, document_id=semantic_db["document_id"])
        assert report.table_profiles == 2
        assert report.axis_bindings >= 10
        assert report.facts >= 10

        permission_profile = (
            session.query(TableSemanticProfile)
            .join(SourceTable, SourceTable.id == TableSemanticProfile.table_id)
            .filter(SourceTable.caption.ilike("%Permitted uses by zone%"))
            .one()
        )
        assert permission_profile.profile_type == "permission_matrix"
        assert permission_profile.row_axis_type == "use"
        assert permission_profile.column_axis_type == "zone"

        restaurant = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="use", canonical_name="restaurant use")
            .one()
        )
        hr1 = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="zone", canonical_name="HR-1")
            .one()
        )
        fact = (
            session.query(SemanticFact)
            .filter_by(
                document_id=semantic_db["document_id"],
                relation_type="permission",
                primary_subject_entity_id=restaurant.id,
                primary_scope_entity_id=hr1.id,
            )
            .one()
        )
        assert fact.normalized_value_json["permission"] == "conditional"
        assert fact.normalized_value_json["markers"] == ["②", "③"]
        assert (
            session.query(SemanticProvenance)
            .filter_by(object_type="semantic_fact", object_id=fact.id, source_type="source_table_cell")
            .count()
            == 1
        )


def test_semantic_enrichment_does_not_emit_negative_facts_for_blank_cells(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        blank_use = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="use", canonical_name="blank example use")
            .one()
        )
        assert (
            session.query(SemanticFact)
            .filter_by(
                document_id=semantic_db["document_id"],
                relation_type="permission",
                primary_subject_entity_id=blank_use.id,
            )
            .count()
            == 0
        )


def test_semantic_enrichment_is_rerunnable_and_validates(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        first = enrich_document_semantics(session, document_id=semantic_db["document_id"])
        second = enrich_document_semantics(session, document_id=semantic_db["document_id"])
        assert first.entities == second.entities
        assert first.facts == second.facts
        validation = validate_document_semantics(session, document_id=semantic_db["document_id"])
        assert validation["ok"] is True


def test_semantic_enrichment_emits_dimensional_standard_fact(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        r1 = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="zone", canonical_name="R-1")
            .one()
        )
        lot_area = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="standard", canonical_name="minimum lot area")
            .one()
        )
        fact = (
            session.query(SemanticFact)
            .filter_by(
                document_id=semantic_db["document_id"],
                relation_type="dimensional_standard",
                primary_subject_entity_id=lot_area.id,
                primary_scope_entity_id=r1.id,
            )
            .one()
        )
        assert fact.value_text == "400 m2"


def test_zone_normalization_corrects_leading_ocr_character():
    assert extract_zones("What uses are permitted in lHR-1?") == ["HR-1"]
    assert normalize_zone_code("lHR-1") == "HR-1"


def test_zone_extraction_does_not_treat_square_metres_as_zone():
    assert extract_zones("Minimum 1 parking space per 100 m2 of floor area") == []


def _add_table_rows(session, table_id: int, rows: list[list[str]]) -> None:
    for row_index, row in enumerate(rows):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table_id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=row[0] if row_index else None,
                    col_header_path=rows[0][col_index] if row_index and col_index < len(rows[0]) else None,
                    text=text,
                    metadata_json={},
                )
            )
