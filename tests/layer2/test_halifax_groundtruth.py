"""Ground-truth evaluation for Halifax Regional Centre LUB.

Runs 26 known-answer queries through the Layer 2 pipeline against a synthetic
Halifax fixture, covering setbacks, zones, overlays, use permissions, parking,
and common interpretive questions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from layer1.db.session import session_scope
from layer1.pipeline.ingest import ingest_file
from layer2.config import Layer2Settings
from layer2.db.init_db import create_all
from layer2.embeddings.clients import MockEmbeddingClient
from layer2.eval.harness import run_eval
from layer2.pipeline.service import embed_document_fragments, run_answer_pipeline

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent))
from halifax_mock_llm import HalifaxMockLLMClient  # noqa: E402

FIXTURE = Path("tests/fixtures/halifax_regional_centre_lub.txt")
EVAL_PATH = Path("evals/halifax_regional_centre_eval.json")


@pytest.fixture()
def halifax_settings() -> Layer2Settings:
    return Layer2Settings(
        DATABASE_URL="sqlite:///unused.db",
        LAYER2_LLM_MODEL="mock-halifax-groundtruth",
        LAYER2_EMBEDDING_MODEL="mock-embedding",
    )


@pytest.fixture()
def halifax_clients():
    return MockEmbeddingClient(), HalifaxMockLLMClient()


@pytest.fixture()
def halifax_document(tmp_path: Path, halifax_clients):
    db_url = f"sqlite:///{tmp_path / 'halifax_eval.db'}"
    create_all(db_url)
    embedding_client, _ = halifax_clients
    with session_scope(db_url) as session:
        document, _ = ingest_file(
            session,
            FIXTURE,
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
        )
        embed_document_fragments(
            session, document_id=document.id, embedding_client=embedding_client,
        )
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id}


def test_halifax_groundtruth_eval_harness(halifax_document, halifax_settings, halifax_clients):
    """Full eval harness run: all 25 Halifax ground-truth cases."""
    embedding_client, llm_client = halifax_clients
    with session_scope(halifax_document["db_url"]) as session:
        summary = run_eval(
            session,
            document_id=halifax_document["document_id"],
            eval_path=EVAL_PATH,
            settings=halifax_settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        assert summary["total_cases"] == 26

        for case in summary["failure_cases"]:
            print(
                f"FAIL: {case['question'][:60]}"
                f"  retrieval={case['retrieval_hit']}"
                f"  answer={case['answer_hit']}"
                f"  claim={case['claim_hit']}"
            )

        assert summary["answer_hits"] >= 21, (
            f"Answer hits {summary['answer_hits']}/26 below 80% threshold"
        )
        assert summary["claim_hits"] >= 21, (
            f"Claim hits {summary['claim_hits']}/26 below 80% threshold"
        )


def test_halifax_setback_er1_front(halifax_document, halifax_settings, halifax_clients):
    """Spot-check: ER-1 front setback returns 6.0 m with dimensional claim."""
    embedding_client, llm_client = halifax_clients
    with session_scope(halifax_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=halifax_document["document_id"],
            question_text="What is the front setback for the ER-1 zone?",
            known_facts={"municipality": "HRM", "zone_code": "ER-1"},
            settings=halifax_settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        answer = result["answer_log"].final_answer_text.lower()
        assert "6.0 m" in answer
        assert "er-1" in answer
        assert result["claims"]
        claim = result["claims"][0]
        assert claim.claim_type.value == "dimensional_standard"
        assert claim.zone_code == "ER-1"


def test_halifax_use_permission_multiunit_cen1(halifax_document, halifax_settings, halifax_clients):
    """Spot-check: multi-unit dwelling permitted in CEN-1."""
    embedding_client, llm_client = halifax_clients
    with session_scope(halifax_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=halifax_document["document_id"],
            question_text="Is a multi-unit dwelling use permitted in the CEN-1 zone?",
            known_facts={"municipality": "HRM", "zone_code": "CEN-1"},
            settings=halifax_settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        answer = result["answer_log"].final_answer_text.lower()
        assert "permitted" in answer
        assert "cen-1" in answer
        assert result["claims"]
        claim = result["claims"][0]
        assert claim.claim_type.value == "use_permission"


def test_halifax_heritage_overlay(halifax_document, halifax_settings, halifax_clients):
    """Spot-check: heritage demolition requires Heritage Advisory Committee approval."""
    embedding_client, llm_client = halifax_clients
    with session_scope(halifax_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=halifax_document["document_id"],
            question_text="What are the heritage conservation district demolition requirements?",
            known_facts={"municipality": "HRM"},
            settings=halifax_settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        answer = result["answer_log"].final_answer_text.lower()
        assert "heritage advisory committee" in answer
        assert "demolition" in answer


def test_halifax_parking_exemption_downtown(halifax_document, halifax_settings, halifax_clients):
    """Spot-check: no off-street parking in downtown zones."""
    embedding_client, llm_client = halifax_clients
    with session_scope(halifax_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=halifax_document["document_id"],
            question_text="Is off-street parking required in the CEN-1 zone?",
            known_facts={"municipality": "HRM", "zone_code": "CEN-1"},
            settings=halifax_settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        answer = result["answer_log"].final_answer_text.lower()
        assert "no off-street parking" in answer


def test_halifax_combination_of_uses(halifax_document, halifax_settings, halifax_clients):
    """Spot-check: home occupation + secondary suite combinable in ER zones."""
    embedding_client, llm_client = halifax_clients
    with session_scope(halifax_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=halifax_document["document_id"],
            question_text="What uses may be combined in the ER-1 zone under the combination of uses provision?",
            known_facts={"municipality": "HRM", "zone_code": "ER-1"},
            settings=halifax_settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        answer = result["answer_log"].final_answer_text.lower()
        assert "home occupation" in answer
        assert "secondary suite" in answer


def test_halifax_schedule15_height_precinct(halifax_document, halifax_settings, halifax_clients):
    """Spot-check: CEN-1 height governed by Schedule 15 height precinct."""
    embedding_client, llm_client = halifax_clients
    with session_scope(halifax_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=halifax_document["document_id"],
            question_text="What is the maximum height in the CEN-1 zone?",
            known_facts={"municipality": "HRM", "zone_code": "CEN-1"},
            settings=halifax_settings,
            embedding_client=embedding_client,
            llm_client=llm_client,
        )
        answer = result["answer_log"].final_answer_text.lower()
        assert "schedule 15" in answer
        assert "cen-1" in answer
