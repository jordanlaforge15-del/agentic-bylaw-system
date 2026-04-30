from pathlib import Path

from layer1.db.session import session_scope
from layer2.db.models import AnswerFeedback, AnswerLog, ClaimFeedback, GeneratedClaim, PromptLog, QuerySession, RetrievalFeedback, RetrievalResult, RetrievalRun
from layer2.eval.harness import run_eval
from layer2.feedback.service import submit_answer_feedback, submit_claim_feedback, submit_retrieval_feedback
from layer2.llm.base import BaseLLMClient
from layer2.pipeline.service import run_answer_pipeline
from layer2.retrieval.service import retrieve_context


class NoClaimLLMClient(BaseLLMClient):
    model_name = "no-claim-test"

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        return (
            '{"answer_text":"The source does not explicitly state a story count, but it provides a maximum height.",'
            '"assumptions":["Only supplied context used."],'
            '"insufficient_source":true,'
            '"cited_fragment_ids":[],'
            '"cited_citation_labels":[],'
            '"claims":[]}'
        )


def test_end_to_end_answer_generation_persists_logs(prepared_document, settings, clients):
    embedding_client, llm_client = clients
    with session_scope(prepared_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=prepared_document["document_id"],
            question_text="Is a temporary use permitted?",
            known_facts={"municipality": "Sampleton"},
            settings=settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        assert "temporary use" in result["answer_log"].final_answer_text.lower()
        assert session.query(QuerySession).count() == 1
        assert session.query(RetrievalRun).count() == 1
        assert session.query(RetrievalResult).count() > 0
        assert session.query(PromptLog).count() == 1
        assert session.query(AnswerLog).count() == 1
        assert session.query(GeneratedClaim).count() >= 1


def test_pipeline_synthesizes_claim_from_structured_table_hit(prepared_document, settings, clients):
    embedding_client, _llm_client = clients
    with session_scope(prepared_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=prepared_document["document_id"],
            question_text="How many stories are permitted in an R1 zone?",
            known_facts={},
            settings=settings,
            embedding_client=embedding_client,
            llm_client=NoClaimLLMClient(),
        )
        assert result["claims"]
        claim = result["claims"][0]
        assert claim.claim_type.value == "dimensional_standard"
        assert claim.zone_code == "R1"
        assert claim.source_table_cell_ids_json


def test_feedback_persistence_and_claim_reuse(prepared_document, settings, clients):
    embedding_client, llm_client = clients
    with session_scope(prepared_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=prepared_document["document_id"],
            question_text="What is the minimum lot area for R1?",
            known_facts={},
            settings=settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        answer_feedback = submit_answer_feedback(
            session,
            answer_log_id=result["answer_log"].id,
            rating=2,
            is_correct=False,
            is_incomplete=True,
            notes="Missing the Schedule B context.",
        )
        claim_feedback = submit_claim_feedback(
            session,
            generated_claim=result["claims"][0],
            is_correct=True,
            corrected_value_text=None,
            corrected_structured_json=None,
            notes="Verified against the source.",
            reviewer_type="planner",
        )
        retrieval_feedback = submit_retrieval_feedback(
            session,
            retrieval_run_id=result["retrieval_run"].id,
            missing_source_fragment_id=12,
            irrelevant_source_fragment_id=6,
            notes="Schedule B should have been included; clause (a) was noise.",
        )
        assert session.query(AnswerFeedback).count() == 1
        assert session.query(ClaimFeedback).count() == 1
        assert session.query(RetrievalFeedback).count() == 1
        bundle = retrieve_context(
            session,
            document_id=prepared_document["document_id"],
            question_text="What is the minimum lot area for R1?",
            known_facts={},
            settings=settings,
            embedding_client=embedding_client,
        )
        assert bundle.cached_claims
        assert bundle.cached_claims[0].verification_status == "verified"
        candidate_ids = [candidate.source_fragment_id for candidate in bundle.candidates]
        assert 12 in candidate_ids
        assert answer_feedback.id and claim_feedback.id and retrieval_feedback.id


def test_eval_harness_smoke(prepared_document, settings, clients):
    embedding_client, llm_client = clients
    with session_scope(prepared_document["db_url"]) as session:
        summary = run_eval(
            session,
            document_id=prepared_document["document_id"],
            eval_path=Path("evals/sampleton_layer2_eval.json"),
            settings=settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        assert summary["total_cases"] == 10
        assert summary["retrieval_hits"] >= 6
