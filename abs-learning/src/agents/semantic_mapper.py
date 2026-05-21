"""Semantic Mapper agent for Phase 2.

Reads a bylaw document, locates the windows most likely to contain zone
definitions, use-permission tables, and development-standards sections, and
calls Anthropic with strict tool-use schemas to extract a :class:`TaxonomyMap`.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from bootstrap.pdf_reader import PdfBootstrapReader
from manifest.models import (
    SourceDocument,
    TaxonomyMap,
    ZoneDesignation,
)


CANONICAL_ZONE_TYPES: Tuple[str, ...] = (
    "residential",
    "commercial",
    "mixed_use",
    "industrial",
    "institutional",
    "open_space",
    "special_area",
    "unknown",
)

CANONICAL_USE_CLASSES: Tuple[str, ...] = (
    "residential_dwelling_single",
    "residential_dwelling_multi",
    "residential_dwelling_accessory",
    "residential_dwelling_cluster",
    "retail_general",
    "retail_food",
    "food_and_beverage",
    "office_general",
    "institutional_education",
    "institutional_health",
    "industrial_light",
    "industrial_general",
    "open_space_park",
    "accommodation",
    "home_occupation",
    "short_term_rental",
    "other",
)

CANONICAL_STANDARDS: Tuple[str, ...] = (
    "height",
    "front_setback",
    "side_setback",
    "rear_setback",
    "lot_coverage",
    "floor_area_ratio",
    "parking",
    "bicycle_parking",
    "landscaping",
    "signage",
    "amenity_space",
    "stepback",
    "podium_height",
)


ZONES_TOOL_NAME = "report_zone_designations"
USES_TOOL_NAME = "report_use_class_mappings"
STANDARDS_TOOL_NAME = "report_standards_categories"


_ZONES_TOOL_SCHEMA: Dict[str, Any] = {
    "name": ZONES_TOOL_NAME,
    "description": "Report identified zone codes and their canonical types.",
    "input_schema": {
        "type": "object",
        "required": ["zones", "confidence"],
        "properties": {
            "zones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["code", "canonical_type"],
                    "properties": {
                        "code": {"type": "string"},
                        "full_name": {"type": ["string", "null"]},
                        "canonical_type": {
                            "type": "string",
                            "enum": list(CANONICAL_ZONE_TYPES),
                        },
                        "description": {"type": ["string", "null"]},
                    },
                },
            },
            "confidence": {"type": "number"},
        },
    },
}

_USES_TOOL_SCHEMA: Dict[str, Any] = {
    "name": USES_TOOL_NAME,
    "description": "Report local use-name → canonical use-class mappings.",
    "input_schema": {
        "type": "object",
        "required": ["mappings", "confidence"],
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["local_term", "canonical_key"],
                    "properties": {
                        "local_term": {"type": "string"},
                        "canonical_key": {
                            "type": "string",
                            "enum": list(CANONICAL_USE_CLASSES),
                        },
                    },
                },
            },
            "confidence": {"type": "number"},
        },
    },
}

_STANDARDS_TOOL_SCHEMA: Dict[str, Any] = {
    "name": STANDARDS_TOOL_NAME,
    "description": "Report normalised development standard categories.",
    "input_schema": {
        "type": "object",
        "required": ["categories", "confidence"],
        "properties": {
            "categories": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(CANONICAL_STANDARDS),
                },
            },
            "confidence": {"type": "number"},
        },
    },
}


ZONES_SYSTEM_PROMPT = """You are a bylaw semantic analyst. You will be given a window of text from a
municipal land-use bylaw. Identify every zone code and, where stated, its
full name and purpose.

Zone codes follow this pattern: 1–4 uppercase letters, a hyphen, one digit
(e.g. ER-1, UC-2, CEN-1, CDD-2). Some documents also use codes without
hyphens (e.g. DD, DH, CLI) — include these too if they are clearly zone names.

For each zone, determine its canonical_type from this fixed list:
  residential, commercial, mixed_use, industrial,
  institutional, open_space, special_area, unknown

Use "unknown" only if the zone's purpose is genuinely unclear from context.

Return ONLY valid JSON matching the required schema. No prose."""


USES_SYSTEM_PROMPT = """You are a bylaw semantic analyst. You will be given a window of text from
a municipal land-use bylaw. Identify rows in use-permission tables that name
permitted, conditional, or prohibited uses.

For each use you find, output:
  - local_term: the use name as written in the document (verbatim)
  - canonical_key: a member of the fixed controlled vocabulary

Controlled vocabulary (canonical_key MUST be one of these):
  residential_dwelling_single, residential_dwelling_multi,
  residential_dwelling_accessory, residential_dwelling_cluster,
  retail_general, retail_food, food_and_beverage,
  office_general, institutional_education, institutional_health,
  industrial_light, industrial_general, open_space_park,
  accommodation, home_occupation, short_term_rental, other

Use "other" only if no canonical_key fits.

Return ONLY valid JSON matching the required schema. No prose."""


STANDARDS_SYSTEM_PROMPT = """You are a bylaw semantic analyst. List every development standard
category heading you observe in the window (e.g. "Maximum Building Height",
"Minimum Front Yard Setback").

Normalise each to a snake_case canonical name from this controlled vocabulary:
  height, front_setback, side_setback, rear_setback,
  lot_coverage, floor_area_ratio, parking, bicycle_parking,
  landscaping, signage, amenity_space, stepback, podium_height

Return ONLY valid JSON matching the required schema. No prose."""


_ZONE_CODE_REGEX = re.compile(r"\b[A-Z]{1,4}-\d\b")
_NUMERIC_QUANTITY_REGEX = re.compile(
    r"\d+\.?\d*\s*(?:m\b|metres?\b|%|spaces?\b)", re.IGNORECASE
)
_WINDOW_SIZE = 15
_MAX_ZONE_WINDOWS = 6
_MAX_STANDARDS_WINDOWS = 4


def _file_url_to_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path


class SemanticMapperAgent:
    """Read a bylaw and produce a :class:`TaxonomyMap`."""

    def __init__(self, anthropic_client, model: str = "claude-sonnet-4-6"):
        self.client = anthropic_client
        self.model = model

    # -------------------------------------------------------------- public
    def map(self, source_doc: SourceDocument) -> TaxonomyMap:
        url = source_doc.source_url
        tmp_path: Optional[str] = None
        try:
            if url.startswith("file://"):
                pdf_path = _file_url_to_path(url)
            else:
                with httpx.Client(follow_redirects=True) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                    try:
                        tmp.write(resp.content)
                        tmp.flush()
                        tmp_path = tmp.name
                    finally:
                        tmp.close()
                pdf_path = tmp_path

            reader = PdfBootstrapReader(pdf_path)
            pages = reader.extract_pages()
            content_zone = reader.detect_content_zone(pages)

            zone_windows = self._find_zone_section_windows(pages, content_zone)
            standards_windows = self._find_standards_windows(pages, content_zone)

            zones, zone_flags, zone_conf = self._extract_zones(zone_windows)
            uses, use_flags, use_conf = self._extract_use_classes(zone_windows)
            stds, std_flags, std_conf = self._extract_standards_categories(
                standards_windows
            )

            confidences = [c for c in (zone_conf, use_conf, std_conf) if c is not None]
            base_confidence = (
                sum(confidences) / len(confidences) if confidences else 1.0
            )
            all_flags = zone_flags + use_flags + std_flags
            confidence = max(0.0, min(1.0, base_confidence - 0.05 * len(all_flags)))

            return TaxonomyMap(
                zone_designations=zones,
                use_class_map=uses,
                standards_categories=stds,
                companion_bylaws_required=[],
                confidence=confidence,
                flags=all_flags,
            )
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ----------------------------------------------- private: window selection
    def _find_zone_section_windows(
        self, pages: Dict[int, str], content_zone: Tuple[int, int]
    ) -> List[Dict[str, Any]]:
        return self._select_windows(
            pages=pages,
            content_zone=content_zone,
            score_fn=self._score_zone_density,
            max_windows=_MAX_ZONE_WINDOWS,
        )

    def _find_standards_windows(
        self, pages: Dict[int, str], content_zone: Tuple[int, int]
    ) -> List[Dict[str, Any]]:
        return self._select_windows(
            pages=pages,
            content_zone=content_zone,
            score_fn=self._score_numeric_density,
            max_windows=_MAX_STANDARDS_WINDOWS,
        )

    @staticmethod
    def _score_zone_density(text: str) -> float:
        zone_count = len(_ZONE_CODE_REGEX.findall(text))
        table_lines = sum(
            1
            for line in text.splitlines()
            if line.count("\t") >= 2 or line.count("|") >= 2
        )
        return zone_count + 0.5 * table_lines

    @staticmethod
    def _score_numeric_density(text: str) -> float:
        return float(len(_NUMERIC_QUANTITY_REGEX.findall(text)))

    @staticmethod
    def _select_windows(
        pages: Dict[int, str],
        content_zone: Tuple[int, int],
        score_fn,
        max_windows: int,
    ) -> List[Dict[str, Any]]:
        start, end = content_zone
        if end < start:
            return []
        scored = []
        for p in range(start, end + 1):
            score = score_fn(pages.get(p, ""))
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda t: (-t[0], t[1]))

        windows: List[Dict[str, Any]] = []
        used: set[int] = set()
        for _, page in scored:
            if page in used:
                continue
            half = _WINDOW_SIZE // 2
            s = max(start, page - half)
            e = min(end, s + _WINDOW_SIZE - 1)
            s = max(start, e - _WINDOW_SIZE + 1)
            if any(p in used for p in range(s, e + 1)):
                continue
            for p in range(s, e + 1):
                used.add(p)
            text = "\n".join(pages.get(p, "") for p in range(s, e + 1))
            windows.append(
                {
                    "window_index": len(windows),
                    "start_page": s,
                    "end_page": e,
                    "text": text,
                }
            )
            if len(windows) >= max_windows:
                break
        return windows

    # --------------------------------------------- private: LLM call wrapper
    def _call_tool(
        self,
        system_prompt: str,
        tool_schema: Dict[str, Any],
        tool_name: str,
        user_text: str,
    ) -> Dict[str, Any]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system_prompt,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_text}],
        )
        for block in getattr(response, "content", []) or []:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == tool_name
            ):
                return dict(block.input or {})
        raise ValueError(
            f"Semantic mapper received no tool_use block for {tool_name!r}"
        )

    @staticmethod
    def _format_user_text(window: Dict[str, Any]) -> str:
        return (
            f"Window {window.get('window_index', '?')} "
            f"(pages {window.get('start_page', '?')}-"
            f"{window.get('end_page', '?')}):\n\n{window.get('text', '')}"
        )

    # ----------------------------------------------- private: zone extraction
    def _extract_zones(
        self, windows: List[Dict[str, Any]]
    ) -> Tuple[List[ZoneDesignation], List[str], Optional[float]]:
        if not windows:
            return [], [], None
        window_results: List[Dict[str, Any]] = []
        for window in windows:
            result = self._call_tool(
                ZONES_SYSTEM_PROMPT,
                _ZONES_TOOL_SCHEMA,
                ZONES_TOOL_NAME,
                self._format_user_text(window),
            )
            window_results.append(result)
        return self._merge_zones(window_results)

    @staticmethod
    def _merge_zones(
        window_results: List[Dict[str, Any]]
    ) -> Tuple[List[ZoneDesignation], List[str], Optional[float]]:
        by_code: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        type_seen: Dict[str, "OrderedDict[str, None]"] = {}
        for result in window_results:
            for zone in result.get("zones", []) or []:
                code = zone.get("code")
                if not code:
                    continue
                canonical_type = zone.get("canonical_type") or "unknown"
                if canonical_type not in CANONICAL_ZONE_TYPES:
                    canonical_type = "unknown"
                full_name = zone.get("full_name")
                description = zone.get("description")
                if code not in by_code:
                    by_code[code] = {
                        "code": code,
                        "full_name": full_name,
                        "canonical_type": canonical_type,
                        "description": description,
                    }
                    type_seen[code] = OrderedDict()
                    type_seen[code][canonical_type] = None
                else:
                    existing = by_code[code]
                    if full_name and (
                        not existing.get("full_name")
                        or len(full_name) > len(existing["full_name"])
                    ):
                        existing["full_name"] = full_name
                    if description and not existing.get("description"):
                        existing["description"] = description
                    type_seen[code][canonical_type] = None

        flags: List[str] = []
        for code, types in type_seen.items():
            if len(types) > 1:
                flags.append(
                    f"zone canonical_type conflict on {code}: {list(types.keys())}"
                )

        designations = [
            ZoneDesignation(
                code=v["code"],
                full_name=v.get("full_name"),
                canonical_type=v["canonical_type"],
                description=v.get("description"),
            )
            for v in by_code.values()
        ]
        confidences = [
            float(r.get("confidence", 1.0)) for r in window_results
        ]
        mean_conf = sum(confidences) / len(confidences) if confidences else None
        return designations, flags, mean_conf

    # ------------------------------------------ private: use-class extraction
    def _extract_use_classes(
        self, windows: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, str], List[str], Optional[float]]:
        if not windows:
            return {}, [], None
        window_results: List[Dict[str, Any]] = []
        for window in windows:
            result = self._call_tool(
                USES_SYSTEM_PROMPT,
                _USES_TOOL_SCHEMA,
                USES_TOOL_NAME,
                self._format_user_text(window),
            )
            window_results.append(result)
        return self._merge_uses(window_results)

    @staticmethod
    def _merge_uses(
        window_results: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, str], List[str], Optional[float]]:
        mapping: Dict[str, str] = {}
        flags: List[str] = []
        for result in window_results:
            for entry in result.get("mappings", []) or []:
                local = entry.get("local_term")
                canonical = entry.get("canonical_key")
                if not local or not canonical:
                    continue
                if canonical not in CANONICAL_USE_CLASSES:
                    continue
                if local in mapping:
                    if mapping[local] != canonical:
                        flags.append(
                            f"use_class mapping conflict on '{local}': "
                            f"{mapping[local]} vs {canonical} (keeping first)"
                        )
                else:
                    mapping[local] = canonical
        confidences = [
            float(r.get("confidence", 1.0)) for r in window_results
        ]
        mean_conf = sum(confidences) / len(confidences) if confidences else None
        return mapping, flags, mean_conf

    # ------------------------------------------ private: standards extraction
    def _extract_standards_categories(
        self, windows: List[Dict[str, Any]]
    ) -> Tuple[List[str], List[str], Optional[float]]:
        if not windows:
            return [], [], None
        window_results: List[Dict[str, Any]] = []
        for window in windows:
            result = self._call_tool(
                STANDARDS_SYSTEM_PROMPT,
                _STANDARDS_TOOL_SCHEMA,
                STANDARDS_TOOL_NAME,
                self._format_user_text(window),
            )
            window_results.append(result)
        return self._merge_standards(window_results)

    @staticmethod
    def _merge_standards(
        window_results: List[Dict[str, Any]]
    ) -> Tuple[List[str], List[str], Optional[float]]:
        category_set: set[str] = set()
        for result in window_results:
            for cat in result.get("categories", []) or []:
                if cat in CANONICAL_STANDARDS:
                    category_set.add(cat)
        confidences = [
            float(r.get("confidence", 1.0)) for r in window_results
        ]
        mean_conf = sum(confidences) / len(confidences) if confidences else None
        return sorted(category_set), [], mean_conf
