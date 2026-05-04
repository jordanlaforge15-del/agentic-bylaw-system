"""Phase I — `--latest-only` MCP scope.

When the MCP server is launched with --latest-only, RetrievalService is
constructed with a default-document-id resolver that scopes every query
to the most recently ingested document unless the caller explicitly
supplies a scoping filter (document_id, municipality, or bylaw_name).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import (
    CitationLookupRequest,
    RetrievalRequest,
    RetrievalService,
    latest_document_id_resolver,
)
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


def _seed_two_docs(db_url: str) -> tuple[int, int]:
    """Seed two documents — older "peninsula" and newer "regional centre" —
    each with a single fragment whose text contains a unique sentinel so
    we can tell which document a search is hitting.
    """
    with session_scope(db_url) as session:
        old = Document(
            municipality="Halifax",
            bylaw_name="Halifax Peninsula Land Use Bylaw",
            source_path="/old.txt",
            file_hash="o" * 64,
            mime_type="text/plain",
            ingestion_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        session.add(old)
        session.flush()
        session.add(
            SourceFragment(
                document_id=old.id,
                fragment_type=FragmentType.SECTION,
                citation_label="40",
                citation_path="40",
                page_start=1,
                page_end=1,
                text="OLD_BYLAW_SENTINEL maximum height shall be 35 feet.",
                parse_status=ParseStatus.PARSED,
                source_block_ids_json=[],
                metadata_json={},
            )
        )
        new = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/new.txt",
            file_hash="n" * 64,
            mime_type="text/plain",
            ingestion_timestamp=datetime(2026, 5, 4, tzinfo=timezone.utc),
        )
        session.add(new)
        session.flush()
        session.add(
            SourceFragment(
                document_id=new.id,
                fragment_type=FragmentType.SECTION,
                citation_label="109",
                citation_path="109",
                page_start=115,
                page_end=115,
                text="NEW_BYLAW_SENTINEL maximum building height as shown on Schedule 15.",
                parse_status=ParseStatus.PARSED,
                source_block_ids_json=[],
                metadata_json={},
            )
        )
        return old.id, new.id


def test_latest_resolver_returns_most_recently_ingested(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    with session_scope(db_url) as session:
        assert latest_document_id_resolver(session) == new_id
        assert latest_document_id_resolver(session) != old_id


def test_search_unscoped_with_default_resolver_only_hits_latest(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        response = service.search(RetrievalRequest(query="maximum height", limit=10))

    assert all(m.document_id == new_id for m in response.matches)
    assert all("NEW_BYLAW_SENTINEL" in m.text for m in response.matches)


def test_search_unscoped_without_resolver_hits_both(tmp_path: Path):
    """Default behaviour without --latest-only must still see all documents."""
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(session)  # no resolver
        response = service.search(RetrievalRequest(query="maximum height", limit=10))

    doc_ids = {m.document_id for m in response.matches}
    assert old_id in doc_ids
    assert new_id in doc_ids


def test_explicit_document_id_overrides_default(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        response = service.search(
            RetrievalRequest(query="maximum height", document_id=old_id, limit=10)
        )
    assert all(m.document_id == old_id for m in response.matches)


def test_explicit_municipality_overrides_default(tmp_path: Path):
    """Comparative queries (filter by municipality across multiple docs) must
    bypass the latest-only default — otherwise the user can't actually compare."""
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        response = service.search(
            RetrievalRequest(query="maximum height", municipality="Halifax", limit=10)
        )
    # municipality="Halifax" matches the old peninsula doc only
    assert all(m.document_id == old_id for m in response.matches)


def test_explicit_bylaw_name_overrides_default(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        response = service.search(
            RetrievalRequest(query="maximum height", bylaw_name="Peninsula", limit=10)
        )
    assert all(m.document_id == old_id for m in response.matches)


def test_list_documents_with_default_returns_only_latest(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        docs = service.list_documents()
    assert len(docs) == 1
    assert docs[0].id == new_id


def test_list_documents_explicit_filter_overrides_default(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        docs = service.list_documents(municipality="Halifax")
    assert {d.id for d in docs} == {old_id}


def test_lookup_citation_with_default_picks_latest_for_ambiguous_path(tmp_path: Path):
    """Two documents share citation_path '40'; without latest-only this
    raises 'ambiguous'. With latest-only, the newer wins automatically.
    """
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    # Add a fragment with citation_path="40" on the new doc too:
    with session_scope(db_url) as session:
        session.add(
            SourceFragment(
                document_id=new_id,
                fragment_type=FragmentType.SECTION,
                citation_label="40",
                citation_path="40",
                page_start=10,
                page_end=10,
                text="NEW_BYLAW_SENTINEL section 40.",
                parse_status=ParseStatus.PARSED,
                source_block_ids_json=[],
                metadata_json={},
            )
        )

    # Without resolver: ambiguous, raises.
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        with pytest.raises(ValueError, match="ambiguous"):
            service.lookup_citation(CitationLookupRequest(citation_path="40"))

    # With latest-only resolver: scoped to new doc, no ambiguity.
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        match = service.lookup_citation(CitationLookupRequest(citation_path="40"))
    assert match.document_id == new_id


def test_resolver_runs_per_request_so_new_ingest_is_picked_up(tmp_path: Path):
    """If a fresh ingest happens after the service was constructed, the
    next request picks up the new latest doc — no server restart needed."""
    db_url = f"sqlite:///{tmp_path / 'latest.db'}"
    create_all(db_url)
    old_id, new_id = _seed_two_docs(db_url)

    # First call - new_id is latest.
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        response = service.search(RetrievalRequest(query="height", limit=10))
        assert all(m.document_id == new_id for m in response.matches)

    # Add an even newer document.
    with session_scope(db_url) as session:
        newest = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/newest.txt",
            file_hash="z" * 64,
            mime_type="text/plain",
            ingestion_timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        session.add(newest)
        session.flush()
        session.add(
            SourceFragment(
                document_id=newest.id,
                fragment_type=FragmentType.SECTION,
                citation_label="109",
                citation_path="109",
                page_start=1,
                page_end=1,
                text="NEWEST_SENTINEL maximum building height.",
                parse_status=ParseStatus.PARSED,
                source_block_ids_json=[],
                metadata_json={},
            )
        )
        newest_id = newest.id

    # New service instance, same resolver: now scopes to the newest doc.
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        response = service.search(RetrievalRequest(query="height", limit=10))
        assert all(m.document_id == newest_id for m in response.matches)


def test_resolver_returns_none_on_empty_db(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'empty.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        assert latest_document_id_resolver(session) is None
        # And the service must not blow up — it just behaves as if no scope.
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        response = service.search(RetrievalRequest(query="anything", limit=5))
        assert response.matches == []
