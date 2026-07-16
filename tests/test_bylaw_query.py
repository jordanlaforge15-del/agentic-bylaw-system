"""Unit tests for ABS-274 / Phase 4 — the intent-routed ``bylaw_query`` mega-tool.

Covers AC-4.2 through AC-4.6. ``bylaw_query`` is a *composer*: it declares an
``intent`` and dispatches to the Phase 2/3 thick tools (``get_zone_profile``,
``get_address_profile``) server-side, so these tests assert on the composed
response shape AND that the composition reuses the underlying implementations
rather than duplicating retrieval logic (FR-4.4 / AC-4.6).

Zone-scoped intents reuse the Regional-Centre seed fixture from
``test_get_zone_profile``; ``address_lookup`` reuses the spatially-seeded
fixture from ``test_get_address_profile``.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from bylaw_retrieval.retrieval import (
    BylawQueryResponse,
    ConformanceCheck,
    RetrievalService,
)
from layer1.db.session import session_scope
from test_get_address_profile import seeded_db  # noqa: F401 — pytest fixture
from test_get_zone_profile import _seed_regional_centre


# ---------------------------------------------------------------------------
# AC-4.2 — zone_feasibility composes the full zone profile (dimensions + uses)
# ---------------------------------------------------------------------------


def test_bylaw_query_zone_feasibility_composes_zone_and_uses(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'feasibility.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).bylaw_query(
            intent="zone_feasibility", zone="HR-2"
        )

    assert isinstance(response, BylawQueryResponse)
    assert response.intent == "zone_feasibility"
    assert response.unrecognized_intent is False
    assert response.address_profile is None

    profile = response.zone_profile
    assert profile is not None
    # Both sections must be populated from the single composition (AC-4.2).
    assert profile.dimensions is not None
    assert profile.dimensions.max_height_m == 25.0
    assert profile.uses is not None
    assert "multi-unit dwelling" in profile.uses.permitted
    # Citations mirror the source profile's.
    assert response.citations
    assert response.citations == profile.citations


# ---------------------------------------------------------------------------
# AC-4.3 — address_lookup returns exactly what get_address_profile would
# ---------------------------------------------------------------------------


def test_bylaw_query_address_lookup_returns_address_profile(seeded_db: str):  # noqa: F811
    address = "100 Robie Street"
    with session_scope(seeded_db) as session:
        service = RetrievalService(session)
        direct = service.get_address_profile(address)
        response = service.bylaw_query(intent="address_lookup", address=address)

    assert response.intent == "address_lookup"
    assert response.zone_profile is None
    assert response.address_profile is not None
    # Composition reuse (FR-4.4): the composed profile is byte-identical to
    # what the thin thick-tool returns directly.
    assert response.address_profile.model_dump() == direct.model_dump()
    assert response.address_profile.zone == "HR-2"
    assert response.citations == direct.citations


# ---------------------------------------------------------------------------
# AC-4.4 — dimensional_check flags a proposal that exceeds the zone maximum
# ---------------------------------------------------------------------------


def test_bylaw_query_dimensional_check_evaluates_proposed(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'dimensional.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).bylaw_query(
            intent="dimensional_check", zone="HR-2", proposed={"height_m": 80}
        )

    assert response.intent == "dimensional_check"
    assert response.zone_profile is not None
    check = response.conformance_check
    assert isinstance(check, ConformanceCheck)
    assert check.zone == "HR-2"

    by_attr = {r.attribute: r for r in check.results}
    assert "height_m" in by_attr
    height = by_attr["height_m"]
    assert height.status == "fail"
    assert height.limit == 25.0
    assert height.comparison == "max"
    assert check.overall == "fail"


def test_bylaw_query_dimensional_check_passes_when_within_limits(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'dimensional_ok.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).bylaw_query(
            intent="dimensional_check",
            zone="HR-2",
            proposed={"height_m": 20, "front_setback_m": 5},
        )

    check = response.conformance_check
    assert check is not None
    by_attr = {r.attribute: r.status for r in check.results}
    assert by_attr["height_m"] == "pass"  # 20 <= 25 max
    assert by_attr["front_setback_m"] == "pass"  # 5 >= 3.0 min
    assert check.overall == "pass"


def test_bylaw_query_dimensional_check_inconclusive_for_unmapped_attribute(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'dimensional_unmapped.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).bylaw_query(
            intent="dimensional_check", zone="HR-2", proposed={"colour": "blue"}
        )

    check = response.conformance_check
    assert check is not None
    assert check.results[0].status == "inconclusive"
    assert check.results[0].comparison == "unknown"
    assert check.overall == "inconclusive"


# ---------------------------------------------------------------------------
# AC-4.5 — an unknown intent returns suggestions, NOT an exception
# ---------------------------------------------------------------------------


def test_bylaw_query_unknown_intent_returns_suggestions(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'unknown.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).bylaw_query(intent="quantum_zoning")

    assert response.intent == "quantum_zoning"
    assert response.unrecognized_intent is True
    assert response.zone_profile is None
    assert response.address_profile is None
    assert response.conformance_check is None
    # FR-4.2: the thin tools lead the suggestion list.
    assert response.suggested_tools[:2] == ["search_bylaw_evidence", "lookup_citation"]


# ---------------------------------------------------------------------------
# AC-4.6 — composition-isolation: zone_feasibility calls get_zone_profile once
# ---------------------------------------------------------------------------


def test_bylaw_query_zone_feasibility_uses_get_zone_profile(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'isolation.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        with mock.patch.object(
            service, "get_zone_profile", wraps=service.get_zone_profile
        ) as spy:
            response = service.bylaw_query(intent="zone_feasibility", zone="HR-2")

    # Exactly one delegation to the Phase 2 implementation — no duplicate
    # inline composition that could drift from get_zone_profile (AC-4.6).
    spy.assert_called_once_with(zone="HR-2")
    assert response.zone_profile is not None


def test_bylaw_query_use_check_uses_get_zone_profile_with_uses_include(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'use_check.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        with mock.patch.object(
            service, "get_zone_profile", wraps=service.get_zone_profile
        ) as spy:
            response = service.bylaw_query(intent="use_check", zone="HR-2")

    spy.assert_called_once_with(zone="HR-2", include=["uses"])
    assert response.zone_profile is not None
    assert response.zone_profile.uses is not None


# ---------------------------------------------------------------------------
# Graceful degradation — a recognised intent missing its required slot
# ---------------------------------------------------------------------------


def test_bylaw_query_zone_intent_missing_zone_suggests_thin_tools(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'missing_zone.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).bylaw_query(intent="zone_feasibility")

    # Recognised intent, but no zone — degrade to suggestions, never crash.
    assert response.unrecognized_intent is False
    assert response.zone_profile is None
    assert response.suggested_tools
