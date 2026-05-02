from __future__ import annotations

from collections import deque
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from layer1.db.base import (
    SemanticEdge,
    SemanticEntity,
    SemanticFact,
    SemanticFactParticipant,
    SourceFragment,
)
from layer1.semantic.extractors import extract_section_refs
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment


def expand_semantic_graph(
    session: Session,
    *,
    document_id: int,
    candidates: list[CandidateFragment],
    max_depth: int,
    max_fragments: int,
    max_nodes: int,
    allowed_edge_types: set[str],
) -> list[CandidateFragment]:
    if max_depth <= 0 or max_fragments <= 0 or max_nodes <= 0:
        return list(candidates)

    expanded = list(candidates)
    queue: deque[tuple[str, int, int, list[str]]] = deque()
    for candidate in candidates:
        for fact_id in _candidate_fact_ids(candidate):
            queue.append(("fact", fact_id, 0, [f"fact:{fact_id}"]))

    visited_facts: set[int] = set()
    visited_entities: set[int] = set()
    seen_fragment_ids = {candidate.source_fragment_id for candidate in candidates if candidate.source_fragment_id}
    expanded_fragments = 0
    visited_nodes = 0

    while queue and visited_nodes < max_nodes and expanded_fragments < max_fragments:
        node_type, node_id, depth, path = queue.popleft()
        if depth > max_depth:
            continue
        if node_type == "fact":
            if node_id in visited_facts:
                continue
            visited_facts.add(node_id)
            visited_nodes += 1
            fact = session.get(SemanticFact, node_id)
            if fact is None or fact.document_id != document_id:
                continue
            if depth == max_depth:
                continue
            participants = _participants(session, fact.id)
            for fragment in _section_fragments_from_fact(session, document_id, fact, participants):
                if fragment.id in seen_fragment_ids or expanded_fragments >= max_fragments:
                    continue
                expanded.append(_fragment_candidate(fragment, depth + 1, path, "semantic_section_reference"))
                seen_fragment_ids.add(fragment.id)
                expanded_fragments += 1
            for edge in _edges_from_fact(session, fact.id, allowed_edge_types):
                _append_edge_targets(queue, edge, depth + 1, path, priority=edge.edge_type == "references")
            for participant in sorted(participants, key=_participant_priority):
                queue.append(("entity", participant.entity_id, depth + 1, [*path, f"entity:{participant.entity_id}"]))
            for fragment in _fragments_from_fact_metadata(session, fact):
                if fragment.id in seen_fragment_ids or expanded_fragments >= max_fragments:
                    continue
                expanded.append(_fragment_candidate(fragment, depth, path, "semantic_fact_source"))
                seen_fragment_ids.add(fragment.id)
                expanded_fragments += 1
        else:
            if node_id in visited_entities:
                continue
            visited_entities.add(node_id)
            visited_nodes += 1
            entity = session.get(SemanticEntity, node_id)
            if entity is None or entity.document_id != document_id:
                continue
            if entity.entity_type == "section_ref":
                for fragment in _resolve_section_fragments(session, document_id, entity.canonical_name):
                    if fragment.id in seen_fragment_ids or expanded_fragments >= max_fragments:
                        continue
                    expanded.append(_fragment_candidate(fragment, depth, path, "semantic_section_reference"))
                    seen_fragment_ids.add(fragment.id)
                    expanded_fragments += 1
            if depth == max_depth:
                continue
            for participant in _facts_for_entity(session, entity.id):
                queue.append(("fact", participant.fact_id, depth + 1, [*path, f"fact:{participant.fact_id}"]))
            for edge in _edges_from_entity(session, entity.id, allowed_edge_types):
                _append_edge_targets(queue, edge, depth + 1, path, priority=edge.edge_type == "references")

    return expanded


def allowed_edge_types_from_string(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _candidate_fact_ids(candidate: CandidateFragment) -> list[int]:
    fact_ids = []
    if candidate.metadata.get("semantic_fact_id"):
        fact_ids.append(int(candidate.metadata["semantic_fact_id"]))
    fact_ids.extend(int(fact_id) for fact_id in candidate.metadata.get("semantic_fact_ids") or [])
    fact_ids.extend(int(fact_id) for fact_id in candidate.reason.get("semantic_fact_ids") or [])
    return sorted(set(fact_ids))


def _participants(session: Session, fact_id: int) -> list[SemanticFactParticipant]:
    return session.query(SemanticFactParticipant).filter(SemanticFactParticipant.fact_id == fact_id).all()


def _facts_for_entity(session: Session, entity_id: int) -> list[SemanticFactParticipant]:
    participants = session.query(SemanticFactParticipant).filter(SemanticFactParticipant.entity_id == entity_id).all()
    fact_ids = [participant.fact_id for participant in participants]
    facts = {fact.id: fact for fact in session.query(SemanticFact).filter(SemanticFact.id.in_(fact_ids)).all()} if fact_ids else {}
    return sorted(participants, key=lambda participant: _fact_priority(facts.get(participant.fact_id)))


def _edges_from_fact(session: Session, fact_id: int, allowed_edge_types: set[str]) -> list[SemanticEdge]:
    return (
        session.query(SemanticEdge)
        .filter(SemanticEdge.source_fact_id == fact_id, SemanticEdge.edge_type.in_(allowed_edge_types))
        .all()
    )


def _edges_from_entity(session: Session, entity_id: int, allowed_edge_types: set[str]) -> list[SemanticEdge]:
    return (
        session.query(SemanticEdge)
        .filter(SemanticEdge.source_entity_id == entity_id, SemanticEdge.edge_type.in_(allowed_edge_types))
        .all()
    )


def _append_edge_targets(
    queue: deque[tuple[str, int, int, list[str]]],
    edge: SemanticEdge,
    depth: int,
    path: list[str],
    *,
    priority: bool = False,
) -> None:
    edge_path = [*path, f"edge:{edge.edge_type}:{edge.id}"]
    if edge.target_fact_id:
        item = ("fact", edge.target_fact_id, depth, [*edge_path, f"fact:{edge.target_fact_id}"])
        queue.appendleft(item) if priority else queue.append(item)
    if edge.target_entity_id:
        item = ("entity", edge.target_entity_id, depth, [*edge_path, f"entity:{edge.target_entity_id}"])
        queue.appendleft(item) if priority else queue.append(item)


def _participant_priority(participant: SemanticFactParticipant) -> tuple[int, int]:
    priority = {"section_ref": 0, "condition": 1, "defined_term": 2}
    return (priority.get(participant.role, 10), participant.id)


def _fact_priority(fact: SemanticFact | None) -> tuple[int, int]:
    if fact is None:
        return (10, 0)
    priority = {"condition_definition": 0, "definition": 1, "requirement": 2}
    return (priority.get(fact.relation_type, 10), fact.id)


def _fragments_from_fact_metadata(session: Session, fact: SemanticFact) -> list[SourceFragment]:
    fragment_ids = fact.metadata_json.get("source_fragment_ids") or []
    fragments = []
    for fragment_id in fragment_ids:
        fragment = session.get(SourceFragment, fragment_id)
        if fragment is not None:
            fragments.append(fragment)
    return fragments


def _section_fragments_from_fact(
    session: Session,
    document_id: int,
    fact: SemanticFact,
    participants: list[SemanticFactParticipant],
) -> list[SourceFragment]:
    section_refs = set(extract_section_refs(fact.value_text or ""))
    for participant in participants:
        entity = session.get(SemanticEntity, participant.entity_id)
        if entity and entity.entity_type == "section_ref":
            section_refs.add(entity.canonical_name)
    fragments = []
    seen = set()
    for section_ref in sorted(section_refs):
        for fragment in _resolve_section_fragments(session, document_id, section_ref):
            if fragment.id in seen:
                continue
            seen.add(fragment.id)
            fragments.append(fragment)
    return fragments


def _resolve_section_fragments(session: Session, document_id: int, section_ref: str) -> list[SourceFragment]:
    labels = _section_label_candidates(section_ref)
    filters = []
    for label in labels:
        filters.extend(
            [
                SourceFragment.citation_label == label,
                SourceFragment.citation_path == label,
                SourceFragment.citation_path.ilike(f"%{label}%"),
                SourceFragment.text.ilike(f"{label}%"),
            ]
        )
    if not filters:
        return []
    direct_matches = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .filter(or_(*filters))
        .order_by(SourceFragment.page_start, SourceFragment.id)
        .limit(3)
        .all()
    )
    if direct_matches:
        return direct_matches
    key = _section_key(section_ref)
    broad_terms = [label for label in labels if any(char.isdigit() for char in label)]
    broad_filters = []
    for term in broad_terms:
        number = re.search(r"\d+", term)
        if number:
            broad_filters.extend(
                [
                    SourceFragment.citation_label.ilike(f"%{number.group(0)}%"),
                    SourceFragment.citation_path.ilike(f"%{number.group(0)}%"),
                    SourceFragment.text.ilike(f"%{number.group(0)}%"),
                ]
            )
    if not key or not broad_filters:
        return []
    candidates = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .filter(or_(*broad_filters))
        .order_by(SourceFragment.page_start, SourceFragment.id)
        .limit(50)
        .all()
    )
    return [
        fragment
        for fragment in candidates
        if key
        in _section_key(" ".join(filter(None, [fragment.citation_label, fragment.citation_path, fragment.text[:80]])))
    ][:3]


def _section_label_candidates(section_ref: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", section_ref.strip())
    without_prefix = re.sub(r"^(section|subsection|clause)\s+", "", normalized, flags=re.I).strip()
    candidates = [normalized]
    if without_prefix and without_prefix != normalized:
        candidates.append(without_prefix)
        candidates.append(re.sub(r"(\d+)\((\d+)\)", r"\1 (\2)", without_prefix))
        candidates.append(f"Section {without_prefix}")
    return list(dict.fromkeys(candidates))


def _section_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _fragment_candidate(fragment: SourceFragment, depth: int, path: list[str], expansion: str) -> CandidateFragment:
    return CandidateFragment(
        source_fragment_id=fragment.id,
        source_type=SourceType.FRAGMENT.value,
        retrieval_channel=RetrievalChannel.CROSS_REFERENCE.value,
        base_score=max(8.0 - (0.5 * depth), 0.5),
        text=fragment.text,
        citation_label=fragment.citation_label,
        citation_path=fragment.citation_path,
        reason={"expansion": expansion, "semantic_graph_depth": depth, "semantic_graph_path": path},
    )
