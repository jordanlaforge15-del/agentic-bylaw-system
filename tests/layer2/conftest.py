from __future__ import annotations

from pathlib import Path

import pytest

from layer1.db.session import session_scope
from layer1.pipeline.ingest import ingest_file
from layer2.config import Layer2Settings
from layer2.db.init_db import create_all
from layer2.embeddings.clients import MockEmbeddingClient
from layer2.llm.clients import MockLLMClient
from layer2.pipeline.service import embed_document_fragments


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'layer2.db'}"


@pytest.fixture()
def settings() -> Layer2Settings:
    return Layer2Settings(
        DATABASE_URL="sqlite:///unused.db",
        LAYER2_LLM_MODEL="mock-layer2",
        LAYER2_EMBEDDING_MODEL="mock-embedding",
    )


@pytest.fixture()
def clients():
    return MockEmbeddingClient(), MockLLMClient()


@pytest.fixture()
def prepared_document(db_url: str, clients):
    create_all(db_url)
    fixture = Path("tests/fixtures/synthetic_bylaw.txt")
    embedding_client, _ = clients
    with session_scope(db_url) as session:
        document, _ = ingest_file(
            session,
            fixture,
            municipality="Sampleton",
            bylaw_name="Synthetic Zoning Bylaw",
        )
        embed_document_fragments(session, document_id=document.id, embedding_client=embedding_client)
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id}
