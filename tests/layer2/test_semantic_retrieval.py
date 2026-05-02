from __future__ import annotations

from layer1.db.base import SourceFragment, SourceTable, SourceTableCell
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics
from layer2.config import Layer2Settings
from layer2.embeddings.clients import MockEmbeddingClient
from layer2.retrieval.semantic_graph import expand_semantic_graph
from layer2.retrieval.semantic import retrieve_semantic_facts
from layer2.retrieval.service import _candidate_pool_limit, retrieve_context
from layer2.pipeline.service import _select_fragments_for_prompt
from layer2.models.schemas import CandidateFragment
from tests.test_semantic_enrichment import semantic_db as semantic_db  # noqa: F401


def test_semantic_retrieval_finds_permission_fact_without_legacy_table_helper(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        candidates = retrieve_semantic_facts(
            session,
            document_id=semantic_db["document_id"],
            question_text="Can I operate a restaurant use in HR-1?",
            top_k=5,
        )

    assert candidates
    assert candidates[0].reason["operation"] == "retrieve_semantic_facts"
    assert candidates[0].metadata["semantic_fact_id"]
    assert "restaurant use" in candidates[0].text
    assert "HR-1" in candidates[0].text
    assert "② ③" in candidates[0].text


def test_retrieve_context_places_semantic_table_fact_before_legacy_candidates(semantic_db):
    settings = Layer2Settings(
        DATABASE_URL=semantic_db["db_url"],
        LAYER2_LLM_MODEL="mock-layer2",
        LAYER2_EMBEDDING_MODEL="mock-embedding",
    )
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        bundle = retrieve_context(
            session,
            document_id=semantic_db["document_id"],
            question_text="Can I operate a restaurant use in HR-1?",
            known_facts={},
            settings=settings,
            embedding_client=MockEmbeddingClient(),
            top_k=5,
        )

    assert bundle.candidates[0].reason["operation"] == "retrieve_semantic_facts"
    assert bundle.query_terms["semantic_retrieval"]["candidate_count"] >= 1
    assert "Table 1A" in bundle.candidates[0].citation_label
    assert "② ③" in bundle.candidates[0].text


def test_semantic_retrieval_summarizes_zone_permission_query_with_ocr_typo(semantic_db):
    settings = Layer2Settings(
        DATABASE_URL=semantic_db["db_url"],
        LAYER2_LLM_MODEL="mock-layer2",
        LAYER2_EMBEDDING_MODEL="mock-embedding",
    )
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        candidates = retrieve_semantic_facts(
            session,
            document_id=semantic_db["document_id"],
            question_text="What uses are permitted in lHR-1 ?",
            top_k=5,
        )
        bundle = retrieve_context(
            session,
            document_id=semantic_db["document_id"],
            question_text="What uses are permitted in lHR-1 ?",
            known_facts={},
            settings=settings,
            embedding_client=MockEmbeddingClient(),
            top_k=5,
        )

    assert candidates
    assert candidates[0].reason["aggregation"] == "permission_zone_summary"
    assert "permitted uses for HR-1" in candidates[0].text
    assert "restaurant use" in candidates[0].text
    assert "cluster housing use" in candidates[0].text
    assert "② ③" in candidates[0].text
    assert "㉑" in candidates[0].text
    assert bundle.candidates[0].reason["aggregation"] == "permission_zone_summary"
    assert bundle.query_terms["semantic_retrieval"]["candidate_count"] >= 1


def test_semantic_retrieval_finds_dimensional_fact(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        candidates = retrieve_semantic_facts(
            session,
            document_id=semantic_db["document_id"],
            question_text="What is the minimum lot area in R1?",
            top_k=5,
        )

    assert candidates
    assert "minimum lot area" in candidates[0].text
    assert "R-1" in candidates[0].text
    assert "400 m2" in candidates[0].text


def test_semantic_retrieval_finds_condition_definition(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        candidates = retrieve_semantic_facts(
            session,
            document_id=semantic_db["document_id"],
            question_text="What does condition ③ mean?",
            top_k=5,
        )

    assert candidates
    assert candidates[0].reason["relation_type"] == "condition_definition"
    assert "③" in candidates[0].text
    assert "commercial use conditions" in candidates[0].text


def test_semantic_retrieval_finds_definition_fact(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        session.add(
            SourceFragment(
                document_id=semantic_db["document_id"],
                fragment_type=FragmentType.SECTION,
                page_start=10,
                page_end=10,
                citation_label="Section 10",
                text="Backyard suite means a secondary dwelling unit located in an accessory building on the same lot.",
                parse_status=ParseStatus.PARSED,
                source_block_ids_json=[],
                metadata_json={},
            )
        )
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        candidates = retrieve_semantic_facts(
            session,
            document_id=semantic_db["document_id"],
            question_text="What is a backyard suite?",
            top_k=5,
        )

    assert candidates
    assert candidates[0].reason["relation_type"] == "definition"
    assert "backyard suite" in candidates[0].text
    assert "secondary dwelling unit" in candidates[0].text


def test_semantic_retrieval_finds_parking_standard_fact(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        table = SourceTable(
            document_id=semantic_db["document_id"],
            caption="Table 15: Required minimum or maximum number of motor vehicle parking spaces per lot, by zone and use",
            page_start=70,
            page_end=70,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(table)
        session.flush()
        _add_table_rows(
            session,
            table.id,
            [
                ["Use", "HR-1", "COR"],
                ["Restaurant use", "Minimum 1 space per 100 m2 floor area", "Maximum 1 space per 75 m2 floor area"],
                ["Office use", "Minimum 1 space per 80 m2 floor area", "Maximum 1 space per 75 m2 floor area"],
            ],
        )
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        candidates = retrieve_semantic_facts(
            session,
            document_id=semantic_db["document_id"],
            question_text="Does HR-1 require parking for restaurant use?",
            top_k=5,
        )

    assert candidates
    assert candidates[0].reason["relation_type"] == "parking_standard"
    assert "restaurant use" in candidates[0].text
    assert "HR-1" in candidates[0].text
    assert "Minimum 1 space per 100 m2" in candidates[0].text


def test_semantic_retrieval_finds_table_one_requirements_and_linked_footnotes(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        table = SourceTable(
            document_id=semantic_db["document_id"],
            caption="Table 1: Requirements for internal conversions and rear additions",
            page_start=80,
            page_end=80,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(table)
        session.flush()
        _add_table_rows(
            session,
            table.id,
            [
                ["Requirement", "Internal conversions", "Rear additions"],
                ["Minimum rear yard", "No change required [1]", "20 feet [2]"],
                ["Minimum side yard for additions", "", "6 feet"],
                ["Additions and structural changes", "", "Permitted to the rear of the building [3]"],
            ],
        )
        for text in [
            "[1] Internal conversions must not increase building height or volume.",
            "[2] Rear additions must comply with rear-yard requirements.",
            "[3] Rear additions and structural changes are limited to the rear of the building.",
        ]:
            session.add(
                SourceFragment(
                    document_id=semantic_db["document_id"],
                    fragment_type=FragmentType.FOOTNOTE,
                    page_start=80,
                    page_end=80,
                    text=text,
                    parse_status=ParseStatus.PARSED,
                    source_block_ids_json=[],
                    metadata_json={},
                )
            )
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        candidates = retrieve_semantic_facts(
            session,
            document_id=semantic_db["document_id"],
            question_text="What requirements apply to rear additions and internal conversions?",
            top_k=8,
        )

    joined = " ".join(candidate.text for candidate in candidates)
    assert candidates
    assert any(candidate.reason["relation_type"] == "requirement" for candidate in candidates)
    assert "Table 1: Requirements for internal conversions and rear additions" in joined
    assert "development_context: rear additions" in joined
    assert "development_context: internal conversions" in joined
    assert "Minimum rear yard" in joined or "minimum rear yard" in joined
    assert "20 feet [2]" in joined
    assert "No change required [1]" in joined
    assert "condition definition: [2] Rear additions must comply with rear-yard requirements." in joined
    assert "condition definition: [1] Internal conversions must not increase building height or volume." in joined


def test_semantic_graph_expands_condition_references_to_section_text(semantic_db):
    settings = Layer2Settings(
        DATABASE_URL=semantic_db["db_url"],
        LAYER2_LLM_MODEL="mock-layer2",
        LAYER2_EMBEDDING_MODEL="mock-embedding",
        LAYER2_SEMANTIC_GRAPH_MAX_DEPTH=5,
        LAYER2_SEMANTIC_GRAPH_MAX_FRAGMENTS=10,
        LAYER2_SEMANTIC_GRAPH_MAX_NODES=50,
    )
    with session_scope(semantic_db["db_url"]) as session:
        section = SourceFragment(
            document_id=semantic_db["document_id"],
            fragment_type=FragmentType.SECTION,
            page_start=90,
            page_end=90,
            citation_label="231.3",
            citation_path="Section 231.3",
            text="231.3 Internal conversions in the ER-3 zone may add dwelling units subject to the standards in this section.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        footnote = SourceFragment(
            document_id=semantic_db["document_id"],
            fragment_type=FragmentType.FOOTNOTE,
            page_start=89,
            page_end=89,
            citation_label="Footnote ㉒",
            text="㉒ Use is permitted subject to Section 231.3.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        table = SourceTable(
            document_id=semantic_db["document_id"],
            caption="Table 1C: Permitted uses by zone",
            page_start=88,
            page_end=88,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add_all([section, footnote, table])
        session.flush()
        _add_table_rows(
            session,
            table.id,
            [
                ["Use", "ER-3"],
                ["Multi-unit dwelling use", "㉒"],
            ],
        )
        section_id = section.id
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        candidates = retrieve_semantic_facts(
            session,
            document_id=semantic_db["document_id"],
            question_text="Can I operate a multi-unit dwelling use in ER-3?",
            top_k=8,
        )
        expanded = expand_semantic_graph(
            session,
            document_id=semantic_db["document_id"],
            candidates=candidates,
            max_depth=settings.semantic_graph_max_depth,
            max_fragments=settings.semantic_graph_max_fragments,
            max_nodes=settings.semantic_graph_max_nodes,
            allowed_edge_types={"conditioned_by", "references", "defines", "applies_to", "excepts", "modifies"},
        )

    joined = " ".join(candidate.text for candidate in expanded)
    assert "Semantic fact (permission): use: multi-unit dwelling use; zone: ER-3" in joined
    assert "condition definition: ㉒ Use is permitted subject to Section 231.3." in joined
    assert "231.3 Internal conversions in the ER-3 zone may add dwelling units" in joined
    assert any(
        candidate.reason.get("expansion") == "semantic_section_reference" and candidate.source_fragment_id == section_id
        for candidate in expanded
    )


def test_candidate_pool_preserves_semantic_section_references_beyond_default_top_k():
    candidates = [
        CandidateFragment(
            source_fragment_id=index,
            source_type="fragment",
            retrieval_channel="full_text",
            base_score=100 - index,
            rerank_score=100 - index,
            text="high scoring context",
            reason={},
        )
        for index in range(1, 10)
    ]
    candidates.append(
        CandidateFragment(
            source_fragment_id=100,
            source_type="fragment",
            retrieval_channel="cross_reference",
            base_score=1,
            rerank_score=1,
            text="231.3 Internal conversions in the ER-3 zone may add dwelling units.",
            reason={"expansion": "semantic_section_reference"},
        )
    )

    limited = _candidate_pool_limit(candidates, top_k=1)

    assert any(candidate.source_fragment_id == 100 for candidate in limited)


def test_prompt_selection_prioritizes_semantic_dependencies_within_budget():
    candidates = [
        CandidateFragment(
            source_fragment_id=index,
            source_type="fragment",
            retrieval_channel="full_text",
            base_score=100 - index,
            rerank_score=100 - index,
            text="unrelated high scoring text " * 20,
            reason={},
        )
        for index in range(1, 8)
    ]
    candidates.append(
        CandidateFragment(
            source_fragment_id=100,
            source_type="fragment",
            retrieval_channel="cross_reference",
            base_score=1,
            rerank_score=1,
            text="231.3 Internal conversions in the ER-3 zone may add dwelling units.",
            reason={"expansion": "semantic_section_reference"},
        )
    )

    selected = _select_fragments_for_prompt(candidates, token_budget=180)

    assert any(candidate.source_fragment_id == 100 for _rank, candidate in selected)


def test_semantic_retrieval_falls_back_when_no_semantic_index(prepared_document, settings):
    with session_scope(prepared_document["db_url"]) as session:
        assert session.query(SourceTable).count() >= 1
        bundle = retrieve_context(
            session,
            document_id=prepared_document["document_id"],
            question_text="What is the minimum lot area for R1?",
            known_facts={},
            settings=settings,
            embedding_client=MockEmbeddingClient(),
            top_k=5,
        )

    assert bundle.query_terms["semantic_retrieval"]["candidate_count"] == 0
    assert any("400 m2" in candidate.text for candidate in bundle.candidates)


def _add_table_rows(session, table_id: int, rows: list[list[str]]) -> None:
    for row_index, row in enumerate(rows):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table_id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=row[0] if row_index else None,
                    col_header_path=rows[0][col_index] if row_index and col_index < len(rows[0]) else None,
                    text=text,
                    metadata_json={},
                )
            )
