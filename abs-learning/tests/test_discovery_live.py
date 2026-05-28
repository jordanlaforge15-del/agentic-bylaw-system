"""Live municipal-site integration tests for :class:`agents.discovery.DiscoveryAgent`.

These tests run against real Canadian municipal websites and assert that specific
known bylaw documents are discovered.  They complement the lighter-weight
``test_discovery_integration.py`` tests (which just assert non-empty results) by
pinning *specific* document URLs and enforcing a per-site API-call budget.

Design goals
------------
* Catch the class of regressions found on 2026-05-23 (ABS-89, ABS-92, ABS-93):
  CDN-served PDF links dropped, CMS-routed /media/<id> links missed, BFS
  exhausted on global nav before reaching content-area bylaw links.
* Each site test caps its own Anthropic budget at ≤ 15 calls and reports
  clearly what was missing when discovery degrades:
  ``"Halifax discovery degraded — only N/M expected bylaws found, missing: [...]"``
* Tests skip (not fail) when the municipal site is unreachable so a municipal-
  site outage doesn't block the CI promotion gate.

Running
-------
Standalone (recommended for budgeting):

    pytest abs-learning/tests/test_discovery_live.py -m integration -v

Combined with other integration tests (shared 60-call session budget):

    pytest abs-learning/ -m integration -v
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
import pytest

from agents.discovery import DiscoveryAgent
from manifest.models import Municipality, SourceDocument


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Site 1: Halifax Plan Area  (Drupal/CMS — CDN-hosted PDFs via /media/<id>)
# ---------------------------------------------------------------------------

# The exact page used in the real-world run that surfaced ABS-89/92/93.
HALIFAX_PLAN_AREA_SEED = (
    "https://www.halifax.ca/about-halifax/regional-community-planning/"
    "community-plan-areas/halifax-plan-area"
)

# Drupal /media/<id> URL slugs that MUST appear in the discovered set.
# These are stable identifiers managed by HRM's CMS; they survive content
# edits as long as the document itself isn't replaced.  ABS-89 tracked the
# bug where cross-subdomain filtering silently dropped every cdn.halifax.ca
# PDF, and ABS-92 tracked the bug where /media/<id> links (no .pdf suffix)
# were dropped by the CMS-routing guard.
HALIFAX_EXPECTED_URL_SLUGS: list[str] = [
    "/media/93483",  # Mainland Land Use Bylaw (primary LUB)
]

# Minimum number of in-scope documents the agent must surface from this seed.
# The real page exposes 6+ candidates when the ABS-89/92/93 fixes are in place.
HALIFAX_MIN_IN_SCOPE = 6

# ---------------------------------------------------------------------------
# Site 2: City of Kelowna, BC  (different CMS — exercises non-Drupal DOM)
# ---------------------------------------------------------------------------

# Kelowna uses a Kentico-based site whose link structure differs from Halifax's
# Drupal /media/<id> pattern.  Exercising this site guards against crawling
# regressions that only affect non-Drupal municipal sites.
KELOWNA_ZONING_SEED = (
    "https://www.kelowna.ca/city-services/zoning-development/zoning/zoning-bylaw"
)

# Floor: at least one bylaw-type document must be classified.
KELOWNA_MIN_BYLAW_DOCS = 1

# ---------------------------------------------------------------------------
# Per-site Anthropic API call budget cap.
# ---------------------------------------------------------------------------

# DiscoveryAgent typically uses 2-5 calls per site (2 classifier batches + 1
# parent-resolver call).  15 gives generous headroom for retries and future
# batch-size changes without opening the door to runaway spending.
SITE_CALL_LIMIT = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _network_alive(url: str) -> bool:
    """Return True if the URL responds with a non-server-error status code."""
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.head(url)
        return resp.status_code < 500
    except (httpx.HTTPError, ValueError):
        return False


def _make_municipality(name: str, code: str, province: str) -> Municipality:
    return Municipality(
        name=name,
        jurisdiction_code=code,
        province=province,
        governing_body=f"{name} Council",
    )


def _source_urls(sources: list[SourceDocument]) -> list[str]:
    return [s.source_url for s in sources]


# ---------------------------------------------------------------------------
# T-LIVE-A  Halifax Plan Area (Drupal / CDN-hosted PDFs / CMS-routed links)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_live_a_halifax_plan_area_finds_mainland_lub(
    anthropic_client,
    api_call_budget,
):
    """Halifax Plan Area seed discovers the Mainland LUB (/media/93483).

    This is the exact seed URL used in the 2026-05-23 real-world run that
    triggered ABS-89, ABS-92, and ABS-93.  A failure here means one of those
    regressions is back, or HRM restructured their site.

    Asserts
    -------
    * All ``HALIFAX_EXPECTED_URL_SLUGS`` appear among the discovered URLs.
    * At least ``HALIFAX_MIN_IN_SCOPE`` in-scope documents are returned.
    * No more than ``SITE_CALL_LIMIT`` Anthropic API calls consumed.
    """
    if not _network_alive(HALIFAX_PLAN_AREA_SEED):
        pytest.skip(
            f"Halifax Plan Area unreachable — skipping live test: "
            f"{HALIFAX_PLAN_AREA_SEED}"
        )

    municipality = _make_municipality(
        "Halifax Regional Municipality", "HRM", "Nova Scotia"
    )

    calls_before = api_call_budget["count"]

    with DiscoveryAgent(
        anthropic_client,
        max_pages=25,
        max_depth=2,
        batch_size=15,
    ) as agent:
        sources = agent.discover(municipality, HALIFAX_PLAN_AREA_SEED)

    calls_used = api_call_budget["count"] - calls_before

    # --- budget assertion first so runaway costs surface before other failures
    assert calls_used <= SITE_CALL_LIMIT, (
        f"Halifax live discovery consumed {calls_used} Anthropic API calls "
        f"(per-site limit: {SITE_CALL_LIMIT}). "
        f"Consider reducing max_pages or batch_size."
    )

    assert sources, (
        "Halifax Plan Area discovery returned no sources. "
        "Either the seed page moved or the crawler is broken. "
        f"Seed: {HALIFAX_PLAN_AREA_SEED}"
    )

    discovered_urls = _source_urls(sources)

    # Check every expected slug appears in at least one discovered URL.
    missing_slugs = [
        slug for slug in HALIFAX_EXPECTED_URL_SLUGS
        if not any(slug in url for url in discovered_urls)
    ]
    found_count = len(HALIFAX_EXPECTED_URL_SLUGS) - len(missing_slugs)
    total_expected = len(HALIFAX_EXPECTED_URL_SLUGS)
    assert not missing_slugs, (
        f"Halifax discovery degraded — only {found_count}/{total_expected} "
        f"expected bylaws found, missing: {missing_slugs}. "
        f"This usually means ABS-89 (CDN link filtering) or ABS-92 "
        f"(CMS-routed /media/<id> links) has regressed. "
        f"All discovered URLs: {discovered_urls}"
    )

    in_scope = [s for s in sources if s.in_scope]
    assert len(in_scope) >= HALIFAX_MIN_IN_SCOPE, (
        f"Halifax Plan Area returned only {len(in_scope)} in-scope document(s) "
        f"(expected ≥ {HALIFAX_MIN_IN_SCOPE}). "
        f"This may indicate ABS-93 (BFS exhausted on nav chrome) has regressed. "
        f"All sources: "
        f"{[(s.document_name, s.document_type, s.in_scope) for s in sources]}"
    )

    logger.info(
        "T-LIVE-A Halifax Plan Area: %d total sources, %d in-scope, "
        "%d API calls (limit %d)",
        len(sources),
        len(in_scope),
        calls_used,
        SITE_CALL_LIMIT,
    )


# ---------------------------------------------------------------------------
# T-LIVE-B  City of Kelowna (Kentico CMS — different DOM structure)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_live_b_kelowna_zoning_bylaw_page_finds_bylaw_documents(
    anthropic_client,
    api_call_budget,
):
    """Kelowna zoning bylaw page discovers at least one classified bylaw document.

    Kelowna runs a Kentico CMS whose link structure differs from Halifax's
    Drupal /media/<id> pattern.  This test exercises the crawler on a non-Drupal
    municipal site and guards against regressions that affect sites without
    CMS-managed media paths.

    Asserts
    -------
    * At least ``KELOWNA_MIN_BYLAW_DOCS`` document(s) classified as
      ``document_type='bylaw'`` are returned.
    * No more than ``SITE_CALL_LIMIT`` Anthropic API calls consumed.
    """
    if not _network_alive(KELOWNA_ZONING_SEED):
        pytest.skip(
            f"Kelowna zoning page unreachable — skipping live test: "
            f"{KELOWNA_ZONING_SEED}"
        )

    municipality = _make_municipality(
        "City of Kelowna", "kelowna", "British Columbia"
    )

    calls_before = api_call_budget["count"]

    with DiscoveryAgent(
        anthropic_client,
        max_pages=20,
        max_depth=2,
        batch_size=15,
    ) as agent:
        sources = agent.discover(municipality, KELOWNA_ZONING_SEED)

    calls_used = api_call_budget["count"] - calls_before

    assert calls_used <= SITE_CALL_LIMIT, (
        f"Kelowna live discovery consumed {calls_used} Anthropic API calls "
        f"(per-site limit: {SITE_CALL_LIMIT}). "
        f"Consider reducing max_pages or batch_size."
    )

    assert sources, (
        "Kelowna zoning bylaw page discovery returned no sources. "
        "Either the seed page moved or the crawler failed to extract any links. "
        f"Seed: {KELOWNA_ZONING_SEED}"
    )

    bylaws = [s for s in sources if s.document_type == "bylaw"]
    assert len(bylaws) >= KELOWNA_MIN_BYLAW_DOCS, (
        f"Kelowna discovery found {len(bylaws)} bylaw document(s) "
        f"(expected ≥ {KELOWNA_MIN_BYLAW_DOCS}). "
        f"The classifier may have failed to recognise the zoning bylaw. "
        f"All sources: {[(s.document_name, s.document_type) for s in sources]}"
    )

    logger.info(
        "T-LIVE-B Kelowna zoning: %d total sources, %d bylaw doc(s), "
        "%d API calls (limit %d)",
        len(sources),
        len(bylaws),
        calls_used,
        SITE_CALL_LIMIT,
    )
