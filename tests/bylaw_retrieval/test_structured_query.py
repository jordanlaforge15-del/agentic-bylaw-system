"""Service-level tests for ABS-270 structured query resolution.

AC-1A.2: ZoneAttributeQuery resolves to the same fragment as the equivalent path lookup.
AC-1A.3: Unknown zone returns suggestions (not a match).
AC-1A.4: Unknown attribute raises ValueError listing accepted vocabulary.
AC-1A.5: Covered by running the existing tests/test_retrieval.py (no modification needed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import CitationLookupRequest, RetrievalService
from bylaw_retrieval.retrieval.schemas import (
    ScheduleRowQuery,
    ZoneAttributeQuery,
)
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


def _add_document(session, municipality: str, bylaw_name: str) -> Document:
    doc = Document(
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
    session.add(doc)
    session.flush()
    return doc


def _add_fragment(
    session,
    document_id: int,
    *,
    text: str,
    citation_path: str | None = None,
    reading_order_start: int = 1,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SECTION,
        citation_label=citation_path,
        citation_path=citation_path,
        parent_fragment_id=None,
        page_start=1,
        page_end=1,
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


# ---------------------------------------------------------------------------
# AC-1A.2: structured query returns same match as equivalent path lookup
# ---------------------------------------------------------------------------


def test_structured_query_returns_match_for_known_zone_attribute(tmp_path: Path):
    """ZoneAttributeQuery resolves to the same fragment as the path-string variant."""
    db_url = f"sqlite:///{tmp_path / 'sq-match.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _add_document(session, "Sampleton", "Zoning Bylaw")
        # The canonical path for zone_attribute queries is "{zone} > {attribute}"
        _add_fragment(
            session, doc.id,
            text="Maximum building height in HR-2 zone is 15 metres.",
            citation_path="HR-2 > max_height",
        )
        document_id = doc.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)

        # Establish the reference result via path-string lookup
        path_response = service.lookup_citation(
            CitationLookupRequest(
                citation_path="HR-2 > max_height",
                document_id=document_id,
            )
        )
        assert path_response.match is not None, "expected path-string lookup to hit"

        # Structured query must return the same fragment
        structured_response = service.lookup_citation(
            CitationLookupRequest(
                structured=ZoneAttributeQuery(zone="HR-2", attribute="max_height"),
                document_id=document_id,
            )
        )
        assert structured_response.match is not None, (
            "structured query should resolve to the same fragment as path lookup"
        )
        assert structured_response.match.citation_path == path_response.match.citation_path
        assert structured_response.match.fragment_id == path_response.match.fragment_id


# ---------------------------------------------------------------------------
# AC-1A.3: unknown zone returns suggestions (miss without exception)
# ---------------------------------------------------------------------------


def test_structured_query_returns_suggestions_on_unknown_zone(tmp_path: Path):
    """A ZoneAttributeQuery for a zone that isn't in the corpus returns
    match=None + at least 1 suggestion (the ABS-261 miss-without-exception contract).
    """
    db_url = f"sqlite:///{tmp_path / 'sq-unknown-zone.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _add_document(session, "Sampleton", "Zoning Bylaw")
        # Store a real HR-2 > max_height fragment so suggestions can surface it
        _add_fragment(
            session, doc.id,
            text="Maximum height in HR-2 zone.",
            citation_path="HR-2 > max_height",
        )
        document_id = doc.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.lookup_citation(
            CitationLookupRequest(
                structured=ZoneAttributeQuery(zone="XYZ", attribute="max_height"),
                document_id=document_id,
            )
        )
        assert response.match is None, "non-existent zone must not produce a match"
        assert len(response.suggestions) >= 1, (
            f"expected at least 1 suggestion, got: {response.suggestions}"
        )


# ---------------------------------------------------------------------------
# AC-1A.4: unknown attribute raises ValueError listing accepted vocabulary
# ---------------------------------------------------------------------------


def test_structured_query_rejects_unknown_attribute(tmp_path: Path):
    """Calling with an unknown attribute raises ValueError immediately,
    before any DB query, listing the accepted attribute vocabulary.
    """
    db_url = f"sqlite:///{tmp_path / 'sq-bad-attr.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _add_document(session, "Sampleton", "Zoning Bylaw")
        _add_fragment(
            session, doc.id,
            text="Some section text.",
            citation_path="HR-2 > max_height",
        )
        document_id = doc.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        with pytest.raises(ValueError) as exc_info:
            service.lookup_citation(
                CitationLookupRequest(
                    structured=ZoneAttributeQuery(zone="HR-2", attribute="nonsense"),
                    document_id=document_id,
                )
            )
        error_msg = str(exc_info.value)
        # Must name the bad attribute
        assert "nonsense" in error_msg
        # Must list accepted vocabulary
        assert "max_height" in error_msg


# ---------------------------------------------------------------------------
# ScheduleRowQuery: same resolution logic
# ---------------------------------------------------------------------------


def test_structured_schedule_row_query_resolves_match(tmp_path: Path):
    """ScheduleRowQuery resolves to the canonical '{schedule} > {row}' path."""
    db_url = f"sqlite:///{tmp_path / 'sq-schedule.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        doc = _add_document(session, "Sampleton", "Zoning Bylaw")
        _add_fragment(
            session, doc.id,
            text="Table 1A row HR-2 permitted uses.",
            citation_path="Table 1A > HR-2",
        )
        document_id = doc.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.lookup_citation(
            CitationLookupRequest(
                structured=ScheduleRowQuery(schedule="Table 1A", row="HR-2"),
                document_id=document_id,
            )
        )
        assert response.match is not None
        assert response.match.citation_path == "Table 1A > HR-2"
