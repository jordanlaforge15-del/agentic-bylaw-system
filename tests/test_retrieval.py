from pathlib import Path

from bylaw_retrieval.openai_tools import (
    OpenAIToolExecutor,
    build_openai_chat_completions_tool_specs,
    build_openai_responses_tool_specs,
)
from bylaw_retrieval.retrieval import CitationLookupRequest, RetrievalRequest, RetrievalService
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.pipeline.ingest import ingest_file


def _seed_document(db_url: str) -> int:
    fixture = Path("tests/fixtures/synthetic_bylaw.txt")
    with session_scope(db_url) as session:
        document, _run = ingest_file(
            session,
            fixture,
            municipality="Sampleton",
            bylaw_name="Synthetic Zoning Bylaw",
        )
        return document.id


def test_retrieval_search_returns_citation_grounded_matches(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'retrieval.db'}"
    create_all(db_url)
    document_id = _seed_document(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="front yard setback",
                document_id=document_id,
                limit=3,
            )
        )
        assert response.matches
        top = response.matches[0]
        assert top.document_id == document_id
        assert top.text
        assert top.score > 0


def test_lookup_citation_returns_context(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'lookup.db'}"
    create_all(db_url)
    document_id = _seed_document(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        outline = service.get_document_outline(document_id, include_text=True)
        cited = next(fragment for fragment in outline.fragments if fragment.citation_path)
        match = service.lookup_citation(
            CitationLookupRequest(citation_path=cited.citation_path, document_id=document_id)
        )
        assert match.citation_path == cited.citation_path
        assert match.text


def test_openai_adapter_mirrors_retrieval_contract(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'openai.db'}"
    create_all(db_url)
    document_id = _seed_document(db_url)
    tool_names = {tool["name"] for tool in build_openai_responses_tool_specs()}
    assert {"list_documents", "get_document_outline", "lookup_citation", "search_bylaw_evidence"} <= tool_names
    chat_tool_names = {tool["function"]["name"] for tool in build_openai_chat_completions_tool_specs()}
    assert chat_tool_names == tool_names

    with session_scope(db_url) as session:
        executor = OpenAIToolExecutor(session)
        search_result = executor.execute(
            "search_bylaw_evidence",
            {"query": "lot coverage", "document_id": document_id, "limit": 2},
        )
        assert search_result["total_matches"] >= 1
