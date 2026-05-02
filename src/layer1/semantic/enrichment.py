from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import Iterable

from sqlalchemy.orm import Session

from layer1.db.base import (
    CrossReference,
    SemanticEdge,
    SemanticEntity,
    SemanticFact,
    SemanticFactParticipant,
    SemanticProvenance,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
)
from layer1.semantic.extractors import (
    extract_condition_refs,
    extract_development_contexts,
    extract_numeric_values,
    extract_section_refs,
    extract_standards,
    extract_table_refs,
    extract_uses,
    extract_zones,
    looks_like_use_label,
    normalize_use,
)

EXTRACTOR_VERSION = "semantic-v1"
REVIEW_AUTO = "auto_accepted"
PERMISSION_MARKERS = {"●", "", "•", "■", "x", "X"}


@dataclass
class SemanticEnrichmentReport:
    document_id: int
    entities: int = 0
    facts: int = 0
    fact_participants: int = 0
    edges: int = 0
    table_profiles: int = 0
    axis_bindings: int = 0
    provenance: int = 0
    warnings: list[str] = field(default_factory=list)

    def model_dump(self) -> dict:
        return {
            "document_id": self.document_id,
            "entities": self.entities,
            "facts": self.facts,
            "fact_participants": self.fact_participants,
            "edges": self.edges,
            "table_profiles": self.table_profiles,
            "axis_bindings": self.axis_bindings,
            "provenance": self.provenance,
            "warnings": self.warnings,
        }


def enrich_document_semantics(session: Session, *, document_id: int) -> SemanticEnrichmentReport:
    _clear_existing_semantics(session, document_id=document_id)
    report = SemanticEnrichmentReport(document_id=document_id)
    cache: dict[tuple[str, str], SemanticEntity] = {}

    fragments = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .order_by(SourceFragment.page_start, SourceFragment.id)
        .all()
    )
    tables = (
        session.query(SourceTable)
        .filter(SourceTable.document_id == document_id)
        .order_by(SourceTable.page_start, SourceTable.id)
        .all()
    )
    for fragment in fragments:
        _extract_fragment_entities(session, report, cache, fragment)
        _extract_definition_fact(session, report, cache, fragment)
        _extract_condition_definition(session, report, cache, fragment)
    for table in tables:
        _enrich_table(session, report, cache, table)
    _enrich_cross_references(session, report, cache, document_id=document_id)
    session.flush()
    _refresh_counts(session, report)
    return report


def validate_document_semantics(session: Session, *, document_id: int) -> dict:
    tables = session.query(SourceTable).filter(SourceTable.document_id == document_id).count()
    profiles = (
        session.query(TableSemanticProfile)
        .join(SourceTable, SourceTable.id == TableSemanticProfile.table_id)
        .filter(SourceTable.document_id == document_id)
        .count()
    )
    facts_without_provenance = 0
    for fact in session.query(SemanticFact).filter(SemanticFact.document_id == document_id).all():
        has_provenance = (
            session.query(SemanticProvenance)
            .filter(
                SemanticProvenance.document_id == document_id,
                SemanticProvenance.object_type == "semantic_fact",
                SemanticProvenance.object_id == fact.id,
            )
            .first()
            is not None
        )
        facts_without_provenance += int(not has_provenance)
    warnings = []
    if tables and not profiles:
        warnings.append("Document has source tables but no semantic table profiles")
    if facts_without_provenance:
        warnings.append(f"{facts_without_provenance} semantic facts lack provenance")
    return {
        "document_id": document_id,
        "ok": not warnings,
        "tables": tables,
        "table_profiles": profiles,
        "semantic_entities": session.query(SemanticEntity).filter(SemanticEntity.document_id == document_id).count(),
        "semantic_facts": session.query(SemanticFact).filter(SemanticFact.document_id == document_id).count(),
        "warnings": warnings,
    }


def _clear_existing_semantics(session: Session, *, document_id: int) -> None:
    table_ids = [row.id for row in session.query(SourceTable.id).filter(SourceTable.document_id == document_id).all()]
    fact_ids = [row.id for row in session.query(SemanticFact.id).filter(SemanticFact.document_id == document_id).all()]
    if fact_ids:
        session.query(SemanticFactParticipant).filter(SemanticFactParticipant.fact_id.in_(fact_ids)).delete(synchronize_session=False)
    if table_ids:
        session.query(TableAxisBinding).filter(TableAxisBinding.table_id.in_(table_ids)).delete(synchronize_session=False)
        session.query(TableSemanticProfile).filter(TableSemanticProfile.table_id.in_(table_ids)).delete(synchronize_session=False)
    for model in [SemanticEdge, SemanticProvenance, SemanticFact, SemanticEntity]:
        session.query(model).filter(model.document_id == document_id).delete(synchronize_session=False)
    session.flush()


def _extract_fragment_entities(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    fragment: SourceFragment,
) -> None:
    text = fragment.text
    for entity_type, values in [
        ("zone", extract_zones(text)),
        ("use", extract_uses(text)),
        ("standard", extract_standards(text)),
        ("development_context", extract_development_contexts(text)),
        ("condition_ref", extract_condition_refs(text)),
        ("section_ref", extract_section_refs(text)),
        ("table_ref", extract_table_refs(text)),
        ("numeric_value", extract_numeric_values(text)),
    ]:
        for value in values:
            entity = _get_or_create_entity(
                session,
                report,
                cache,
                document_id=fragment.document_id,
                entity_type=entity_type,
                canonical_name=value,
                source_text=value,
                confidence=0.85,
                metadata={"source_fragment_id": fragment.id},
            )
            _add_provenance(
                session,
                report,
                document_id=fragment.document_id,
                object_type="semantic_entity",
                object_id=entity.id,
                source_type="source_fragment",
                source_id=fragment.id,
                method="fragment_entity_extractor",
                confidence=0.85,
            )


def _extract_condition_definition(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    fragment: SourceFragment,
) -> None:
    markers = extract_condition_refs(fragment.text)
    if not markers:
        return
    for marker in markers:
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=fragment.document_id,
            entity_type="condition_ref",
            canonical_name=marker,
            source_text=marker,
            confidence=0.95,
            metadata={"source_fragment_id": fragment.id},
        )
        fact = _create_fact(
            session,
            report,
            document_id=fragment.document_id,
            relation_type="condition_definition",
            subject=entity,
            value_text=fragment.text,
            normalized_value={"condition_ref": marker, "text": fragment.text},
            assertion_type="explicit",
            confidence=0.9,
            metadata={"source_fragment_ids": [fragment.id]},
        )
        _add_participant(session, report, fact, entity, "condition", 0.95)
        for section_ref in extract_section_refs(fragment.text):
            section_entity = _get_or_create_entity(
                session,
                report,
                cache,
                document_id=fragment.document_id,
                entity_type="section_ref",
                canonical_name=section_ref,
                source_text=section_ref,
                confidence=0.8,
                metadata={"source_fragment_id": fragment.id},
            )
            _add_participant(session, report, fact, section_entity, "section_ref", 0.8)
            _add_edge(session, report, fragment.document_id, fact, "references", section_entity, [fragment.id], 0.8)
        _add_provenance(
            session,
            report,
            document_id=fragment.document_id,
            object_type="semantic_fact",
            object_id=fact.id,
            source_type="source_fragment",
            source_id=fragment.id,
            method="condition_definition_extractor",
            confidence=0.9,
        )


def _enrich_table(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
) -> None:
    cells = (
        session.query(SourceTableCell)
        .filter(SourceTableCell.table_id == table.id)
        .order_by(SourceTableCell.row_index, SourceTableCell.col_index)
        .all()
    )
    if not cells:
        return
    rows = _rows_by_index(cells)
    profile_type, row_axis_type, column_axis_type, value_type, confidence = _classify_table(table, rows)
    profile = TableSemanticProfile(
        table_id=table.id,
        profile_type=profile_type,
        row_axis_type=row_axis_type,
        column_axis_type=column_axis_type,
        value_type=value_type,
        confidence=confidence,
        review_status=REVIEW_AUTO,
        metadata_json={"caption": table.caption},
    )
    session.add(profile)
    session.flush()
    report.table_profiles += 1
    _add_provenance(
        session,
        report,
        document_id=table.document_id,
        object_type="table_semantic_profile",
        object_id=profile.id,
        source_type="source_table",
        source_id=table.id,
        method="table_profiler",
        confidence=confidence,
    )
    if profile_type == "permission_matrix":
        _extract_permission_table_facts(session, report, cache, table, rows)
    elif profile_type == "parking_matrix":
        _extract_parking_table_facts(session, report, cache, table, rows)
    elif profile_type == "requirement_matrix":
        _extract_requirement_table_facts(session, report, cache, table, rows)
    elif profile_type == "dimensional_matrix":
        _extract_dimensional_table_facts(session, report, cache, table, rows)


def _extract_definition_fact(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    fragment: SourceFragment,
) -> None:
    match = re.match(r"\s*([A-Z][A-Za-z0-9 /'()-]{1,80}?)\s+means\s+(.+)", fragment.text)
    if not match:
        return
    term = normalize_use(match.group(1))
    if not term:
        return
    entity = _get_or_create_entity(
        session,
        report,
        cache,
        document_id=fragment.document_id,
        entity_type="defined_term",
        canonical_name=term,
        source_text=match.group(1),
        confidence=0.9,
        metadata={"source_fragment_id": fragment.id},
    )
    fact = _create_fact(
        session,
        report,
        document_id=fragment.document_id,
        relation_type="definition",
        subject=entity,
        value_text=fragment.text,
        normalized_value={"term": term, "definition": match.group(2).strip()},
        assertion_type="explicit",
        confidence=0.9,
        metadata={"source_fragment_ids": [fragment.id]},
    )
    _add_participant(session, report, fact, entity, "defined_term", 0.9)
    _add_provenance(
        session,
        report,
        document_id=fragment.document_id,
        object_type="semantic_fact",
        object_id=fact.id,
        source_type="source_fragment",
        source_id=fragment.id,
        method="definition_fragment_extractor",
        confidence=0.9,
    )


def _classify_table(
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> tuple[str, str | None, str | None, str | None, float]:
    caption = (table.caption or "").lower()
    row_labels = [_row_label(cells) for idx, cells in rows.items() if idx > 0]
    headers = [_cell_text(cell) for cell in rows.get(_header_row_index(rows), [])]
    zone_density = _zone_density(headers)
    use_density = sum(1 for label in row_labels if looks_like_use_label(label)) / max(len(row_labels), 1)
    standard_density = sum(1 for text in headers + row_labels if extract_standards(text)) / max(len(headers) + len(row_labels), 1)
    context_density = sum(1 for text in headers + row_labels if extract_development_contexts(text)) / max(
        len(headers) + len(row_labels), 1
    )
    parking_signal = "parking" in caption or any("parking" in text.lower() for text in headers + row_labels)
    if ("permitted uses by zone" in caption or (zone_density >= 0.4 and use_density >= 0.35)) and not parking_signal:
        return "permission_matrix", "use", "zone", "permission_marker", 0.9
    if parking_signal and (zone_density >= 0.2 or standard_density >= 0.2):
        return "parking_matrix", "use", "standard", "requirement", 0.75
    if standard_density >= 0.2 and context_density >= 0.1:
        return "requirement_matrix", "standard", "development_context", "requirement", 0.78
    if standard_density >= 0.2 and (zone_density >= 0.2 or any(extract_zones(label) for label in row_labels)):
        return "dimensional_matrix", "zone", "standard", "numeric_or_text", 0.8
    if len(rows) <= 8 and all(len(cells) <= 2 for cells in rows.values()):
        return "key_value_table", "key", "value", "text", 0.55
    return "unknown", None, None, None, 0.4


def _extract_permission_table_facts(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> None:
    header_idx = _header_row_index(rows)
    header_cells = rows.get(header_idx, [])
    column_entities: dict[int, SemanticEntity] = {}
    for cell in header_cells[1:]:
        zones = extract_zones(cell.text)
        if not zones:
            continue
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="zone",
            canonical_name=zones[0],
            source_text=cell.text,
            confidence=0.95,
            metadata={"source_table_id": table.id, "source_table_cell_id": cell.id},
        )
        column_entities[cell.col_index] = entity
        _add_axis_binding(session, report, table, "column", cell.col_index, entity, cell.text, 0.95)
    for row_index, row_cells in rows.items():
        if row_index == header_idx or _is_repeated_header_row(row_cells):
            continue
        row_label = _row_label(row_cells)
        if not looks_like_use_label(row_label):
            continue
        use_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="use",
            canonical_name=normalize_use(row_label),
            source_text=row_label,
            confidence=0.9,
            metadata={"source_table_id": table.id, "row_index": row_index},
        )
        _add_axis_binding(session, report, table, "row", row_index, use_entity, row_label, 0.9)
        for cell in row_cells[1:]:
            marker = cell.text.strip()
            if not marker:
                continue
            zone_entity = column_entities.get(cell.col_index)
            if zone_entity is None:
                continue
            conditions = [
                _get_or_create_entity(
                    session,
                    report,
                    cache,
                    document_id=table.document_id,
                    entity_type="condition_ref",
                    canonical_name=condition,
                    source_text=condition,
                    confidence=0.9,
                    metadata={"source_table_cell_id": cell.id},
                )
                for condition in extract_condition_refs(marker)
            ]
            normalized = _normalize_permission_marker(marker)
            fact = _create_fact(
                session,
                report,
                document_id=table.document_id,
                relation_type="permission",
                subject=use_entity,
                scope=zone_entity,
                value_text=marker,
                normalized_value=normalized,
                assertion_type="inferred_from_legend" if normalized["permission"] != "unknown" else "explicit",
                confidence=0.88,
                metadata={
                    "source_table_id": table.id,
                    "source_table_cell_ids": [cell.id],
                    "row_index": row_index,
                    "col_index": cell.col_index,
                },
            )
            _add_participant(session, report, fact, use_entity, "use", 0.9)
            _add_participant(session, report, fact, zone_entity, "zone", 0.95)
            for condition in conditions:
                _add_participant(session, report, fact, condition, "condition", 0.9)
                _add_edge(session, report, table.document_id, fact, "conditioned_by", condition, [cell.id], 0.85)
            _add_provenance(
                session,
                report,
                document_id=table.document_id,
                object_type="semantic_fact",
                object_id=fact.id,
                source_type="source_table_cell",
                source_id=cell.id,
                method="permission_matrix_fact_extractor",
                confidence=0.88,
            )


def _extract_dimensional_table_facts(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> None:
    header_idx = _header_row_index(rows)
    standard_entities: dict[int, SemanticEntity] = {}
    for cell in rows.get(header_idx, [])[1:]:
        standards = extract_standards(cell.text)
        if not standards:
            continue
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="standard",
            canonical_name=standards[0],
            source_text=cell.text,
            confidence=0.85,
            metadata={"source_table_cell_id": cell.id},
        )
        standard_entities[cell.col_index] = entity
        _add_axis_binding(session, report, table, "column", cell.col_index, entity, cell.text, 0.85)
    for row_index, row_cells in rows.items():
        if row_index == header_idx:
            continue
        row_label = _row_label(row_cells)
        zones = extract_zones(row_label)
        if not zones:
            continue
        zone_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="zone",
            canonical_name=zones[0],
            source_text=row_label,
            confidence=0.9,
            metadata={"source_table_id": table.id, "row_index": row_index},
        )
        _add_axis_binding(session, report, table, "row", row_index, zone_entity, row_label, 0.9)
        for cell in row_cells[1:]:
            value = cell.text.strip()
            standard_entity = standard_entities.get(cell.col_index)
            if not value or standard_entity is None:
                continue
            fact = _create_fact(
                session,
                report,
                document_id=table.document_id,
                relation_type="dimensional_standard",
                subject=standard_entity,
                scope=zone_entity,
                value_text=value,
                normalized_value={"values": extract_numeric_values(value), "raw": value},
                assertion_type="explicit",
                confidence=0.82,
                metadata={"source_table_id": table.id, "source_table_cell_ids": [cell.id]},
            )
            _add_participant(session, report, fact, standard_entity, "standard", 0.85)
            _add_participant(session, report, fact, zone_entity, "zone", 0.9)
            _add_provenance(
                session,
                report,
                document_id=table.document_id,
                object_type="semantic_fact",
                object_id=fact.id,
                source_type="source_table_cell",
                source_id=cell.id,
                method="dimensional_matrix_fact_extractor",
                confidence=0.82,
            )


def _extract_parking_table_facts(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> None:
    header_idx = _header_row_index(rows)
    header_cells = rows.get(header_idx, [])
    zone_entities: dict[int, SemanticEntity] = {}
    for cell in header_cells[1:]:
        zones = extract_zones(cell.text)
        if not zones:
            continue
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="zone",
            canonical_name=zones[0],
            source_text=cell.text,
            confidence=0.9,
            metadata={"source_table_id": table.id, "source_table_cell_id": cell.id},
        )
        zone_entities[cell.col_index] = entity
        _add_axis_binding(session, report, table, "column", cell.col_index, entity, cell.text, 0.9)
    parking_standard = _get_or_create_entity(
        session,
        report,
        cache,
        document_id=table.document_id,
        entity_type="standard",
        canonical_name="parking spaces",
        source_text=table.caption or "parking spaces",
        confidence=0.85,
        metadata={"source_table_id": table.id},
    )
    for row_index, row_cells in rows.items():
        if row_index == header_idx or _is_repeated_header_row(row_cells):
            continue
        row_label = _row_label(row_cells)
        if not looks_like_use_label(row_label):
            continue
        use_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="use",
            canonical_name=normalize_use(row_label),
            source_text=row_label,
            confidence=0.88,
            metadata={"source_table_id": table.id, "row_index": row_index},
        )
        _add_axis_binding(session, report, table, "row", row_index, use_entity, row_label, 0.88)
        for cell in row_cells[1:]:
            value = cell.text.strip()
            zone_entity = zone_entities.get(cell.col_index)
            if not value or zone_entity is None:
                continue
            fact = _create_fact(
                session,
                report,
                document_id=table.document_id,
                relation_type="parking_standard",
                subject=use_entity,
                scope=zone_entity,
                value_text=value,
                normalized_value={"values": extract_numeric_values(value), "raw": value},
                assertion_type="explicit",
                confidence=0.84,
                metadata={
                    "source_table_id": table.id,
                    "source_table_cell_ids": [cell.id],
                    "row_index": row_index,
                    "col_index": cell.col_index,
                },
            )
            _add_participant(session, report, fact, use_entity, "use", 0.88)
            _add_participant(session, report, fact, zone_entity, "zone", 0.9)
            _add_participant(session, report, fact, parking_standard, "standard", 0.85)
            _add_provenance(
                session,
                report,
                document_id=table.document_id,
                object_type="semantic_fact",
                object_id=fact.id,
                source_type="source_table_cell",
                source_id=cell.id,
                method="parking_matrix_fact_extractor",
                confidence=0.84,
            )


def _extract_requirement_table_facts(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> None:
    header_idx = _header_row_index(rows)
    header_cells = rows.get(header_idx, [])
    context_entities: dict[int, SemanticEntity] = {}
    for cell in header_cells[1:]:
        contexts = extract_development_contexts(cell.text)
        if not contexts:
            contexts = [re.sub(r"\s+", " ", cell.text.strip().lower())] if cell.text.strip() else []
        if not contexts:
            continue
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="development_context",
            canonical_name=contexts[0],
            source_text=cell.text,
            confidence=0.82,
            metadata={"source_table_id": table.id, "source_table_cell_id": cell.id},
        )
        context_entities[cell.col_index] = entity
        _add_axis_binding(session, report, table, "column", cell.col_index, entity, cell.text, 0.82)
    for row_index, row_cells in rows.items():
        if row_index == header_idx:
            continue
        row_label = _row_label(row_cells)
        standards = extract_standards(row_label)
        contexts = extract_development_contexts(row_label)
        if not standards and not contexts:
            continue
        standard_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="standard",
            canonical_name=standards[0] if standards else contexts[0],
            source_text=row_label,
            confidence=0.82,
            metadata={"source_table_id": table.id, "row_index": row_index},
        )
        _add_axis_binding(session, report, table, "row", row_index, standard_entity, row_label, 0.82)
        for row_context in contexts:
            context_entity = _get_or_create_entity(
                session,
                report,
                cache,
                document_id=table.document_id,
                entity_type="development_context",
                canonical_name=row_context,
                source_text=row_label,
                confidence=0.76,
                metadata={"source_table_id": table.id, "row_index": row_index},
            )
            _add_axis_binding(session, report, table, "row", row_index, context_entity, row_label, 0.76)
        for cell in row_cells[1:]:
            value = cell.text.strip()
            if not value:
                continue
            context_entity = context_entities.get(cell.col_index)
            if context_entity is None:
                continue
            condition_entities = [
                _get_or_create_entity(
                    session,
                    report,
                    cache,
                    document_id=table.document_id,
                    entity_type="condition_ref",
                    canonical_name=condition,
                    source_text=condition,
                    confidence=0.86,
                    metadata={"source_table_cell_id": cell.id},
                )
                for condition in extract_condition_refs(value)
            ]
            fact = _create_fact(
                session,
                report,
                document_id=table.document_id,
                relation_type="requirement",
                subject=standard_entity,
                scope=context_entity,
                value_text=value,
                normalized_value={"values": extract_numeric_values(value), "raw": value},
                assertion_type="explicit",
                confidence=0.8,
                metadata={
                    "source_table_id": table.id,
                    "source_table_cell_ids": [cell.id],
                    "row_index": row_index,
                    "col_index": cell.col_index,
                },
            )
            _add_participant(session, report, fact, standard_entity, "standard", 0.82)
            _add_participant(session, report, fact, context_entity, "development_context", 0.82)
            for row_context in contexts:
                row_context_entity = _get_or_create_entity(
                    session,
                    report,
                    cache,
                    document_id=table.document_id,
                    entity_type="development_context",
                    canonical_name=row_context,
                    source_text=row_label,
                    confidence=0.76,
                    metadata={"source_table_id": table.id, "row_index": row_index},
                )
                _add_participant(session, report, fact, row_context_entity, "development_context", 0.76)
            for condition in condition_entities:
                _add_participant(session, report, fact, condition, "condition", 0.86)
                _add_edge(session, report, table.document_id, fact, "conditioned_by", condition, [cell.id], 0.8)
            _add_provenance(
                session,
                report,
                document_id=table.document_id,
                object_type="semantic_fact",
                object_id=fact.id,
                source_type="source_table_cell",
                source_id=cell.id,
                method="requirement_matrix_fact_extractor",
                confidence=0.8,
            )


def _enrich_cross_references(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    *,
    document_id: int,
) -> None:
    for ref in session.query(CrossReference).filter(CrossReference.document_id == document_id).all():
        source_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=document_id,
            entity_type="section_ref",
            canonical_name=ref.raw_reference_text,
            source_text=ref.raw_reference_text,
            confidence=0.75,
            metadata={"cross_reference_id": ref.id},
        )
        target_entity = None
        if ref.target_citation_guess:
            target_entity = _get_or_create_entity(
                session,
                report,
                cache,
                document_id=document_id,
                entity_type="section_ref",
                canonical_name=ref.target_citation_guess,
                source_text=ref.target_citation_guess,
                confidence=0.7,
                metadata={"cross_reference_id": ref.id},
            )
        edge = SemanticEdge(
            document_id=document_id,
            source_entity_id=source_entity.id,
            edge_type="references",
            target_entity_id=target_entity.id if target_entity else None,
            source_fragment_ids_json=[ref.source_fragment_id],
            confidence=ref.confidence,
            metadata_json={"cross_reference_id": ref.id},
        )
        session.add(edge)
        session.flush()
        report.edges += 1
        _add_provenance(
            session,
            report,
            document_id=document_id,
            object_type="semantic_edge",
            object_id=edge.id,
            source_type="cross_reference",
            source_id=ref.id,
            method="cross_reference_edge_extractor",
            confidence=ref.confidence,
        )


def _get_or_create_entity(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    *,
    document_id: int,
    entity_type: str,
    canonical_name: str,
    source_text: str | None,
    confidence: float,
    metadata: dict,
) -> SemanticEntity:
    canonical_name = canonical_name.strip()
    key = (entity_type, canonical_name.lower())
    if key in cache:
        return cache[key]
    existing = (
        session.query(SemanticEntity)
        .filter(
            SemanticEntity.document_id == document_id,
            SemanticEntity.entity_type == entity_type,
            SemanticEntity.canonical_name == canonical_name,
        )
        .first()
    )
    if existing:
        cache[key] = existing
        return existing
    entity = SemanticEntity(
        document_id=document_id,
        entity_type=entity_type,
        canonical_name=canonical_name,
        aliases_json=[],
        source_text=source_text,
        confidence=confidence,
        review_status=REVIEW_AUTO,
        metadata_json=metadata,
    )
    session.add(entity)
    session.flush()
    report.entities += 1
    cache[key] = entity
    return entity


def _create_fact(
    session: Session,
    report: SemanticEnrichmentReport,
    *,
    document_id: int,
    relation_type: str,
    subject: SemanticEntity | None = None,
    object_: SemanticEntity | None = None,
    scope: SemanticEntity | None = None,
    value_text: str | None,
    normalized_value: dict,
    assertion_type: str,
    confidence: float,
    metadata: dict,
) -> SemanticFact:
    fact = SemanticFact(
        document_id=document_id,
        relation_type=relation_type,
        primary_subject_entity_id=subject.id if subject else None,
        primary_object_entity_id=object_.id if object_ else None,
        primary_scope_entity_id=scope.id if scope else None,
        value_text=value_text,
        normalized_value_json=normalized_value,
        assertion_type=assertion_type,
        confidence=confidence,
        review_status=REVIEW_AUTO,
        metadata_json=metadata,
    )
    session.add(fact)
    session.flush()
    report.facts += 1
    return fact


def _add_participant(
    session: Session,
    report: SemanticEnrichmentReport,
    fact: SemanticFact,
    entity: SemanticEntity,
    role: str,
    confidence: float,
) -> None:
    session.add(
        SemanticFactParticipant(
            fact_id=fact.id,
            entity_id=entity.id,
            role=role,
            confidence=confidence,
            metadata_json={},
        )
    )
    report.fact_participants += 1


def _add_axis_binding(
    session: Session,
    report: SemanticEnrichmentReport,
    table: SourceTable,
    axis: str,
    index: int,
    entity: SemanticEntity,
    raw_label: str,
    confidence: float,
) -> None:
    binding = TableAxisBinding(
        table_id=table.id,
        axis=axis,
        index=index,
        entity_id=entity.id,
        raw_label=raw_label,
        confidence=confidence,
        metadata_json={},
    )
    session.add(binding)
    session.flush()
    report.axis_bindings += 1
    _add_provenance(
        session,
        report,
        document_id=table.document_id,
        object_type="table_axis_binding",
        object_id=binding.id,
        source_type="source_table",
        source_id=table.id,
        method="table_axis_binder",
        confidence=confidence,
    )


def _add_edge(
    session: Session,
    report: SemanticEnrichmentReport,
    document_id: int,
    source_fact: SemanticFact,
    edge_type: str,
    target_entity: SemanticEntity,
    source_ids: list[int],
    confidence: float,
) -> None:
    edge = SemanticEdge(
        document_id=document_id,
        source_fact_id=source_fact.id,
        edge_type=edge_type,
        target_entity_id=target_entity.id,
        source_fragment_ids_json=[],
        confidence=confidence,
        metadata_json={"source_table_cell_ids": source_ids},
    )
    session.add(edge)
    report.edges += 1


def _add_provenance(
    session: Session,
    report: SemanticEnrichmentReport,
    *,
    document_id: int,
    object_type: str,
    object_id: int,
    source_type: str,
    source_id: int | None,
    method: str,
    confidence: float | None,
) -> None:
    session.add(
        SemanticProvenance(
            document_id=document_id,
            object_type=object_type,
            object_id=object_id,
            source_type=source_type,
            source_id=source_id,
            extraction_method=method,
            extractor_version=EXTRACTOR_VERSION,
            confidence=confidence,
            metadata_json={},
        )
    )
    report.provenance += 1


def _rows_by_index(cells: Iterable[SourceTableCell]) -> dict[int, list[SourceTableCell]]:
    rows: dict[int, list[SourceTableCell]] = defaultdict(list)
    for cell in cells:
        rows[cell.row_index].append(cell)
    return {idx: sorted(row, key=lambda item: item.col_index) for idx, row in sorted(rows.items())}


def _header_row_index(rows: dict[int, list[SourceTableCell]]) -> int:
    scores = {}
    for row_index, row_cells in rows.items():
        labels = [_cell_text(cell) for cell in row_cells]
        scores[row_index] = len(
            [label for label in labels if extract_zones(label) or extract_standards(label) or extract_development_contexts(label)]
        )
    return max(scores, key=scores.get) if scores else 0


def _row_label(row_cells: list[SourceTableCell]) -> str:
    if not row_cells:
        return ""
    return _cell_text(sorted(row_cells, key=lambda item: item.col_index)[0])


def _cell_text(cell: SourceTableCell) -> str:
    return re.sub(r"\s+", " ", cell.text or "").strip()


def _zone_density(labels: list[str]) -> float:
    return sum(1 for label in labels if extract_zones(label)) / max(len(labels), 1)


def _is_repeated_header_row(row_cells: list[SourceTableCell]) -> bool:
    labels = [_cell_text(cell) for cell in row_cells]
    return len(labels) > 2 and _zone_density(labels[1:]) >= 0.5


def _normalize_permission_marker(marker: str) -> dict:
    stripped = marker.strip()
    conditions = extract_condition_refs(stripped)
    if stripped in PERMISSION_MARKERS:
        return {"permission": "permitted", "markers": []}
    if conditions:
        return {"permission": "conditional", "markers": conditions}
    return {"permission": "unknown", "raw": stripped}


def _refresh_counts(session: Session, report: SemanticEnrichmentReport) -> None:
    report.entities = session.query(SemanticEntity).filter(SemanticEntity.document_id == report.document_id).count()
    report.facts = session.query(SemanticFact).filter(SemanticFact.document_id == report.document_id).count()
    report.edges = session.query(SemanticEdge).filter(SemanticEdge.document_id == report.document_id).count()
    table_ids = [row.id for row in session.query(SourceTable.id).filter(SourceTable.document_id == report.document_id).all()]
    if table_ids:
        report.table_profiles = session.query(TableSemanticProfile).filter(TableSemanticProfile.table_id.in_(table_ids)).count()
        report.axis_bindings = session.query(TableAxisBinding).filter(TableAxisBinding.table_id.in_(table_ids)).count()
    fact_ids = [row.id for row in session.query(SemanticFact.id).filter(SemanticFact.document_id == report.document_id).all()]
    if fact_ids:
        report.fact_participants = (
            session.query(SemanticFactParticipant).filter(SemanticFactParticipant.fact_id.in_(fact_ids)).count()
        )
    report.provenance = session.query(SemanticProvenance).filter(SemanticProvenance.document_id == report.document_id).count()
