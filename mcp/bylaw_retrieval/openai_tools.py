from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from bylaw_retrieval.retrieval import (
    ATTRIBUTE_VOCABULARY,
    CitationLookupRequest,
    RetrievalRequest,
    RetrievalService,
)


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
                "Use this when the user or agent already knows the citation.\n\n"
                "Provide exactly one of 'citation_path' or 'structured'. "
                "Use 'structured' with kind='zone_attribute' to look up a zone's attribute rule "
                "without guessing the canonical path format."
            ),
            "parameters": {
                "type": "object",
                "description": (
                    "Provide exactly one of 'citation_path' or 'structured'."
                ),
                "properties": {
                    "citation_path": {
                        "type": "string",
                        "description": (
                            "Exact citation path. Mutually exclusive with 'structured'."
                        ),
                    },
                    "structured": {
                        "description": (
                            "Structured query. Mutually exclusive with 'citation_path'. "
                            "Set 'kind' to 'zone_attribute' or 'schedule_row'."
                        ),
                        "oneOf": [
                            {
                                "type": "object",
                                "required": ["kind", "zone", "attribute"],
                                "properties": {
                                    "kind": {"type": "string", "const": "zone_attribute"},
                                    "zone": {"type": "string"},
                                    "attribute": {
                                        "type": "string",
                                        "enum": sorted(ATTRIBUTE_VOCABULARY),
                                    },
                                },
                                "additionalProperties": False,
                            },
                            {
                                "type": "object",
                                "required": ["kind", "schedule", "row"],
                                "properties": {
                                    "kind": {"type": "string", "const": "schedule_row"},
                                    "schedule": {"type": "string"},
                                    "row": {"type": "string"},
                                },
                                "additionalProperties": False,
                            },
                        ],
                    },
                    "document_id": {"type": "integer"},
                    "include_context": {"type": "boolean", "default": False},
                    "include_cross_references": {"type": "boolean", "default": False},
                    "include_tables": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "search_bylaw_evidence",
            "description": (
                "Search bylaw source fragments using a natural-language question or a "
                "compact retrieval query. Use this to gather citation-grounded evidence "
                "about what rules may affect a built-form question. "
                "CRITICAL: if the question references ANY address, parcel, intersection, "
                "named place, or coordinate (e.g. '6321 Quinpool Road', 'PID 00012345', "
                "'Halifax Citadel'), you MUST populate the structured 'location' argument. "
                "Embedding the address only in 'query' produces text-only matches and "
                "silently skips the spatial datasets (zone, height precinct, FAR, "
                "heritage, bonus zoning) needed for property-specific answers. "
                "Example for '6321 Quinpool Road': set query='maximum building height' "
                "and location={civic_number: '6321', street: 'Quinpool Road'}. "
                "If the response's 'notes' array contains a warning that 'location' was "
                "missing, re-issue the call with the slot set."
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
                    "include_context": {"type": "boolean", "default": False},
                    "include_cross_references": {"type": "boolean", "default": False},
                    "include_tables": {"type": "boolean", "default": False},
                    "include_datasets": {"type": "boolean", "default": False},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 5,
                        "description": (
                            "Maximum fragments to return. Default 5; bump to 15 when the "
                            "question covers multiple dimensions (e.g. zone feasibility); cap is 50."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_address_profile",
            "description": (
                "Use this at the start of a case-bound conversation when the user "
                "mentions an address, parcel, or named place. Returns the zone, "
                "overlay precincts, heritage status, and citations in one call. "
                "Saves multiple lookups. The 'address' argument is free text in the "
                "same shape the search_bylaw_evidence 'location' slot accepts. If the "
                "address can't be resolved, the response carries 'unresolvable': true "
                "with empty citations rather than an error. Read "
                "'resolution_quality' before stating the zone — anything below "
                "'rooftop' means the point was estimated and may sit on a "
                "neighbouring parcel. 'civic_address_status': 'not_found' means the "
                "civic number does not exist in the municipality's own data: there is "
                "no zone, do not geocode it, and offer "
                "'valid_civic_number_ranges' / 'suggested_civic_numbers' instead. "
                "'governing_bylaw_status': 'not_held' means the zone belongs to a "
                "by-law that is NOT in this corpus — 'governing_bylaw' names it. "
                "The zone code may be stated but carries no citation, and NO "
                "standard behind it (uses, height, setbacks, floor area) is "
                "available: name that by-law and say it must be consulted "
                "directly, never substitute a figure from another one. Each "
                "entry in 'overlays' carries the same pair for itself — a "
                "parcel with a held zone can still sit under a height or FAR "
                "precinct from a by-law we lack, and "
                "'governing_bylaw_held': false is the same hard stop for "
                "that overlay: never read its standard out of the equivalent "
                "schedule in a by-law we do hold."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                },
                "required": ["address"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "bylaw_query",
            "description": (
                "Use this when the user has stated both a location AND a "
                "specific intent (feasibility, use permission, dimensional "
                "check). Saves multiple round-trips by composing the full "
                "answer server-side. For exploratory questions where you "
                "don't yet know the intent, use the thin tools. 'intent' is "
                "one of zone_feasibility, address_lookup, use_check, "
                "dimensional_check. Pass 'zone' for zone-scoped intents, "
                "'address' for address_lookup, and 'proposed' (e.g. "
                "{\"height_m\": 80}) for dimensional_check. An unrecognised "
                "intent returns 'unrecognized_intent': true with a "
                "'suggested_tools' list rather than an error."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "address": {"type": "string"},
                    "zone": {"type": "string"},
                    "proposed": {"type": "object", "additionalProperties": True},
                },
                "required": ["intent"],
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
        # Deliberately unscoped (ABS-413): this executor is an eval/dev
        # harness where the caller owns the session and decides the corpus;
        # the retrieval_enabled publish gate applies to deployments, not here.
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
            response = service.lookup_citation(request)
            # ABS-261: lookup_citation now returns a
            # CitationLookupResponse envelope. To preserve the existing
            # OpenAI-adapter contract for hits (flat RetrievalMatch
            # shape), unwrap on success. Misses surface the new
            # match-null + suggestions envelope so the calling LLM can
            # self-correct instead of retrying random variants.
            if response.match is not None:
                return response.match.model_dump(mode="json")
            return response.model_dump(mode="json")
        if tool_name == "search_bylaw_evidence":
            request = _validated(RetrievalRequest, args)
            return service.search(request).model_dump(mode="json")
        if tool_name == "get_address_profile":
            return service.get_address_profile(str(args.get("address") or "")).model_dump(
                mode="json"
            )
        if tool_name == "bylaw_query":
            return service.bylaw_query(
                intent=str(args.get("intent") or ""),
                address=args.get("address"),
                zone=args.get("zone"),
                proposed=args.get("proposed"),
            ).model_dump(mode="json")
        raise ValueError(f"Unsupported OpenAI retrieval tool: {tool_name}")


def _validated(model_cls, payload: dict[str, Any]):
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

