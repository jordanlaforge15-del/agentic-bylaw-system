from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from layer1.db.base import (
    SemanticEntity,
    SemanticFact,
    SemanticFactParticipant,
    SourceFragment,
    SourceTable,
    SourceTableCell,
)
from layer1.semantic.extractors import (
    extract_condition_refs,
    extract_development_contexts,
    extract_standards,
    extract_zones,
    normalize_use,
)
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment


def retrieve_semantic_facts(
    session: Session,
    *,
    document_id: int,
    question_text: str,
    top_k: int,
) -> list[CandidateFragment]:
    request = _semantic_request_from_question(question_text)
    if not request["entities"]:
        return []
    facts = _find_facts(session, document_id=document_id, request=request)
    candidates: list[CandidateFragment] = []
    aggregate = _permission_zone_summary_candidate(session, facts, request)
    if aggregate:
        candidates.append(aggregate)
    candidates.extend(_fact_to_candidate(session, fact, request) for fact in facts[: max(top_k, 8)])
    return candidates[: max(top_k, 8)]


def _semantic_request_from_question(question_text: str) -> dict[str, Any]:
    normalized = question_text.lower()
    relation_types = []
    standards = extract_standards(question_text)
    condition_refs = extract_condition_refs(question_text)
    development_contexts = extract_development_contexts(question_text)
    if any(token in normalized for token in ["permitted", "allowed", "can i", "can a", "can an", "operate"]):
        relation_types.append("permission")
    if "condition" in normalized and any(token in normalized for token in ["apply", "applies", "permitted", "allowed"]):
        relation_types.append("permission")
    if "parking" in normalized:
        relation_types.append("parking_standard")
    if development_contexts:
        relation_types.append("requirement")
    if standards or any(
        term in normalized for term in ["minimum", "maximum", "required", "height", "setback", "frontage", "lot area", "lot coverage"]
    ):
        relation_types.append("dimensional_standard")
    if condition_refs:
        relation_types.append("condition_definition")
    if "define" in normalized or normalized.startswith("what is "):
        relation_types.append("definition")
    zones = extract_zones(question_text)
    use_name = _use_name_from_question(normalized)
    defined_term = _defined_term_from_question(normalized) if "definition" in relation_types and not zones and not standards else None
    entities = []
    entities.extend({"type": "zone", "name": zone} for zone in zones)
    entities.extend({"type": "standard", "name": standard} for standard in standards)
    entities.extend({"type": "development_context", "name": context} for context in development_contexts)
    entities.extend({"type": "condition_ref", "name": condition_ref} for condition_ref in condition_refs)
    if use_name:
        entities.append({"type": "use", "name": use_name})
    if defined_term:
        entities.append({"type": "defined_term", "name": defined_term})
    relation_types = list(dict.fromkeys(relation_types))
    return {"relation_types": relation_types, "entities": entities}


def _find_facts(session: Session, *, document_id: int, request: dict[str, Any]) -> list[SemanticFact]:
    relation_types = request["relation_types"]
    query = session.query(SemanticFact).filter(SemanticFact.document_id == document_id)
    if relation_types:
        query = query.filter(SemanticFact.relation_type.in_(relation_types))
    facts = query.order_by(SemanticFact.confidence.desc(), SemanticFact.id).all()
    scored = []
    requested_entities = request["entities"]
    for fact in facts:
        participants = _participants(session, fact.id)
        score = _fact_match_score(session, participants, requested_entities)
        if score <= 0:
            continue
        relation_bonus = 1.0 if fact.relation_type in relation_types else 0.0
        scored.append((score + relation_bonus + (fact.confidence or 0.0), fact))
    return [fact for _, fact in sorted(scored, key=lambda item: item[0], reverse=True)]


def _permission_zone_summary_candidate(
    session: Session,
    facts: list[SemanticFact],
    request: dict[str, Any],
) -> CandidateFragment | None:
    if "permission" not in request["relation_types"]:
        return None
    requested_zones = {entity["name"] for entity in request["entities"] if entity["type"] == "zone"}
    requested_uses = {entity["name"] for entity in request["entities"] if entity["type"] == "use"}
    if not requested_zones or requested_uses:
        return None

    rows = []
    source_table_id = None
    source_table_cell_ids: list[int] = []
    semantic_fact_ids = []
    for fact in facts:
        if fact.relation_type != "permission":
            continue
        participants = _fact_entities_by_role(session, fact.id)
        zone = participants.get("zone")
        use = participants.get("use")
        if zone is None or use is None or zone.canonical_name not in requested_zones:
            continue
        permission = (fact.normalized_value_json or {}).get("permission") or "permission"
        value = fact.value_text or permission
        rows.append((use.canonical_name, permission, value, fact))
        semantic_fact_ids.append(fact.id)
        metadata = fact.metadata_json or {}
        source_table_id = source_table_id or metadata.get("source_table_id")
        source_table_cell_ids.extend(metadata.get("source_table_cell_ids") or [])

    if not rows:
        return None

    rows = sorted(rows, key=lambda item: item[0])
    zone_label = sorted(requested_zones)[0]
    table = session.get(SourceTable, source_table_id) if source_table_id else None
    fragments = [f"Semantic facts (permission): permitted uses for {zone_label}"]
    fragments.extend(f"{use_name}: {permission} ({value})" for use_name, permission, value, _fact in rows)
    if table and table.caption:
        fragments.append(f"source table: {table.caption}")
    unique_cell_ids = sorted(set(source_table_cell_ids))
    for cell_id in unique_cell_ids[:12]:
        cell = session.get(SourceTableCell, cell_id)
        if cell:
            fragments.append(f"source cell row {cell.row_index}, column {cell.col_index}: {cell.text}")

    return CandidateFragment(
        source_table_id=source_table_id,
        source_table_cell_id=unique_cell_ids[0] if unique_cell_ids else None,
        source_type=SourceType.TABLE.value,
        retrieval_channel=RetrievalChannel.TABLE.value,
        base_score=30.0 + max((fact.confidence or 0.0 for *_rest, fact in rows), default=0.0),
        text=" | ".join(fragments),
        citation_label=table.caption if table else None,
        citation_path=None,
        reason={
            "operation": "retrieve_semantic_facts",
            "aggregation": "permission_zone_summary",
            "relation_type": "permission",
            "semantic_request": request,
            "semantic_fact_ids": semantic_fact_ids,
        },
        metadata={
            "semantic_fact_ids": semantic_fact_ids,
            "source_table_id": source_table_id,
            "source_table_cell_ids": unique_cell_ids,
        },
    )


def _fact_entities_by_role(session: Session, fact_id: int) -> dict[str, SemanticEntity]:
    entities = {}
    for participant in _participants(session, fact_id):
        entity = session.get(SemanticEntity, participant.entity_id)
        if entity:
            entities[participant.role] = entity
    return entities


def _fact_match_score(
    session: Session,
    participants: list[SemanticFactParticipant],
    requested_entities: list[dict[str, str]],
) -> float:
    if not requested_entities:
        return 0.0
    participant_entities = [session.get(SemanticEntity, participant.entity_id) for participant in participants]
    grouped: dict[str, list[str]] = {}
    for requested in requested_entities:
        grouped.setdefault(requested["type"], []).append(requested["name"])
    score = 0.0
    for entity_type, requested_names in grouped.items():
        matched = False
        for entity in participant_entities:
            if entity is None or entity.entity_type != entity_type:
                continue
            if any(_entity_name_matches(entity, requested_name) for requested_name in requested_names):
                matched = True
                break
        if matched:
            score += 2.0
    required_score = len(grouped) * 2.0
    return score if score >= required_score else 0.0


def _entity_name_matches(entity: SemanticEntity, requested_name: str) -> bool:
    requested = requested_name.lower()
    canonical = entity.canonical_name.lower()
    if requested == canonical:
        return True
    if requested.replace("-", "") == canonical.replace("-", ""):
        return True
    if requested.rstrip("s") == canonical.rstrip("s"):
        return True
    return requested in [str(alias).lower() for alias in entity.aliases_json or []]


def _fact_to_candidate(session: Session, fact: SemanticFact, request: dict[str, Any]) -> CandidateFragment:
    metadata = dict(fact.metadata_json or {})
    source_table_id = metadata.get("source_table_id")
    source_table_cell_ids = metadata.get("source_table_cell_ids") or []
    source_table_cell_id = source_table_cell_ids[0] if source_table_cell_ids else None
    source_fragment_ids = metadata.get("source_fragment_ids") or []
    source_fragment_id = source_fragment_ids[0] if source_fragment_ids else None
    evidence = _evidence_text(session, fact, source_table_id, source_table_cell_ids, source_fragment_ids)
    return CandidateFragment(
        source_fragment_id=source_fragment_id,
        source_table_id=source_table_id,
        source_table_cell_id=source_table_cell_id,
        source_type=SourceType.TABLE.value if source_table_id else SourceType.FRAGMENT.value,
        retrieval_channel=RetrievalChannel.TABLE.value if source_table_id else RetrievalChannel.FULL_TEXT.value,
        base_score=20.0 + (fact.confidence or 0.0),
        text=evidence,
        citation_label=_citation_label(session, source_table_id, source_fragment_id),
        citation_path=None,
        reason={
            "operation": "retrieve_semantic_facts",
            "semantic_fact_id": fact.id,
            "relation_type": fact.relation_type,
            "semantic_request": request,
        },
        metadata={"semantic_fact_id": fact.id, **metadata},
    )


def _evidence_text(
    session: Session,
    fact: SemanticFact,
    table_id: int | None,
    cell_ids: list[int],
    fragment_ids: list[int],
) -> str:
    participants = []
    condition_entity_ids = []
    for participant in _participants(session, fact.id):
        entity = session.get(SemanticEntity, participant.entity_id)
        if entity:
            participants.append(f"{participant.role}: {entity.canonical_name}")
            if participant.role == "condition":
                condition_entity_ids.append(entity.id)
    pieces = [f"Semantic fact ({fact.relation_type}): {'; '.join(participants)}"]
    if fact.value_text:
        pieces.append(f"value: {fact.value_text}")
    if table_id:
        table = session.get(SourceTable, table_id)
        if table and table.caption:
            pieces.append(f"source table: {table.caption}")
    for cell_id in cell_ids:
        cell = session.get(SourceTableCell, cell_id)
        if cell:
            pieces.append(f"source cell row {cell.row_index}, column {cell.col_index}: {cell.text}")
    for fragment_id in fragment_ids:
        fragment = session.get(SourceFragment, fragment_id)
        if fragment:
            pieces.append(fragment.text)
    for definition in _condition_definition_texts(session, fact.document_id, condition_entity_ids):
        pieces.append(f"condition definition: {definition}")
    return " | ".join(pieces)


def _condition_definition_texts(session: Session, document_id: int, entity_ids: list[int]) -> list[str]:
    if not entity_ids:
        return []
    rows = (
        session.query(SemanticFact)
        .join(SemanticFactParticipant, SemanticFactParticipant.fact_id == SemanticFact.id)
        .filter(SemanticFact.document_id == document_id)
        .filter(SemanticFact.relation_type == "condition_definition")
        .filter(SemanticFactParticipant.entity_id.in_(entity_ids))
        .order_by(SemanticFact.id)
        .all()
    )
    return [fact.value_text for fact in rows if fact.value_text]


def _citation_label(session: Session, table_id: int | None, fragment_id: int | None) -> str | None:
    if table_id:
        table = session.get(SourceTable, table_id)
        return table.caption if table else None
    if fragment_id:
        fragment = session.get(SourceFragment, fragment_id)
        return fragment.citation_label if fragment else None
    return None


def _participants(session: Session, fact_id: int) -> list[SemanticFactParticipant]:
    return (
        session.query(SemanticFactParticipant)
        .filter(SemanticFactParticipant.fact_id == fact_id)
        .order_by(SemanticFactParticipant.id)
        .all()
    )


def _use_name_from_question(normalized_question: str) -> str | None:
    cleaned = re.sub(r"[^a-z0-9 -]+", " ", normalized_question)
    stop_prefixes = {
        "a",
        "an",
        "the",
        "what",
        "which",
        "does",
        "do",
        "condition",
        "conditions",
        "apply",
        "applies",
        "to",
        "for",
        "require",
        "requires",
        "parking",
        "operate",
        "allowed",
        "permitted",
        "can",
        "i",
        "is",
        "are",
    }
    candidates = []
    for match in re.finditer(r"\b([a-z][a-z0-9-]*(?:\s+[a-z][a-z0-9-]*){0,5}\s+use)\b", cleaned):
        words = match.group(1).split()
        while words and (words[0] in stop_prefixes or re.fullmatch(r"[a-z]{1,4}-?\d[a-z]?|\d+", words[0])):
            words.pop(0)
        candidate = normalize_use(" ".join(words))
        if candidate and candidate not in {"land use", "permitted use", "temporary use"}:
            candidates.append(candidate)
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (len(candidate.split()), len(candidate)))


def _defined_term_from_question(normalized_question: str) -> str | None:
    cleaned = re.sub(r"[^a-z0-9 -]+", " ", normalized_question).strip()
    for prefix in ["what is a ", "what is an ", "what is the ", "what is ", "define a ", "define an ", "define the ", "define "]:
        if cleaned.startswith(prefix):
            term = cleaned[len(prefix) :].strip()
            return normalize_use(term) if term else None
    return None
