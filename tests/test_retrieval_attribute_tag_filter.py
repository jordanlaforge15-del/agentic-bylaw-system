"""Tests for ABS-45 — RetrievalRequest.attribute_tag_filter.

Four behaviours pinned:

* No filter (default) — backwards-compatible; every existing test path
  expects this case to return the same matches it always has.
* Filter applied — only fragments whose ``attribute_tags`` includes
  the requested key are scored.
* Union semantic across multiple tags — any-of, not all-of.
* Empty result when no fragment carries the tag.

The tests run against sqlite via ``create_all`` (the JSONB column
stores as JSON text under that backend, and the service falls back to
LIKE-matching).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalService
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


def _make_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'tag_filter.db'}"
    create_all(db_url)
    return db_url


def _seed(session) -> dict[str, int]:
    document = Document(
        municipality="HRM",
        bylaw_name="Test Bylaw",
        source_path="t.pdf",
        file_hash="h",
        mime_type="application/pdf",
        page_count=1,
        parser_version="test",
    )
    session.add(document)
    session.flush()
    fragments = {
        "front_setback": SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.2.1",
            page_start=1,
            page_end=1,
            text="The minimum front yard setback shall be 4.5 metres.",
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
            attribute_tags=["front_setback_m"],
        ),
        "height": SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.3.1",
            page_start=2,
            page_end=2,
            text="The maximum building height shall be 14 metres.",
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
            attribute_tags=["building_height_m"],
        ),
        "multi": SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.4.1",
            page_start=3,
            page_end=3,
            text=(
                "Both the minimum side yard setback and the maximum building "
                "height in this district shall conform to Table 4.4."
            ),
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
            attribute_tags=["side_setback_left_m", "side_setback_right_m", "building_height_m"],
        ),
        "untagged": SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="5.1",
            page_start=4,
            page_end=4,
            text="Definitions of building, lot, and yard apply across this bylaw.",
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
            attribute_tags=[],
        ),
    }
    for fragment in fragments.values():
        session.add(fragment)
    session.flush()
    return {name: fragment.id for name, fragment in fragments.items()}


def test_default_request_returns_all_text_matches(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed(session)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(query="building height", limit=10)
        )
    # All fragments mentioning "building height" surface — height,
    # multi, and untagged-ish (the untagged fragment mentions
    # "building"). The filter is OFF here.
    citations = {m.citation_path for m in response.matches}
    assert "4.3.1" in citations
    assert "4.4.1" in citations


def test_filter_applied_restricts_to_tagged_fragments(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed(session)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="setback",
                attribute_tag_filter=["front_setback_m"],
                limit=10,
            )
        )
    citations = {m.citation_path for m in response.matches}
    # Only the front-setback clause carries front_setback_m. The
    # multi-tag clause is in the corpus but tagged with side_setback,
    # not front_setback, so it must NOT surface.
    assert citations == {"4.2.1"}


def test_multiple_tags_unions(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed(session)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="height setback",
                attribute_tag_filter=["building_height_m", "front_setback_m"],
                limit=10,
            )
        )
    citations = {m.citation_path for m in response.matches}
    # Any-of semantic: 4.2.1 carries front_setback_m, 4.3.1 carries
    # building_height_m, 4.4.1 carries building_height_m (alongside
    # side setbacks). All three are eligible. The untagged
    # definitions clause must NOT surface.
    assert citations == {"4.2.1", "4.3.1", "4.4.1"}


def test_filter_with_no_matches_returns_empty(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed(session)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="anything",
                attribute_tag_filter=["lot_coverage_percent"],
                limit=10,
            )
        )
    assert response.matches == []
    assert response.total_matches == 0


def test_filter_helper_rejects_empty_list() -> None:
    """``_attribute_tag_filter_clause`` must raise on empty input.

    The service uses ``if request.attribute_tag_filter:`` as the gate
    so an empty list shouldn't reach the helper — but if some future
    caller bypasses the gate, we'd rather crash than silently emit a
    no-op disjunction that's always-true on postgres (and breaks the
    "tag must be present" contract).
    """
    from bylaw_retrieval.retrieval.service import _attribute_tag_filter_clause

    with pytest.raises(ValueError, match="non-empty"):
        _attribute_tag_filter_clause([], dialect_name="postgresql")
    with pytest.raises(ValueError, match="non-empty"):
        _attribute_tag_filter_clause([], dialect_name="sqlite")
