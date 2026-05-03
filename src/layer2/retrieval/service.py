from __future__ import annotations

from math import sqrt
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from layer1.db.base import Document, SourceFragment, SourceTable, SourceTableCell
from layer2.config import Layer2Settings
from layer2.db.models import FragmentEmbedding, GeneratedClaim, RetrievalFeedback
from layer2.embeddings.base import BaseEmbeddingClient
from layer2.llm.base import BaseLLMClient
from layer2.models.enums import RetrievalChannel, SourceType, VerificationStatus
from layer2.models.schemas import CachedClaimContext, CandidateFragment, QueryUnderstanding, RetrievalBundle
from layer2.retrieval.datasets import expand_datasets
from layer2.retrieval.expansion import expand_cross_references, expand_hierarchy
from layer2.retrieval.merge import merge_and_dedupe_candidates
from layer2.retrieval.api import execute_retrieval_plan
from layer2.retrieval.planner import create_retrieval_plan
from layer2.retrieval.query_understanding import understand_question
from layer2.retrieval.semantic_graph import allowed_edge_types_from_string, expand_semantic_graph
from layer2.retrieval.semantic import retrieve_semantic_facts
from layer2.rerank.heuristic import rerank_candidates

SEARCH_STOPWORDS = {
    "what",
    "are",
    "the",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "can",
    "how",
    "many",
    "does",
    "zone",
    "uses",
    "use",
}


def _normalized_terms(question: str) -> list[str]:
    return [
        term
        for term in "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in question).lower().split()
        if len(term) > 1 and term not in SEARCH_STOPWORDS
    ]


def build_metadata_filters(
    session: Session,
    *,
    document_id: int | None,
    municipality: str | None,
    known_facts: dict[str, Any] | None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {"document_id": document_id, "municipality": municipality}
    if known_facts:
        filters["known_facts"] = known_facts
    if document_id is None and municipality:
        document = session.query(Document).filter(Document.municipality.ilike(municipality)).first()
        if document:
            filters["document_id"] = document.id
    return filters


def full_text_candidates(
    session: Session,
    *,
    document_id: int,
    understanding: QueryUnderstanding,
    top_k: int,
) -> list[CandidateFragment]:
    terms = [term for term in _normalized_terms(understanding.normalized_question) if len(term) > 2]
    if not terms:
        return []
    combined = " ".join(terms)
    if session.bind and session.bind.dialect.name == "postgresql":
        rank = func.ts_rank_cd(
            func.to_tsvector(
                "english",
                func.coalesce(SourceFragment.citation_label, "") + " " + func.coalesce(SourceFragment.text, ""),
            ),
            func.plainto_tsquery("english", combined),
        )
        rows = (
            session.query(SourceFragment, rank.label("rank"))
            .filter(SourceFragment.document_id == document_id)
            .filter(
                func.to_tsvector(
                    "english",
                    func.coalesce(SourceFragment.citation_label, "") + " " + func.coalesce(SourceFragment.text, ""),
                ).op("@@")(func.plainto_tsquery("english", combined))
            )
            .order_by(rank.desc())
            .limit(top_k)
            .all()
        )
        return [
            CandidateFragment(
                source_fragment_id=fragment.id,
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=float(score or 0.0),
                text=fragment.text,
                citation_label=fragment.citation_label,
                citation_path=fragment.citation_path,
                reason={"terms": terms},
            )
            for fragment, score in rows
        ]
    like_filters = [SourceFragment.text.ilike(f"%{term}%") for term in terms]
    rows = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .filter(or_(*like_filters))
        .limit(top_k * 2)
        .all()
    )
    candidates = []
    for fragment in rows:
        score = sum(1 for term in terms if term in fragment.text.lower() or term in (fragment.citation_label or "").lower())
        candidates.append(
            CandidateFragment(
                source_fragment_id=fragment.id,
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=score / max(len(terms), 1),
                text=fragment.text,
                citation_label=fragment.citation_label,
                citation_path=fragment.citation_path,
                reason={"terms": terms},
            )
        )
    return sorted(candidates, key=lambda item: item.base_score, reverse=True)[:top_k]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left)) or 1.0
    right_norm = sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)


def vector_candidates(
    session: Session,
    *,
    document_id: int,
    question_text: str,
    embedding_client: BaseEmbeddingClient,
    top_k: int,
) -> list[CandidateFragment]:
    query_embedding = embedding_client.embed_text(question_text)
    embeddings = (
        session.query(FragmentEmbedding, SourceFragment)
        .join(SourceFragment, SourceFragment.id == FragmentEmbedding.source_fragment_id)
        .filter(FragmentEmbedding.document_id == document_id)
        .all()
    )
    scored = []
    for embedding_row, fragment in embeddings:
        score = _cosine_similarity(query_embedding, list(embedding_row.embedding))
        scored.append(
            CandidateFragment(
                source_fragment_id=fragment.id,
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.VECTOR.value,
                base_score=score,
                text=fragment.text,
                citation_label=fragment.citation_label,
                citation_path=fragment.citation_path,
                reason={"embedding_model": embedding_row.embedding_model},
            )
        )
    return sorted(scored, key=lambda item: item.base_score, reverse=True)[:top_k]


def table_candidates(session: Session, *, document_id: int, understanding: QueryUnderstanding, top_k: int) -> list[CandidateFragment]:
    joined_rows = (
        session.query(SourceTableCell, SourceTable)
        .join(SourceTable, SourceTable.id == SourceTableCell.table_id)
        .filter(SourceTable.document_id == document_id)
        .all()
    )
    terms = _normalized_terms(understanding.normalized_question)
    row_zone_hits: set[tuple[int, int]] = set()
    for cell, _table in joined_rows:
        cell_terms = " ".join(filter(None, [cell.text, cell.row_header_path, cell.col_header_path])).upper()
        normalized_cell_terms = cell_terms.replace("-", "")
        if any(zone in normalized_cell_terms for zone in understanding.zone_keywords):
            row_zone_hits.add((cell.table_id, cell.row_index))
    candidates = []

    dimensional_query = any(topic in understanding.topics for topic in {"height", "setback", "parking"}) or any(
        concept in {"minimum lot area", "maximum height", "lot frontage", "lot coverage"}
        for concept in understanding.legal_concepts
    )

    grouped_by_table: dict[int, list[tuple[SourceTableCell, SourceTable]]] = {}
    for cell, table in joined_rows:
        grouped_by_table.setdefault(table.id, []).append((cell, table))

    if dimensional_query:
        ordered_tables = sorted(
            {
                table.id: table
                for _cell, table in joined_rows
            }.values(),
            key=lambda table: (table.page_start, table.id),
        )
        nearby_zone_context: dict[int, str] = {}
        for idx, table in enumerate(ordered_tables):
            context = ""
            for prev_table in reversed(ordered_tables[max(0, idx - 3) : idx]):
                if prev_table.page_start != table.page_start:
                    continue
                prev_rows = grouped_by_table.get(prev_table.id, [])
                prev_text = " ".join(
                    " ".join(filter(None, [cell.text, cell.row_header_path, cell.col_header_path]))
                    for cell, _ in prev_rows[:6]
                )
                normalized_prev_text = prev_text.upper().replace("-", "")
                if understanding.zone_keywords and any(zone in normalized_prev_text for zone in understanding.zone_keywords):
                    context = prev_text
                    break
            nearby_zone_context[table.id] = context

        for rows in grouped_by_table.values():
            ordered = sorted(rows, key=lambda item: (item[0].row_index, item[0].col_index))
            for idx, (cell, table) in enumerate(ordered):
                base_text = " ".join(filter(None, [cell.text, cell.row_header_path, cell.col_header_path, table.caption]))
                lower_text = base_text.lower()
                if not any(
                    marker in lower_text
                    for marker in [
                        "height maximum",
                        "maximum height",
                        "lot area minimum",
                        "minimum lot area",
                        "lot frontage minimum",
                        "minimum frontage",
                        "lot coverage maximum",
                        "maximum coverage",
                    ]
                ):
                    continue

                pair_text = base_text
                cell_ids = [cell.id]
                for next_cell, _next_table in ordered[idx + 1 : idx + 4]:
                    if next_cell.row_index == cell.row_index:
                        continue
                    next_text = " ".join(filter(None, [next_cell.text, next_cell.row_header_path, next_cell.col_header_path]))
                    if any(
                        marker in next_text.lower()
                        for marker in [
                            "height maximum",
                            "maximum height",
                            "lot area minimum",
                            "minimum lot area",
                            "lot frontage minimum",
                            "minimum frontage",
                            "lot coverage maximum",
                            "maximum coverage",
                        ]
                    ):
                        break
                    pair_text = f"{base_text} {next_text}".strip()
                    cell_ids.append(next_cell.id)
                    break

                normalized_pair_text = pair_text.upper().replace("-", "")
                if understanding.zone_keywords and not any(zone in normalized_pair_text for zone in understanding.zone_keywords):
                    zone_context = nearby_zone_context.get(table.id)
                    if zone_context:
                        pair_text = f"{zone_context} {pair_text}".strip()
                        normalized_pair_text = pair_text.upper().replace("-", "")
                row_hit = (cell.table_id, cell.row_index) in row_zone_hits
                score = 1.5
                if row_hit:
                    score += 1.5
                if any(zone in normalized_pair_text for zone in understanding.zone_keywords):
                    score += 1.0
                if "height" in lower_text and "height" in understanding.normalized_question:
                    score += 1.5
                if any(token in understanding.normalized_question for token in ["story", "stories", "storey", "storeys"]):
                    score += 1.0
                candidates.append(
                    CandidateFragment(
                        source_table_id=table.id,
                        source_table_cell_id=cell.id,
                        source_type=SourceType.TABLE_CELL.value,
                        retrieval_channel=RetrievalChannel.TABLE.value,
                        base_score=score,
                        text=pair_text,
                        citation_label=table.caption,
                        citation_path=None,
                        reason={
                            "row_index": cell.row_index,
                            "col_index": cell.col_index,
                            "row_zone_hit": row_hit,
                            "cell_ids": cell_ids,
                            "pattern": "dimensional_pair",
                        },
                    )
                )

    for cell, table in joined_rows:
        cell_text = " ".join(filter(None, [cell.text, cell.row_header_path, cell.col_header_path, table.caption]))
        score = sum(1 for term in terms if term in cell_text.lower())
        row_hit = (cell.table_id, cell.row_index) in row_zone_hits
        column_hint = any(term in cell_text.lower() for term in ["lot", "area", "parking", "zone"])
        if row_hit:
            score += 2
        if understanding.zone_keywords and any(zone in cell_text.upper().replace("-", "") for zone in understanding.zone_keywords):
            score += 2
        if score == 0 and not column_hint and not row_hit:
            continue
        candidates.append(
            CandidateFragment(
                source_table_id=table.id,
                source_table_cell_id=cell.id,
                source_type=SourceType.TABLE_CELL.value,
                retrieval_channel=RetrievalChannel.TABLE.value,
                base_score=max(score / max(len(terms), 1), 0.25),
                text=cell_text,
                citation_label=table.caption,
                citation_path=None,
                reason={"row_index": cell.row_index, "col_index": cell.col_index, "row_zone_hit": row_hit},
            )
        )
    return sorted(candidates, key=lambda item: item.base_score, reverse=True)[:top_k]


def cached_claim_candidates(
    session: Session,
    *,
    document_id: int,
    understanding: QueryUnderstanding,
    max_claims: int,
) -> list[CachedClaimContext]:
    query = (
        session.query(GeneratedClaim)
        .filter(
            GeneratedClaim.document_id == document_id,
            GeneratedClaim.verification_status == VerificationStatus.VERIFIED,
        )
        .order_by(GeneratedClaim.created_at.desc())
    )
    claims = query.limit(max_claims * 3).all()
    scored = []
    for claim in claims:
        text = " ".join(
            filter(
                None,
                [
                    claim.topic,
                    claim.canonical_subject,
                    claim.canonical_predicate,
                    claim.canonical_object_text,
                    claim.normalized_value_text,
                ],
            )
        ).lower()
        score = sum(1 for term in understanding.normalized_question.split() if term in text)
        scored.append((score, claim))
    scored.sort(key=lambda item: (item[0], item[1].confidence or 0.0), reverse=True)
    return [
        CachedClaimContext(
            claim_id=claim.id,
            claim_type=claim.claim_type.value,
            topic=claim.topic,
            text=" ".join(filter(None, [claim.topic, claim.canonical_object_text, claim.normalized_value_text])),
            source_fragment_ids=list(claim.source_fragment_ids_json),
            verification_status=claim.verification_status.value,
            confidence=claim.confidence,
        )
        for score, claim in scored[:max_claims]
        if score > 0 or claim.confidence
    ]


def apply_feedback_adjustments(
    session: Session,
    *,
    document_id: int,
    candidates: list[CandidateFragment],
) -> list[CandidateFragment]:
    relevant_feedback = session.query(RetrievalFeedback).all()
    missing_counts: dict[int, int] = {}
    irrelevant_counts: dict[int, int] = {}
    for feedback in relevant_feedback:
        if feedback.missing_source_fragment_id:
            missing_counts[feedback.missing_source_fragment_id] = missing_counts.get(feedback.missing_source_fragment_id, 0) + 1
        if feedback.irrelevant_source_fragment_id:
            irrelevant_counts[feedback.irrelevant_source_fragment_id] = irrelevant_counts.get(feedback.irrelevant_source_fragment_id, 0) + 1
    for candidate in candidates:
        if not candidate.source_fragment_id:
            continue
        candidate.rerank_score += 0.15 * missing_counts.get(candidate.source_fragment_id, 0)
        candidate.rerank_score -= 0.2 * irrelevant_counts.get(candidate.source_fragment_id, 0)
    return sorted(candidates, key=lambda item: item.rerank_score, reverse=True)


def _candidate_pool_limit(candidates: list[CandidateFragment], top_k: int) -> list[CandidateFragment]:
    limit = max(top_k * 2, 6)
    kept = list(candidates[:limit])
    seen = {_candidate_identity(candidate) for candidate in kept}
    for candidate in candidates[limit:]:
        if candidate.reason.get("expansion") != "semantic_section_reference":
            continue
        identity = _candidate_identity(candidate)
        if identity in seen:
            continue
        kept.append(candidate)
        seen.add(identity)
    return kept


def _candidate_identity(candidate: CandidateFragment) -> tuple:
    return (
        candidate.source_fragment_id,
        candidate.source_table_id,
        candidate.source_table_cell_id,
        candidate.external_dataset_id,
        candidate.external_dataset_feature_id,
        candidate.source_type,
    )


def retrieve_context(
    session: Session,
    *,
    document_id: int,
    question_text: str,
    known_facts: dict[str, Any] | None,
    settings: Layer2Settings,
    embedding_client: BaseEmbeddingClient,
    planner_llm_client: BaseLLMClient | None = None,
    top_k: int | None = None,
) -> RetrievalBundle:
    top_k = top_k or settings.top_k
    understanding = understand_question(question_text)
    metadata_filters = build_metadata_filters(
        session,
        document_id=document_id,
        municipality=(known_facts or {}).get("municipality"),
        known_facts=known_facts,
    )
    plan = create_retrieval_plan(question_text, known_facts=known_facts, llm_client=planner_llm_client)
    semantic = retrieve_semantic_facts(session, document_id=document_id, question_text=question_text, top_k=top_k)
    planned = execute_retrieval_plan(session, document_id=document_id, plan=plan, top_k=top_k)
    fts = full_text_candidates(session, document_id=document_id, understanding=understanding, top_k=top_k)
    vector = vector_candidates(
        session,
        document_id=document_id,
        question_text=question_text,
        embedding_client=embedding_client,
        top_k=top_k,
    )
    tables = table_candidates(session, document_id=document_id, understanding=understanding, top_k=max(2, top_k // 2))
    merged = merge_and_dedupe_candidates(semantic, planned, fts, vector, tables)
    expanded = expand_hierarchy(session, document_id, merged)
    expanded = expand_cross_references(session, expanded)
    expanded = expand_datasets(session, expanded)
    expanded = expand_semantic_graph(
        session,
        document_id=document_id,
        candidates=expanded,
        max_depth=settings.semantic_graph_max_depth,
        max_fragments=settings.semantic_graph_max_fragments,
        max_nodes=settings.semantic_graph_max_nodes,
        allowed_edge_types=allowed_edge_types_from_string(settings.semantic_graph_allowed_edge_types),
    )
    reranked = rerank_candidates(merge_and_dedupe_candidates(expanded), understanding)
    reranked = apply_feedback_adjustments(session, document_id=document_id, candidates=reranked)
    cached_claims = cached_claim_candidates(
        session,
        document_id=document_id,
        understanding=understanding,
        max_claims=settings.max_cached_claims,
    )
    return RetrievalBundle(
        understanding=understanding,
        candidates=_candidate_pool_limit(reranked, top_k),
        cached_claims=cached_claims,
        metadata_filters=metadata_filters,
        query_terms={
            **understanding.model_dump(),
            "retrieval_plan": plan.model_dump(),
            "semantic_retrieval": {"candidate_count": len(semantic)},
        },
    )
