from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from layer1.db.session import session_scope
from layer2.config import Layer2Settings, get_settings
from layer2.db.init_db import create_all
from layer2.db.models import AnswerLog, GeneratedClaim, QuerySession, RetrievalRun
from layer2.embeddings.base import BaseEmbeddingClient
from layer2.embeddings.clients import (
    HashingEmbeddingClient,
    OpenAICompatibleEmbeddingClient,
    SentenceTransformerEmbeddingClient,
)
from layer2.eval.harness import run_eval
from layer2.feedback.service import submit_answer_feedback, submit_claim_feedback, submit_retrieval_feedback
from layer2.llm.base import BaseLLMClient
from layer2.llm.clients import MockLLMClient, OpenAICompatibleLLMClient
from layer2.pipeline.service import embed_document_fragments, run_answer_pipeline
from layer2.retrieval.service import retrieve_context

app = typer.Typer(help="Layer 2 retrieval, answering, claims, and feedback CLI")
console = Console()


def _settings() -> Layer2Settings:
    return get_settings()


def _resolve_embedding_client(model: str | None, settings: Layer2Settings) -> BaseEmbeddingClient:
    model_name = model or settings.embedding_model
    if model_name.startswith("mock") or model_name.startswith("hashing"):
        return HashingEmbeddingClient(model_name=model_name, dimensions=settings.embedding_dimensions)
    if model_name.startswith("sentence-transformers:"):
        return SentenceTransformerEmbeddingClient(model_name=model_name.split(":", 1)[1])
    if settings.embedding_base_url:
        return OpenAICompatibleEmbeddingClient(
            base_url=settings.embedding_base_url,
            model_name=model_name,
            api_key=settings.embedding_api_key,
            dimensions=settings.embedding_dimensions,
        )
    return HashingEmbeddingClient(model_name=model_name, dimensions=settings.embedding_dimensions)


def _resolve_llm_client(model: str | None, settings: Layer2Settings) -> BaseLLMClient:
    model_name = model or settings.llm_model
    if model_name.startswith("mock") or not settings.llm_base_url:
        return MockLLMClient()
    return OpenAICompatibleLLMClient(
        base_url=settings.llm_base_url,
        model_name=model_name,
        api_key=settings.llm_api_key,
    )


def _json_print(payload: Any) -> None:
    console.print_json(json.dumps(payload, default=str))


def _parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise typer.BadParameter(f"Invalid boolean value: {value}")


@app.callback()
def main() -> None:
    """Layer 2 retrieval-first CLI."""


@app.command("init-db")
def init_db(db_url: str | None = typer.Option(None, "--db-url")) -> None:
    create_all(db_url)
    console.print("[green]Layer 2 schema created.[/green]")


@app.command("embed-fragments")
def embed_fragments(
    document_id: int,
    db_url: str | None = typer.Option(None, "--db-url"),
    embedding_model: str | None = typer.Option(None, "--embedding-model"),
    replace_existing: bool = typer.Option(False, "--replace-existing"),
) -> None:
    settings = _settings()
    embedding_client = _resolve_embedding_client(embedding_model, settings)
    with session_scope(db_url) as session:
        count = embed_document_fragments(
            session,
            document_id=document_id,
            embedding_client=embedding_client,
            replace_existing=replace_existing,
        )
    _json_print({"document_id": document_id, "embedding_model": embedding_client.model_name, "embedded": count})


@app.command("retrieve")
def retrieve(
    document_id: int,
    question: str = typer.Option(..., "--question"),
    db_url: str | None = typer.Option(None, "--db-url"),
    embedding_model: str | None = typer.Option(None, "--embedding-model"),
    top_k: int | None = typer.Option(None, "--top-k"),
) -> None:
    settings = _settings()
    embedding_client = _resolve_embedding_client(embedding_model, settings)
    with session_scope(db_url) as session:
        bundle = retrieve_context(
            session,
            document_id=document_id,
            question_text=question,
            known_facts={},
            settings=settings,
            embedding_client=embedding_client,
            top_k=top_k,
        )
    _json_print(
        {
            "understanding": bundle.understanding.model_dump(),
            "cached_claims": [claim.model_dump() for claim in bundle.cached_claims],
            "candidates": [candidate.model_dump() for candidate in bundle.candidates],
        }
    )


@app.command("answer")
def answer(
    document_id: int,
    question: str = typer.Option(..., "--question"),
    db_url: str | None = typer.Option(None, "--db-url"),
    model: str | None = typer.Option(None, "--model"),
    embedding_model: str | None = typer.Option(None, "--embedding-model"),
    top_k: int | None = typer.Option(None, "--top-k"),
    token_budget: int | None = typer.Option(None, "--token-budget"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    settings = _settings()
    embedding_client = _resolve_embedding_client(embedding_model, settings)
    llm_client = _resolve_llm_client(model, settings)
    with session_scope(db_url) as session:
        result = run_answer_pipeline(
            session,
            document_id=document_id,
            question_text=question,
            known_facts={},
            settings=settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
            top_k=top_k,
            token_budget=token_budget,
        )
        payload = {
            "query_session_id": result["query_session"].id,
            "retrieval_run_id": result["retrieval_run"].id,
            "answer_id": result["answer_log"].id,
            "answer_text": result["answer_log"].final_answer_text,
            "claim_ids": [claim.id for claim in result["claims"]],
        }
        if debug:
            payload["selected_fragments"] = [fragment.model_dump() for fragment in result["selected_fragments"]]
    _json_print(payload)


@app.command("answer-batch")
def answer_batch(
    document_id: int,
    eval_set: Path = typer.Option(..., "--eval-set", exists=True, readable=True),
    db_url: str | None = typer.Option(None, "--db-url"),
    model: str | None = typer.Option(None, "--model"),
    embedding_model: str | None = typer.Option(None, "--embedding-model"),
    resume: bool = typer.Option(False, "--resume"),
) -> None:
    settings = _settings()
    embedding_client = _resolve_embedding_client(embedding_model, settings)
    llm_client = _resolve_llm_client(model, settings)
    with session_scope(db_url) as session:
        results = run_eval(
            session,
            document_id=document_id,
            eval_path=eval_set,
            settings=settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
            resume=resume,
        )
    _json_print(results)


@app.command("run-eval")
def run_eval_command(
    document_id: int,
    eval_set: Path = typer.Option(..., "--eval-set", exists=True, readable=True),
    db_url: str | None = typer.Option(None, "--db-url"),
    model: str | None = typer.Option(None, "--model"),
    embedding_model: str | None = typer.Option(None, "--embedding-model"),
    resume: bool = typer.Option(False, "--resume"),
) -> None:
    answer_batch(document_id=document_id, eval_set=eval_set, db_url=db_url, model=model, embedding_model=embedding_model, resume=resume)


@app.command("show-query")
def show_query(query_session_id: int, db_url: str | None = typer.Option(None, "--db-url")) -> None:
    with session_scope(db_url) as session:
        row = session.get(QuerySession, query_session_id)
        if row is None:
            raise typer.BadParameter(f"Query session {query_session_id} not found")
        _json_print(_row_dict(row))


@app.command("show-answer")
def show_answer(answer_id: int, db_url: str | None = typer.Option(None, "--db-url")) -> None:
    with session_scope(db_url) as session:
        row = session.get(AnswerLog, answer_id)
        if row is None:
            raise typer.BadParameter(f"Answer {answer_id} not found")
        _json_print(_row_dict(row))


@app.command("show-retrieval")
def show_retrieval(retrieval_run_id: int, db_url: str | None = typer.Option(None, "--db-url")) -> None:
    with session_scope(db_url) as session:
        row = session.get(RetrievalRun, retrieval_run_id)
        if row is None:
            raise typer.BadParameter(f"Retrieval run {retrieval_run_id} not found")
        _json_print(_row_dict(row))


@app.command("submit-answer-feedback")
def submit_answer_feedback_command(
    answer_id: int,
    db_url: str | None = typer.Option(None, "--db-url"),
    rating: int | None = typer.Option(None, "--rating"),
    is_correct: str | None = typer.Option(None, "--is-correct"),
    is_incomplete: str | None = typer.Option(None, "--is-incomplete"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    with session_scope(db_url) as session:
        feedback = submit_answer_feedback(
            session,
            answer_log_id=answer_id,
            rating=rating,
            is_correct=_parse_optional_bool(is_correct),
            is_incomplete=_parse_optional_bool(is_incomplete),
            notes=notes,
        )
        _json_print(_row_dict(feedback))


@app.command("submit-claim-feedback")
def submit_claim_feedback_command(
    claim_id: int,
    db_url: str | None = typer.Option(None, "--db-url"),
    is_correct: str | None = typer.Option(None, "--is-correct"),
    corrected_value_text: str | None = typer.Option(None, "--corrected-value-text"),
    corrected_structured_json: str | None = typer.Option(None, "--corrected-structured-json"),
    notes: str | None = typer.Option(None, "--notes"),
    reviewer_type: str | None = typer.Option(None, "--reviewer-type"),
) -> None:
    with session_scope(db_url) as session:
        claim = session.get(GeneratedClaim, claim_id)
        if claim is None:
            raise typer.BadParameter(f"Claim {claim_id} not found")
        feedback = submit_claim_feedback(
            session,
            generated_claim=claim,
            is_correct=_parse_optional_bool(is_correct),
            corrected_value_text=corrected_value_text,
            corrected_structured_json=json.loads(corrected_structured_json) if corrected_structured_json else None,
            notes=notes,
            reviewer_type=reviewer_type,
        )
        _json_print(_row_dict(feedback))


@app.command("submit-retrieval-feedback")
def submit_retrieval_feedback_command(
    retrieval_run_id: int,
    db_url: str | None = typer.Option(None, "--db-url"),
    missing_source_fragment_id: int | None = typer.Option(None, "--missing-source-fragment-id"),
    irrelevant_source_fragment_id: int | None = typer.Option(None, "--irrelevant-source-fragment-id"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    with session_scope(db_url) as session:
        feedback = submit_retrieval_feedback(
            session,
            retrieval_run_id=retrieval_run_id,
            missing_source_fragment_id=missing_source_fragment_id,
            irrelevant_source_fragment_id=irrelevant_source_fragment_id,
            notes=notes,
        )
        _json_print(_row_dict(feedback))


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        column.name: getattr(getattr(row, column.name), "value", getattr(row, column.name))
        for column in row.__table__.columns
    }
