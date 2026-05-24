"""Structure Analyst agent for Phase 1.

Uses the Anthropic SDK to extract a citation hierarchy from sampled windows
of bylaw text and merges per-window results into a single
:class:`ParserConfig`.
"""
from __future__ import annotations

import os
import tempfile
from collections import Counter
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from bootstrap.pdf_reader import PdfBootstrapReader
from manifest.models import (
    CitationLevel,
    CitationScheme,
    ParserConfig,
    SourceDocument,
)


SYSTEM_PROMPT = """You are a bylaw document structure analyst. You will be given a window of
text extracted from a municipal land-use bylaw PDF. Your task is to identify
the citation hierarchy — the numbered/lettered scheme that uniquely identifies
every provision.

Focus only on the citation structure. Do not summarise content.

For each level of the hierarchy you find, produce:
  - level: a short name (part, section, subsection, clause, subclause)
  - pattern: a Python regex that matches the citation label at the START of a
    line (use ^ anchor). The pattern must match ONLY the label characters
    themselves, not the content that follows.
    HARD RULES for pattern construction:
      * The pattern must successfully match the bare label string in
        isolation (e.g. "Part I", "1", "(a)", "(i)") with re.match.
        Validate this mentally before emitting the pattern.
      * Do NOT add lookaheads, lookbehinds, or trailing required groups
        that demand whitespace, punctuation, or any other context after
        the label. Bare labels with no trailing characters MUST match.
      * Use \\b only as a non-consuming end-of-label boundary; never
        require a literal whitespace character after the label.
      * Examples of acceptable patterns:
          part      -> ^Part [IVXLC]+
          section   -> ^\\d{1,3}\\b
          clause    -> ^\\([a-z]{1,2}\\)
          subclause -> ^\\([ivxlc]+\\)
  - label_format: a human-readable template showing the label shape,
    e.g. "Part {roman}" or "({alpha})"

Also identify:
  - Any lines that appear to be schedule or appendix headings (different
    numbering, often alphabetic like "Schedule A" or "Appendix 1")
  - Any table caption patterns
  - A representative full citation example assembled from the levels you found
  - The separator character used to join levels in cross-references

If you are uncertain about a pattern, include it with a flag rather than
omitting it. A flagged uncertain pattern is more useful than a missing one.

The flags list is for genuine uncertainty or ambiguity that downstream
parsers must know about (e.g. "two competing patterns observed for
section level"). Do NOT add flags for benign observations such as page
footers, table-of-contents leaders, diagram captions, or commentary on
chapter/part naming. Keep the flags list short — at most 2 entries
unless the document is genuinely pathological.

Return ONLY valid JSON matching the required schema. No prose."""


_TOOL_NAME = "report_citation_hierarchy"

_TOOL_SCHEMA: Dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Report the citation hierarchy and related structural patterns "
        "extracted from a window of bylaw text."
    ),
    "input_schema": {
        "type": "object",
        "required": [
            "hierarchy",
            "schedule_patterns",
            "citation_example",
            "separator",
            "flags",
        ],
        "properties": {
            "hierarchy": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["level", "pattern", "label_format"],
                    "properties": {
                        "level": {"type": "string"},
                        "pattern": {"type": "string"},
                        "label_format": {"type": "string"},
                    },
                },
            },
            "schedule_patterns": {
                "type": "array",
                "items": {"type": "string"},
            },
            "table_caption_pattern": {"type": ["string", "null"]},
            "citation_example": {"type": "string"},
            "separator": {"type": "string"},
            "flags": {
                "type": "array",
                "items": {"type": "string"},
            },
            "confidence": {"type": "number"},
        },
    },
}


def _file_url_to_path(url: str) -> str:
    """Translate a ``file://`` URL into a local filesystem path."""
    parsed = urlparse(url)
    # netloc may be empty (file:///abs/path) or "localhost".
    return parsed.path


class StructureAnalystAgent:
    """Analyse a bylaw PDF and emit a :class:`ParserConfig`."""

    def __init__(self, anthropic_client, model: str = "claude-sonnet-4-6"):
        self.client = anthropic_client
        self.model = model

    # ------------------------------------------------------------------ public
    def analyse(
        self,
        source_doc: SourceDocument,
        jurisdiction_code: Optional[str] = None,
    ) -> ParserConfig:
        """Run the full window-sampling + LLM extraction + merge pipeline."""
        url = source_doc.source_url

        tmp_path: Optional[str] = None
        try:
            if url.startswith("file://"):
                pdf_path = _file_url_to_path(url)
            else:
                # Download to a temp file. Keep it open across the read so the
                # tempfile is cleaned up afterwards.
                with httpx.Client(follow_redirects=True) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    suffix = ".pdf"
                    tmp = tempfile.NamedTemporaryFile(
                        suffix=suffix, delete=False
                    )
                    try:
                        tmp.write(resp.content)
                        tmp.flush()
                        tmp_path = tmp.name
                    finally:
                        tmp.close()
                pdf_path = tmp_path

            reader = PdfBootstrapReader(pdf_path)
            pages = reader.extract_pages()
            zone = reader.detect_content_zone(pages)
            windows = reader.sample_windows(
                pages, zone, n_windows=4, window_size=25
            )

            window_results: List[Dict[str, Any]] = []
            for window in windows:
                window_results.append(self._extract_patterns_from_window(window))

            effective_jurisdiction = jurisdiction_code or "unknown"
            return self._merge_patterns(
                window_results, jurisdiction_code=effective_jurisdiction
            )
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ----------------------------------------------------------------- private
    def _extract_patterns_from_window(self, window: dict) -> dict:
        """Send one window to Claude and return the parsed tool input dict."""
        text = window["text"]
        user_message = (
            f"Window {window.get('window_index', '?')} "
            f"(pages {window.get('start_page', '?')}-"
            f"{window.get('end_page', '?')}):\n\n{text}"
        )

        # System prompt + tool schema are static across every window call;
        # mark them cacheable so the per-window LLM cost drops to roughly
        # (per-window text) + (cached-prefix at 10% read cost) instead of
        # paying for the full ~3-5K prefix on every call. See ABS-94.
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            tools=[{**_TOOL_SCHEMA, "cache_control": {"type": "ephemeral"}}],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_message}],
        )

        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "tool_use" and getattr(
                block, "name", None
            ) == _TOOL_NAME:
                payload = block.input
                # Normalise the dict so callers can rely on the keys existing.
                return {
                    "hierarchy": payload.get("hierarchy", []),
                    "schedule_patterns": payload.get("schedule_patterns", []),
                    "table_caption_pattern": payload.get(
                        "table_caption_pattern"
                    ),
                    "citation_example": payload.get("citation_example", ""),
                    "separator": payload.get("separator", ""),
                    "flags": payload.get("flags", []),
                    "confidence": payload.get("confidence", 1.0),
                }

        raise ValueError(
            "Structure analyst received no tool_use block from Claude; "
            "free-text responses are not accepted."
        )

    def _merge_patterns(
        self,
        window_results: List[Dict[str, Any]],
        jurisdiction_code: str = "unknown",
    ) -> ParserConfig:
        """Merge per-window extraction dicts into a single ParserConfig."""
        # ---- hierarchy: per-level majority vote --------------------------
        level_patterns: Dict[str, Counter] = {}
        level_label_formats: Dict[str, Counter] = {}
        level_first_seen: List[str] = []  # preserve insertion order

        for result in window_results:
            for entry in result.get("hierarchy", []) or []:
                level = entry.get("level")
                pattern = entry.get("pattern")
                label_format = entry.get("label_format", "")
                if not level or pattern is None:
                    continue
                if level not in level_patterns:
                    level_patterns[level] = Counter()
                    level_label_formats[level] = Counter()
                    level_first_seen.append(level)
                level_patterns[level][pattern] += 1
                level_label_formats[level][label_format] += 1

        chosen_hierarchy: List[Dict[str, str]] = []
        conflict_flags: List[str] = []
        num_conflicts = 0
        for level in level_first_seen:
            patterns_counter = level_patterns[level]
            best_pattern, _ = patterns_counter.most_common(1)[0]
            best_label_format, _ = level_label_formats[level].most_common(1)[0]
            chosen_hierarchy.append(
                {
                    "level": level,
                    "pattern": best_pattern,
                    "label_format": best_label_format,
                }
            )
            distinct_patterns = sorted(set(patterns_counter.keys()))
            if len(distinct_patterns) > 1:
                num_conflicts += 1
                conflict_flags.append(
                    f"hierarchy conflict on level '{level}': {distinct_patterns}"
                )

        # ---- schedule_patterns: deduped union ---------------------------
        schedule_set = set()
        for result in window_results:
            for sp in result.get("schedule_patterns", []) or []:
                schedule_set.add(sp)
        schedule_patterns = sorted(schedule_set)

        # ---- table_caption_pattern: most common non-null ----------------
        caption_counter: Counter = Counter()
        for result in window_results:
            value = result.get("table_caption_pattern")
            if value:
                caption_counter[value] += 1
        if caption_counter:
            table_caption_pattern: Optional[str] = caption_counter.most_common(1)[0][0]
        else:
            table_caption_pattern = None

        # ---- citation_example / separator: mode, first-occurrence tie ---
        citation_example = self._mode_with_first_tiebreak(
            result.get("citation_example", "") for result in window_results
        )
        separator = self._mode_with_first_tiebreak(
            result.get("separator", "") for result in window_results
        )

        # ---- flags: order-preserving union with conflict flags ----------
        merged_flags: List[str] = []
        seen_flags = set()
        for result in window_results:
            for flag in result.get("flags", []) or []:
                if flag not in seen_flags:
                    seen_flags.add(flag)
                    merged_flags.append(flag)
        for flag in conflict_flags:
            if flag not in seen_flags:
                seen_flags.add(flag)
                merged_flags.append(flag)

        # ---- confidence -------------------------------------------------
        if window_results:
            confidences = [
                float(result.get("confidence", 1.0)) for result in window_results
            ]
            mean_confidence = sum(confidences) / len(confidences)
        else:
            mean_confidence = 1.0
        confidence = mean_confidence - 0.05 * num_conflicts
        confidence = max(0.0, min(1.0, confidence))

        # ---- ParserConfig assembly --------------------------------------
        citation_scheme = CitationScheme(
            full_citation_example=citation_example,
            separator=separator,
            hierarchy=[CitationLevel(**entry) for entry in chosen_hierarchy],
        )
        return ParserConfig(
            parser_version=f"docling:{jurisdiction_code.lower()}",
            citation_scheme=citation_scheme,
            schedule_patterns=schedule_patterns,
            table_caption_pattern=table_caption_pattern,
            confidence=confidence,
            flags=merged_flags,
        )

    @staticmethod
    def _mode_with_first_tiebreak(values) -> str:
        """Return the most common value; on ties, the first to appear."""
        counts: Counter = Counter()
        order: List[str] = []
        for value in values:
            if value is None:
                continue
            if value not in counts:
                order.append(value)
            counts[value] += 1
        if not counts:
            return ""
        max_count = max(counts.values())
        for value in order:
            if counts[value] == max_count:
                return value
        return ""
