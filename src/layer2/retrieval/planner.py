from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from layer2.llm.base import BaseLLMClient
from layer2.models.schemas import RetrievalOperation, RetrievalPlan

KNOWN_ZONE_CODES = {
    "CEN-2",
    "CEN-1",
    "CDD-2",
    "CDD-1",
    "HR-2",
    "HR-1",
    "ER-3",
    "ER-2",
    "ER-1",
    "CH-2",
    "CH-1",
    "UC-2",
    "UC-1",
    "DD",
    "DH",
    "COR",
    "CLI",
    "LI",
    "HRI",
    "INS",
    "DND",
    "PCF",
    "RPK",
    "WA",
}

STANDARD_ALIASES = {
    "front_yard": ["front yard", "front setback", "frontyard", "front-yard"],
    "side_yard": ["side yard", "side setback", "sideyard", "side-yard"],
    "rear_yard": ["rear yard", "rear setback", "back yard", "backyard", "back-yard"],
    "flankage_yard": ["flankage yard", "flanking yard", "flankage setback"],
    "building_height": ["height", "building height", "maximum height", "stories", "storeys"],
    "lot_frontage": ["frontage", "lot frontage", "street frontage", "required frontage"],
    "lot_area": ["lot area", "minimum lot area", "lot size"],
    "lot_coverage": ["lot coverage", "coverage"],
    "open_space": ["open space", "landscaped open space", "amenity space"],
    "density": ["density", "population density", "persons per acre", "units per acre"],
    "parking": ["parking", "parking requirement", "parking spaces", "stalls"],
    "accessory_structure": ["accessory building", "accessory buildings", "accessory structure", "accessory structures"],
}

ZONE_RE = re.compile(r"\b([A-Z]{1,3})-?\s?(\d[A-Z]?)(?:-?\s?([A-Z]))?\b", re.I)
CITATION_RE = re.compile(r"(?:section|subsection|clause|schedule)\s+([A-Za-z0-9\.\(\)-]+)", re.I)


def normalize_zone_code(value: str | None) -> str | None:
    if not value:
        return None
    upper_value = value.upper()
    compact_value = re.sub(r"[\s-]+", "", upper_value)
    for zone_code in sorted(KNOWN_ZONE_CODES, key=len, reverse=True):
        compact_zone = zone_code.replace("-", "")
        if re.search(rf"(?<![A-Z0-9]){re.escape(zone_code)}(?![A-Z0-9])", upper_value):
            return zone_code
        if compact_zone in compact_value and re.search(rf"\b{re.escape(zone_code)}\s+ZONE\b", upper_value):
            return zone_code
    match = ZONE_RE.search(upper_value)
    if not match:
        return None
    parts = [match.group(1), match.group(2)]
    if match.group(3):
        parts.append(match.group(3))
    return "-".join(parts)


def normalize_standard_type(question_text: str) -> str | None:
    normalized = question_text.lower().replace("-", " ")
    compact = normalized.replace(" ", "")
    if "backyard suite" in normalized:
        return None
    if "accessory" in normalized and any(token in normalized for token in ["building", "structure"]):
        return "accessory_structure"
    if "parking" in normalized:
        return "parking"
    if "open space" in normalized:
        return "open_space"
    if "density" in normalized or "persons per acre" in normalized or "dwelling units" in normalized:
        return "density"
    for standard_type, aliases in STANDARD_ALIASES.items():
        if any(alias in normalized or alias.replace(" ", "") in compact for alias in aliases):
            return standard_type
    if "setback" in normalized or "yard" in normalized:
        return "setback"
    return None


def build_planner_prompt(question_text: str, known_facts: dict[str, Any] | None = None) -> tuple[str, str]:
    system_prompt = (
        "You translate municipal land-use bylaw questions into bounded retrieval plans. "
        "Return one JSON object only. Do not answer the question. Use only these tools: "
        "get_standard, get_zone_context, get_section, get_definitions, search_context. "
        "Use intents: lookup_dimensional_standard, lookup_use_permission, "
        "lookup_parking_requirement, lookup_definition, retrieve_section, general_context_search. "
        "Use standard_type values: front_yard, side_yard, rear_yard, flankage_yard, "
        "building_height, lot_frontage, lot_area, lot_coverage, open_space, density, parking, accessory_structure, setback, unknown. "
        "Normalize zones such as R2 to R-2 and C2A to C-2A."
    )
    user_prompt = (
        f"Question: {question_text}\n"
        f"Known facts: {known_facts or {}}\n"
        "Return this shape:\n"
        "{\n"
        '  "intent": "string",\n'
        '  "entities": {"zone": "R-2 or null", "standard_type": "side_yard or null", "use_name": "string or null", "citation": "string or null"},\n'
        '  "aliases": {"zone": ["R2"], "standard_type": ["sideyard"]},\n'
        '  "recommended_calls": [{"tool": "get_standard", "args": {"zone": "R-2", "standard_type": "side_yard"}, "rationale": "string"}],\n'
        '  "expected_answer_shape": "string",\n'
        '  "confidence": 0.8\n'
        "}"
    )
    return system_prompt, user_prompt


def create_retrieval_plan(
    question_text: str,
    *,
    known_facts: dict[str, Any] | None = None,
    llm_client: BaseLLMClient | None = None,
) -> RetrievalPlan:
    raw_plan: dict[str, Any] | None = None
    if llm_client is not None:
        system_prompt, user_prompt = build_planner_prompt(question_text, known_facts)
        try:
            raw_output = llm_client.generate(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.0)
            raw_plan = json.loads(_extract_json(raw_output))
        except Exception:
            raw_plan = None
    plan = _fallback_plan(question_text, known_facts) if raw_plan is None else _coerce_plan(raw_plan, question_text, known_facts)
    return _validate_and_repair_plan(plan, question_text, known_facts)


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if not match:
        raise ValueError("No JSON object found in planner output")
    return match.group(0)


def _coerce_plan(raw_plan: dict[str, Any], question_text: str, known_facts: dict[str, Any] | None) -> RetrievalPlan:
    try:
        return RetrievalPlan.model_validate(raw_plan)
    except ValidationError:
        return _fallback_plan(question_text, known_facts)


def _fallback_plan(question_text: str, known_facts: dict[str, Any] | None) -> RetrievalPlan:
    zone = normalize_zone_code((known_facts or {}).get("zone") or question_text)
    standard_type = normalize_standard_type(question_text)
    citation_match = CITATION_RE.search(question_text)
    normalized = question_text.lower()
    entities: dict[str, Any] = {"zone": zone, "standard_type": standard_type}
    aliases: dict[str, list[str]] = {}
    calls: list[RetrievalOperation] = []
    use_type = None
    area_context = None

    if zone:
        aliases["zone"] = [zone, zone.replace("-", "")]
    if standard_type:
        aliases["standard_type"] = STANDARD_ALIASES.get(standard_type, [standard_type.replace("_", " ")])
    if "day care" in normalized:
        use_type = "day care facility"
        entities["use_name"] = use_type
    if "daycare" in normalized:
        use_type = "daycare use"
        entities["use_name"] = use_type
    if "backyard suite" in normalized:
        use_type = "backyard suite use"
        entities["use_name"] = use_type
    if "shipping container" in normalized:
        use_type = "shipping container"
        entities["use_name"] = use_type
    office_match = re.search(r"\boffice\s+use\b", normalized)
    if office_match:
        use_type = "office use"
        entities["use_name"] = use_type
    if "accessory structure" in normalized and any(token in normalized for token in ["permit", "permitted", "allowed", "can "]):
        use_type = "accessory structure or use"
        entities["use_name"] = use_type
    if "south end" in normalized:
        area_context = "South End"
        entities["area_context"] = area_context

    permission_question = bool(use_type) and any(
        token in normalized for token in ["permitted", "allowed", "can i", "can a", "can an", "operate", "have"]
    )

    if use_type == "backyard suite use":
        intent = "lookup_use_permission"
        calls.append(
            RetrievalOperation(
                tool="search_context",
                args={"query": f"{question_text} backyard suite use permitted Tables 1A 1B 1C 1D black dot white circle"},
            )
        )
        if zone:
            calls.append(RetrievalOperation(tool="get_zone_context", args={"zone": zone}))
    elif permission_question:
        intent = "lookup_use_permission"
        calls.append(
            RetrievalOperation(
                tool="search_context",
                args={"query": f"{question_text} {use_type} permitted Tables 1A 1B 1C 1D black dot white circle"},
            )
        )
        if zone:
            calls.append(RetrievalOperation(tool="get_zone_context", args={"zone": zone}))
    elif standard_type in {
        "front_yard",
        "side_yard",
        "rear_yard",
        "flankage_yard",
        "building_height",
        "lot_frontage",
        "lot_area",
        "lot_coverage",
        "open_space",
        "density",
        "parking",
        "accessory_structure",
        "setback",
    }:
        intent = "lookup_parking_requirement" if standard_type == "parking" else "lookup_dimensional_standard"
        if zone:
            args: dict[str, Any] = {"zone": zone, "standard_type": standard_type}
            if use_type:
                args["use_type"] = use_type
            if area_context:
                args["area_context"] = area_context
            calls.append(
                RetrievalOperation(
                    tool="get_standard",
                    args=args,
                    rationale="Question asks for a zoning dimensional or parking standard.",
                )
            )
            calls.append(RetrievalOperation(tool="get_zone_context", args={"zone": zone}))
            if any(term in normalized for term in ["day care", "apartment house", "south end", "schedule a", "schedule b"]):
                calls.append(RetrievalOperation(tool="search_context", args={"query": question_text}))
        else:
            calls.append(RetrievalOperation(tool="search_context", args={"query": question_text}))
    elif citation_match:
        intent = "retrieve_section"
        citation = citation_match.group(1)
        entities["citation"] = citation
        calls.append(RetrievalOperation(tool="get_section", args={"citation_or_heading": citation}))
    elif normalized.startswith("what is") or "define" in normalized:
        intent = "lookup_definition"
        term = re.sub(r"^(what is|what does|define)\s+", "", normalized).strip(" ?")
        entities["terms"] = [term]
        calls.append(RetrievalOperation(tool="get_definitions", args={"terms": [term]}))
    else:
        intent = "general_context_search"
        calls.append(RetrievalOperation(tool="search_context", args={"query": question_text}))

    return RetrievalPlan(
        intent=intent,
        entities=entities,
        aliases=aliases,
        recommended_calls=calls,
        expected_answer_shape="value_or_table_by_applicability" if standard_type else None,
        confidence=0.65,
    )


def _validate_and_repair_plan(
    plan: RetrievalPlan,
    question_text: str,
    known_facts: dict[str, Any] | None,
) -> RetrievalPlan:
    fallback = _fallback_plan(question_text, known_facts)
    zone = normalize_zone_code(plan.entities.get("zone") or fallback.entities.get("zone"))
    standard_type = plan.entities.get("standard_type") or fallback.entities.get("standard_type")
    if standard_type == "unknown":
        standard_type = fallback.entities.get("standard_type")
    entities = dict(plan.entities)
    entities["zone"] = zone
    entities["standard_type"] = standard_type

    allowed_tools = {"get_standard", "get_zone_context", "get_section", "get_definitions", "search_context"}
    calls = [call for call in plan.recommended_calls if call.tool in allowed_tools]
    if not calls:
        calls = fallback.recommended_calls
    if standard_type and zone and not any(call.tool == "get_standard" for call in calls):
        calls.insert(0, RetrievalOperation(tool="get_standard", args={"zone": zone, "standard_type": standard_type}))
    if zone and not any(call.tool == "get_zone_context" for call in calls):
        calls.append(RetrievalOperation(tool="get_zone_context", args={"zone": zone}))
    normalized = question_text.lower()
    use_name = fallback.entities.get("use_name")
    if use_name and any(token in normalized for token in ["permitted", "allowed", "can i", "can a", "can an", "operate", "have"]):
        calls.insert(
            0,
            RetrievalOperation(
                tool="search_context",
                args={"query": f"{question_text} {use_name} permitted Tables 1A 1B 1C 1D black dot white circle"},
            ),
        )
    if standard_type == "parking" and not any(call.tool == "search_context" for call in calls):
        calls.append(RetrievalOperation(tool="search_context", args={"query": question_text}))

    repaired_calls = []
    for call in calls:
        args = dict(call.args)
        if call.tool in {"get_standard", "get_zone_context"}:
            args["zone"] = normalize_zone_code(args.get("zone") or zone)
        if call.tool == "get_standard":
            args["standard_type"] = args.get("standard_type") or standard_type
            if "use_type" not in args and fallback.entities.get("use_name"):
                args["use_type"] = fallback.entities["use_name"]
            if "area_context" not in args and fallback.entities.get("area_context"):
                args["area_context"] = fallback.entities["area_context"]
        repaired_calls.append(RetrievalOperation(tool=call.tool, args=args, rationale=call.rationale))

    aliases = dict(plan.aliases)
    if zone:
        aliases.setdefault("zone", [zone, zone.replace("-", "")])
    if standard_type:
        aliases.setdefault("standard_type", STANDARD_ALIASES.get(standard_type, [standard_type.replace("_", " ")]))
    return RetrievalPlan(
        intent=plan.intent or fallback.intent,
        entities=entities,
        aliases=aliases,
        recommended_calls=repaired_calls,
        expected_answer_shape=plan.expected_answer_shape or fallback.expected_answer_shape,
        confidence=plan.confidence or fallback.confidence,
    )
