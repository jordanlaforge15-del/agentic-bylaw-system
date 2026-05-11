"""Server-side advisories on RetrievalResponse.notes.

When the LLM caller embeds an address in the query string but doesn't
populate the 'location' field, the spatial datasets (zone, height,
FAR, heritage, bonus zoning) are silently skipped. The response's
'notes' field surfaces a corrective hint so the LLM can re-issue with
the right shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import (
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
)
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'notes.db'}"
    create_all(url)
    with session_scope(url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/fixture.pdf",
            file_hash="n" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            page_count=10,
        )
        session.add(document)
        session.flush()
        session.add(
            SourceFragment(
                document_id=document.id,
                fragment_type=FragmentType.SECTION,
                citation_label="109",
                citation_path="109",
                page_start=1,
                page_end=1,
                text="Maximum building height shall not exceed the maximum required.",
                parse_status=ParseStatus.PARSED,
                source_block_ids_json=[],
                metadata_json={},
            )
        )
    return url


def test_notes_warn_when_civic_address_in_query_but_no_location(db_url: str):
    """Direct repro of the 6321 Quinpool failure mode: address baked into
    query, no location slot. Server must surface the corrective hint."""
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="maximum building height at 6321 Quinpool Road",
                limit=5,
            )
        )
    assert response.notes
    note = response.notes[0]
    assert "6321 Quinpool Road" in note
    assert "location" in note.lower()
    assert "civic_number" in note
    assert "'6321'" in note
    assert "'Quinpool Road'" in note


def test_notes_warn_for_parcel_id_in_query(db_url: str):
    """PIDs are also worth catching — same shape, different field."""
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="zone for PID 00012345 please",
                limit=5,
            )
        )
    assert response.notes
    note = response.notes[0]
    assert "00012345" in note
    assert "parcel_id" in note


def test_no_notes_when_location_slot_supplied(db_url: str):
    """Caller populated the slot — nothing to warn about, even if the
    address text is also present in the query.

    Uses the ``geometry`` shape so resolution is a no-op; otherwise the
    test environment (no civic-address dataset, no Google geocoder) would
    correctly emit the geocoder-failure note and mask the property under
    test (the address-in-query advisory must NOT fire when a slot is
    present).
    """
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="maximum building height at 6321 Quinpool Road",
                location=LocationSlot(
                    geometry={
                        "type": "Point",
                        "coordinates": [-63.5980, 44.6488],
                    }
                ),
                limit=5,
            )
        )
    assert response.notes == []


def test_no_notes_when_query_has_no_address_pattern(db_url: str):
    """Generic queries (no address-shaped text) must not get false-positive
    advisories. Otherwise every result carries noise."""
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="maximum building height regulations",
                limit=5,
            )
        )
    assert response.notes == []


def test_notes_persist_when_no_text_matches_either(db_url: str):
    """Even if the response has zero matches, the address-in-query advisory
    should still appear so the LLM knows what to do differently."""
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="zzzx_no_match_token at 6321 Quinpool Road",
                limit=5,
            )
        )
    assert response.notes
    assert "6321 Quinpool Road" in response.notes[0]
