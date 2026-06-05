"""Unit tests for ABS-271: tunable result-set size on search_bylaw_evidence.

Covers AC-1B.1 through AC-1B.5.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalService
from layer1.db.base import SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


def _add_document(session, municipality="Sampleton", bylaw_name="Zoning Bylaw"):
    from layer1.db.base import Document

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


def _add_fragment(session, document_id: int, *, text: str, citation_path: str, page_start: int, reading_order_start: int):
    frag = SourceFragment(
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
    session.add(frag)
    return frag


def _seed_fragments(db_url: str, count: int = 20) -> tuple[int, int]:
    """Seed ``count`` fragments all containing the token 'setback'.

    Returns (document_id, fragment_count_seeded).
    """
    create_all(db_url)
    with session_scope(db_url) as session:
        doc = _add_document(session)
        for i in range(count):
            _add_fragment(
                session,
                doc.id,
                text=f"Section {i + 1}: setback requirement applies in zone R-{i + 1}.",
                citation_path=f"{i + 1}",
                page_start=i + 1,
                reading_order_start=i + 1,
            )
        return doc.id, count


# ---------------------------------------------------------------------------
# AC-1B.1 — Default preserves current behaviour (limit=5 → 5 results)
# ---------------------------------------------------------------------------

def test_search_bylaw_evidence_limit_default_preserves_current_behavior(tmp_path: Path):
    """Call without ``limit`` returns exactly the default number of results.

    Snapshot: default is 5 (FR-1B.3).  We seed more than 5 matching
    fragments so this would return more than 5 if the default were
    absent or higher.
    """
    db_url = f"sqlite:///{tmp_path / 'default.db'}"
    doc_id, _n = _seed_fragments(db_url, count=20)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        request = RetrievalRequest(
            query="setback",
            document_id=doc_id,
            # No explicit limit — rely on the default.
        )
        assert request.limit == 5, f"default limit should be 5, got {request.limit}"
        response = service.search(request)

    # Must return exactly the default (5) results even though 20 match.
    assert len(response.matches) == 5, (
        f"expected default of 5 matches, got {len(response.matches)}"
    )


# ---------------------------------------------------------------------------
# AC-1B.2 — limit caps the result set
# ---------------------------------------------------------------------------

def test_search_bylaw_evidence_limit_caps_results(tmp_path: Path):
    """limit=15 returns at most 15; limit=3 returns at most 3."""
    db_url = f"sqlite:///{tmp_path / 'cap.db'}"
    doc_id, _ = _seed_fragments(db_url, count=20)

    with session_scope(db_url) as session:
        service = RetrievalService(session)

        r15 = service.search(RetrievalRequest(query="setback", document_id=doc_id, limit=15))
        assert len(r15.matches) <= 15, "limit=15 must return at most 15 results"
        assert len(r15.matches) == 15, "with 20 matching fragments, limit=15 should be saturated"

        r3 = service.search(RetrievalRequest(query="setback", document_id=doc_id, limit=3))
        assert len(r3.matches) <= 3, "limit=3 must return at most 3 results"
        assert len(r3.matches) == 3, "with 20 matching fragments, limit=3 should be saturated"


# ---------------------------------------------------------------------------
# AC-1B.3 — Ranking is unchanged by limit
# ---------------------------------------------------------------------------

def test_search_bylaw_evidence_limit_preserves_ranking(tmp_path: Path):
    """The first 5 results of limit=15 equal the 5 results of limit=5 in order.

    FR-1B.2: adding ``limit`` must NOT change result ordering.
    """
    db_url = f"sqlite:///{tmp_path / 'rank.db'}"
    doc_id, _ = _seed_fragments(db_url, count=20)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        r5 = service.search(RetrievalRequest(query="setback", document_id=doc_id, limit=5))
        r15 = service.search(RetrievalRequest(query="setback", document_id=doc_id, limit=15))

    ids_5 = [m.fragment_id for m in r5.matches]
    ids_15_prefix = [m.fragment_id for m in r15.matches[:5]]

    assert ids_5 == ids_15_prefix, (
        f"Top-5 ordering must be identical regardless of limit.\n"
        f"  limit=5  IDs: {ids_5}\n"
        f"  limit=15 prefix: {ids_15_prefix}"
    )


# ---------------------------------------------------------------------------
# AC-1B.4 — Out-of-range values raise ValidationError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_limit", [0, -1, 51])
def test_search_bylaw_evidence_limit_validation(bad_limit: int):
    """limit=0, limit=-1, limit=51 must raise pydantic ValidationError.

    FR-1B.1: server-side validation is 1 <= limit <= 50.
    """
    with pytest.raises(ValidationError):
        RetrievalRequest(query="anything", limit=bad_limit)


# ---------------------------------------------------------------------------
# AC-1B.5 — MCP tool schema documents limit with min/max constraints
# ---------------------------------------------------------------------------

def test_search_bylaw_evidence_mcp_schema_has_limit_constraints():
    """The Pydantic schema for RetrievalRequest must expose limit with
    min=1 and max=50 so MCP and OpenAI tool-spec consumers see
    the correct constraints.
    """
    schema = RetrievalRequest.model_json_schema()
    props = schema.get("properties", {})
    assert "limit" in props, "RetrievalRequest schema must include 'limit' property"
    limit_schema = props["limit"]
    assert limit_schema.get("minimum") == 1 or limit_schema.get("exclusiveMinimum") is not None, (
        "limit schema must declare a minimum of 1"
    )
    assert limit_schema.get("maximum") == 50 or limit_schema.get("exclusiveMaximum") is not None, (
        "limit schema must declare a maximum of 50"
    )
