from pathlib import Path

from bylaw_retrieval.openai_tools import (
    OpenAIToolExecutor,
    build_openai_chat_completions_tool_specs,
    build_openai_responses_tool_specs,
)
from bylaw_retrieval.retrieval import CitationLookupRequest, RetrievalRequest, RetrievalService
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
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


def _add_document(session, municipality: str, bylaw_name: str) -> Document:
    document = Document(
        municipality=municipality,
        bylaw_name=bylaw_name,
        source_path=f"{municipality}-{bylaw_name}.txt",
        source_url=None,
        file_hash=f"{municipality}-{bylaw_name}",
        version_label=None,
        consolidation_date=None,
        mime_type="text/plain",
        page_count=1,
        parser_version="test",
    )
    session.add(document)
    session.flush()
    return document


def _add_fragment(
    session,
    document_id: int,
    *,
    text: str,
    citation_path: str | None = None,
    page_start: int = 1,
    reading_order_start: int = 1,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SECTION,
        citation_label=citation_path,
        citation_path=citation_path,
        parent_fragment_id=None,
        page_start=page_start,
        page_end=page_start,
        reading_order_start=reading_order_start,
        reading_order_end=reading_order_start,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    return fragment


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


def test_retrieval_search_scores_matches_after_500_fragments(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'late-match.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        document = _add_document(session, "Sampleton", "Large Zoning Bylaw")
        for index in range(500):
            _add_fragment(
                session,
                document.id,
                text=f"Irrelevant administrative section {index}",
                citation_path=f"{index + 1}",
                page_start=index + 1,
                reading_order_start=index + 1,
            )
        _add_fragment(
            session,
            document.id,
            text="Accessory dwelling unit height and rear yard setback rules.",
            citation_path="501",
            page_start=501,
            reading_order_start=501,
        )

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="accessory dwelling unit height",
                document_id=document.id,
                limit=1,
            )
        )

    assert response.total_matches == 1
    assert response.matches[0].citation_path == "501"


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


def test_lookup_citation_requires_document_id_for_ambiguous_citations(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'ambiguous-lookup.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        first = _add_document(session, "Sampleton", "Zoning Bylaw")
        second = _add_document(session, "Exampleville", "Zoning Bylaw")
        _add_fragment(session, first.id, text="Sampleton section 4.2 text", citation_path="4.2")
        _add_fragment(session, second.id, text="Exampleville section 4.2 text", citation_path="4.2")

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        try:
            service.lookup_citation(CitationLookupRequest(citation_path="4.2"))
        except ValueError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected ambiguous citation lookup to fail")

        assert "ambiguous across documents" in message
        scoped = service.lookup_citation(CitationLookupRequest(citation_path="4.2", document_id=second.id))
        assert scoped.document_id == second.id
        assert scoped.text == "Exampleville section 4.2 text"


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
