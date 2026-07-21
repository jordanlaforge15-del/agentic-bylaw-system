"""ABS-413 — explicit ``retrieval_enabled`` publish scope.

Production (advisor app, MCP server, monitoring) constructs
``RetrievalService`` with ``retrieval_enabled_resolver``: the corpus is
exactly the set of documents an operator has enabled, nothing is derived
from ingestion recency, and — critically — the scope FAILS CLOSED: zero
enabled documents means zero results, never "everything".
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import (
    CitationLookupRequest,
    RetrievalRequest,
    RetrievalService,
    retrieval_enabled_resolver,
    scoped_linked_datasets,
)
from layer1.db.base import Document, ExternalDataset, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


def _add_doc(session, *, bylaw_name: str, sentinel: str, enabled: bool, hash_char: str) -> int:
    doc = Document(
        municipality="HRM",
        bylaw_name=bylaw_name,
        source_path=f"/{hash_char}.txt",
        file_hash=hash_char * 64,
        mime_type="text/plain",
        ingestion_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retrieval_enabled=enabled,
    )
    session.add(doc)
    session.flush()
    session.add(
        SourceFragment(
            document_id=doc.id,
            fragment_type=FragmentType.SECTION,
            citation_label="10",
            citation_path=f"10-{hash_char}",
            page_start=1,
            page_end=1,
            text=f"{sentinel} maximum building height applies.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
    )
    return doc.id


def _seed(db_url: str, *, enable_first: bool = True, enable_second: bool = False) -> tuple[int, int]:
    with session_scope(db_url) as session:
        first = _add_doc(
            session,
            bylaw_name="Enabled Test By-law",
            sentinel="ENABLED_SENTINEL",
            enabled=enable_first,
            hash_char="a",
        )
        second = _add_doc(
            session,
            bylaw_name="Disabled Test By-law",
            sentinel="DISABLED_SENTINEL",
            enabled=enable_second,
            hash_char="b",
        )
        return first, second


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'flag.db'}"
    create_all(url)
    return url


def test_resolver_returns_only_enabled_ids(db_url: str):
    first, second = _seed(db_url, enable_first=True, enable_second=True)
    with session_scope(db_url) as session:
        assert retrieval_enabled_resolver(session) == sorted([first, second])
        session.get(Document, second).retrieval_enabled = False
        session.flush()
        assert retrieval_enabled_resolver(session) == [first]


def test_resolver_returns_empty_list_never_none(db_url: str):
    # The fail-closed contract: [] both when documents exist but none are
    # enabled AND when the corpus is empty. (The latest-* resolvers return
    # None in that case, which the scope checks treat as "unscoped".)
    with session_scope(db_url) as session:
        assert retrieval_enabled_resolver(session) == []
    _seed(db_url, enable_first=False, enable_second=False)
    with session_scope(db_url) as session:
        assert retrieval_enabled_resolver(session) == []


def test_search_hits_only_enabled_documents(db_url: str):
    _seed(db_url)
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        response = service.search(RetrievalRequest(query="maximum building height", top_k=10))
        texts = [r.text for r in response.matches]
        assert any("ENABLED_SENTINEL" in t for t in texts)
        assert not any("DISABLED_SENTINEL" in t for t in texts)


def test_explicit_document_id_cannot_escape_scope(db_url: str):
    _, disabled_id = _seed(db_url)
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        response = service.search(
            RetrievalRequest(query="maximum building height", document_id=disabled_id, top_k=10)
        )
        assert response.matches == []


def test_list_documents_scoped_and_flag_surfaced(db_url: str):
    first, _ = _seed(db_url)
    with session_scope(db_url) as session:
        scoped = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        docs = scoped.list_documents(None, None, 10)
        assert [d.id for d in docs] == [first]
        assert docs[0].retrieval_enabled is True

        unscoped = RetrievalService(session)
        all_docs = unscoped.list_documents(None, None, 10)
        assert len(all_docs) == 2
        assert {d.id: d.retrieval_enabled for d in all_docs}[first] is True


def test_zero_enabled_documents_fails_closed(db_url: str):
    first, second = _seed(db_url, enable_first=False, enable_second=False)
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        assert service.search(RetrievalRequest(query="maximum building height")).matches == []
        assert service.list_documents(None, None, 10) == []
        lookup = service.lookup_citation(CitationLookupRequest(citation_path="10-a"))
        assert lookup.match is None
        assert (
            scoped_linked_datasets(
                session, default_document_id_resolver=retrieval_enabled_resolver
            )
            == []
        )


def test_scoped_linked_datasets_excludes_disabled_documents(db_url: str):
    _, disabled_id = _seed(db_url)
    with session_scope(db_url) as session:
        fragment = (
            session.query(SourceFragment)
            .filter(SourceFragment.document_id == disabled_id)
            .one()
        )
        session.add(
            ExternalDataset(
                name="test_zoning_boundaries",
                format="geojson",
                content_hash="h-zone",
                crs="EPSG:4326",
                feature_count=1,
                linked_document_id=disabled_id,
                linked_fragment_id=fragment.id,
                linked_fragment_citation="10",
                schema_mapping_json={},
                parse_status=ParseStatus.PARSED,
                metadata_json={"link_status": "linked"},
            )
        )
        session.flush()
        assert (
            scoped_linked_datasets(
                session, default_document_id_resolver=retrieval_enabled_resolver
            )
            == []
        )
        # Unscoped sees it — the eviction is purely a function of the flag.
        assert len(scoped_linked_datasets(session)) == 1


def test_outline_of_disabled_document_reads_as_not_found(db_url: str):
    first, disabled_id = _seed(db_url)
    with session_scope(db_url) as session:
        scoped = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        assert scoped.get_document_outline(first).document.id == first
        with pytest.raises(ValueError, match=f"Document {disabled_id} not found"):
            scoped.get_document_outline(disabled_id)
        # Without a resolver the same call works — dev/debug surfaces keep
        # full visibility.
        unscoped = RetrievalService(session)
        assert unscoped.get_document_outline(disabled_id).document.id == disabled_id


def test_no_resolver_remains_unscoped(db_url: str):
    _seed(db_url)
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        texts = [
            r.text
            for r in service.search(
                RetrievalRequest(query="maximum building height", top_k=10)
            ).matches
        ]
        assert any("ENABLED_SENTINEL" in t for t in texts)
        assert any("DISABLED_SENTINEL" in t for t in texts)


def test_flag_flip_is_picked_up_per_request(db_url: str):
    # The resolver runs per request: enabling a doc mid-session surfaces it
    # without reconstructing the service.
    _, second = _seed(db_url)
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        before = service.search(RetrievalRequest(query="maximum building height", top_k=10))
        assert not any("DISABLED_SENTINEL" in r.text for r in before.matches)
        session.get(Document, second).retrieval_enabled = True
        session.flush()
        after = service.search(RetrievalRequest(query="maximum building height", top_k=10))
        assert any("DISABLED_SENTINEL" in r.text for r in after.matches)
