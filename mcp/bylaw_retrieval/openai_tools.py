from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from bylaw_retrieval.retrieval import CitationLookupRequest, RetrievalRequest, RetrievalService


def build_openai_responses_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "list_documents",
            "description": (
                "List available bylaw documents before retrieval. "
                "Use this when the conversation has not yet selected the right municipality or bylaw."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "municipality": {"type": "string"},
                    "bylaw_name": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_document_outline",
            "description": (
                "Get the citation map and top-level structure for one bylaw document. "
                "Use this when the agent needs section names or citation ranges before issuing a narrower search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "integer"},
                    "max_fragments": {"type": "integer", "minimum": 1, "maximum": 500, "default": 250},
                    "include_text": {"type": "boolean", "default": False},
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "lookup_citation",
            "description": (
                "Retrieve the authoritative fragment for an exact citation path such as '4.2' or 'Schedule B > 3'. "
                "Use this when the user or agent already knows the citation."
            ),
            "parameters": {
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
            },
        },
        {
            "type": "function",
            "name": "search_bylaw_evidence",
            "description": (
                "Search bylaw source fragments using a natural-language question or a compact retrieval query. "
                "Use this to gather citation-grounded evidence about what rules may affect a built-form question. "
                "If the question references a specific address, parcel, intersection, named place, or coordinate, "
                "populate the 'location' argument rather than embedding the address in 'query' — the retrieval API "
                "will use it to spatially filter any geo datasets linked to matching fragments (e.g. height precincts)."
            ),
            "parameters": {
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
            },
        },
    ]


def build_openai_chat_completions_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in build_openai_responses_tool_specs()
    ]


def build_openai_tool_specs() -> list[dict[str, Any]]:
    """Backward-compatible alias for the Responses API tool shape."""
    return build_openai_responses_tool_specs()


@dataclass
class OpenAIToolExecutor:
    session: Session

    def execute(self, tool_name: str, arguments_json: str | dict[str, Any]) -> dict[str, Any]:
        args = arguments_json
        if isinstance(arguments_json, str):
            args = json.loads(arguments_json)
        service = RetrievalService(self.session)

        if tool_name == "list_documents":
            return {
                "documents": [
                    doc.model_dump(mode="json")
                    for doc in service.list_documents(
                        municipality=args.get("municipality"),
                        bylaw_name=args.get("bylaw_name"),
                        limit=args.get("limit", 50),
                    )
                ]
            }
        if tool_name == "get_document_outline":
            return service.get_document_outline(
                document_id=args["document_id"],
                max_fragments=args.get("max_fragments", 250),
                include_text=args.get("include_text", False),
            ).model_dump(mode="json")
        if tool_name == "lookup_citation":
            request = _validated(CitationLookupRequest, args)
            return service.lookup_citation(request).model_dump(mode="json")
        if tool_name == "search_bylaw_evidence":
            request = _validated(RetrievalRequest, args)
            return service.search(request).model_dump(mode="json")
        raise ValueError(f"Unsupported OpenAI retrieval tool: {tool_name}")


def _validated(model_cls, payload: dict[str, Any]):
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

