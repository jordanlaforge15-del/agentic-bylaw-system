from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from layer2.config import Layer2Settings
from layer2.embeddings.base import BaseEmbeddingClient
from layer2.llm.base import BaseLLMClient
from layer2.models.schemas import EvalCase
from layer2.pipeline.service import run_answer_pipeline


def load_eval_cases(path: str | Path) -> list[EvalCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [EvalCase.model_validate(item) for item in payload]


def _normalized_value(value):
    return getattr(value, "value", value)


def run_eval(
    session: Session,
    *,
    document_id: int,
    eval_path: str | Path,
    settings: Layer2Settings,
    embedding_client: BaseEmbeddingClient,
    llm_client: BaseLLMClient,
    resume: bool = False,
) -> dict[str, Any]:
    cases = load_eval_cases(eval_path)
    results = []
    for case in cases:
        pipeline_result = run_answer_pipeline(
            session,
            document_id=document_id,
            question_text=case.question,
            known_facts=case.known_facts_json,
            settings=settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        answer_text = pipeline_result["answer_log"].final_answer_text.lower()
        selected_fragment_ids = [fragment.source_fragment_id for fragment in pipeline_result["selected_fragments"] if fragment.source_fragment_id]
        claim_rows = pipeline_result["claims"]
        retrieval_hit = not case.expected_fragment_ids or any(
            fragment_id in selected_fragment_ids for fragment_id in case.expected_fragment_ids
        )
        answer_hit = all(keyword.lower() in answer_text for keyword in case.expected_answer_keywords)
        claim_hit = all(
            any(_normalized_value(getattr(claim, key, None)) == value for claim in claim_rows)
            for shape in case.expected_claim_shapes
            for key, value in shape.items()
        ) if case.expected_claim_shapes else True
        results.append(
            {
                "question": case.question,
                "retrieval_hit": retrieval_hit,
                "answer_hit": answer_hit,
                "claim_hit": claim_hit,
                "selected_fragment_ids": selected_fragment_ids,
                "answer_id": pipeline_result["answer_log"].id,
            }
        )
    retrieval_hits = sum(1 for item in results if item["retrieval_hit"])
    answer_hits = sum(1 for item in results if item["answer_hit"])
    claim_hits = sum(1 for item in results if item["claim_hit"])
    return {
        "total_cases": len(results),
        "retrieval_hits": retrieval_hits,
        "answer_hits": answer_hits,
        "claim_hits": claim_hits,
        "results": results,
        "failure_cases": [item for item in results if not (item["retrieval_hit"] and item["answer_hit"] and item["claim_hit"])],
    }
