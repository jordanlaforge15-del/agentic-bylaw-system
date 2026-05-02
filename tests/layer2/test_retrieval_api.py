from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete

from layer1.db.base import CrossReference, Document, PageBlock, SourceFragment, SourceTable, SourceTableCell
from layer1.db.session import session_scope
from layer1.models.enums import BlockType, FragmentType, ParseStatus, ResolutionStatus
from layer2.db.init_db import create_all
from layer2.embeddings.clients import MockEmbeddingClient
from layer2.llm.base import BaseLLMClient
from layer2.pipeline.service import embed_document_fragments, run_answer_pipeline
from layer2.retrieval.api import get_standard
from layer2.retrieval.planner import create_retrieval_plan
from layer2.retrieval.service import retrieve_context


class FunctionalBylawLLM(BaseLLMClient):
    model_name = "functional-bylaw-test"

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        if "translate municipal land-use bylaw questions into bounded retrieval plans" in system_prompt.lower():
            return self._plan(user_prompt)
        return self._answer(user_prompt)

    def _plan(self, user_prompt: str) -> str:
        question = user_prompt.lower()
        if "sideyard" in question or "side yard" in question:
            standard_type = "side_yard"
        elif "height" in question:
            standard_type = "building_height"
        elif "frontage" in question:
            standard_type = "lot_frontage"
        else:
            standard_type = "unknown"
        return json.dumps(
            {
                "intent": "lookup_dimensional_standard",
                "entities": {"zone": "R2", "standard_type": standard_type, "use_name": None},
                "aliases": {"zone": ["R2"], "standard_type": [standard_type.replace("_", " ")]},
                "recommended_calls": [
                    {
                        "tool": "get_standard",
                        "args": {"zone": "R2", "standard_type": standard_type},
                        "rationale": "Lookup a dimensional zoning standard.",
                    }
                ],
                "expected_answer_shape": "value_or_table_by_applicability",
                "confidence": 0.9,
            }
        )

    def _answer(self, user_prompt: str) -> str:
        question_line = next((line for line in user_prompt.splitlines() if line.startswith("Question:")), "")
        question = question_line.lower()
        context = user_prompt.lower()
        if "sideyard" in question or "side yard" in question:
            has_context = all(token in context for token in ["r-2 zone", "side yard", "r 1 uses", "duplex", "4000", "5000"])
            answer = (
                "In the R-2 Zone, Section 37 gives side yard requirements by use: "
                "R-1 uses require 4 ft, duplex requires 5 ft, and a 3-unit or 4-unit "
                "apartment building requires 6 ft. Section 43(a) also states that a "
                "semi-detached dwelling requires a minimum side yard of 5 ft, with no "
                "setback along the common lot boundary when subdivided."
            )
            return self._payload(answer, insufficient=not has_context)
        if "height" in question:
            has_context = "maximum height" in context and "35 feet" in context and "r-2 zone" in context
            return self._payload("The maximum building height in the R-2 Zone is 35 feet.", insufficient=not has_context)
        if "frontage" in question:
            has_context = all(token in context for token in ["lot frontage", "r 1 uses", "40", "duplex", "50"])
            answer = "In the R-2 Zone, Section 37 lists required lot frontage as 40 ft for R-1 uses, 50 ft for duplex, and 80 ft for 3-unit and 4-unit apartment buildings."
            return self._payload(answer, insufficient=not has_context)
        return self._payload("The supplied source is insufficient for a grounded answer.", insufficient=True)

    def _payload(self, answer: str, *, insufficient: bool) -> str:
        return json.dumps(
            {
                "answer_text": answer,
                "assumptions": ["Only supplied context was considered."],
                "insufficient_source": insufficient,
                "cited_fragment_ids": [],
                "cited_citation_labels": [],
                "claims": [],
            }
        )


@pytest.fixture()
def latest_ingestion_document(tmp_path: Path) -> dict:
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    data = json.loads(Path("ingest_15.json").read_text(encoding="utf-8"))
    with session_scope(db_url) as session:
        for model in [CrossReference, SourceTableCell, SourceTable, SourceFragment, PageBlock, Document]:
            session.execute(delete(model))
        document_row = dict(data["document"])
        document_row["ingestion_timestamp"] = _parse_dt(document_row.get("ingestion_timestamp"))
        document_row["consolidation_date"] = _parse_date(document_row.get("consolidation_date"))
        session.add(Document(**document_row))
        session.flush()
        for row in data["page_blocks"]:
            item = dict(row)
            item["block_type"] = BlockType(item["block_type"])
            session.add(PageBlock(**item))
        for row in data["source_fragments"]:
            item = dict(row)
            item["fragment_type"] = FragmentType(item["fragment_type"])
            item["parse_status"] = ParseStatus(item["parse_status"])
            session.add(SourceFragment(**item))
        session.flush()
        for row in data["source_tables"]:
            item = dict(row)
            item["parse_status"] = ParseStatus(item["parse_status"])
            session.add(SourceTable(**item))
        session.flush()
        for row in data["source_table_cells"]:
            session.add(SourceTableCell(**dict(row)))
        session.flush()
        for row in data["cross_references"]:
            item = dict(row)
            item["resolution_status"] = ResolutionStatus(item["resolution_status"])
            session.add(CrossReference(**item))
        embed_document_fragments(session, document_id=data["document"]["id"], embedding_client=MockEmbeddingClient())
    return {"db_url": db_url, "document_id": data["document"]["id"]}


def test_planner_normalizes_user_question_to_structured_retrieval_plan():
    plan = create_retrieval_plan(
        "What is the sideyard setback the R2 zone ?",
        llm_client=FunctionalBylawLLM(),
    )
    assert plan.intent == "lookup_dimensional_standard"
    assert plan.entities["zone"] == "R-2"
    assert plan.entities["standard_type"] == "side_yard"
    assert plan.recommended_calls[0].tool == "get_standard"
    assert plan.recommended_calls[0].args["zone"] == "R-2"


def test_get_standard_retrieves_r2_side_yard_requirement_context(latest_ingestion_document):
    with session_scope(latest_ingestion_document["db_url"]) as session:
        candidates = get_standard(
            session,
            document_id=latest_ingestion_document["document_id"],
            zone="R-2",
            standard_type="side_yard",
            top_k=10,
        )
    context = "\n".join(candidate.text for candidate in candidates)
    assert "R-2 ZONE" in context
    assert "Side Yard" in context
    assert "R 1 Uses" in context
    assert "duplex" in context
    assert "4000" in context
    assert "5000" in context


@pytest.mark.parametrize(
    ("question", "expected_keywords"),
    [
        ("What is the sideyard setback the R2 zone ?", ["4 ft", "5 ft", "6 ft", "semi-detached"]),
        ("What is the maximum building height in the R2 zone?", ["35 feet"]),
        ("What street frontage is required in the R2 zone?", ["40 ft", "50 ft", "80 ft"]),
    ],
)
def test_planned_answer_pipeline_handles_user_supplied_zoning_questions(
    latest_ingestion_document,
    settings,
    question,
    expected_keywords,
):
    llm = FunctionalBylawLLM()
    with session_scope(latest_ingestion_document["db_url"]) as session:
        result = run_answer_pipeline(
            session,
            document_id=latest_ingestion_document["document_id"],
            question_text=question,
            known_facts={},
            settings=settings,
            embedding_client=MockEmbeddingClient(),
            llm_client=llm,
            planner_llm_client=llm,
            top_k=12,
        )
        answer = result["answer_log"].final_answer_text
        assert result["answer_log"].answer_status.value == "completed"
        assert all(keyword.lower() in answer.lower() for keyword in expected_keywords)


@pytest.mark.parametrize(
    ("question", "expected_snippets"),
    [
        (
            "What is the maximum building height in the R-3 zone?",
            ["R-3 Zone", "Size of Building", "vertical angle of 60 degrees"],
        ),
        (
            "What yard setbacks apply to an apartment building in the R-3 zone?",
            ["R-3 Zone", "official street line", "no less than 20 feet", "lot line", "not less than 10 feet"],
        ),
        (
            "What parking is required for an apartment house in the South End R-3 zone?",
            ["Special Parking", "South End", "one parking space for each dwelling unit", "one parking space for every two dwelling units"],
        ),
        (
            "Can an accessory building be located in a side yard in the R-2 zone?",
            ["Accessory buildings may be located in front yards, side yards, and flankage yards", "yard requirements that are applicable to main buildings"],
        ),
        (
            "What lot area and street frontage do I need for an R-3 apartment building?",
            ["R-3 Zone", "minimum lot area", "8,100 square feet", "minimum continuous street frontage", "90 feet"],
        ),
    ],
)
def test_retrieval_api_handles_customer_density_due_diligence_questions(
    latest_ingestion_document,
    settings,
    question,
    expected_snippets,
):
    with session_scope(latest_ingestion_document["db_url"]) as session:
        bundle = retrieve_context(
            session,
            document_id=latest_ingestion_document["document_id"],
            question_text=question,
            known_facts={},
            settings=settings,
            embedding_client=MockEmbeddingClient(),
            top_k=12,
        )
    context = "\n".join(candidate.text for candidate in bundle.candidates[:8])
    assert all(snippet.lower() in context.lower() for snippet in expected_snippets)


@pytest.mark.parametrize(
    ("question", "expected_snippets"),
    [
        (
            "What is the maximum lot coverage for a duplex in the R-2 zone?",
            ["R-2 ZONE", "Lot coverage - Maximum lot coverage shall be 35 percent"],
        ),
        (
            "What minimum lot area is required for an R-2 duplex?",
            ["R-2 ZONE", "duplex", "5000"],
        ),
        (
            "What is the minimum front yard for R-2 uses in the South End Area?",
            ["FRONT YARD SETBACK", "South End", "majority of residential buildings"],
        ),
        (
            "How much open space is required for an R-3 apartment house with two-bedroom units?",
            ["OPEN SPACE", "120 square feet of open space", "100 square feet shall be landscaped open space"],
        ),
        (
            "What population density is allowed for an R-3 apartment building in Schedule A?",
            ["250 persons per acre", "Schedule A"],
        ),
        (
            "Are day care facilities permitted in the R-3 zone?",
            ["R-3 Zone", "day care facility"],
        ),
        (
            "What parking is required for a day care facility used as an R-3 multiple dwelling use?",
            ["day care facility as an R-3 (Multiple dwelling) use", "one separately accessible parking space", "1,200 square feet"],
        ),
        (
            "What is the minimum side yard for R-2A additions?",
            ["R-2A ZONE", "Minimum Side Yard for Additions", "6 feet"],
        ),
        (
            "What is the maximum lot coverage for R-2A uses?",
            ["R-2A ZONE", "Maximum Lot Coverage", "40%"],
        ),
        (
            "What is the required frontage for a townhouse in the R-2T zone?",
            ["R-2T ZONE", "Minimum lot frontage", "18 feet per townhouse, plus 20 feet"],
        ),
    ],
)
def test_retrieval_api_handles_additional_customer_buildout_questions(
    latest_ingestion_document,
    settings,
    question,
    expected_snippets,
):
    with session_scope(latest_ingestion_document["db_url"]) as session:
        bundle = retrieve_context(
            session,
            document_id=latest_ingestion_document["document_id"],
            question_text=question,
            known_facts={},
            settings=settings,
            embedding_client=MockEmbeddingClient(),
            top_k=12,
        )
    context = "\n".join(candidate.text for candidate in bundle.candidates[:8])
    assert all(snippet.lower() in context.lower() for snippet in expected_snippets)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)
