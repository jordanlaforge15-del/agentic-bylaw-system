"""Unit tests for :class:`agents.discovery.DiscoveryAgent`.

Everything below runs with a mocked Anthropic client and a fake httpx
transport — no network and no API calls. The integration test that
exercises a real municipal site lives in ``test_discovery_integration``.
"""
from __future__ import annotations

from typing import Dict, List
from unittest.mock import MagicMock

import httpx
import pytest

from agents.discovery import (
    CLASSIFIER_TOOL_NAME,
    PARENT_RESOLVER_TOOL_NAME,
    CrawledCandidate,
    DiscoveryAgent,
    companions_from_sources,
)
from manifest.models import Municipality, SourceDocument
from pipeline import _find_primary_source


# --------------------------------------------------------------------- helpers
def _municipality() -> Municipality:
    return Municipality(
        name="Town of Sampleton",
        jurisdiction_code="sampleton",
        province="Nova Scotia",
        governing_body="Council of Sampleton",
    )


def _tool_block(payload: dict, tool_name: str):
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = payload
    response = MagicMock()
    response.content = [block]
    return response


def _stub_classifier_response(documents: List[dict]):
    return _tool_block({"documents": documents}, CLASSIFIER_TOOL_NAME)


def _stub_parent_response(links: List[dict]):
    return _tool_block({"links": links}, PARENT_RESOLVER_TOOL_NAME)


def _seq_messages(*responses):
    """Return a side_effect that yields one mocked response per call."""
    iterator = iter(responses)

    def fake_create(*args, **kwargs):
        return next(iterator)

    return fake_create


# A tiny site: a bylaws index page linking to a primary land-use bylaw,
# a subdivision bylaw, an amendment, a zoning schedule PDF, and a news
# post the classifier should drop.
SAMPLETON_INDEX_HTML = """<!DOCTYPE html>
<html><head><title>Bylaws — Sampleton</title></head>
<body>
  <h1>Sampleton Bylaws</h1>
  <ul>
    <li><a href="/bylaws/lub.pdf">Land Use By-Law</a></li>
    <li><a href="/bylaws/subdivision.pdf">Subdivision By-Law</a></li>
    <li><a href="/bylaws/amend-2024-03.pdf">Bylaw No. 2024-03 — Heritage Amendment</a></li>
    <li><a href="/bylaws/zoning-map.pdf">Zoning Map (Schedule A)</a></li>
    <li><a href="/news/road-closure-2024">Road closure notice</a></li>
    <li><a href="https://offsite.example.org/skip">Off-site link</a></li>
    <li><a href="mailto:planning@sampleton.ca">Email planning</a></li>
  </ul>
</body></html>"""

# Linked from the index — we want the crawler to follow the HTML page,
# extract its title, but the classifier to drop it (news article).
NEWS_PAGE_HTML = """<html><head><title>Road closure notice</title></head>
<body><p>Main Street will close on Saturday.</p></body></html>"""


def _make_transport(routes: Dict[str, httpx.Response]) -> httpx.MockTransport:
    """Build an httpx mock that returns the configured response per URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        if key not in routes:
            return httpx.Response(404, text="not found")
        # Same response for HEAD or GET.
        return routes[key]

    return httpx.MockTransport(handler)


def _httpx_client(routes: Dict[str, httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=_make_transport(routes),
        follow_redirects=True,
        timeout=5.0,
    )


def _full_site_routes() -> Dict[str, httpx.Response]:
    return {
        "https://sampleton.example.com/bylaws": httpx.Response(
            200,
            text=SAMPLETON_INDEX_HTML,
            headers={"content-type": "text/html"},
        ),
        "https://sampleton.example.com/news/road-closure-2024": httpx.Response(
            200,
            text=NEWS_PAGE_HTML,
            headers={"content-type": "text/html"},
        ),
        "https://sampleton.example.com/bylaws/lub.pdf": httpx.Response(
            200, headers={"content-type": "application/pdf"}
        ),
        "https://sampleton.example.com/bylaws/subdivision.pdf": httpx.Response(
            200, headers={"content-type": "application/pdf"}
        ),
        "https://sampleton.example.com/bylaws/amend-2024-03.pdf": httpx.Response(
            200, headers={"content-type": "application/pdf"}
        ),
        "https://sampleton.example.com/bylaws/zoning-map.pdf": httpx.Response(
            200, headers={"content-type": "application/pdf"}
        ),
    }


# ----------------------------------------------------------------------- T-A
def test_crawler_enumerates_pdfs_dedups_and_stays_in_domain():
    """T-A: crawler picks up linked PDFs, drops off-site + mailto links."""
    client = _httpx_client(_full_site_routes())
    fake_llm = MagicMock()  # never called in this test
    agent = DiscoveryAgent(
        fake_llm, http_client=client, max_pages=20, max_depth=2
    )
    candidates = agent._crawl("https://sampleton.example.com/bylaws")
    urls = {c.url for c in candidates}

    assert "https://sampleton.example.com/bylaws/lub.pdf" in urls
    assert "https://sampleton.example.com/bylaws/subdivision.pdf" in urls
    assert "https://sampleton.example.com/bylaws/amend-2024-03.pdf" in urls
    assert "https://sampleton.example.com/bylaws/zoning-map.pdf" in urls
    # The news page is on the same domain — crawler keeps it, the
    # classifier drops it.
    assert (
        "https://sampleton.example.com/news/road-closure-2024" in urls
    ), "Same-domain HTML pages should be enumerated as candidates"
    # Off-domain (different registrable domain), mailto, and the seed page
    # itself should NOT be candidates. NB: sampleton.example.com (seed) and
    # offsite.example.org have different eTLD+1, so the eTLD+1 filter drops
    # the off-site link as intended.
    assert not any("offsite.example.org" in u for u in urls)
    assert not any(u.startswith("mailto:") for u in urls)
    assert "https://sampleton.example.com/bylaws" not in urls, (
        "Seed page is not itself a candidate"
    )

    # Each candidate carries discovery context the classifier prompt uses.
    by_url = {c.url: c for c in candidates}
    lub = by_url["https://sampleton.example.com/bylaws/lub.pdf"]
    assert lub.is_pdf is True
    assert lub.link_text == "Land Use By-Law"
    assert lub.discovered_on == "https://sampleton.example.com/bylaws"


# ----------------------------------------------------------------------- T-B
def test_crawler_obeys_max_pages_cap():
    """T-B: max_pages caps total candidates without infinite loops."""
    client = _httpx_client(_full_site_routes())
    agent = DiscoveryAgent(
        MagicMock(), http_client=client, max_pages=2, max_depth=2
    )
    candidates = agent._crawl("https://sampleton.example.com/bylaws")
    assert len(candidates) == 2


# ----------------------------------------------------------------------- T-C
def test_crawler_handles_invalid_seed():
    """T-C: empty / malformed seed returns no candidates rather than raising."""
    agent = DiscoveryAgent(
        MagicMock(),
        http_client=_httpx_client({}),
        max_pages=5,
        max_depth=1,
    )
    assert agent._crawl("") == []
    assert agent._crawl("not-a-url") == []


# ----------------------------------------------------------------------- ABS-89
# Cross-subdomain crawling: many municipal sites publish nav pages on
# www.<city>.<tld> and serve the actual bylaw PDFs from cdn.<city>.<tld>.
# The crawler must follow the link across the subdomain boundary by
# default, with an opt-out for callers that need stricter scope.

# Mimics the HRM site layout: seed page on www.<eTLD+1>, bylaw PDFs on
# cdn.<eTLD+1>. Uses RFC-2606 reserved example.com / example.org so the
# eTLD+1 (registrable domain) check returns deterministic values.
_HRM_LIKE_INDEX_HTML = """<!DOCTYPE html>
<html><head><title>Halifax Plan Area</title></head>
<body>
  <h1>Halifax Plan Area Bylaws</h1>
  <ul>
    <li><a href="https://cdn.example.com/docs/mainland-lub.pdf">Mainland LUB</a></li>
    <li><a href="https://cdn.example.com/docs/regional-mps.pdf">Regional MPS</a></li>
    <li><a href="https://offsite.example.org/bylaws/x.pdf">Off-site link</a></li>
  </ul>
</body></html>"""


def _hrm_like_routes():
    return {
        "https://www.example.com/plan-area": httpx.Response(
            200,
            text=_HRM_LIKE_INDEX_HTML,
            headers={"content-type": "text/html"},
        ),
        "https://cdn.example.com/docs/mainland-lub.pdf": httpx.Response(
            200, headers={"content-type": "application/pdf"}
        ),
        "https://cdn.example.com/docs/regional-mps.pdf": httpx.Response(
            200, headers={"content-type": "application/pdf"}
        ),
    }


def test_crawler_follows_cross_subdomain_same_registrable_domain():
    """ABS-89: seed on www.<host> follows links to cdn.<host>."""
    client = _httpx_client(_hrm_like_routes())
    agent = DiscoveryAgent(
        MagicMock(), http_client=client, max_pages=10, max_depth=2
    )
    candidates = agent._crawl("https://www.example.com/plan-area")
    urls = {c.url for c in candidates}

    assert "https://cdn.example.com/docs/mainland-lub.pdf" in urls, (
        "PDFs on a sibling subdomain (cdn.*) must be enumerated when the "
        "registrable domain matches the seed's"
    )
    assert "https://cdn.example.com/docs/regional-mps.pdf" in urls
    assert not any("example.org" in u for u in urls), (
        "Different registrable domain (example.org vs example.com) must "
        "still be filtered"
    )


def test_crawler_with_allowed_hosts_uses_explicit_set():
    """ABS-89: explicit allowed_hosts overrides the eTLD+1 default."""
    client = _httpx_client(_hrm_like_routes())
    agent = DiscoveryAgent(
        MagicMock(),
        http_client=client,
        max_pages=10,
        max_depth=2,
        allowed_hosts={"www.example.com"},
    )
    candidates = agent._crawl("https://www.example.com/plan-area")
    urls = {c.url for c in candidates}

    assert not any("cdn.example.com" in u for u in urls), (
        "With allowed_hosts={www.example.com}, the cdn.* subdomain is now "
        "out of scope and must be filtered"
    )


# --------------------------------------------------------------------- ABS-93
# Priority queue: bylaw-relevant links must be visited before global nav,
# even when the nav appears earlier in the DOM (as it does on every real
# municipal site).

def test_crawler_prioritises_content_links_over_nav_within_max_pages():
    """ABS-93: 30 nav links followed by 5 bylaw links in DOM order;
    with max_pages=10 the bylaws should still all be found."""
    nav_anchors = "\n".join(
        f'<a href="/nav/section-{i}">Nav section {i}</a>'
        for i in range(30)
    )
    content_anchors = "\n".join([
        '<a href="/media/93483">Land Use By-law</a>',
        '<a href="/media/93274">Municipal Planning Strategy</a>',
        '<a href="/docs/sched-A.pdf">Schedule A</a>',
        '<a href="/media/93227">ZM-1 Zoning</a>',
        '<a href="/media/93314">ZM-2 Schedules</a>',
    ])
    index_html = (
        "<!DOCTYPE html><html><body>"
        + nav_anchors
        + content_anchors
        + "</body></html>"
    )
    routes = {
        "https://www.example.com/index": httpx.Response(
            200, text=index_html, headers={"content-type": "text/html"}
        ),
    }
    # All nav pages exist as empty HTML so the crawler doesn't error
    # when (if) it visits them; bylaw PDFs return content-type pdf.
    for i in range(30):
        routes[f"https://www.example.com/nav/section-{i}"] = httpx.Response(
            200, text="<html><body>nav</body></html>",
            headers={"content-type": "text/html"},
        )
    for slug in ("93483", "93274", "93227", "93314"):
        routes[f"https://www.example.com/media/{slug}"] = httpx.Response(
            200, headers={"content-type": "application/pdf"}
        )
    routes["https://www.example.com/docs/sched-A.pdf"] = httpx.Response(
        200, headers={"content-type": "application/pdf"}
    )

    agent = DiscoveryAgent(
        MagicMock(),
        http_client=_httpx_client(routes),
        max_pages=10,
        max_depth=2,
    )
    candidates = agent._crawl("https://www.example.com/index")
    urls = {c.url for c in candidates}

    # All five bylaw-relevant links should be in the candidate set,
    # even though they appear AFTER 30 nav links in DOM order.
    bylaw_urls = {
        "https://www.example.com/media/93483",
        "https://www.example.com/media/93274",
        "https://www.example.com/media/93227",
        "https://www.example.com/media/93314",
        "https://www.example.com/docs/sched-A.pdf",
    }
    missing = bylaw_urls - urls
    assert not missing, (
        f"Priority queue should have surfaced bylaw links within "
        f"max_pages=10, but these were missed: {sorted(missing)}. "
        f"Candidates collected: {sorted(urls)}"
    )


def test_looks_like_content_link_helper():
    """ABS-93: content-likely detection covers PDFs, CMS routes, and
    bylaw-keyword link text."""
    from agents.discovery import _looks_like_content_link

    # By URL pattern.
    assert _looks_like_content_link("https://x.test/media/123", "")
    assert _looks_like_content_link("https://x.test/node/45", "")
    assert _looks_like_content_link("https://x.test/files/9", "")
    assert _looks_like_content_link("https://x.test/foo/bar.pdf", "")
    # By link text.
    assert _looks_like_content_link("https://x.test/x", "Land Use By-law")
    assert _looks_like_content_link("https://x.test/y", "Schedule A")
    assert _looks_like_content_link("https://x.test/z", "Zoning Map")
    assert _looks_like_content_link("https://x.test/a", "MPS Amendment")
    # Negative cases.
    assert not _looks_like_content_link("https://x.test/about", "About us")
    assert not _looks_like_content_link("https://x.test/contact", "Contact")
    assert not _looks_like_content_link("https://x.test/", "")


# --------------------------------------------------------------------- ABS-92
# CMS-routed PDF links: many municipal sites (Halifax uses Drupal's /media/X)
# serve PDFs through URLs that don't end in .pdf. The crawler must recognize
# the post-redirect content-type and treat them as PDF candidates.

def test_crawler_captures_cms_routed_pdf_when_content_type_is_pdf():
    """ABS-92: a /media/<id>-style URL that resolves to application/pdf
    becomes a PDF candidate, not a silently-dropped HTML fetch."""
    index_html = """<!DOCTYPE html>
<html><body>
  <a href="/media/93483">Halifax Mainland Land Use By-law</a>
</body></html>"""
    routes = {
        "https://www.example.com/plan-area": httpx.Response(
            200, text=index_html, headers={"content-type": "text/html"}
        ),
        # CMS route returns PDF content directly (httpx mock has no redirect
        # support, but the effect on the crawler is the same: GET returns
        # application/pdf, _fetch_html returns (None, "application/pdf")).
        "https://www.example.com/media/93483": httpx.Response(
            200, headers={"content-type": "application/pdf"}
        ),
    }
    client = _httpx_client(routes)
    agent = DiscoveryAgent(
        MagicMock(), http_client=client, max_pages=10, max_depth=2
    )
    candidates = agent._crawl("https://www.example.com/plan-area")

    media = [c for c in candidates if c.url.endswith("/media/93483")]
    assert len(media) == 1, (
        f"Expected /media/93483 to be enumerated, got: {[c.url for c in candidates]}"
    )
    assert media[0].is_pdf is True, (
        f"CMS-routed PDF must carry is_pdf=True, got is_pdf={media[0].is_pdf}"
    )
    assert media[0].link_text == "Halifax Mainland Land Use By-law"
    assert media[0].content_type == "application/pdf"


def test_crawler_non_pdf_non_html_response_still_skipped():
    """ABS-92 regression: only application/pdf gets the new treatment.
    A non-HTML non-PDF response (e.g. image/png) must still be dropped."""
    index_html = """<html><body>
      <a href="/asset/foo.png">image</a>
    </body></html>"""
    routes = {
        "https://www.example.com/index": httpx.Response(
            200, text=index_html, headers={"content-type": "text/html"}
        ),
        "https://www.example.com/asset/foo.png": httpx.Response(
            200, headers={"content-type": "image/png"}
        ),
    }
    agent = DiscoveryAgent(
        MagicMock(), http_client=_httpx_client(routes), max_pages=10, max_depth=2
    )
    candidates = agent._crawl("https://www.example.com/index")
    assert not any(c.url.endswith("/asset/foo.png") for c in candidates)


def test_registrable_domain_helper():
    """ABS-89: _registrable_domain returns eTLD+1, empty for invalid hosts."""
    from agents.discovery import _registrable_domain

    assert _registrable_domain("https://www.halifax.ca/x") == "halifax.ca"
    assert _registrable_domain("https://cdn.halifax.ca/y") == "halifax.ca"
    assert _registrable_domain("https://halifax.ca/z") == "halifax.ca"
    assert _registrable_domain("https://example.org/q") == "example.org"
    assert _registrable_domain("https://example.org") != _registrable_domain(
        "https://example.com"
    )
    # Degenerate cases — no public suffix, no host.
    assert _registrable_domain("") == ""
    assert _registrable_domain("not-a-url") == ""


# ----------------------------------------------------------------------- T-D
def test_classifier_filters_low_confidence_and_invalid_enums():
    """T-D: confidence floor + canonical enums gate the classifier output."""
    fake_llm = MagicMock()
    fake_llm.messages.create.return_value = _stub_classifier_response(
        [
            {
                "url": "https://sampleton.example.com/bylaws/lub.pdf",
                "document_name": "Land Use By-Law",
                "document_type": "bylaw",
                "document_role": "Primary land-use bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "is_primary_bylaw": True,
                "confidence": 0.95,
            },
            # Dropped: below confidence floor.
            {
                "url": "https://sampleton.example.com/maybe.pdf",
                "document_name": "Maybe Relevant",
                "document_type": "bylaw",
                "document_role": "?",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "confidence": 0.10,
            },
            # Dropped: invalid document_type enum.
            {
                "url": "https://sampleton.example.com/garbage.pdf",
                "document_name": "Garbage",
                "document_type": "minutes",  # not a canonical type
                "document_role": "?",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": False,
                "confidence": 0.9,
            },
            # Dropped: empty name.
            {
                "url": "https://sampleton.example.com/empty.pdf",
                "document_name": "   ",
                "document_type": "bylaw",
                "document_role": "?",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "confidence": 0.9,
            },
        ]
    )
    agent = DiscoveryAgent(fake_llm, http_client=_httpx_client({}))
    classified = agent._classify(
        [
            CrawledCandidate(
                url="https://sampleton.example.com/bylaws/lub.pdf",
                link_text="Land Use By-Law",
                discovered_on="https://sampleton.example.com/bylaws",
                is_pdf=True,
            ),
            CrawledCandidate(
                url="https://sampleton.example.com/maybe.pdf",
                link_text="Maybe Relevant",
                discovered_on="https://sampleton.example.com/bylaws",
                is_pdf=True,
            ),
            CrawledCandidate(
                url="https://sampleton.example.com/garbage.pdf",
                link_text="Garbage",
                discovered_on="https://sampleton.example.com/bylaws",
                is_pdf=True,
            ),
            CrawledCandidate(
                url="https://sampleton.example.com/empty.pdf",
                link_text="",
                discovered_on="https://sampleton.example.com/bylaws",
                is_pdf=True,
            ),
        ],
        _municipality(),
    )
    assert len(classified) == 1
    assert classified[0].url.endswith("/lub.pdf")
    assert classified[0].is_primary_bylaw is True

    # Confirm we routed through forced tool-use, like the other agents.
    fake_llm.messages.create.assert_called_once()
    kwargs = fake_llm.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {
        "type": "tool",
        "name": CLASSIFIER_TOOL_NAME,
    }


# ----------------------------------------------------------------------- T-E
def test_classifier_batches_when_exceeds_batch_size():
    """T-E: classifier is called once per batch; results are merged on URL."""
    candidates = [
        CrawledCandidate(
            url=f"https://sampleton.example.com/doc-{i}.pdf",
            link_text=f"Doc {i}",
            discovered_on="https://sampleton.example.com/bylaws",
            is_pdf=True,
        )
        for i in range(5)
    ]
    batch_one = _stub_classifier_response(
        [
            {
                "url": f"https://sampleton.example.com/doc-{i}.pdf",
                "document_name": f"Doc {i}",
                "document_type": "bylaw",
                "document_role": "Bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "confidence": 0.9,
            }
            for i in (0, 1)
        ]
    )
    batch_two = _stub_classifier_response(
        [
            {
                "url": f"https://sampleton.example.com/doc-{i}.pdf",
                "document_name": f"Doc {i}",
                "document_type": "bylaw",
                "document_role": "Bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "confidence": 0.9,
            }
            for i in (2, 3)
        ]
    )
    batch_three = _stub_classifier_response(
        [
            {
                "url": "https://sampleton.example.com/doc-4.pdf",
                "document_name": "Doc 4",
                "document_type": "bylaw",
                "document_role": "Bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "confidence": 0.9,
            }
        ]
    )
    fake_llm = MagicMock()
    fake_llm.messages.create.side_effect = _seq_messages(
        batch_one, batch_two, batch_three
    )
    agent = DiscoveryAgent(
        fake_llm, http_client=_httpx_client({}), batch_size=2
    )
    classified = agent._classify(candidates, _municipality())
    assert {c.url for c in classified} == {
        f"https://sampleton.example.com/doc-{i}.pdf" for i in range(5)
    }
    assert fake_llm.messages.create.call_count == 3


# ----------------------------------------------------------------------- T-F
def test_parent_resolver_links_amendments_and_schedules():
    """T-F: parent resolver overrides classifier hints with the global view."""
    classify_call = _stub_classifier_response(
        [
            {
                "url": "https://sampleton.example.com/bylaws/lub.pdf",
                "document_name": "Land Use By-Law",
                "document_type": "bylaw",
                "document_role": "Primary land-use bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "is_primary_bylaw": True,
                "confidence": 0.95,
            },
            {
                "url": "https://sampleton.example.com/bylaws/subdivision.pdf",
                "document_name": "Subdivision By-Law",
                "document_type": "bylaw",
                "document_role": "Subdivision bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "confidence": 0.9,
            },
            {
                "url": "https://sampleton.example.com/bylaws/amend-2024-03.pdf",
                "document_name": "Bylaw 2024-03 Heritage Amendment",
                "document_type": "amendment",
                "document_role": "Heritage amendment",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": False,
                "amendment_series": "Bylaw No. 2024-03",
                # Classifier was wrong — it suggested the subdivision bylaw.
                "suggested_parent": "Subdivision By-Law",
                "confidence": 0.85,
            },
            {
                "url": "https://sampleton.example.com/bylaws/zoning-map.pdf",
                "document_name": "Zoning Map Schedule A",
                "document_type": "schedule",
                "document_role": "Zoning map",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "suggested_parent": None,
                "confidence": 0.9,
            },
        ]
    )
    parent_call = _stub_parent_response(
        [
            {
                "document_name": "Land Use By-Law",
                "parent_document": None,
            },
            {
                "document_name": "Subdivision By-Law",
                "parent_document": None,
            },
            {
                "document_name": "Bylaw 2024-03 Heritage Amendment",
                "parent_document": "Land Use By-Law",
            },
            {
                "document_name": "Zoning Map Schedule A",
                "parent_document": "Land Use By-Law",
            },
        ]
    )
    fake_llm = MagicMock()
    fake_llm.messages.create.side_effect = _seq_messages(
        classify_call, parent_call
    )

    candidates = [
        CrawledCandidate(
            url="https://sampleton.example.com/bylaws/lub.pdf",
            link_text="Land Use By-Law",
            discovered_on="https://sampleton.example.com/bylaws",
            is_pdf=True,
        ),
        CrawledCandidate(
            url="https://sampleton.example.com/bylaws/subdivision.pdf",
            link_text="Subdivision By-Law",
            discovered_on="https://sampleton.example.com/bylaws",
            is_pdf=True,
        ),
        CrawledCandidate(
            url="https://sampleton.example.com/bylaws/amend-2024-03.pdf",
            link_text="Bylaw No. 2024-03 — Heritage Amendment",
            discovered_on="https://sampleton.example.com/bylaws",
            is_pdf=True,
        ),
        CrawledCandidate(
            url="https://sampleton.example.com/bylaws/zoning-map.pdf",
            link_text="Zoning Map (Schedule A)",
            discovered_on="https://sampleton.example.com/bylaws",
            is_pdf=True,
        ),
    ]
    agent = DiscoveryAgent(
        fake_llm, http_client=_httpx_client({}), batch_size=10
    )
    classified = agent._classify(candidates, _municipality())
    classified = agent._resolve_parents(classified, _municipality())
    by_name = {c.document_name: c for c in classified}

    assert by_name["Land Use By-Law"].suggested_parent is None
    assert by_name["Subdivision By-Law"].suggested_parent is None
    # The resolver corrected the classifier's wrong parent guess.
    assert (
        by_name["Bylaw 2024-03 Heritage Amendment"].suggested_parent
        == "Land Use By-Law"
    )
    # The classifier didn't pick a parent for the schedule; the resolver did.
    assert by_name["Zoning Map Schedule A"].suggested_parent == "Land Use By-Law"


# ----------------------------------------------------------------------- T-G
def test_parent_resolver_ignores_hallucinated_parents():
    """T-G: resolver naming a non-existent parent is ignored, not blindly trusted."""
    classify_call = _stub_classifier_response(
        [
            {
                "url": "https://sampleton.example.com/bylaws/lub.pdf",
                "document_name": "Land Use By-Law",
                "document_type": "bylaw",
                "document_role": "Primary land-use bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "is_primary_bylaw": True,
                "confidence": 0.95,
            },
            {
                "url": "https://sampleton.example.com/bylaws/sched-a.pdf",
                "document_name": "Schedule A",
                "document_type": "schedule",
                "document_role": "Zoning map schedule",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "suggested_parent": "Land Use By-Law",
                "confidence": 0.9,
            },
        ]
    )
    parent_call = _stub_parent_response(
        [
            # The resolver invents a bylaw that isn't in the candidate set.
            {
                "document_name": "Schedule A",
                "parent_document": "Phantom Bylaw 1999",
            },
        ]
    )
    fake_llm = MagicMock()
    fake_llm.messages.create.side_effect = _seq_messages(
        classify_call, parent_call
    )
    agent = DiscoveryAgent(fake_llm, http_client=_httpx_client({}))
    classified = agent._classify(
        [
            CrawledCandidate(
                url="https://sampleton.example.com/bylaws/lub.pdf",
                link_text="LUB",
                discovered_on="https://sampleton.example.com/bylaws",
                is_pdf=True,
            ),
            CrawledCandidate(
                url="https://sampleton.example.com/bylaws/sched-a.pdf",
                link_text="Schedule A",
                discovered_on="https://sampleton.example.com/bylaws",
                is_pdf=True,
            ),
        ],
        _municipality(),
    )
    classified = agent._resolve_parents(classified, _municipality())
    by_name = {c.document_name: c for c in classified}
    # Hallucinated parent dropped; we keep the classifier's guess instead.
    assert by_name["Schedule A"].suggested_parent == "Land Use By-Law"


# ----------------------------------------------------------------------- T-H
def test_to_source_documents_picks_one_primary_by_confidence():
    """T-H: among multiple bylaw candidates, the highest-confidence wins."""
    fake_llm = MagicMock()
    # Two unparented in-scope bylaws — both could be the primary by structure.
    fake_llm.messages.create.side_effect = _seq_messages(
        _stub_classifier_response(
            [
                {
                    "url": "https://sampleton.example.com/bylaws/lub.pdf",
                    "document_name": "Land Use By-Law",
                    "document_type": "bylaw",
                    "document_role": "Primary land-use bylaw",
                    "format": "pdf",
                    "access_method": "direct_download",
                    "in_scope": True,
                    "is_primary_bylaw": True,
                    "confidence": 0.95,
                },
                {
                    "url": "https://sampleton.example.com/bylaws/subdivision.pdf",
                    "document_name": "Subdivision By-Law",
                    "document_type": "bylaw",
                    "document_role": "Subdivision bylaw",
                    "format": "pdf",
                    "access_method": "direct_download",
                    "in_scope": True,
                    "is_primary_bylaw": False,
                    "confidence": 0.85,
                },
            ]
        ),
    )
    agent = DiscoveryAgent(fake_llm, http_client=_httpx_client({}))
    classified = agent._classify(
        [
            CrawledCandidate(
                url="https://sampleton.example.com/bylaws/lub.pdf",
                link_text="LUB",
                discovered_on="https://sampleton.example.com/bylaws",
                is_pdf=True,
            ),
            CrawledCandidate(
                url="https://sampleton.example.com/bylaws/subdivision.pdf",
                link_text="Subdivision",
                discovered_on="https://sampleton.example.com/bylaws",
                is_pdf=True,
            ),
        ],
        _municipality(),
    )
    sources = agent._to_source_documents(classified)
    primary = _find_primary_source(sources)
    assert primary is not None
    # is_primary_bylaw=True + higher confidence both point at LUB.
    assert primary.document_name == "Land Use By-Law"

    # The subdivision bylaw remains in-scope but is NOT promoted to primary —
    # parent_document stays None (it's still a top-level bylaw) yet the
    # primary lookup tie-breaks on confidence + is_primary_bylaw.
    subdivision = next(s for s in sources if "Subdivision" in s.document_name)
    assert subdivision.in_scope is True


# ----------------------------------------------------------------------- T-I
def test_companions_from_sources_picks_in_scope_non_primary_bylaws():
    """T-I: helper feeds SemanticMapper companion_bylaws_required."""
    sources = [
        SourceDocument(
            document_name="Land Use By-Law",
            document_type="bylaw",
            document_role="Primary",
            parent_document=None,
            source_url="https://sampleton.example.com/bylaws/lub.pdf",
            format="pdf",
            access_method="direct_download",
            page_count_estimate=None,
            last_amended=None,
            amendment_series=None,
            in_scope=True,
        ),
        SourceDocument(
            document_name="Subdivision By-Law",
            document_type="bylaw",
            document_role="Subdivision",
            parent_document=None,
            source_url="https://sampleton.example.com/bylaws/subdivision.pdf",
            format="pdf",
            access_method="direct_download",
            page_count_estimate=None,
            last_amended=None,
            amendment_series=None,
            in_scope=True,
        ),
        SourceDocument(
            document_name="Sign By-Law",
            document_type="bylaw",
            document_role="Sign",
            parent_document=None,
            source_url="https://sampleton.example.com/bylaws/sign.pdf",
            format="pdf",
            access_method="direct_download",
            page_count_estimate=None,
            last_amended=None,
            amendment_series=None,
            in_scope=True,
        ),
        SourceDocument(
            document_name="Heritage Design Guidelines",
            document_type="policy",
            document_role="Heritage policy",
            parent_document=None,
            source_url="https://sampleton.example.com/bylaws/heritage.pdf",
            format="pdf",
            access_method="direct_download",
            page_count_estimate=None,
            last_amended=None,
            amendment_series=None,
            in_scope=True,
        ),
        # Out of scope — should not appear in companions.
        SourceDocument(
            document_name="Bylaw 2024-03 Heritage Amendment",
            document_type="amendment",
            document_role="Amendment",
            parent_document="Land Use By-Law",
            source_url="https://sampleton.example.com/bylaws/amend-2024-03.pdf",
            format="pdf",
            access_method="direct_download",
            page_count_estimate=None,
            last_amended=None,
            amendment_series="2024-03",
            in_scope=False,
        ),
        # In-scope schedule — schedules are not companion bylaws.
        SourceDocument(
            document_name="Zoning Map Schedule A",
            document_type="schedule",
            document_role="Zoning map",
            parent_document="Land Use By-Law",
            source_url="https://sampleton.example.com/bylaws/zoning-map.pdf",
            format="pdf",
            access_method="direct_download",
            page_count_estimate=None,
            last_amended=None,
            amendment_series=None,
            in_scope=True,
        ),
    ]
    companions = companions_from_sources(sources)
    assert companions == [
        "Subdivision By-Law",
        "Sign By-Law",
        "Heritage Design Guidelines",
    ]


# ----------------------------------------------------------------------- T-J
def test_discover_end_to_end_with_mocks():
    """T-J: full discover() against a mocked site + LLM produces a manifest-ready list."""
    routes = _full_site_routes()
    client = _httpx_client(routes)
    classify_call = _stub_classifier_response(
        [
            {
                "url": "https://sampleton.example.com/bylaws/lub.pdf",
                "document_name": "Land Use By-Law",
                "document_type": "bylaw",
                "document_role": "Primary land-use bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "is_primary_bylaw": True,
                "confidence": 0.95,
            },
            {
                "url": "https://sampleton.example.com/bylaws/subdivision.pdf",
                "document_name": "Subdivision By-Law",
                "document_type": "bylaw",
                "document_role": "Subdivision bylaw",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "is_primary_bylaw": False,
                "confidence": 0.9,
            },
            {
                "url": "https://sampleton.example.com/bylaws/amend-2024-03.pdf",
                "document_name": "Bylaw 2024-03 Heritage Amendment",
                "document_type": "amendment",
                "document_role": "Heritage amendment",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": False,
                "amendment_series": "Bylaw No. 2024-03",
                "confidence": 0.85,
            },
            {
                "url": "https://sampleton.example.com/bylaws/zoning-map.pdf",
                "document_name": "Zoning Map Schedule A",
                "document_type": "schedule",
                "document_role": "Zoning map",
                "format": "pdf",
                "access_method": "direct_download",
                "in_scope": True,
                "confidence": 0.9,
            },
            # The classifier correctly drops the news page (no entry).
        ]
    )
    parent_call = _stub_parent_response(
        [
            {"document_name": "Land Use By-Law", "parent_document": None},
            {"document_name": "Subdivision By-Law", "parent_document": None},
            {
                "document_name": "Bylaw 2024-03 Heritage Amendment",
                "parent_document": "Land Use By-Law",
            },
            {
                "document_name": "Zoning Map Schedule A",
                "parent_document": "Land Use By-Law",
            },
        ]
    )
    fake_llm = MagicMock()
    fake_llm.messages.create.side_effect = _seq_messages(
        classify_call, parent_call
    )

    agent = DiscoveryAgent(
        fake_llm, http_client=client, max_pages=20, max_depth=2, batch_size=10
    )
    sources = agent.discover(
        _municipality(), "https://sampleton.example.com/bylaws"
    )

    # Acceptance #1: primary bylaw is identifiable.
    primary = _find_primary_source(sources)
    assert primary is not None
    assert primary.document_name == "Land Use By-Law"
    assert primary.document_type == "bylaw"
    assert primary.parent_document is None
    assert primary.in_scope is True

    # Acceptance #1: at least one companion bylaw.
    companions = companions_from_sources(sources)
    assert "Subdivision By-Law" in companions

    # Acceptance #3: result composes cleanly with run_learning_pipeline —
    # _find_primary_source returns a SourceDocument the pipeline can run on.
    # Output round-trips through the SourceDocument schema (already enforced
    # by Pydantic on construction); also confirm the news page was dropped.
    assert all(
        "news/road-closure-2024" not in s.source_url for s in sources
    ), "News article should have been dropped by the classifier"


# ----------------------------------------------------------------------- T-K
def test_classifier_raises_when_no_tool_use_block():
    """T-K: empty response from the LLM surfaces a clear error."""
    fake_llm = MagicMock()
    empty = MagicMock()
    empty.content = []  # no tool_use block
    fake_llm.messages.create.return_value = empty
    agent = DiscoveryAgent(fake_llm, http_client=_httpx_client({}))
    with pytest.raises(ValueError, match="no tool_use block"):
        agent._classify(
            [
                CrawledCandidate(
                    url="https://sampleton.example.com/bylaws/lub.pdf",
                    link_text="LUB",
                    discovered_on="https://sampleton.example.com/bylaws",
                    is_pdf=True,
                )
            ],
            _municipality(),
        )
