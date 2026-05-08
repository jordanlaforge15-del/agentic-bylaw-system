"""Bylaw-retrieval tool definitions for the chat backend.

Each tool here mirrors a method on ``RetrievalService``. The tool
descriptions and JSON schemas are copied verbatim from
``mcp/bylaw_retrieval/server.py`` and ``mcp/bylaw_retrieval/openai_tools.py``
so that the LLM sees the same wording it has been trained against in
this codebase. Updating one and not the other is a regression — keep
the strings in sync.

Why we don't import from the MCP openai_tools module: that module
emits OpenAI-style tool specs and bakes in JSON-string argument
parsing. Our gateway already speaks unified ``ToolDefinition`` /
``ToolUseBlock`` types, so we just need the wording.

Each handler:
- Accepts the LLM's ``tool_use.input`` dict.
- Translates it into the appropriate Pydantic request model via
  ``model_validate`` so malformed inputs raise ValidationError early.
- Calls the synchronous ``RetrievalService`` method directly.
- Returns ``json.dumps(response.model_dump(mode="json"))`` so the LLM
  receives structured JSON rather than a stringified Python dict.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from advisor.llm import ToolDefinition
from advisor.llm.tool_loop import ToolHandler
from bylaw_retrieval.retrieval import (
    CitationLookupRequest,
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
)


# Description copied verbatim from ``mcp/bylaw_retrieval/openai_tools.py``
# (list_documents). Kept here rather than importing because the MCP
# module's specs are OpenAI-shape, not Anthropic-shape.
_DESC_LIST_DOCUMENTS = (
    "List available bylaw documents before retrieval. "
    "Use this when the conversation has not yet selected the right municipality or bylaw."
)

_DESC_GET_DOCUMENT_OUTLINE = (
    "Get the citation map and top-level structure for one bylaw document. "
    "Use this when the agent needs section names or citation ranges before issuing a narrower search."
)

_DESC_LOOKUP_CITATION = (
    "Retrieve the authoritative fragment for an exact citation path such as '4.2' or 'Schedule B > 3'. "
    "Use this when the user or agent already knows the citation."
)

# This is the long form copied from ``server.py:search_bylaw_evidence``
# (the docstring with the location-slot guidance the LLM has been
# trained against in this codebase). DO NOT shorten this; the wording
# is what coaxes the model to populate ``location`` correctly.
_DESC_SEARCH_BYLAW_EVIDENCE = (
    "Search for citation-grounded bylaw evidence.\n\n"
    "Use this when translating a user question into a citation-grounded "
    "retrieval request across one or more bylaws.\n\n"
    "====================================================================\n"
    "CRITICAL: ADDRESSES MUST GO IN THE 'location' FIELD, NOT IN 'query'.\n"
    "====================================================================\n\n"
    "If the user mentions ANY of:\n"
    "  - a street address (\"6321 Quinpool Road\", \"5648 Bilby Street\")\n"
    "  - a parcel id (\"PID 00012345\")\n"
    "  - an intersection (\"corner of Spring Garden and Queen\")\n"
    "  - a named place (\"Halifax Citadel\", \"Public Gardens\")\n\n"
    "you MUST populate the structured ``location`` argument. Embedding the "
    "address only in ``query`` produces TEXT-ONLY matches and silently "
    "skips the spatial datasets (zone, height precinct, FAR, heritage "
    "district, bonus zoning, shadow impact) — exactly the data needed to "
    "answer most planning questions about a specific property.\n\n"
    "Example call for \"what's the max height at 6321 Quinpool Road\":\n\n"
    "    search_bylaw_evidence(\n"
    "        query=\"maximum building height\",\n"
    "        location={\n"
    "            \"civic_number\": \"6321\",\n"
    "            \"street\": \"Quinpool Road\"\n"
    "        }\n"
    "    )\n\n"
    "Other ``location`` shapes:\n"
    "  - civic_number + street (+ optional unit) for street addresses\n"
    "  - parcel_id when known\n"
    "  - named_place for landmarks\n"
    "  - intersection_streets: list of 2+ street names\n"
    "  - geometry: caller-supplied GeoJSON Point/Polygon in EPSG:4326\n\n"
    "--------------------------------------------------------------------\n"
    "Reading the response:\n\n"
    "Each match's ``linked_datasets[*].location_confidence`` reports the "
    "geocoder's confidence in the address-to-coordinate step (0..1). "
    "Values below ~0.85 mean the geocoder fell back to "
    "RANGE_INTERPOLATED or GEOMETRIC_CENTER quality. When you see a "
    "low-confidence value, qualify your answer accordingly — the "
    "spatial match may have hit a neighbouring precinct rather than "
    "the actual property.\n\n"
    "The response's top-level ``notes`` array carries server-side "
    "advisories. If you see a note saying the address should have been "
    "in the 'location' field, RE-ISSUE the call with the slot populated "
    "— do not just ignore it."
)


# JSON Schemas — copied verbatim from
# ``mcp/bylaw_retrieval/openai_tools.py``. These define the shape the
# LLM must emit. Keep additionalProperties=False so the model can't
# pass typo'd field names that would silently no-op on the request
# model.

_SCHEMA_LIST_DOCUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "municipality": {"type": "string"},
        "bylaw_name": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    },
    "additionalProperties": False,
}

_SCHEMA_GET_DOCUMENT_OUTLINE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_id": {"type": "integer"},
        "max_fragments": {"type": "integer", "minimum": 1, "maximum": 500, "default": 250},
        "include_text": {"type": "boolean", "default": False},
    },
    "required": ["document_id"],
    "additionalProperties": False,
}

_SCHEMA_LOOKUP_CITATION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "citation_path": {"type": "string"},
        "document_id": {"type": "integer"},
        "include_context": {"type": "boolean", "default": True},
        "include_cross_references": {"type": "boolean", "default": True},
        "include_tables": {"type": "boolean", "default": True},
    },
    "required": ["citation_path"],
    "additionalProperties": False,
}

_SCHEMA_SEARCH_BYLAW_EVIDENCE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "document_id": {"type": "integer"},
        "municipality": {"type": "string"},
        "bylaw_name": {"type": "string"},
        "citation_path_prefix": {"type": "string"},
        "page": {"type": "integer", "minimum": 1},
        "page_start": {"type": "integer", "minimum": 1},
        "page_end": {"type": "integer", "minimum": 1},
        "location": {
            "type": "object",
            "description": (
                "Structured location slot. Set the fields you have; leave the rest null. "
                "For street addresses use civic_number + street (and optional unit). "
                "For parcel ids use parcel_id. For landmarks use named_place. "
                "For intersections supply two or more street names in intersection_streets. "
                "If you already have a GeoJSON point or polygon (EPSG:4326), pass it as 'geometry' "
                "to skip geocoding entirely."
            ),
            "properties": {
                "civic_number": {"type": "string"},
                "street": {"type": "string"},
                "unit": {"type": "string"},
                "parcel_id": {"type": "string"},
                "named_place": {"type": "string"},
                "intersection_streets": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "geometry": {
                    "type": "object",
                    "description": "GeoJSON Point or Polygon in EPSG:4326.",
                },
            },
            "additionalProperties": False,
        },
        "include_context": {"type": "boolean", "default": True},
        "include_cross_references": {"type": "boolean", "default": True},
        "include_tables": {"type": "boolean", "default": True},
        "include_datasets": {"type": "boolean", "default": True},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8},
    },
    "required": ["query"],
    "additionalProperties": False,
}


# A simple factory protocol for tests: ``service`` is callable so tests
# can inject a single live RetrievalService bound to a sqlite session,
# while production injects a callable that opens a fresh session per
# invocation. Either works.
ServiceFactory = Callable[[], RetrievalService]


def build_bylaw_tools(
    retrieval_service: RetrievalService | ServiceFactory,
) -> tuple[list[ToolDefinition], dict[str, ToolHandler]]:
    """Build tool definitions and async handlers bound to a retrieval service.

    ``retrieval_service`` may be either:
    - a ``RetrievalService`` instance (handlers will reuse it directly),
    - or a zero-arg callable returning a ``RetrievalService`` (lets the
      caller open a fresh DB session per request without leaking it
      across tool calls).

    The returned tuple is the standard shape consumed by
    ``ChatSession`` and ``run_tool_loop``: a list of ``ToolDefinition``
    plus a name -> async handler dict.
    """

    @contextmanager
    def _resolve_cm() -> Iterator[RetrievalService]:
        # The factory may yield three shapes:
        #   * a bare RetrievalService — yield it as-is (test fixtures
        #     manage their own session lifecycle).
        #   * a callable returning a service — call and yield. Same
        #     test path, just lazy.
        #   * a callable returning a context manager — production's
        #     ``session_scope``-backed factory. Enter the cm, yield
        #     the service, and exit through the ``with`` block on the
        #     way out so the underlying SQLAlchemy session is closed
        #     and any open transaction is committed/rolled back.
        # The third case is the load-bearing one: the previous
        # implementation called ``__enter__()`` and discarded the cm,
        # which leaked one DB connection per tool call and left
        # transactions ``idle in transaction`` until Postgres killed
        # them via ``idle_in_transaction_session_timeout``.
        if callable(retrieval_service):
            result = retrieval_service()
            if hasattr(result, "__enter__"):
                with result as service:
                    yield service
                return
            yield result
            return
        yield retrieval_service

    async def list_documents_handler(payload: dict[str, Any]) -> str:
        # ``list_documents`` takes plain kwargs rather than a request
        # model, so we pluck the supported fields explicitly. Unknown
        # fields are silently ignored — the JSON Schema above is what
        # constrains the LLM, not our parsing here.
        with _resolve_cm() as service:
            documents = service.list_documents(
                municipality=payload.get("municipality"),
                bylaw_name=payload.get("bylaw_name"),
                limit=payload.get("limit", 50),
            )
            body = {
                "documents": [doc.model_dump(mode="json") for doc in documents]
            }
            return json.dumps(body)

    async def get_document_outline_handler(payload: dict[str, Any]) -> str:
        with _resolve_cm() as service:
            outline = service.get_document_outline(
                document_id=payload["document_id"],
                max_fragments=payload.get("max_fragments", 250),
                include_text=payload.get("include_text", False),
            )
            return json.dumps(outline.model_dump(mode="json"))

    async def lookup_citation_handler(payload: dict[str, Any]) -> str:
        # model_validate will raise ValidationError on missing required
        # fields; the tool loop catches that and surfaces it to the LLM
        # as a tool_result error so it can self-correct.
        request = CitationLookupRequest.model_validate(payload)
        with _resolve_cm() as service:
            match = service.lookup_citation(request)
            return json.dumps(match.model_dump(mode="json"))

    async def search_bylaw_evidence_handler(payload: dict[str, Any]) -> str:
        # Mirror the MCP server's location-slot handling: a missing
        # location stays None; a present-but-empty dict produces an
        # empty LocationSlot (still useful — it disables spatial
        # filtering rather than crashing).
        location_payload = payload.get("location")
        location = (
            LocationSlot.model_validate(location_payload)
            if location_payload is not None
            else None
        )
        request = RetrievalRequest(
            query=payload["query"],
            document_id=payload.get("document_id"),
            municipality=payload.get("municipality"),
            bylaw_name=payload.get("bylaw_name"),
            citation_path_prefix=payload.get("citation_path_prefix"),
            page=payload.get("page"),
            page_start=payload.get("page_start"),
            page_end=payload.get("page_end"),
            location=location,
            include_context=payload.get("include_context", True),
            include_cross_references=payload.get("include_cross_references", True),
            include_tables=payload.get("include_tables", True),
            include_datasets=payload.get("include_datasets", True),
            limit=payload.get("limit", 8),
        )
        with _resolve_cm() as service:
            response = service.search(request)
            return json.dumps(response.model_dump(mode="json"))

    tool_defs = [
        ToolDefinition(
            name="list_documents",
            description=_DESC_LIST_DOCUMENTS,
            input_schema=_SCHEMA_LIST_DOCUMENTS,
        ),
        ToolDefinition(
            name="get_document_outline",
            description=_DESC_GET_DOCUMENT_OUTLINE,
            input_schema=_SCHEMA_GET_DOCUMENT_OUTLINE,
        ),
        ToolDefinition(
            name="lookup_citation",
            description=_DESC_LOOKUP_CITATION,
            input_schema=_SCHEMA_LOOKUP_CITATION,
        ),
        ToolDefinition(
            name="search_bylaw_evidence",
            description=_DESC_SEARCH_BYLAW_EVIDENCE,
            input_schema=_SCHEMA_SEARCH_BYLAW_EVIDENCE,
        ),
    ]

    handlers: dict[str, ToolHandler] = {
        "list_documents": list_documents_handler,
        "get_document_outline": get_document_outline_handler,
        "lookup_citation": lookup_citation_handler,
        "search_bylaw_evidence": search_bylaw_evidence_handler,
    }

    return tool_defs, handlers
