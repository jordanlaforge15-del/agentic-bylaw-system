from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import Document, SourceFragment, SourceTable, SourceTableCell
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer2.config import Layer2Settings
from layer2.db.init_db import create_all
from layer2.embeddings.clients import MockEmbeddingClient
from layer2.retrieval.api import search_context
from layer2.retrieval.service import retrieve_context


@pytest.fixture()
def table_1a_document(tmp_path: Path) -> dict:
    db_url = f"sqlite:///{tmp_path / 'table_1a.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="regionalcentrelub-eff-26april13-case24469toclinked.pdf",
            file_hash="table1a",
            mime_type="application/pdf",
            page_count=400,
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        table = SourceTable(
            document_id=document.id,
            caption="Table 1A: Permitted uses by zone (DD, DH, CEN-2, CEN-1, COR, HR-2, and HR-1)",
            page_start=45,
            page_end=47,
            parse_status=ParseStatus.PARSED,
            metadata_json={"source": "Image #1"},
        )
        session.add(table)
        session.flush()
        rows = [
            ["Residential", "DD", "DH", "CEN-2", "CEN-1", "COR", "HR-2", "HR-1"],
            ["Single-unit dwelling use", "●", "●", "●", "●", "●", "㉑", "⑮"],
            ["Cluster housing use", "", "", "", "", "㉑", "㉑", "㉑"],
            ["Commercial", "DD", "DH", "CEN-2", "CEN-1", "COR", "HR-2", "HR-1"],
            ["Restaurant use", "●", "●", "●", "●", "●", "③", "② ③"],
        ]
        for row_index, row in enumerate(rows):
            for col_index, text in enumerate(row):
                session.add(
                    SourceTableCell(
                        table_id=table.id,
                        row_index=row_index,
                        col_index=col_index,
                        row_header_path=row[0] if row_index else None,
                        col_header_path=rows[0][col_index] if row_index and col_index < len(rows[0]) else None,
                        text=text,
                        metadata_json={},
                    )
                )
        footnotes = [
            "② Use is permitted in HR-1 only when located in a local commercial area.",
            "③ Use is permitted subject to commercial use conditions in the table footnotes.",
            "⑮ Use is permitted subject to residential use conditions in the table footnotes.",
            "㉑ Use is permitted subject to cluster housing conditions in the table footnotes.",
            "A black dot means the use is permitted in Tables 1A to 1D.",
        ]
        for offset, text in enumerate(footnotes):
            session.add(
                SourceFragment(
                    document_id=document.id,
                    fragment_type=FragmentType.FOOTNOTE,
                    citation_label=None,
                    citation_path=None,
                    page_start=47,
                    page_end=47,
                    reading_order_start=100 + offset,
                    reading_order_end=100 + offset,
                    text=text,
                    parse_status=ParseStatus.PARSED,
                    confidence=0.9,
                    source_block_ids_json=[],
                    metadata_json={},
                )
            )
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id}


def test_direct_search_exposes_zone_specific_permission_table_cell(table_1a_document):
    with session_scope(table_1a_document["db_url"]) as session:
        candidates = search_context(
            session,
            document_id=table_1a_document["document_id"],
            query="Can I operate a restaurant use in HR-1?",
            top_k=5,
        )

    top = candidates[0]
    assert top.source_type == "table"
    assert top.retrieval_channel == "table"
    assert "Restaurant use" in top.text
    assert "HR-1=② ③" in top.text
    assert "② Use is permitted" in top.text
    assert "③ Use is permitted" in top.text


def test_direct_search_exposes_image_table_cluster_housing_conditions(table_1a_document):
    with session_scope(table_1a_document["db_url"]) as session:
        candidates = search_context(
            session,
            document_id=table_1a_document["document_id"],
            query="Is cluster housing use permitted in COR?",
            top_k=5,
        )

    context = "\n".join(candidate.text for candidate in candidates)
    assert "Cluster housing use" in context
    assert "COR=㉑" in context
    assert "㉑ Use is permitted" in context


def test_public_retrieval_plan_surfaces_restaurant_permission_from_table(table_1a_document):
    settings = Layer2Settings(
        DATABASE_URL=table_1a_document["db_url"],
        LAYER2_LLM_MODEL="mock-layer2",
        LAYER2_EMBEDDING_MODEL="mock-embedding",
    )
    with session_scope(table_1a_document["db_url"]) as session:
        bundle = retrieve_context(
            session,
            document_id=table_1a_document["document_id"],
            question_text="Can I operate a restaurant use in HR-1?",
            known_facts={},
            settings=settings,
            embedding_client=MockEmbeddingClient(),
            top_k=5,
        )

    context = "\n".join(candidate.text for candidate in bundle.candidates[:3])
    assert "Restaurant use" in context
    assert "HR-1=② ③" in context
    assert "Applicable footnotes" in context
