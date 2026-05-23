"""Semantic Mapper agent for Phase 2.

Reads a bylaw document, locates the windows most likely to contain zone
definitions, use-permission tables, and development-standards sections, and
calls Anthropic with strict tool-use schemas to extract a :class:`TaxonomyMap`.

The canonical vocabulary (zone types, use classes, standards categories)
lives in ``abs-learning/fixtures/canonical_vocabulary_v1.json`` and is loaded
at module import time. The agent constructor accepts a ``vocabulary_path``
override so a city can be re-run against a newer vocabulary without a code
release. The version is persisted on every produced :class:`TaxonomyMap`.

The tool schemas accept any string for ``canonical_key`` / ``categories``;
out-of-vocabulary proposals are routed to
:attr:`TaxonomyMap.vocabulary_extension_candidates` and
:attr:`TaxonomyMap.proposed_categories` instead of being silently dropped.
This is the diagnostic loop that lets us see what a new city *wanted* to say
before we clamp it.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from bootstrap.pdf_reader import PdfBootstrapReader
from manifest.models import (
    SourceDocument,
    TaxonomyMap,
    VocabularyExtensionCandidate,
    ZoneDesignation,
)


DEFAULT_VOCABULARY_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures"
    / "canonical_vocabulary_v1.json"
)


def load_vocabulary(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the canonical vocabulary fixture.

    Returns a dict with keys ``version``, ``zone_types``, ``use_classes``,
    ``standards``. ``path`` defaults to the bundled v1 fixture.
    """
    resolved = Path(path) if path is not None else DEFAULT_VOCABULARY_PATH
    with resolved.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("version", "zone_types", "use_classes", "standards"):
        if key not in data:
            raise ValueError(
                f"Vocabulary fixture {resolved} missing required key {key!r}"
            )
    return data


_DEFAULT_VOCABULARY = load_vocabulary()

# Convenience re-exports of the bundled v1 vocabulary for code that doesn't
# construct a :class:`SemanticMapperAgent`. The authoritative per-run source
# is whatever the agent was constructed with — these constants are the floor.
CANONICAL_ZONE_TYPES: Tuple[str, ...] = tuple(_DEFAULT_VOCABULARY["zone_types"])
CANONICAL_USE_CLASSES: Tuple[str, ...] = tuple(_DEFAULT_VOCABULARY["use_classes"])
CANONICAL_STANDARDS: Tuple[str, ...] = tuple(_DEFAULT_VOCABULARY["standards"])
DEFAULT_VOCABULARY_VERSION: str = str(_DEFAULT_VOCABULARY["version"])


ZONES_TOOL_NAME = "report_zone_designations"
USES_TOOL_NAME = "report_use_class_mappings"
STANDARDS_TOOL_NAME = "report_standards_categories"


def _zones_tool_schema(zone_types: Tuple[str, ...]) -> Dict[str, Any]:
    return {
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
                                "enum": list(zone_types),
                            },
                            "description": {"type": ["string", "null"]},
                        },
                    },
                },
                "confidence": {"type": "number"},
            },
        },
    }


def _uses_tool_schema() -> Dict[str, Any]:
    # No enum constraint on canonical_key: the LLM is told to prefer the
    # controlled vocabulary in the system prompt, but is free to propose
    # snake_case keys outside it. Out-of-vocab proposals are routed to
    # vocabulary_extension_candidates in the merge step.
    return {
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
                            "canonical_key": {"type": "string"},
                        },
                    },
                },
                "confidence": {"type": "number"},
            },
        },
    }


def _standards_tool_schema() -> Dict[str, Any]:
    # Same treatment as the uses schema — accept any snake_case string and
    # route out-of-vocab proposals to proposed_categories in the merge step.
    return {
        "name": STANDARDS_TOOL_NAME,
        "description": "Report normalised development standard categories.",
        "input_schema": {
            "type": "object",
            "required": ["categories", "confidence"],
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "confidence": {"type": "number"},
            },
        },
    }


def _uses_system_prompt(use_classes: Tuple[str, ...]) -> str:
    vocab = ", ".join(use_classes)
    return f"""You are a bylaw semantic analyst. You will be given a window of text from
a municipal land-use bylaw. Identify rows in use-permission tables that name
permitted, conditional, or prohibited uses.

For each use you find, output:
  - local_term: the use name as written in the document (verbatim)
  - canonical_key: PREFER a member of the controlled vocabulary below; if
    nothing fits, propose your own snake_case key (e.g.
    "cannabis_retail", "data_centre", "rooming_house"). Do NOT force a use
    into "other" when a more specific local concept exists — the operator
    will review proposals to extend the vocabulary.

Controlled vocabulary (prefer these when they fit):
  {vocab}

Return ONLY valid JSON matching the required schema. No prose."""


def _standards_system_prompt(standards: Tuple[str, ...]) -> str:
    vocab = ", ".join(standards)
    return f"""You are a bylaw semantic analyst. List every development standard
category heading you observe in the window (e.g. "Maximum Building Height",
"Minimum Front Yard Setback").

Normalise each to a snake_case canonical name. PREFER the controlled
vocabulary below when a heading fits; if nothing fits, propose your own
snake_case name (e.g. "green_roof_coverage", "tree_canopy",
"ev_parking_minimum"). Do NOT drop a heading because it isn't in the
vocabulary — proposals will be reviewed to extend it.

Controlled vocabulary (prefer these when they fit):
  {vocab}

Return ONLY valid JSON matching the required schema. No prose."""


def _zones_system_prompt(zone_types: Tuple[str, ...]) -> str:
    vocab = ", ".join(zone_types)
    return f"""You are a bylaw semantic analyst. You will be given a window of text from a
municipal land-use bylaw. Identify every zone code and, where stated, its
full name and purpose.

Zone codes follow this pattern: 1–4 uppercase letters, a hyphen, one digit
(e.g. ER-1, UC-2, CEN-1, CDD-2). Some documents also use codes without
hyphens (e.g. DD, DH, CLI) — include these too if they are clearly zone names.

For each zone, determine its canonical_type from this fixed list:
  {vocab}

Use "unknown" only if the zone's purpose is genuinely unclear from context.

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


def _pick_canonical_type_winner(counts: "OrderedDict[str, int]") -> str:
    """Pick the winning canonical_type for a zone code by majority vote.

    Tie-break rules (in order):
      1. Higher vote count wins outright.
      2. On a tie, prefer any non-``unknown`` type over ``unknown``.
      3. Among still-tied non-``unknown`` types, pick alphabetically for
         determinism (insertion order is intentionally NOT used here —
         which window saw a code first is an arbitrary artifact of
         windowing, not a quality signal).
    """
    max_count = max(counts.values())
    leaders = [t for t, c in counts.items() if c == max_count]
    if len(leaders) == 1:
        return leaders[0]
    non_unknown = [t for t in leaders if t != "unknown"]
    pool = non_unknown if non_unknown else leaders
    return sorted(pool)[0]


class SemanticMapperAgent:
    """Read a bylaw and produce a :class:`TaxonomyMap`."""

    def __init__(
        self,
        anthropic_client,
        model: str = "claude-sonnet-4-6",
        vocabulary_path: Optional[Path] = None,
    ):
        self.client = anthropic_client
        self.model = model
        vocab = load_vocabulary(vocabulary_path) if vocabulary_path else _DEFAULT_VOCABULARY
        self.vocabulary_version: str = str(vocab["version"])
        self.zone_types: Tuple[str, ...] = tuple(vocab["zone_types"])
        self.use_classes: Tuple[str, ...] = tuple(vocab["use_classes"])
        self.standards: Tuple[str, ...] = tuple(vocab["standards"])
        self._zones_tool_schema = _zones_tool_schema(self.zone_types)
        self._uses_tool_schema = _uses_tool_schema()
        self._standards_tool_schema = _standards_tool_schema()
        self._zones_system_prompt = _zones_system_prompt(self.zone_types)
        self._uses_system_prompt = _uses_system_prompt(self.use_classes)
        self._standards_system_prompt = _standards_system_prompt(self.standards)

    # -------------------------------------------------------------- public
    def map(
        self,
        source_doc: SourceDocument,
        companions: Optional[List[str]] = None,
    ) -> TaxonomyMap:
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

            zones, zone_flags, zone_conf, zone_agreement = self._extract_zones(
                zone_windows
            )
            (
                uses,
                use_candidates,
                use_flags,
                use_conf,
                use_agreement,
            ) = self._extract_use_classes(zone_windows)
            stds, proposed_stds, std_flags, std_conf = self._extract_standards_categories(
                standards_windows
            )

            llm_confidences = [
                c for c in (zone_conf, use_conf, std_conf) if c is not None
            ]
            base_confidence = (
                sum(llm_confidences) / len(llm_confidences)
                if llm_confidences
                else 1.0
            )
            # Cross-window agreement is a stronger signal than the LLM's
            # per-window self-reported confidence — a flat 1.0 per window
            # is meaningless if four windows disagreed about a zone's
            # canonical_type. Combine the two by averaging.
            agreement_signals = [
                a for a in (zone_agreement, use_agreement) if a is not None
            ]
            mean_agreement = (
                sum(agreement_signals) / len(agreement_signals)
                if agreement_signals
                else 1.0
            )

            all_flags = list(zone_flags) + list(use_flags) + list(std_flags)
            if use_candidates:
                terms = sorted({c.proposed_canonical_key for c in use_candidates})
                preview = ", ".join(terms[:5])
                more = f" (+{len(terms) - 5} more)" if len(terms) > 5 else ""
                all_flags.append(
                    f"vocabulary_extension_candidates: {len(use_candidates)} "
                    f"use-class proposal(s) outside v{self.vocabulary_version} vocab "
                    f"[{preview}{more}]"
                )
            if proposed_stds:
                preview = ", ".join(proposed_stds[:5])
                more = (
                    f" (+{len(proposed_stds) - 5} more)" if len(proposed_stds) > 5 else ""
                )
                all_flags.append(
                    f"proposed_standards_categories: {len(proposed_stds)} "
                    f"standard(s) outside v{self.vocabulary_version} vocab "
                    f"[{preview}{more}]"
                )
            confidence = max(0.0, min(1.0, (base_confidence + mean_agreement) / 2))

            return TaxonomyMap(
                zone_designations=zones,
                use_class_map=uses,
                standards_categories=stds,
                companion_bylaws_required=list(companions or []),
                confidence=confidence,
                flags=all_flags,
                vocabulary_version=self.vocabulary_version,
                vocabulary_extension_candidates=use_candidates,
                proposed_categories=proposed_stds,
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
                self._zones_system_prompt,
                self._zones_tool_schema,
                ZONES_TOOL_NAME,
                self._format_user_text(window),
            )
            window_results.append(result)
        return self._merge_zones(window_results, self.zone_types)

    @staticmethod
    def _merge_zones(
        window_results: List[Dict[str, Any]],
        zone_types: Tuple[str, ...],
    ) -> Tuple[List[ZoneDesignation], List[str], Optional[float], Optional[float]]:
        """Merge zone observations across windows with majority-vote conflict resolution.

        When the same code is reported with conflicting ``canonical_type``
        values across windows, pick the winner by majority vote; tie-break
        by preferring non-``unknown`` types, then alphabetical for
        determinism. The conflict is still recorded as a flag so reviewers
        can audit, but the resolution is deterministic and surfaced in
        the returned ``ZoneDesignation``.

        Returns ``(designations, flags, mean_llm_confidence,
        mean_per_code_agreement)``. The agreement signal is the mean of
        ``max_votes / total_votes`` across all codes that received any
        votes; 1.0 means every code was unanimous across windows.
        """
        by_code: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        # type_counts[code][canonical_type] = number of windows that voted
        # for that type on that code. Insertion order on the inner dict is
        # preserved so that alphabetical-tie-break behavior is also
        # deterministic in the rare all-tie-all-unknown case.
        type_counts: Dict[str, "OrderedDict[str, int]"] = {}
        for result in window_results:
            for zone in result.get("zones", []) or []:
                code = zone.get("code")
                if not code:
                    continue
                canonical_type = zone.get("canonical_type") or "unknown"
                if canonical_type not in zone_types:
                    canonical_type = "unknown"
                full_name = zone.get("full_name")
                description = zone.get("description")
                if code not in by_code:
                    by_code[code] = {
                        "code": code,
                        "full_name": full_name,
                        "description": description,
                    }
                    type_counts[code] = OrderedDict()
                else:
                    existing = by_code[code]
                    if full_name and (
                        not existing.get("full_name")
                        or len(full_name) > len(existing["full_name"])
                    ):
                        existing["full_name"] = full_name
                    if description and not existing.get("description"):
                        existing["description"] = description
                type_counts[code][canonical_type] = (
                    type_counts[code].get(canonical_type, 0) + 1
                )

        flags: List[str] = []
        agreement_rates: List[float] = []
        for code, counts in type_counts.items():
            total = sum(counts.values())
            max_count = max(counts.values()) if counts else 0
            agreement_rates.append(max_count / total if total else 1.0)
            if len(counts) > 1:
                winner = _pick_canonical_type_winner(counts)
                flags.append(
                    f"zone canonical_type conflict on {code}: "
                    f"{list(counts.keys())} (winner: {winner!r})"
                )
            else:
                winner = next(iter(counts))
            by_code[code]["canonical_type"] = winner

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
        mean_agreement = (
            sum(agreement_rates) / len(agreement_rates)
            if agreement_rates
            else None
        )
        return designations, flags, mean_conf, mean_agreement

    # ------------------------------------------ private: use-class extraction
    def _extract_use_classes(
        self, windows: List[Dict[str, Any]]
    ) -> Tuple[
        Dict[str, str],
        List[VocabularyExtensionCandidate],
        List[str],
        Optional[float],
    ]:
        if not windows:
            return {}, [], [], None
        window_results: List[Tuple[Dict[str, Any], int]] = []
        for window in windows:
            result = self._call_tool(
                self._uses_system_prompt,
                self._uses_tool_schema,
                USES_TOOL_NAME,
                self._format_user_text(window),
            )
            window_results.append((result, int(window.get("window_index", 0))))
        return self._merge_uses(window_results, self.use_classes)

    @staticmethod
    def _merge_uses(
        window_results: List[Tuple[Dict[str, Any], int]],
        use_classes: Tuple[str, ...],
    ) -> Tuple[
        Dict[str, str],
        List[VocabularyExtensionCandidate],
        List[str],
        Optional[float],
        Optional[float],
    ]:
        """Merge use-class mappings with majority-vote conflict resolution.

        When the same local term is mapped to different canonical keys
        across windows, pick the winner by majority vote; tie-break by
        insertion order (first-seen wins on a tie). Returns
        ``(mapping, candidates, flags, mean_llm_confidence,
        mean_per_term_agreement)``.
        """
        flags: List[str] = []
        candidates: "OrderedDict[Tuple[str, str], VocabularyExtensionCandidate]" = (
            OrderedDict()
        )
        confidences: List[float] = []
        # vote_counts[local_term][canonical_key] = number of windows that
        # mapped local→canonical. Insertion order preserved on the inner
        # dict so a tie breaks toward the first-seen mapping.
        vote_counts: "OrderedDict[str, OrderedDict[str, int]]" = OrderedDict()
        for result, window_index in window_results:
            confidences.append(float(result.get("confidence", 1.0)))
            for entry in result.get("mappings", []) or []:
                local = entry.get("local_term")
                canonical = entry.get("canonical_key")
                if not local or not canonical:
                    continue
                if canonical not in use_classes:
                    key = (local, canonical)
                    if key not in candidates:
                        candidates[key] = VocabularyExtensionCandidate(
                            local_term=local,
                            proposed_canonical_key=canonical,
                            was_in_vocabulary=False,
                            source_window=window_index,
                        )
                    continue
                if local not in vote_counts:
                    vote_counts[local] = OrderedDict()
                vote_counts[local][canonical] = (
                    vote_counts[local].get(canonical, 0) + 1
                )

        mapping: Dict[str, str] = {}
        agreement_rates: List[float] = []
        for local, counts in vote_counts.items():
            total = sum(counts.values())
            max_count = max(counts.values()) if counts else 0
            agreement_rates.append(max_count / total if total else 1.0)
            if len(counts) > 1:
                # Tie-break by insertion order (first-seen on ties).
                winner = max(counts.items(), key=lambda kv: kv[1])[0]
                flags.append(
                    f"use_class mapping conflict on '{local}': "
                    f"{list(counts.keys())} (winner: {winner!r})"
                )
            else:
                winner = next(iter(counts))
            mapping[local] = winner

        mean_conf = sum(confidences) / len(confidences) if confidences else None
        mean_agreement = (
            sum(agreement_rates) / len(agreement_rates)
            if agreement_rates
            else None
        )
        return (
            mapping,
            list(candidates.values()),
            flags,
            mean_conf,
            mean_agreement,
        )

    # ------------------------------------------ private: standards extraction
    def _extract_standards_categories(
        self, windows: List[Dict[str, Any]]
    ) -> Tuple[List[str], List[str], List[str], Optional[float]]:
        if not windows:
            return [], [], [], None
        window_results: List[Dict[str, Any]] = []
        for window in windows:
            result = self._call_tool(
                self._standards_system_prompt,
                self._standards_tool_schema,
                STANDARDS_TOOL_NAME,
                self._format_user_text(window),
            )
            window_results.append(result)
        return self._merge_standards(window_results, self.standards)

    @staticmethod
    def _merge_standards(
        window_results: List[Dict[str, Any]],
        standards: Tuple[str, ...],
    ) -> Tuple[List[str], List[str], List[str], Optional[float]]:
        category_set: set[str] = set()
        proposed: "OrderedDict[str, None]" = OrderedDict()
        for result in window_results:
            for cat in result.get("categories", []) or []:
                if not isinstance(cat, str) or not cat:
                    continue
                if cat in standards:
                    category_set.add(cat)
                else:
                    proposed.setdefault(cat, None)
        confidences = [
            float(r.get("confidence", 1.0)) for r in window_results
        ]
        mean_conf = sum(confidences) / len(confidences) if confidences else None
        return sorted(category_set), list(proposed.keys()), [], mean_conf
