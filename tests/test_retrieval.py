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
                query="residential zones",  # actually appears in synthetic_bylaw.txt
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
        # ABS-261: lookup_citation now returns a CitationLookupResponse
        # envelope. On a hit, ``match`` is populated and ``suggestions``
        # is empty.
        response = service.lookup_citation(
            CitationLookupRequest(citation_path=cited.citation_path, document_id=document_id)
        )
        assert response.match is not None
        assert response.match.citation_path == cited.citation_path
        assert response.match.text
        assert response.suggestions == []


def test_lookup_citation_returns_suggestions_on_path_mismatch(tmp_path: Path):
    """ABS-261 regression test.

    A near-miss path (the model's natural prose form 'Table 1A' against
    a stored canonical 'Part II > [Table 1A]') must NOT raise — it must
    return ``match=None`` with the nearest canonical paths in
    ``suggestions``. Before this fix, ``lookup_citation`` raised
    ``ValueError`` and the advisor's tool-use loop thrashed to
    ``max_iterations=10``, burning ~2× the necessary tokens on ~38% of
    multi-turn turns.
    """
    db_url = f"sqlite:///{tmp_path / 'suggest.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _add_document(session, "Sampleton", "Zoning Bylaw")
        _add_fragment(
            session, doc.id, text="Use permissions for residential zones.",
            citation_path="Part II > [Table 1A]",
        )
        _add_fragment(
            session, doc.id, text="Setback standards by zone.",
            citation_path="Part III > [Table 3]",
            reading_order_start=2,
        )
        _add_fragment(
            session, doc.id, text="Floor area ratio overlay.",
            citation_path="Part IV > 112 > (a)",
            reading_order_start=3,
        )
        document_id = doc.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        # Human-form prose path the model is likely to send.
        response = service.lookup_citation(
            CitationLookupRequest(citation_path="Table 1A", document_id=document_id)
        )
        assert response.match is None, "near-miss must not return a match"
        # The canonical form should be the top suggestion (or at least
        # appear in the list).
        assert "Part II > [Table 1A]" in response.suggestions, (
            f"suggestions did not include the canonical path: {response.suggestions}"
        )
        # And nothing should raise — the regression we're guarding.


def test_lookup_citation_no_similar_paths_returns_empty_envelope(tmp_path: Path):
    """When the document has nothing to suggest, ``lookup_citation`` must
    still return cleanly (match=None, suggestions=[]) rather than raise.

    This is the signal the agent needs to switch tools (e.g., to
    ``search_bylaw_evidence``) instead of looping on ``lookup_citation``.
    Modeled here with a document that has ZERO fragments carrying a
    ``citation_path`` — for example, a draft / partially-parsed ingest.
    On realistic full ingests the suggestion list will usually contain
    a few low-confidence entries that rapidfuzz surfaces via partial
    string overlap; that's a feature, not a bug (it lets the agent see
    why no good match exists), but it makes a "must be empty" assertion
    sensitive to corpus shape, which we sidestep here.
    """
    db_url = f"sqlite:///{tmp_path / 'nosimilar.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _add_document(session, "Sampleton", "Zoning Bylaw")
        # Intentionally NO citation_path on the only fragment — the
        # document carries text but has no addressable citations yet.
        _add_fragment(session, doc.id, text="Use permissions.", citation_path=None)
        document_id = doc.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.lookup_citation(
            CitationLookupRequest(citation_path="Schedule 99", document_id=document_id)
        )
        assert response.match is None
        # No candidates to rank → empty suggestions, and crucially: no
        # exception raised. That's the contract guarantee.
        assert response.suggestions == []


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
        # The ambiguous-across-documents case STILL raises — see
        # CitationLookupResponse docstring. The caller can only resolve
        # it by re-issuing with an explicit document_id, and a
        # suggestion list of identical paths in different docs would
        # be more confusing than helpful.
        try:
            service.lookup_citation(CitationLookupRequest(citation_path="4.2"))
        except ValueError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected ambiguous citation lookup to fail")

        assert "ambiguous across documents" in message
        scoped = service.lookup_citation(
            CitationLookupRequest(citation_path="4.2", document_id=second.id)
        )
        assert scoped.match is not None
        assert scoped.match.document_id == second.id
        assert scoped.match.text == "Exampleville section 4.2 text"
        assert scoped.suggestions == []


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
            # "residential" actually appears in synthetic_bylaw.txt; "lot
            # coverage" historically passed only because every parsed
            # fragment scored a +1 baseline regardless of query match.
            {"query": "residential zones", "document_id": document_id, "limit": 2},
        )
        assert search_result["total_matches"] >= 1
