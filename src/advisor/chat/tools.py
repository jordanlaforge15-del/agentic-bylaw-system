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
- Returns ``json.dumps(...)`` of a *compact* projection of the
  retrieval response. The full Pydantic shape (kept for the external
  MCP server's wire contract) carries fields the LLM does not read
  and dozens of redundant matches; every tool_result block we hand
  back gets replayed verbatim on every subsequent turn, so any field
  we ship is billed N times. See ``compact.py`` for the projection
  rules and the LLM-relevant subset.
"""
from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from advisor.chat.compact import (
    compact_address_profile,
    compact_adjacent_zoning,
    compact_bylaw_query,
    compact_citation_lookup,
    compact_document_list,
    compact_match,
    compact_outline,
    compact_search_response,
    compact_zone_profile,
)
from advisor.llm import ToolDefinition
from advisor.llm.tool_loop import ToolHandler
from bylaw_retrieval.openai_tools import ATTRIBUTE_TAG_FILTER_PROPERTY
from bylaw_retrieval.retrieval import (
    ATTRIBUTE_VOCABULARY,
    BYLAW_INTENTS,
    CitationLookupRequest,
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
)
from layer2.compliance.db.models import SubmissionAttributeSource
from layer2.compliance.evaluator import (
    DocumentFilters,
    EvaluationRequest,
    EvaluatorService,
    SubmissionAttributeInput,
)


def _coerce_stringified_object_arg(
    payload: dict[str, Any], key: str
) -> dict[str, Any]:
    """Return ``payload`` with ``payload[key]`` parsed from a JSON string.

    LLMs sometimes serialize a nested object tool argument as a JSON *string*
    instead of a nested object — e.g. ``{"structured": "{\\"kind\\": ...}"}``
    rather than ``{"structured": {"kind": ...}}``. Pydantic then rejects the
    string with ``model_attributes_type``. When ``payload[key]`` is a string
    that parses to a dict, substitute the parsed dict so validation succeeds;
    otherwise leave ``payload`` untouched and let normal validation run. Does
    not mutate the input dict.
    """
    value = payload.get(key)
    if not isinstance(value, str):
        return payload
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return payload
    if not isinstance(parsed, dict):
        return payload
    return {**payload, key: parsed}


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
    "Use this when the user or agent already knows the citation.\n\n"
    "Response shape: `{match: <fragment> | null, suggestions: [<path>, ...], instruction: <next-step>}`. "
    "If `match` is null, DO NOT retry lookup_citation with a guessed variant. Instead: "
    "(a) if `suggestions` is non-empty, pick the closest candidate verbatim and re-issue lookup_citation with that exact string; "
    "(b) if `suggestions` is empty, switch to `search_bylaw_evidence` or `get_document_outline` — the path doesn't exist in this document.\n\n"
    "A hit carries `match.operative_clauses`: the other clauses of the same "
    "provision — the section's own (a)/(b)/(1.5) limbs, or, when you looked up "
    "a clause, its siblings. These are PART OF THE RULE, not related reading. "
    "A section whose text ends on a colon ('…shall have no restriction on the "
    "maximum size of its footprint, except:') states no standard at all by "
    "itself; the numbers are in its clauses. Quote every clause that applies to "
    "the zone in question — several limits often bind at once, and reporting "
    "one of them as if it were the only one is a wrong answer, not a partial "
    "one. If `operative_clauses_omitted` is non-zero, the provision is longer "
    "than what you were shown: re-read it with `search_bylaw_evidence` and "
    "`citation_path_prefix` set to the provision's path."
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
    "— do not just ignore it.\n\n"
    "Each match may carry ``operative_clauses``: the other limbs of the "
    "SAME provision, delivered with the match because they are part of the "
    "rule it states rather than related reading. A clause that ranked on its "
    "own is frequently one of several limits that bind together — a size "
    "limit stated as a floor area in one subsection and as a footprint in "
    "another. Read every operative clause before answering, and report every "
    "limit that applies to the zone in question; naming one when two bind is "
    "a wrong answer, not an incomplete one. ``operative_clauses_omitted`` "
    "non-zero means the provision is longer than what you were shown — "
    "re-issue with ``citation_path_prefix`` set to the provision's path.\n\n"
    "--------------------------------------------------------------------\n"
    "Narrowing by regulated attribute via 'attribute_tag_filter':\n\n"
    "When the question is about a specific regulated dimension (height, a "
    "setback, lot coverage, floor area ratio, parking), pass the matching "
    "attribute IDs in ``attribute_tag_filter``. It is an indexed hard "
    "pre-filter: only clauses tagged with at least one of those IDs are "
    "scored, so the answer comes from the clauses that actually regulate "
    "the attribute instead of whatever prose happens to share vocabulary. "
    "Valid IDs are the 'id' values in the Phase-1 attribute taxonomy "
    "(src/layer2/compliance/attributes/taxonomy.yaml) — e.g. "
    "building_height_m, front_setback_m, lot_coverage_percent, "
    "floor_area_ratio, parking_stalls_count. Multiple IDs union. Omit the "
    "field for exploratory questions; never send an empty array.\n\n"
    "--------------------------------------------------------------------\n"
    "Tuning the result-set size via 'limit':\n\n"
    "Default 5; bump to 15 when the question covers multiple dimensions "
    "(e.g. zone feasibility, height, setbacks, and FAR all at once). "
    "Cap is 50. Higher limits reduce iteration count but increase the "
    "payload size billed on every subsequent turn — use the smallest "
    "value that covers the question's breadth."
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
    "description": (
        "Provide exactly one of 'citation_path' or 'structured'. "
        "Use 'citation_path' when you already know the exact path string. "
        "Use 'structured' (zone_attribute, schedule_row, or permitted_use) to "
        "resolve by zone + attribute, schedule + row, or a single use × zone "
        "permission-matrix cell — without guessing the path format. "
        "Prefer the 'permitted_use' kind when the question is 'is <use> "
        "permitted in <zone>?': it returns a typed permission (permitted / "
        "conditional / not_permitted) plus any footnote condition, instead of "
        "leaving you to infer it from table prose."
    ),
    "properties": {
        "citation_path": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Exact citation path, e.g. '4.2' or 'Schedule B > 3'. "
                "Mutually exclusive with 'structured'."
            ),
        },
        "structured": {
            "description": (
                "Structured query variant. Mutually exclusive with 'citation_path'. "
                "Set 'kind' to 'zone_attribute', 'schedule_row', or 'permitted_use'."
            ),
            "oneOf": [
                {
                    "type": "object",
                    "required": ["kind", "zone", "attribute"],
                    "properties": {
                        "kind": {"type": "string", "const": "zone_attribute"},
                        "zone": {
                            "type": "string",
                            "description": "Zone code, e.g. 'HR-2'.",
                        },
                        "attribute": {
                            "type": "string",
                            "enum": sorted(ATTRIBUTE_VOCABULARY),
                            "description": "Attribute to look up.",
                        },
                    },
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "required": ["kind", "schedule", "row"],
                    "properties": {
                        "kind": {"type": "string", "const": "schedule_row"},
                        "schedule": {
                            "type": "string",
                            "description": "Schedule name, e.g. 'Table 1A'.",
                        },
                        "row": {
                            "type": "string",
                            "description": "Row identifier within the schedule, e.g. 'HR-2'.",
                        },
                    },
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "required": ["kind", "use", "zone"],
                    "properties": {
                        "kind": {"type": "string", "const": "permitted_use"},
                        "use": {
                            "type": "string",
                            "description": "Use name, e.g. 'Restaurant use'.",
                        },
                        "zone": {
                            "type": "string",
                            "description": "Zone code, e.g. 'HR-2'.",
                        },
                    },
                    "additionalProperties": False,
                },
            ],
        },
        "document_id": {"type": "integer"},
        "include_context": {"type": "boolean", "default": True},
        "include_cross_references": {"type": "boolean", "default": True},
        "include_tables": {"type": "boolean", "default": True},
    },
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
        # ABS-479: imported, not re-typed, so this surface and the
        # OpenAI-shaped spec in ``mcp/bylaw_retrieval/openai_tools.py``
        # cannot drift apart (the failure mode ABS-469 had to clean up).
        "attribute_tag_filter": dict(ATTRIBUTE_TAG_FILTER_PROPERTY),
        "include_context": {"type": "boolean", "default": True},
        "include_cross_references": {"type": "boolean", "default": True},
        "include_tables": {"type": "boolean", "default": True},
        "include_datasets": {"type": "boolean", "default": True},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 5,
            "description": (
                "Maximum fragments to return. Default 5; bump to 15 when the question covers "
                "multiple dimensions (e.g. zone feasibility); cap is 50."
            ),
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


# --- get_address_profile -------------------------------------------------
#
# Thick case-open tool (ABS-273 / Phase 3). Description copied verbatim from
# the MCP server's tool docstring headline so the LLM sees the same wording
# across both surfaces. FR-3.5.
_DESC_GET_ADDRESS_PROFILE = (
    "Use this at the start of a case-bound conversation when the user "
    "mentions an address, parcel, or named place. Returns the zone, overlay "
    "precincts, heritage status, and citations in one call. Saves multiple "
    "lookups.\n\n"
    "The 'address' argument is free text in the same shape the "
    "search_bylaw_evidence 'location' slot accepts — a civic address "
    "(\"100 Robie Street\") or a parcel id (\"PID 00012345\"). The tool "
    "resolves the address spatially, then composes the zone plus every "
    "linked overlay (height precinct, FAR precinct, heritage district, "
    "bonus zoning) into one AddressProfile.\n\n"
    "If the address can't be resolved, the response carries "
    "'unresolvable': true with empty citations rather than an error — fall "
    "back to search_bylaw_evidence with the location slot in that case.\n\n"
    "READ THE RESOLUTION QUALITY BEFORE YOU STATE THE ZONE. The response's "
    "'resolution_quality' says how the address became a point: 'rooftop' "
    "matched the building; 'interpolated' means the civic number was NOT "
    "found and the position was estimated along the street; 'centroid' and "
    "'approximate' are coarser still. Anything other than 'rooftop' means "
    "the point may sit on a neighbouring parcel, so the zone — and every "
    "setback, height and floor-area figure derived from it — may be the "
    "wrong property's. In that case the response carries 'caveats' and an "
    "'instruction': surface the uncertainty to the user instead of stating "
    "the zone as fact. 'outside_mapped_area': true means the address "
    "resolved but fell outside every mapped boundary — say so; do not "
    "report a zone.\n\n"
    "DOES THE ADDRESS EXIST? 'civic_address_status' is checked against the "
    "municipality's own data before anything is looked up. 'not_found' means "
    "the street is known and NO published civic address or street-segment "
    "range covers this number — the address does not exist. The response "
    "then carries no zone at all, plus 'valid_civic_number_ranges' and "
    "'suggested_civic_numbers': tell the user the address could not be "
    "found, quote those, and ask them to confirm. Do NOT re-issue the lookup "
    "or fall back to search_bylaw_evidence for the same address — a "
    "geocoder answers a fabricated address by estimating a position from the "
    "surrounding numbering, which is the failure this check exists to stop. "
    "'confirmed' means the number exists; 'unverifiable' means no municipal "
    "address data was in scope and says nothing either way.\n\n"
    "IS THE ZONE SAFE TO RELY ON? Independent of resolution quality: "
    "'zone_boundary_distance_m' (with 'nearest_other_zone') reports when the "
    "point sits within ~25 m of a different zone, and 'parcel_zones' lists "
    "every zone the parcel intersects when the lot is split between more "
    "than one. A split lot has no single governing zone — say so and ask "
    "where on the lot the work is proposed.\n\n"
    "WHICH BY-LAW GOVERNS THIS PARCEL? The zoning mapping is "
    "municipality-wide, so a zone code can belong to a by-law that is not in "
    "this corpus. 'governing_bylaw' names the by-law that governs the "
    "resolved parcel and 'governing_bylaw_status' says whether we hold it. "
    "'not_held' is a hard stop, not a hedge: the zone code is the "
    "municipality's own published mapping and may be stated, but NO standard "
    "behind it — permitted uses, height, setbacks, floor area, parking — is "
    "available here, and the standards of the by-laws that ARE available do "
    "not apply to this parcel. Name the governing by-law, say it must be "
    "consulted directly with HRM, and do not substitute a figure from "
    "another by-law. The zone will carry no citation in that case, because "
    "there is no honest one to give.\n\n"
    "THE SAME QUESTION FOR EACH OVERLAY, SEPARATELY. The height-precinct, "
    "FAR and other schedule layers are municipality-wide too, so a parcel "
    "whose 'governing_bylaw_status' is 'held' can still sit under an overlay "
    "from a by-law we do not hold. Each entry in 'overlays' carries its own "
    "'governing_bylaw' / 'governing_bylaw_held', and 'governing_bylaw_held': "
    "false means the same hard stop for THAT overlay: state the mapped "
    "value, name the by-law it comes from, and do NOT read the standard out "
    "of the equivalent schedule in a by-law we do hold — that schedule does "
    "not govern this ground."
)

_SCHEMA_GET_ADDRESS_PROFILE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "address": {
            "type": "string",
            "description": (
                "Free-text address, parcel id, or named place to ground the "
                "case on, e.g. '100 Robie Street' or 'PID 00012345'."
            ),
        },
    },
    "required": ["address"],
    "additionalProperties": False,
}


# --- get_adjacent_zoning (ABS-375) ---------------------------------------
#
# Resolves the zone of the parcels ABUTTING a subject address's parcel.
# Development-standards and variance reports punt rear/side setback verdicts
# to the customer ("UNCERTAIN — depends on adjacent zoning") because the
# governing setback is conditional on the neighbouring lot's zone, and the
# agent had no mid-run way to resolve it. This tool closes that gap so the
# report can give a definitive PASS/FAIL and a variance writer can pin the
# governing setback provision instead of adopting the applicant's figure.
# Description mirrored in mcp/bylaw_retrieval/server.py — keep in sync.
_DESC_GET_ADJACENT_ZONING = (
    "Use this when a setback (or any standard) is conditional on the ZONE OF "
    "AN ABUTTING PROPERTY — e.g. a Downtown (DH) lot whose required side yard "
    "is 0.0 m where it abuts another DH lot but greater where it abuts a "
    "residential zone. Returns the subject parcel's own zone plus every "
    "abutting parcel's zone (with a coarse compass direction), so you can "
    "resolve the governing setback row and give a DEFINITIVE pass/fail "
    "instead of deferring the abutting-zone question to the customer.\n\n"
    "The 'address' argument is free text in the same shape the "
    "search_bylaw_evidence 'location' slot accepts — a civic address "
    "(\"1250 Robie Street\") or a parcel id (\"PID 00012345\").\n\n"
    "Reading the response: 'distinct_neighbour_zones' is the set of zones "
    "among the neighbours — a single-element list means every abutting lot "
    "shares one zone, so the abutting-zone condition is unambiguous. A "
    "neighbour whose 'zone' is null abuts but its centroid matched no zone "
    "polygon (sliver / right-of-way) — treat it as non-determinative rather "
    "than a residential abutment. If the address can't be resolved or no "
    "parcels dataset is ingested, the response carries 'unresolvable': true "
    "or a 'note' — fall back to search_bylaw_evidence in that case."
)

_SCHEMA_GET_ADJACENT_ZONING: dict[str, Any] = {
    "type": "object",
    "properties": {
        "address": {
            "type": "string",
            "description": (
                "Free-text address or parcel id whose abutting parcels' "
                "zoning to resolve, e.g. '1250 Robie Street' or "
                "'PID 00012345'."
            ),
        },
    },
    "required": ["address"],
    "additionalProperties": False,
}


# --- get_zone_profile (ABS-272 thick tool) --------------------------------
#
# Description copied verbatim from ``mcp/bylaw_retrieval/server.py``
# (get_zone_profile docstring). FR-2.6: tell the model this is the
# one-call path for a zone's standards, and point drill-down at
# lookup_citation. Keep the two strings in sync.
_DESC_GET_ZONE_PROFILE = (
    "Use this when the user asks about a specific zone's standards "
    "(height, lot coverage, setbacks, floor area ratio, permitted/"
    "not-permitted uses, parking). Returns everything in one call as a "
    "structured ZoneProfile — height, coverage, setbacks and FAR under "
    "'dimensions'; permitted/not-permitted lists under 'uses'; parking "
    "applicability under 'parking'; and a 'citations' list backing every "
    "populated field.\n\n"
    "Prefer this over issuing several search_bylaw_evidence calls for the "
    "same zone — it collapses that sequence into one call. The internal "
    "implementation still uses semantic retrieval, so edge cases the DTO "
    "doesn't anticipate can fall back to search_bylaw_evidence.\n\n"
    "'uses' may also carry an 'undetermined' list: uses whose permission "
    "the ingested source did not yield. Those are extraction gaps, not "
    "prohibitions — report them as not determinable from the ingested "
    "source, never as permitted or prohibited.\n\n"
    "Filter the response with 'include' (any of 'dimensions', 'uses', "
    "'parking', 'citations'); omit it to get everything. A field is null "
    "when the bylaw is silent or retrieval couldn't extract it "
    "confidently, and 'unknown_zone' is true when the zone wasn't found "
    "(no exception is raised). For drill-down on any single citation, "
    "pass its citation_path to lookup_citation."
)

_SCHEMA_GET_ZONE_PROFILE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "zone": {
            "type": "string",
            "description": "Zone code, e.g. 'HR-2', 'COR', 'CEN-2'.",
        },
        "include": {
            "type": "array",
            "description": (
                "Optional list of sections to populate. Any of 'dimensions', "
                "'uses', 'parking', 'citations'. Omit for all sections."
            ),
            "items": {
                "type": "string",
                "enum": ["dimensions", "uses", "parking", "citations"],
            },
        },
    },
    "required": ["zone"],
    "additionalProperties": False,
}


# --- evaluate_submission_against_bylaws ----------------------------------
#
# Description copied verbatim from the MCP server's tool docstring so
# the LLM sees the same wording across the MCP server and the
# in-process advisor path. Keep the two in sync — divergence will
# surface as inconsistent agent behaviour between the two surfaces.
_DESC_EVALUATE_SUBMISSION = (
    "Evaluate a development submission's attributes against the "
    "applicable bylaw clauses for a given location. Returns a "
    "structured compliance matrix: per-attribute applicable clauses, "
    "computed deltas, and pass/fail/uncertain verdicts. Output is "
    "ADVISORY — never present a verdict without the citing clause.\n\n"
    "USE THIS when the user has stated specific proposed values "
    "(height, setbacks, use class, parking, etc.) AND a location. If "
    "only one of attributes or location is provided, fall back to "
    "search_bylaw_evidence instead.\n\n"
    "Always populate 'location' — same rule as search_bylaw_evidence. "
    "An evaluation without a location skips spatial filtering and may "
    "surface clauses from the wrong zone.\n\n"
    "If overall_status is 'incomplete', the next turn should ASK the "
    "user for the attributes listed in 'unevaluated_attributes' rather "
    "than guessing values."
)

_SCHEMA_EVALUATE_SUBMISSION: dict[str, Any] = {
    "type": "object",
    "properties": {
        "attributes": {
            "type": "array",
            "description": (
                "List of submitted attribute values. attribute_key MUST "
                "be a valid ID from the Phase-1 taxonomy (e.g. "
                "'front_setback_m', 'building_height_m', 'zone_code')."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "attribute_key": {"type": "string"},
                    "value": {
                        "description": (
                            "Submitted value — number, integer, string, "
                            "or boolean per the attribute's value_type."
                        )
                    },
                    "unit": {"type": "string"},
                    "source": {
                        "type": "string",
                        "enum": ["manual", "extracted", "derived", "override"],
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": ["attribute_key"],
                "additionalProperties": False,
            },
        },
        "location": {
            "type": "object",
            "description": (
                "Same LocationSlot shape used by search_bylaw_evidence. "
                "Use civic_number + street for street addresses; "
                "parcel_id for known parcels; intersection_streets for "
                "intersections; named_place for landmarks; geometry for "
                "caller-supplied GeoJSON Point/Polygon in EPSG:4326."
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
                "geometry": {"type": "object"},
            },
            "additionalProperties": False,
        },
        "document_filters": {
            "type": "object",
            "properties": {
                "municipality": {"type": "string"},
                "bylaw_name": {"type": "string"},
                "citation_path_prefix": {"type": "string"},
                "document_id": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "taxonomy_version": {"type": "string"},
        "per_attribute_limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 25,
            "default": 8,
        },
        "submission_id": {"type": "integer"},
        "persist_decision": {"type": "boolean", "default": False},
    },
    "required": ["attributes"],
    "additionalProperties": False,
}


# --- bylaw_query (ABS-274 / Phase 4 intent-routed mega-tool) --------------
#
# Description per FR-4.5. Registered AFTER the thin tools and the Phase 2/3
# thick tools (see build order below) so the model preferentially reaches
# for the narrower tools when its intent is ambiguous; bylaw_query is the
# explicit "I already know the location AND the intent" shortcut.
#
# The valid intents are listed inline (BYLAW_INTENTS — the shared
# vocabulary) so the model sees them, but ``intent`` is deliberately NOT a
# hard JSON-Schema enum: an out-of-vocabulary intent must still reach the
# server so it can return unrecognized_intent=True with thin-tool
# suggestions (FR-4.2) rather than being rejected at the schema layer.
_DESC_BYLAW_QUERY = (
    "Use this when the user has stated both a location AND a specific "
    "intent (feasibility, use permission, dimensional check). Saves "
    "multiple round-trips by composing the full answer server-side. For "
    "exploratory questions where you don't yet know the intent, use the "
    "thin tools.\n\n"
    "Declare 'intent' as one of: "
    + ", ".join(BYLAW_INTENTS)
    + ".\n"
    "  - zone_feasibility: pass 'zone' — returns the full ZoneProfile "
    "(dimensions + uses + parking) in one call.\n"
    "  - address_lookup: pass 'address' — returns the AddressProfile "
    "(zone + overlays + citations).\n"
    "  - use_check: pass 'zone' — returns the zone's permitted / "
    "not-permitted use lists.\n"
    "  - dimensional_check: pass 'zone' and 'proposed' (e.g. "
    "{\"height_m\": 80}) — returns the dimensions plus a ConformanceCheck "
    "flagging each proposed value as pass / fail / inconclusive.\n\n"
    "An intent outside this list returns 'unrecognized_intent': true with "
    "a 'suggested_tools' list to fall back to — never an error."
)

_SCHEMA_BYLAW_QUERY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "description": (
                "The intent to route on. One of: " + ", ".join(BYLAW_INTENTS) + "."
            ),
        },
        "address": {
            "type": "string",
            "description": (
                "Free-text address / parcel / named place. Required for "
                "intent='address_lookup'."
            ),
        },
        "zone": {
            "type": "string",
            "description": (
                "Zone code, e.g. 'HR-2'. Required for zone_feasibility, "
                "use_check, and dimensional_check."
            ),
        },
        "proposed": {
            "type": "object",
            "description": (
                "Proposed dimensional values for intent='dimensional_check', "
                "e.g. {\"height_m\": 80, \"front_setback_m\": 2}. Keys: "
                "height_m, lot_coverage_pct, far, front_setback_m, "
                "side_setback_m, rear_setback_m."
            ),
            "additionalProperties": True,
        },
    },
    "required": ["intent"],
    "additionalProperties": False,
}


# Tier-upgrade tool — the agent calls this when it self-detects that
# the user's question outgrew the purchased tier. Distinct from a
# text-only convention because a structured tool call gives the
# frontend a deterministic SSE event to render the upgrade modal
# against (no NLP on the assistant's prose).
_DESC_REQUEST_TIER_UPGRADE = (
    "Ask the user to upgrade the active case to a higher tier when you "
    "have determined that completing thorough research will require more "
    "retrieval rounds or token budget than the current tier allows.\n\n"
    "Call this tool when:\n"
    "* You have called retrieval tools four or more times on a single "
    "  sub-question and still feel uncertain.\n"
    "* You see that remaining token budget will not cover the additional "
    "  retrieval rounds the question still needs.\n"
    "* The user's question has expanded mid-conversation in a way that "
    "  changes the tier classification (e.g. a new property was "
    "  introduced, an overlay zone surfaced).\n\n"
    "After calling this tool, STOP your investigation and return a brief "
    "summary of what you've found so far. Do NOT bluff completion."
)

_SCHEMA_REQUEST_TIER_UPGRADE = {
    "type": "object",
    "properties": {
        "recommended_tier": {
            "type": "string",
            "enum": ["standard", "complex"],
            "description": (
                "The tier you believe the user should upgrade to. Only "
                "'standard' or 'complex' are valid — there is no upgrade "
                "below the user's current tier."
            ),
        },
        "reason": {
            "type": "string",
            "description": (
                "One short paragraph explaining what the additional tier "
                "budget would let you investigate. Surfaced verbatim to "
                "the user in the upgrade modal."
            ),
        },
    },
    "required": ["recommended_tier", "reason"],
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
            return json.dumps(compact_document_list(list(documents)))

    async def get_document_outline_handler(payload: dict[str, Any]) -> str:
        with _resolve_cm() as service:
            outline = service.get_document_outline(
                document_id=payload["document_id"],
                max_fragments=payload.get("max_fragments", 250),
                include_text=payload.get("include_text", False),
            )
            return json.dumps(compact_outline(outline))

    async def lookup_citation_handler(payload: dict[str, Any]) -> str:
        # Some models serialize the nested ``structured`` argument as a JSON
        # *string* (e.g. '{"kind": "permitted_use", "use": ..., "zone": ...}')
        # instead of a nested object. model_validate then rejects it with
        # "Input should be a valid dictionary" and the structured permitted-use
        # path never reaches the resolver — the model thrashes re-issuing the
        # call until it hits the iteration cap and falls back to ungrounded
        # prose (ABS-280, observed with Opus on TC-005 T5). Coerce a stringified
        # nested object back to a dict before validation.
        payload = _coerce_stringified_object_arg(payload, "structured")
        # model_validate will raise ValidationError on missing required
        # fields; the tool loop catches that and surfaces it to the LLM
        # as a tool_result error so it can self-correct.
        request = CitationLookupRequest.model_validate(payload)
        with _resolve_cm() as service:
            response = service.lookup_citation(request)
            # Before ABS-261 this called compact_match() directly and a
            # missed path raised ValueError out of the service — the
            # tool loop then thrashed re-issuing variants until it hit
            # max_iterations. compact_citation_lookup now produces a
            # match-or-suggestions envelope with an inline instruction
            # telling the model exactly what to do next, eliminating
            # the destructive retry pattern.
            return json.dumps(compact_citation_lookup(response))

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
            # ABS-479: passthrough for the indexed attribute_tags pre-filter.
            # An empty list is rejected by RetrievalRequest's validator; the
            # resulting ValidationError is caught by the tool loop and handed
            # back to the model as a tool error, not raised out of the request.
            attribute_tag_filter=payload.get("attribute_tag_filter"),
            include_context=payload.get("include_context", True),
            include_cross_references=payload.get("include_cross_references", True),
            include_tables=payload.get("include_tables", True),
            include_datasets=payload.get("include_datasets", True),
            limit=int(os.environ["ADVISOR_FORCE_SEARCH_LIMIT"]) if "ADVISOR_FORCE_SEARCH_LIMIT" in os.environ else payload.get("limit", 5),
        )
        with _resolve_cm() as service:
            response = service.search(request)
            return json.dumps(compact_search_response(response))

    async def get_address_profile_handler(payload: dict[str, Any]) -> str:
        """Resolve a free-text address into its zone + overlay profile.

        Returns the compact projection so the (replayed-every-turn)
        tool_result stays small; the unresolvable case carries an explicit
        fall-back instruction the model can act on.
        """
        address = str(payload.get("address") or "")
        with _resolve_cm() as service:
            profile = service.get_address_profile(address)
            return json.dumps(compact_address_profile(profile))

    async def get_adjacent_zoning_handler(payload: dict[str, Any]) -> str:
        """Resolve the zoning of the parcels abutting an address's parcel.

        Returns the compact projection so the replayed-every-turn tool_result
        stays small; the unresolvable / no-parcels case carries an explicit
        fall-back instruction the model can act on (ABS-375).
        """
        address = str(payload.get("address") or "")
        with _resolve_cm() as service:
            profile = service.get_adjacent_zoning(address)
            return json.dumps(compact_adjacent_zoning(profile))

    async def get_zone_profile_handler(payload: dict[str, Any]) -> str:
        # ABS-272 thick tool. ``zone`` is required; ``include`` filters
        # which sections are populated. An unknown zone returns a DTO
        # with unknown_zone=True rather than raising, so the tool loop
        # never thrashes on a typo'd zone code.
        zone = payload["zone"]
        include = payload.get("include")
        with _resolve_cm() as service:
            profile = service.get_zone_profile(zone=zone, include=include)
            return json.dumps(compact_zone_profile(profile))

    async def bylaw_query_handler(payload: dict[str, Any]) -> str:
        # ABS-274 / Phase 4 intent-routed composer. ``intent`` is required;
        # the service dispatches to get_zone_profile / get_address_profile
        # per intent (FR-4.2/4.4). An unrecognised intent or a missing
        # required slot returns a fall-back envelope rather than raising,
        # so the tool loop never thrashes.
        intent = str(payload.get("intent") or "")
        with _resolve_cm() as service:
            response = service.bylaw_query(
                intent=intent,
                address=payload.get("address"),
                zone=payload.get("zone"),
                proposed=payload.get("proposed"),
            )
            return json.dumps(compact_bylaw_query(response))

    async def evaluate_submission_handler(payload: dict[str, Any]) -> str:
        """Run the compliance evaluator against the supplied attributes + location.

        The evaluator reuses whatever RetrievalService the chat backend
        is already bound to (via ``_resolve_cm``), so the same
        enabled-documents / document-id scope rules apply automatically.
        """
        location_payload = payload.get("location")
        location = (
            LocationSlot.model_validate(location_payload)
            if location_payload is not None
            else None
        )
        attribute_inputs: list[SubmissionAttributeInput] = []
        for entry in payload.get("attributes") or []:
            attr_key = entry.get("attribute_key")
            if not isinstance(attr_key, str) or not attr_key:
                continue
            source_token = entry.get("source") or "manual"
            try:
                source = SubmissionAttributeSource(source_token)
            except ValueError:
                source = SubmissionAttributeSource.MANUAL
            attribute_inputs.append(
                SubmissionAttributeInput(
                    attribute_key=attr_key,
                    value=entry.get("value"),
                    unit=entry.get("unit"),
                    source=source,
                    confidence=float(entry.get("confidence", 1.0)),
                )
            )
        filters_payload = payload.get("document_filters")
        filters = (
            DocumentFilters(
                municipality=filters_payload.get("municipality"),
                bylaw_name=filters_payload.get("bylaw_name"),
                citation_path_prefix=filters_payload.get("citation_path_prefix"),
                document_id=filters_payload.get("document_id"),
            )
            if filters_payload
            else None
        )
        request = EvaluationRequest(
            attributes=attribute_inputs,
            location=location,
            document_filters=filters,
            taxonomy_version=payload.get("taxonomy_version"),
            per_attribute_limit=int(payload.get("per_attribute_limit", 8)),
            submission_id=payload.get("submission_id"),
            persist_decision=bool(payload.get("persist_decision", False)),
        )
        with _resolve_cm() as service:
            evaluator = EvaluatorService(
                service.session, retrieval_service=service
            )
            response = evaluator.evaluate(request)
            return json.dumps(response.to_json())

    async def request_tier_upgrade_handler(payload: dict[str, Any]) -> str:
        """Surface a tier-upgrade prompt to the user and pause the agent.

        Returns a tool-result string the agent reads as a cue to halt
        the current investigation. The actual UI prompt is delivered
        via a side-channel: the chat session's ``last_turn_upgrade_request``
        attribute is populated, and the API layer translates that into
        a ``case_upgrade_offer`` SSE event the frontend listens for.

        We never raise from this handler — a malformed payload still
        produces a tool_result the agent can read, just with a
        explanatory message rather than the structured prompt.
        """
        recommended_tier = str(payload.get("recommended_tier") or "standard")
        if recommended_tier not in {"standard", "complex"}:
            recommended_tier = "standard"
        reason = str(payload.get("reason") or "Additional research depth required.")
        # Stash the offer on a module-level callable so the chat session
        # picks it up via its ``on_tool_call`` style hook. We attach via
        # the ``ChatSession.metadata`` field through a custom attribute
        # set by the route. See chat/session.py for the consumer.
        _LAST_UPGRADE_REQUEST.append(
            {"recommended_tier": recommended_tier, "reason": reason}
        )
        return (
            "Upgrade prompt surfaced to the user. Pause this "
            "investigation, summarise findings to date, and wait for "
            "the user's decision before continuing."
        )

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
        ToolDefinition(
            name="get_address_profile",
            description=_DESC_GET_ADDRESS_PROFILE,
            input_schema=_SCHEMA_GET_ADDRESS_PROFILE,
        ),
        ToolDefinition(
            name="get_adjacent_zoning",
            description=_DESC_GET_ADJACENT_ZONING,
            input_schema=_SCHEMA_GET_ADJACENT_ZONING,
        ),
        ToolDefinition(
            name="get_zone_profile",
            description=_DESC_GET_ZONE_PROFILE,
            input_schema=_SCHEMA_GET_ZONE_PROFILE,
        ),
        ToolDefinition(
            name="evaluate_submission_against_bylaws",
            description=_DESC_EVALUATE_SUBMISSION,
            input_schema=_SCHEMA_EVALUATE_SUBMISSION,
        ),
        # Phase 4 mega-tool — placed AFTER the thin + Phase 2/3 thick tools
        # so the model tries the narrower tools first when intent is unclear.
        ToolDefinition(
            name="bylaw_query",
            description=_DESC_BYLAW_QUERY,
            input_schema=_SCHEMA_BYLAW_QUERY,
        ),
        # ABS-383: ``request_tier_upgrade`` is intentionally NOT registered.
        # The beta pivot retired tier upgrades — chat bills the account
        # token wallet, not per-case tier credits — so the agent is no
        # longer offered a tool to prompt one. The handler function and the
        # ``_LAST_UPGRADE_REQUEST`` drain remain in this module as dormant
        # no-ops (nothing appends to the buffer now); ``ChatSession`` still
        # drains it each turn, harmlessly getting an empty list.
    ]

    handlers: dict[str, ToolHandler] = {
        "list_documents": list_documents_handler,
        "get_document_outline": get_document_outline_handler,
        "lookup_citation": lookup_citation_handler,
        "search_bylaw_evidence": search_bylaw_evidence_handler,
        "get_address_profile": get_address_profile_handler,
        "get_adjacent_zoning": get_adjacent_zoning_handler,
        "get_zone_profile": get_zone_profile_handler,
        "evaluate_submission_against_bylaws": evaluate_submission_handler,
        "bylaw_query": bylaw_query_handler,
    }

    return tool_defs, handlers


# Per-process buffer of in-flight upgrade requests. ``ChatSession``
# drains this after each turn so the chat route can emit a
# ``case_upgrade_offer`` SSE event. Bounded by drain frequency: every
# turn empties it, so it never exceeds a handful of entries.
_LAST_UPGRADE_REQUEST: list[dict[str, str]] = []


def drain_upgrade_requests() -> list[dict[str, str]]:
    """Atomically take and clear pending upgrade requests.

    Called from ``ChatSession.send_user_message_blocking`` after the
    tool loop returns. Returns the list of requests fired during this
    turn (usually 0 or 1; multiple are possible if the agent calls the
    tool more than once but the frontend only renders the first).
    """
    drained = list(_LAST_UPGRADE_REQUEST)
    _LAST_UPGRADE_REQUEST.clear()
    return drained
