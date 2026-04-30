from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import re
from typing import Any

from sqlalchemy.orm import Session

from layer1.db.base import SourceFragment, SourceTableCell
from layer2.config import Layer2Settings
from layer2.claims.parser import parse_answer_payload
from layer2.db.models import (
    AnswerLog,
    FragmentEmbedding,
    GeneratedClaim,
    PromptLog,
    QuerySession,
    RetrievalResult,
    RetrievalRun,
)
from layer2.embeddings.base import BaseEmbeddingClient
from layer2.llm.base import BaseLLMClient
from layer2.models.enums import AnswerStatus, ClaimStatus, QuerySessionStatus, RetrievalRunStatus
from layer2.models.schemas import PromptContext, StructuredClaim
from layer2.prompts.builder import build_prompt
from layer2.retrieval.service import retrieve_context


def embed_document_fragments(
    session: Session,
    *,
    document_id: int,
    embedding_client: BaseEmbeddingClient,
    replace_existing: bool = False,
) -> int:
    fragments = (
        session.query(SourceFragment)
        .filter(SourceFragment.document_id == document_id)
        .order_by(SourceFragment.id)
        .all()
    )
    existing = {
        row.source_fragment_id: row
        for row in session.query(FragmentEmbedding).filter(
            FragmentEmbedding.document_id == document_id,
            FragmentEmbedding.embedding_model == embedding_client.model_name,
        )
    }
    texts = [fragment.text for fragment in fragments if replace_existing or fragment.id not in existing]
    target_fragments = [fragment for fragment in fragments if replace_existing or fragment.id not in existing]
    if not target_fragments:
        return 0
    vectors = embedding_client.embed_texts(texts)
    count = 0
    for fragment, vector in zip(target_fragments, vectors):
        if replace_existing and fragment.id in existing:
            session.delete(existing[fragment.id])
        session.add(
            FragmentEmbedding(
                document_id=document_id,
                source_fragment_id=fragment.id,
                embedding_model=embedding_client.model_name,
                embedding=vector,
                metadata_json={"citation_label": fragment.citation_label},
            )
        )
        count += 1
    session.flush()
    return count


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _select_fragments_for_prompt(candidates, token_budget: int):
    selected = []
    consumed = 0
    for rank, candidate in enumerate(candidates, start=1):
        cost = _estimate_tokens(candidate.text) + 30
        if selected and consumed + cost > token_budget:
            continue
        selected.append((rank, candidate))
        consumed += cost
    return selected


def _persist_claims(
    session: Session,
    *,
    document_id: int,
    query_session_id: int,
    answer_log_id: int,
    model_name: str,
    claims: Iterable[StructuredClaim],
) -> list[GeneratedClaim]:
    persisted = []
    for claim in claims:
        row = GeneratedClaim(
            query_session_id=query_session_id,
            answer_log_id=answer_log_id,
            document_id=document_id,
            claim_type=claim.claim_type,
            topic=claim.topic,
            canonical_subject=claim.canonical_subject,
            canonical_predicate=claim.canonical_predicate,
            canonical_object_text=claim.canonical_object_text,
            numeric_value=claim.numeric_value,
            normalized_value_text=claim.normalized_value_text,
            unit=claim.unit,
            operator=claim.operator,
            zone_code=claim.zone_code,
            use_name=claim.use_name,
            applicability_text=claim.applicability_text,
            condition_text=claim.condition_text,
            exception_text=claim.exception_text,
            source_fragment_ids_json=claim.source_fragment_ids,
            source_table_cell_ids_json=claim.source_table_cell_ids,
            citation_text=claim.citation_text,
            claim_status=ClaimStatus.ACTIVE,
            confidence=claim.confidence,
            model_name=model_name,
        )
        session.add(row)
        persisted.append(row)
    session.flush()
    return persisted


def _parse_numeric_value(text: str) -> tuple[float | None, str | None]:
    match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z.%/]+)?", text)
    if not match:
        return None, None
    raw_number = match.group(1).replace(",", "")
    try:
        value = float(raw_number)
    except ValueError:
        value = None
    return value, match.group(2)


def _topic_to_claim_type(topic: str) -> str:
    if "parking" in topic:
        return "parking_requirement"
    if any(token in topic for token in ["height", "frontage", "area", "coverage", "setback"]):
        return "dimensional_standard"
    return "general_regulation"


def _synthesize_claims_from_candidates(
    session: Session,
    *,
    retrieval_bundle,
    selected_fragments,
) -> list[StructuredClaim]:
    synthesized: list[StructuredClaim] = []
    for candidate in selected_fragments:
        if candidate.source_type != "table_cell":
            continue
        if candidate.reason.get("pattern") != "dimensional_pair":
            continue
        cell_ids = candidate.reason.get("cell_ids") or []
        if len(cell_ids) < 2:
            continue
        value_cell = session.get(SourceTableCell, cell_ids[1])
        if value_cell is None or not value_cell.text:
            continue
        topic_text = candidate.text.lower()
        if "height" in topic_text:
            topic = "maximum height"
        elif "frontage" in topic_text:
            topic = "minimum lot frontage"
        elif "area" in topic_text:
            topic = "minimum lot area"
        elif "coverage" in topic_text:
            topic = "maximum lot coverage"
        elif "parking" in topic_text:
            topic = "parking requirement"
        else:
            topic = candidate.text.splitlines()[0][:80]
        numeric_value, unit = _parse_numeric_value(value_cell.text)
        zone_code = retrieval_bundle.understanding.zone_keywords[0] if retrieval_bundle.understanding.zone_keywords else None
        operator = None
        normalized_topic = topic.lower()
        if "maximum" in normalized_topic:
            operator = "<="
        elif "minimum" in normalized_topic:
            operator = ">="
        synthesized.append(
            StructuredClaim(
                claim_type=_topic_to_claim_type(topic),
                topic=topic,
                canonical_subject=f"{zone_code} zone" if zone_code else None,
                canonical_predicate=topic,
                canonical_object_text=value_cell.text,
                numeric_value=numeric_value,
                normalized_value_text=value_cell.text,
                unit=unit,
                operator=operator,
                zone_code=zone_code,
                source_fragment_ids=[],
                source_table_cell_ids=cell_ids,
                citation_text=candidate.citation_label,
                confidence=0.7,
            )
        )
        break
    return synthesized


def run_answer_pipeline(
    session: Session,
    *,
    document_id: int,
    question_text: str,
    known_facts: dict[str, Any] | None,
    settings: Layer2Settings,
    embedding_client: BaseEmbeddingClient,
    llm_client: BaseLLMClient,
    planner_llm_client: BaseLLMClient | None = None,
    top_k: int | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    token_budget = token_budget or settings.token_budget
    query_session = QuerySession(
        document_id=document_id,
        municipality=(known_facts or {}).get("municipality"),
        question_text=question_text,
        known_facts_json=known_facts or {},
        status=QuerySessionStatus.RETRIEVING,
    )
    session.add(query_session)
    session.flush()

    retrieval_bundle = retrieve_context(
        session,
        document_id=document_id,
        question_text=question_text,
        known_facts=known_facts,
        settings=settings,
        embedding_client=embedding_client,
        planner_llm_client=planner_llm_client,
        top_k=top_k,
    )
    query_session.normalized_question_text = retrieval_bundle.understanding.normalized_question
    retrieval_run = RetrievalRun(
        query_session_id=query_session.id,
        retrieval_version=settings.retrieval_version,
        metadata_filters_json=retrieval_bundle.metadata_filters,
        query_terms_json=retrieval_bundle.query_terms,
        status=RetrievalRunStatus.COMPLETED,
    )
    session.add(retrieval_run)
    session.flush()

    selected = _select_fragments_for_prompt(retrieval_bundle.candidates, token_budget)
    for rank, candidate in enumerate(retrieval_bundle.candidates, start=1):
        session.add(
            RetrievalResult(
                retrieval_run_id=retrieval_run.id,
                source_fragment_id=candidate.source_fragment_id,
                source_table_id=candidate.source_table_id,
                source_table_cell_id=candidate.source_table_cell_id,
                source_type=candidate.source_type,
                retrieval_channel=candidate.retrieval_channel,
                base_score=candidate.base_score,
                rerank_score=candidate.rerank_score,
                selected_for_prompt=any(item[1] == candidate for item in selected),
                rank_order=rank,
                reason_json=candidate.reason,
                metadata_json=candidate.metadata,
            )
        )
    session.flush()

    selected_fragments = [candidate for _, candidate in selected]
    prompt_context = PromptContext(
        question_text=question_text,
        known_facts=known_facts or {},
        fragments=selected_fragments,
        cached_claims=retrieval_bundle.cached_claims,
    )
    system_prompt, user_prompt, assembled_context = build_prompt(prompt_context, settings.prompt_version)
    prompt_log = PromptLog(
        query_session_id=query_session.id,
        retrieval_run_id=retrieval_run.id,
        prompt_version=settings.prompt_version,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        assembled_context_text=assembled_context,
        model_name=llm_client.model_name,
        model_parameters_json={"temperature": 0.0},
        fragment_ids_json=[fragment.source_fragment_id for fragment in selected_fragments if fragment.source_fragment_id],
        claim_ids_json=[claim.claim_id for claim in retrieval_bundle.cached_claims],
    )
    session.add(prompt_log)
    session.flush()

    query_session.status = QuerySessionStatus.ANSWERING
    raw_output = llm_client.generate(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.0)
    payload = parse_answer_payload(raw_output)
    if not payload.claims:
        payload.claims = _synthesize_claims_from_candidates(
            session,
            retrieval_bundle=retrieval_bundle,
            selected_fragments=selected_fragments,
        )
    answer_status = AnswerStatus.INSUFFICIENT_SOURCE if payload.insufficient_source else AnswerStatus.COMPLETED
    answer_log = AnswerLog(
        query_session_id=query_session.id,
        prompt_log_id=prompt_log.id,
        raw_model_output=raw_output,
        final_answer_text=payload.answer_text,
        answer_status=answer_status,
        confidence=min((claim.confidence or 0.0) for claim in payload.claims) if payload.claims else None,
        metadata_json={
            "assumptions": payload.assumptions,
            "cited_fragment_ids": payload.cited_fragment_ids,
            "cited_citation_labels": payload.cited_citation_labels,
        },
    )
    session.add(answer_log)
    session.flush()

    claims = _persist_claims(
        session,
        document_id=document_id,
        query_session_id=query_session.id,
        answer_log_id=answer_log.id,
        model_name=llm_client.model_name,
        claims=payload.claims,
    )
    query_session.status = QuerySessionStatus.COMPLETED
    query_session.completed_at = datetime.now(timezone.utc)
    session.flush()
    return {
        "query_session": query_session,
        "retrieval_run": retrieval_run,
        "prompt_log": prompt_log,
        "answer_log": answer_log,
        "claims": claims,
        "selected_fragments": selected_fragments,
    }
