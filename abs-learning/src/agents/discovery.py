"""Discovery agent for Phase 2.

Given a starting URL for a municipality, crawls 1-2 levels into the site,
gathers candidate documents (PDFs, HTML pages), and uses Anthropic tool-use
to classify each candidate into a :class:`SourceDocument`. The output drops
straight into :func:`pipeline.run_learning_pipeline` without manual editing.

## Design choice — crawler + LLM classifier

We considered three approaches (see ABS-75):

* **Crawler + LLM classifier** *(chosen)*. Deterministic crawl enumerates
  candidates from the seed URL; the LLM only classifies. Keeps the call
  count predictable (3-6 calls / city), matches the windowing pattern in
  :mod:`agents.structure_analyst` and :mod:`agents.semantic_mapper`, and
  lets unit tests mock the network and the LLM independently.
* **LLM-driven targeted fetch.** Give the LLM ``fetch_url`` / ``list_links``
  tools and let it crawl. More adaptive to weird municipal site
  structures, but variable cost per run and requires an MCP-style tool
  harness inside the agent. Harder to unit-test.
* **Hybrid.** Cheap crawl + targeted-fetch passes for amendment / schedule
  gaps. Worth adding once we observe real-world misses; not justified
  before then.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
import tldextract

from manifest.models import Municipality, SourceDocument


logger = logging.getLogger(__name__)


# Use only the bundled PSL snapshot — no network fetch on first use. This
# keeps the agent usable in offline/CI contexts and makes unit tests
# deterministic. The snapshot is refreshed by upgrading the tldextract
# package, which is sufficient for our use case (relatively stable
# municipal-site domain layouts).
_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


# ---------------------------------------------------------------- public types
@dataclass
class CrawledCandidate:
    """A URL the crawler enumerated, with the hints the classifier needs."""

    url: str
    link_text: str
    discovered_on: str
    page_title: Optional[str] = None
    content_type: Optional[str] = None
    is_pdf: bool = False


# ---------------------------------------------------------------- LLM contract
CLASSIFIER_TOOL_NAME = "report_source_documents"
PARENT_RESOLVER_TOOL_NAME = "report_parent_links"

CANONICAL_DOCUMENT_TYPES: Tuple[str, ...] = (
    "bylaw",
    "schedule",
    "appendix",
    "map",
    "amendment",
    "policy",
)
CANONICAL_FORMATS: Tuple[str, ...] = ("pdf", "html", "xml", "docx")
CANONICAL_ACCESS_METHODS: Tuple[str, ...] = (
    "direct_download",
    "paginated_html",
    "portal",
    "api",
)


_CLASSIFIER_TOOL_SCHEMA: Dict[str, Any] = {
    "name": CLASSIFIER_TOOL_NAME,
    "description": (
        "Classify each candidate URL as a municipal source document or "
        "discard it. Skip a candidate by omitting it from the output."
    ),
    "input_schema": {
        "type": "object",
        "required": ["documents"],
        "properties": {
            "documents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "url",
                        "document_name",
                        "document_type",
                        "document_role",
                        "format",
                        "access_method",
                        "in_scope",
                        "confidence",
                    ],
                    "properties": {
                        "url": {"type": "string"},
                        "document_name": {"type": "string"},
                        "document_type": {
                            "type": "string",
                            "enum": list(CANONICAL_DOCUMENT_TYPES),
                        },
                        "document_role": {"type": "string"},
                        "format": {
                            "type": "string",
                            "enum": list(CANONICAL_FORMATS),
                        },
                        "access_method": {
                            "type": "string",
                            "enum": list(CANONICAL_ACCESS_METHODS),
                        },
                        "in_scope": {"type": "boolean"},
                        "is_primary_bylaw": {"type": "boolean"},
                        "suggested_parent": {"type": ["string", "null"]},
                        "amendment_series": {"type": ["string", "null"]},
                        "page_count_estimate": {"type": ["integer", "null"]},
                        "confidence": {"type": "number"},
                    },
                },
            }
        },
    },
}


_PARENT_RESOLVER_TOOL_SCHEMA: Dict[str, Any] = {
    "name": PARENT_RESOLVER_TOOL_NAME,
    "description": (
        "For each child document, report the document_name of its parent, "
        "or null if it has no parent in this set."
    ),
    "input_schema": {
        "type": "object",
        "required": ["links"],
        "properties": {
            "links": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["document_name", "parent_document"],
                    "properties": {
                        "document_name": {"type": "string"},
                        "parent_document": {"type": ["string", "null"]},
                    },
                },
            }
        },
    },
}


CLASSIFIER_SYSTEM_PROMPT = """You are a municipal-records analyst. You will be given a numbered list of
candidate documents discovered by crawling a municipality's website. Each entry
includes the URL, the link text it was found under, the page it was discovered
on, and (where available) a page title and HTTP content-type.

Classify each candidate that is plausibly a land-use bylaw, subdivision bylaw,
zoning amendment, design guideline, sign bylaw, schedule, appendix, or
amending bylaw. **Skip news posts, agendas, minutes, application forms, FAQ
pages, calendars, and contact pages by leaving them out of the response.**

For each kept candidate produce:

- document_name: a clean human-readable title (strip "PDF", file size,
  effective date suffixes; do NOT invent — use the link text or page title
  verbatim where possible).
- document_type: one of {bylaw, schedule, appendix, map, amendment, policy}.
  Land-use / zoning / subdivision / sign / design bylaws are "bylaw". Zoning
  schedules / maps embedded in or referenced by a bylaw are "schedule".
  Amending bylaws (e.g. "Bylaw No. 2023-04 — Amendment") are "amendment".
  Council policies and design guidelines are "policy".
- document_role: a short free-text role (e.g. "Primary land-use bylaw",
  "Subdivision bylaw", "Sign bylaw", "Heritage area amendment").
- format: pdf | html | xml | docx (infer from the URL extension or content-type).
- access_method: direct_download for downloadable PDFs; paginated_html for
  bylaws served as a series of HTML pages; portal if the URL points at a
  login-walled document portal; api for machine-readable feeds. When in
  doubt, prefer direct_download for .pdf URLs and paginated_html for HTML.
- in_scope: true for the primary land-use bylaw and its in-force companions
  (subdivision, sign, design guidelines); false for archival amendments
  unless they are explicitly the in-force consolidation.
- is_primary_bylaw: true ONLY for the candidate you believe is the
  municipality's primary land-use / zoning bylaw. At most one per request.
- suggested_parent: the document_name of the bylaw this candidate amends or
  is a schedule of, when obvious; null otherwise.
- amendment_series: the bylaw number (e.g. "Bylaw No. 2023-04") if
  identifiable in the link text or title; null otherwise.
- page_count_estimate: integer if stated in the link text (rare) or null.
- confidence: 0.0-1.0 — your confidence that the classification is correct.

Return ONLY valid JSON via the tool. No prose."""


PARENT_RESOLVER_SYSTEM_PROMPT = """You are linking child documents (amendments, schedules, appendices) to
their parent bylaw. Given a list of all documents this municipality publishes,
for each non-bylaw document report the document_name of the parent bylaw it
belongs to, or null if there is no clear parent in this set.

Rules:

- Schedules / appendices / maps almost always have a parent bylaw — pick the
  most specific one whose role they extend (e.g. zoning schedule → primary
  land-use bylaw; sign-bylaw schedule → sign bylaw).
- Amendments name their target bylaw in the title — link to that.
- Standalone bylaws (the primary land-use bylaw, the sign bylaw, the
  subdivision bylaw) have NO parent and should appear with
  parent_document: null.
- If you cannot link a candidate confidently, return parent_document: null.

Return ONLY valid JSON via the tool. No prose."""


# ---------------------------------------------------------------- HTML parsing
class _LinkExtractor(HTMLParser):
    """Pulls <a href> + visible text + <title> from an HTML body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Tuple[str, str]] = []
        self.title: Optional[str] = None
        self._in_title = False
        self._in_anchor = False
        self._current_href: Optional[str] = None
        self._current_text: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._in_anchor = True
                self._current_href = href
                self._current_text = []

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._in_anchor:
            text = " ".join("".join(self._current_text).split()).strip()
            if self._current_href:
                self.links.append((self._current_href, text))
            self._in_anchor = False
            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str):
        if self._in_title and self.title is None:
            stripped = data.strip()
            if stripped:
                self.title = stripped
        if self._in_anchor:
            self._current_text.append(data)


# ---------------------------------------------------------------- agent
@dataclass
class _ClassifiedCandidate:
    """Internal carrier between the LLM classifier and the final assembly."""

    url: str
    document_name: str
    document_type: str
    document_role: str
    format: str
    access_method: str
    in_scope: bool
    confidence: float
    is_primary_bylaw: bool = False
    suggested_parent: Optional[str] = None
    amendment_series: Optional[str] = None
    page_count_estimate: Optional[int] = None
    flags: List[str] = field(default_factory=list)


class DiscoveryAgent:
    """Crawl a municipal site and emit a ``List[SourceDocument]``."""

    DEFAULT_MAX_PAGES = 25
    DEFAULT_MAX_DEPTH = 2
    DEFAULT_BATCH_SIZE = 15
    DEFAULT_CONFIDENCE_FLOOR = 0.4

    def __init__(
        self,
        anthropic_client,
        http_client: Optional[httpx.Client] = None,
        model: str = "claude-sonnet-4-6",
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        batch_size: int = DEFAULT_BATCH_SIZE,
        confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
        allowed_hosts: Optional[Set[str]] = None,
    ) -> None:
        self.client = anthropic_client
        self._owned_http_client = http_client is None
        self.http = http_client or httpx.Client(
            follow_redirects=True,
            timeout=20.0,
            headers={
                "User-Agent": (
                    "ABS-DiscoveryAgent/0.1 (+https://github.com/"
                    "jordanlaforge15-del/agentic-bylaw-system)"
                )
            },
        )
        self.model = model
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.batch_size = batch_size
        self.confidence_floor = confidence_floor
        # When None, the crawler accepts any URL whose registrable domain
        # (eTLD+1) matches the seed's. When provided, the crawler accepts
        # only URLs whose exact host is in this set — use this when the
        # default eTLD+1 scope is too broad (e.g. seed on a shared platform
        # subdomain).
        self.allowed_hosts = allowed_hosts

    def close(self) -> None:
        if self._owned_http_client:
            self.http.close()

    def __enter__(self) -> "DiscoveryAgent":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------- public entrypoint
    def discover(
        self,
        municipality: Municipality,
        seed_url: str,
    ) -> List[SourceDocument]:
        """Crawl from ``seed_url`` and return classified source documents."""
        candidates = self._crawl(seed_url)
        if not candidates:
            logger.warning(
                "DiscoveryAgent: no candidates crawled from %s", seed_url
            )
            return []

        classified = self._classify(candidates, municipality)
        classified = self._resolve_parents(classified, municipality)
        return self._to_source_documents(classified)

    # --------------------------------------------------------- private: crawl
    def _crawl(self, seed_url: str) -> List[CrawledCandidate]:
        """BFS crawl from ``seed_url`` within the same registrable domain.

        Returns a deduplicated list of ``CrawledCandidate`` capped at
        ``self.max_pages``. PDFs are recorded as candidates but not
        recursed into; HTML pages have their links extracted and pushed
        onto the queue up to ``self.max_depth``.

        Scope: by default, accepts any URL whose registrable domain (eTLD+1)
        matches the seed's, so a seed on ``www.<city>.<tld>`` will follow
        links to ``cdn.<city>.<tld>`` where municipalities commonly serve
        their bylaw PDFs. Override with ``allowed_hosts`` for stricter scope.
        """
        seed_url = _normalize_url(seed_url)
        seed_host = _host_of(seed_url)
        seed_domain = _registrable_domain(seed_url)
        if not seed_host or not seed_domain:
            return []

        candidates: Dict[str, CrawledCandidate] = {}
        visited_pages: Set[str] = set()
        queue: List[Tuple[str, int, str, str]] = [(seed_url, 0, "", seed_url)]

        while queue and len(candidates) < self.max_pages:
            url, depth, link_text, discovered_on = queue.pop(0)
            url = _normalize_url(url)
            if not url:
                continue
            if not self._host_allowed(url, seed_domain):
                continue

            is_pdf = _looks_like_pdf(url)
            if is_pdf:
                if url in candidates:
                    continue
                # HEAD the PDF rather than GET so the classifier batch
                # payload doesn't balloon with binary data we never read.
                content_type = self._head_content_type(url)
                candidates[url] = CrawledCandidate(
                    url=url,
                    link_text=link_text,
                    discovered_on=discovered_on,
                    page_title=None,
                    content_type=content_type,
                    is_pdf=True,
                )
                continue

            if url in visited_pages:
                continue
            visited_pages.add(url)

            page_body, content_type = self._fetch_html(url)
            if page_body is None:
                # CMS-routed PDFs (Drupal /media/<id>, /node/<id>, etc.)
                # don't end in .pdf but the response after redirects is
                # application/pdf. Capture them as PDF candidates rather
                # than silently dropping. Keep the canonical URL (the
                # /media/<id> form), not the redirect target — that's
                # what the seed page actually links to.
                if (
                    content_type
                    and "application/pdf" in content_type.lower()
                    and url not in candidates
                ):
                    candidates[url] = CrawledCandidate(
                        url=url,
                        link_text=link_text,
                        discovered_on=discovered_on,
                        page_title=None,
                        content_type=content_type,
                        is_pdf=True,
                    )
                continue

            extractor = _LinkExtractor()
            try:
                extractor.feed(page_body)
            except Exception as exc:  # noqa: BLE001 — HTMLParser is permissive, raises rarely
                logger.debug("HTMLParser raised on %s: %s", url, exc)

            # Record the page itself when it's not the seed — bylaws are
            # sometimes published as paginated HTML rather than PDFs.
            if depth > 0 and url not in candidates:
                candidates[url] = CrawledCandidate(
                    url=url,
                    link_text=link_text,
                    discovered_on=discovered_on,
                    page_title=extractor.title,
                    content_type=content_type,
                    is_pdf=False,
                )

            if depth >= self.max_depth:
                continue

            for href, text in extractor.links:
                child = _resolve_href(url, href)
                if not child:
                    continue
                if not self._host_allowed(child, seed_domain):
                    continue
                if child in candidates:
                    continue
                if child in visited_pages:
                    continue
                queue.append((child, depth + 1, text, url))

        return list(candidates.values())

    def _host_allowed(self, url: str, seed_domain: str) -> bool:
        """Return True if ``url``'s host falls inside the crawl scope.

        With an explicit ``allowed_hosts`` set, match on exact hostname.
        Otherwise default to registrable-domain (eTLD+1) match against the
        seed's domain.
        """
        host = _host_of(url)
        if not host:
            return False
        if self.allowed_hosts is not None:
            return host in self.allowed_hosts
        return _registrable_domain(url) == seed_domain

    def _fetch_html(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        try:
            resp = self.http.get(url)
            resp.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Crawler GET %s failed: %s", url, exc)
            return None, None
        content_type = resp.headers.get("content-type")
        if content_type and "text/html" not in content_type.lower():
            return None, content_type
        # Cap the body to keep memory under control. Municipal indexes are
        # typically small; an absurd page suggests something is off.
        body = resp.text[:200_000]
        return body, content_type

    def _head_content_type(self, url: str) -> Optional[str]:
        try:
            resp = self.http.head(url)
            if resp.status_code >= 400:
                return None
            return resp.headers.get("content-type")
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Crawler HEAD %s failed: %s", url, exc)
            return None

    # ------------------------------------------------------ private: classify
    def _classify(
        self,
        candidates: List[CrawledCandidate],
        municipality: Municipality,
    ) -> List[_ClassifiedCandidate]:
        if not candidates:
            return []

        classified: Dict[str, _ClassifiedCandidate] = {}
        for batch in _chunked(candidates, self.batch_size):
            results = self._classify_batch(batch, municipality)
            for entry in results:
                url = entry.get("url")
                if not url:
                    continue
                conf = float(entry.get("confidence", 0.0))
                if conf < self.confidence_floor:
                    continue
                doc_type = entry.get("document_type")
                if doc_type not in CANONICAL_DOCUMENT_TYPES:
                    continue
                fmt = entry.get("format")
                if fmt not in CANONICAL_FORMATS:
                    continue
                access = entry.get("access_method")
                if access not in CANONICAL_ACCESS_METHODS:
                    continue
                name = (entry.get("document_name") or "").strip()
                if not name:
                    continue
                cc = _ClassifiedCandidate(
                    url=url,
                    document_name=name,
                    document_type=doc_type,
                    document_role=(entry.get("document_role") or "").strip()
                    or doc_type.title(),
                    format=fmt,
                    access_method=access,
                    in_scope=bool(entry.get("in_scope", False)),
                    confidence=conf,
                    is_primary_bylaw=bool(entry.get("is_primary_bylaw", False)),
                    suggested_parent=_clean_optional_str(
                        entry.get("suggested_parent")
                    ),
                    amendment_series=_clean_optional_str(
                        entry.get("amendment_series")
                    ),
                    page_count_estimate=_clean_optional_int(
                        entry.get("page_count_estimate")
                    ),
                )
                # Last write wins on duplicates: classifier may revisit a URL
                # if the same link appears across batches.
                classified[url] = cc

        return list(classified.values())

    def _classify_batch(
        self,
        batch: List[CrawledCandidate],
        municipality: Municipality,
    ) -> List[Dict[str, Any]]:
        user_text = _format_classifier_prompt(municipality, batch)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=CLASSIFIER_SYSTEM_PROMPT,
            tools=[_CLASSIFIER_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": CLASSIFIER_TOOL_NAME},
            messages=[{"role": "user", "content": user_text}],
        )
        payload = _extract_tool_input(response, CLASSIFIER_TOOL_NAME)
        return list(payload.get("documents") or [])

    # ------------------------------------------------ private: parent linking
    def _resolve_parents(
        self,
        classified: List[_ClassifiedCandidate],
        municipality: Municipality,
    ) -> List[_ClassifiedCandidate]:
        """Reconcile classifier-suggested parents into a clean graph."""
        if len(classified) < 2:
            return classified

        has_children = any(
            c.document_type in {"amendment", "schedule", "appendix", "map"}
            for c in classified
        )
        if not has_children:
            return classified

        names_by_url = {c.url: c.document_name for c in classified}
        user_text = _format_parent_prompt(municipality, classified)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=PARENT_RESOLVER_SYSTEM_PROMPT,
                tools=[_PARENT_RESOLVER_TOOL_SCHEMA],
                tool_choice={
                    "type": "tool",
                    "name": PARENT_RESOLVER_TOOL_NAME,
                },
                messages=[{"role": "user", "content": user_text}],
            )
            payload = _extract_tool_input(response, PARENT_RESOLVER_TOOL_NAME)
            links = payload.get("links") or []
        except Exception as exc:  # noqa: BLE001 — fall back to classifier hints
            logger.warning(
                "Parent resolver failed; falling back to per-batch hints: %s",
                exc,
            )
            return classified

        parent_by_name: Dict[str, Optional[str]] = {}
        valid_names = set(names_by_url.values())
        for entry in links:
            child = (entry.get("document_name") or "").strip()
            parent = entry.get("parent_document")
            parent = parent.strip() if isinstance(parent, str) else None
            if not child or child not in valid_names:
                continue
            if parent and parent not in valid_names:
                # Resolver hallucinated a parent — ignore.
                continue
            parent_by_name[child] = parent

        for c in classified:
            if c.document_name in parent_by_name:
                c.suggested_parent = parent_by_name[c.document_name]
            # If the resolver left a name out, keep the classifier's guess.
        return classified

    # ----------------------------------------------- private: assemble output
    def _to_source_documents(
        self,
        classified: List[_ClassifiedCandidate],
    ) -> List[SourceDocument]:
        if not classified:
            return []

        # Enforce at-most-one primary bylaw (document_type=bylaw, parent=None,
        # in_scope=True). Ties: highest confidence.
        primary_candidates = sorted(
            (
                c
                for c in classified
                if c.document_type == "bylaw"
                and (
                    c.is_primary_bylaw
                    or (c.suggested_parent is None and c.in_scope)
                )
            ),
            key=lambda c: c.confidence,
            reverse=True,
        )
        primary_url: Optional[str] = (
            primary_candidates[0].url if primary_candidates else None
        )

        sources: List[SourceDocument] = []
        for c in sorted(
            classified, key=lambda c: (-int(c.in_scope), -c.confidence)
        ):
            if c.url == primary_url:
                parent: Optional[str] = None
            else:
                parent = c.suggested_parent
            sources.append(
                SourceDocument(
                    document_name=c.document_name,
                    document_type=c.document_type,  # type: ignore[arg-type]
                    document_role=c.document_role,
                    parent_document=parent,
                    source_url=c.url,
                    format=c.format,  # type: ignore[arg-type]
                    access_method=c.access_method,  # type: ignore[arg-type]
                    page_count_estimate=c.page_count_estimate,
                    last_amended=None,
                    amendment_series=c.amendment_series,
                    in_scope=c.in_scope,
                )
            )
        return sources


# ---------------------------------------------------------- module-level helpers
def companions_from_sources(sources: Iterable[SourceDocument]) -> List[str]:
    """Return the in-scope companion bylaw names for a discovery result.

    The "primary" source is the in-scope ``document_type='bylaw'`` with
    ``parent_document=None``; every OTHER in-scope ``bylaw`` or ``policy``
    is a companion. This is the signal :class:`SemanticMapperAgent` needs
    to populate ``TaxonomyMap.companion_bylaws_required`` without
    re-crawling.
    """
    primary_name: Optional[str] = None
    for source in sources:
        if (
            source.document_type == "bylaw"
            and source.parent_document is None
            and source.in_scope
        ):
            primary_name = source.document_name
            break
    companions: List[str] = []
    seen: Set[str] = set()
    for source in sources:
        if not source.in_scope:
            continue
        if source.document_type not in {"bylaw", "policy"}:
            continue
        if source.document_name == primary_name:
            continue
        if source.document_name in seen:
            continue
        seen.add(source.document_name)
        companions.append(source.document_name)
    return companions


# ---------------------------------------------------------- private: utilities
def _normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""
    url, _ = urldefrag(url)
    return url


def _host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _registrable_domain(url: str) -> str:
    """Return the eTLD+1 of ``url`` (e.g. ``halifax.ca`` for both
    ``www.halifax.ca`` and ``cdn.halifax.ca``). Empty string on hosts
    with no public suffix (e.g. ``localhost``, raw IPs).
    """
    host = _host_of(url)
    if not host:
        return ""
    ext = _TLD_EXTRACT(host)
    if not ext.domain or not ext.suffix:
        return ""
    return f"{ext.domain}.{ext.suffix}"


def _looks_like_pdf(url: str) -> bool:
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return False
    return path.lower().endswith(".pdf")


def _resolve_href(base_url: str, href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith(("mailto:", "tel:", "javascript:")):
        return ""
    try:
        resolved = urljoin(base_url, href)
    except ValueError:
        return ""
    return _normalize_url(resolved)


def _chunked(items: List[CrawledCandidate], size: int):
    if size <= 0:
        size = 1
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _clean_optional_str(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _clean_optional_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_classifier_prompt(
    municipality: Municipality,
    batch: List[CrawledCandidate],
) -> str:
    lines: List[str] = [
        f"Municipality: {municipality.name} "
        f"({municipality.jurisdiction_code}, {municipality.province})",
        "",
        "Candidates:",
    ]
    for idx, c in enumerate(batch, start=1):
        lines.append(
            json.dumps(
                {
                    "index": idx,
                    "url": c.url,
                    "link_text": c.link_text,
                    "discovered_on": c.discovered_on,
                    "page_title": c.page_title,
                    "content_type": c.content_type,
                    "is_pdf": c.is_pdf,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def _format_parent_prompt(
    municipality: Municipality,
    classified: List[_ClassifiedCandidate],
) -> str:
    lines: List[str] = [
        f"Municipality: {municipality.name} "
        f"({municipality.jurisdiction_code})",
        "",
        "All documents in this municipality:",
    ]
    for idx, c in enumerate(classified, start=1):
        lines.append(
            json.dumps(
                {
                    "index": idx,
                    "document_name": c.document_name,
                    "document_type": c.document_type,
                    "document_role": c.document_role,
                    "in_scope": c.in_scope,
                    "is_primary_bylaw": c.is_primary_bylaw,
                    "amendment_series": c.amendment_series,
                    "classifier_suggested_parent": c.suggested_parent,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def _extract_tool_input(response, tool_name: str) -> Dict[str, Any]:
    for block in getattr(response, "content", []) or []:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == tool_name
        ):
            return dict(block.input or {})
    raise ValueError(
        f"DiscoveryAgent received no tool_use block for {tool_name!r}"
    )
