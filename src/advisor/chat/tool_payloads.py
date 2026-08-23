"""ABS-517: bounded capture of tool inputs and results for RCA.

``ToolLoopMetricsEvent`` used to carry only a tool call's name, error
state and latency. That is enough to cost a turn and enough to spot a
handler that keeps throwing, but not enough to explain a *wrong answer*:
when an eval case omits a provision, the transcript could not say
whether the provision never came back from retrieval or came back and
was dropped during synthesis. Those two failures live in different
layers and have opposite fixes, so guessing between them sends work to
the wrong place.

This module renders a tool invocation's input and output into a form
small enough to ship on every turn's SSE stream and persist in every
eval transcript. Three bounds do the work:

* per-value truncation on the input, so a tool called with a pasted
  submission body doesn't drag the whole body along;
* head truncation on the result, with the pre-truncation length kept
  alongside so a reader knows what was cut;
* a citation index over the *whole* result, which is what makes the
  bound safe. Head truncation alone would answer "was s.333(1)(a)
  retrieved?" only when the provision happened to rank near the top; a
  50-match search response runs to tens of kilobytes. The index is a
  list of short strings covering every match, so the question is
  answerable outright even when the excerpt stops at match three.

Both bounds are env-tunable. Setting ``ADVISOR_TOOL_RESULT_EXCERPT_CHARS``
to 0 disables result capture entirely (inputs and the citation index are
small and stay on) for an operator who wants the leaner stream back.

Privacy note: this data reaches exactly one place it did not before —
the SSE stream of the session that produced it, i.e. back to the user
whose own conversation it is. It is not logged and not shared across
sessions.
"""
from __future__ import annotations

import json
import os
from typing import Any

from advisor.llm import ContentBlock

# Head of the serialized handler output kept on each metric. 4000 chars
# is a few retrieval matches — enough to eyeball the shape of what came
# back — while keeping a 33-call turn's added payload in the low
# hundreds of KB. ``result_citations`` covers what the head misses.
_DEFAULT_RESULT_EXCERPT_CHARS = 4000

# Per-string-value cap on captured tool input. Tool arguments are
# normally short model-authored queries; the cap exists for the
# outliers (``evaluate_submission`` carries a user's project payload).
_DEFAULT_INPUT_VALUE_CHARS = 500

# Ceiling on the citation index. A search capped at
# ADVISOR_COMPACT_MAX_MATCHES=50 yields at most ~50 distinct citations,
# so this is headroom rather than a routine clip; the overflow marker
# keeps a clipped list from reading as a complete one.
_CITATION_CAP = 80

# JSON keys the citation index harvests. These are the fields
# ``advisor.chat.compact`` projects onto every retrieval match.
_CITATION_KEYS = ("citation_label", "citation_path")

_TRUNCATION_MARKER = "… [truncated]"


def _env_int(name: str, default: int) -> int:
    """Read a non-negative int from the environment, falling back on junk."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def result_excerpt_limit() -> int:
    """Max chars of handler output to keep. 0 disables result capture."""
    return _env_int(
        "ADVISOR_TOOL_RESULT_EXCERPT_CHARS", _DEFAULT_RESULT_EXCERPT_CHARS
    )


def input_value_limit() -> int:
    """Max chars per string value inside a captured tool input."""
    return _env_int("ADVISOR_TOOL_INPUT_VALUE_CHARS", _DEFAULT_INPUT_VALUE_CHARS)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATION_MARKER


def _bound_value(value: Any, limit: int) -> Any:
    """Recursively cap string leaves, leaving structure intact.

    Structure is preserved rather than flattened because the shape of
    the arguments is itself diagnostic — ``citation_path_prefix`` being
    present at all is the difference between a broad search and a
    scoped one.
    """
    if isinstance(value, str):
        return _truncate(value, limit)
    if isinstance(value, dict):
        return {k: _bound_value(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_bound_value(v, limit) for v in value]
    return value


def bound_tool_input(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Capture a tool's arguments with long string values truncated.

    Returns ``None`` only for a ``None`` input, so a consumer can tell
    "not recorded" from "called with no arguments".
    """
    if payload is None:
        return None
    limit = input_value_limit()
    return {key: _bound_value(value, limit) for key, value in payload.items()}


def _render_output(output: str | list[ContentBlock] | None) -> str | None:
    """Flatten a handler return value to one string, or None if absent."""
    if output is None:
        return None
    if isinstance(output, str):
        return output
    parts: list[str] = []
    for block in output:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
        elif hasattr(block, "model_dump"):
            parts.append(json.dumps(block.model_dump(mode="json")))
        else:
            parts.append(str(block))
    return "\n".join(parts)


def render_tool_result(
    output: str | list[ContentBlock] | None,
    error: str | None = None,
) -> tuple[str | None, int | None, bool]:
    """Return ``(excerpt, full_length, truncated)`` for a tool's output.

    A failed call reports its error text as the excerpt: for RCA the
    reason a call produced nothing is as load-bearing as the payload a
    successful one produced, and ``is_error`` on the metric already
    marks which of the two a reader is looking at.
    """
    text = error if error is not None else _render_output(output)
    if text is None:
        return None, None, False
    limit = result_excerpt_limit()
    if limit == 0:
        # Capture switched off. The length still ships — it costs
        # nothing and distinguishes "disabled" from "empty result".
        return None, len(text), False
    return _truncate(text, limit), len(text), len(text) > limit


def extract_result_citations(
    output: str | list[ContentBlock] | None,
) -> list[str]:
    """List every citation named in a JSON tool result, in result order.

    Order is preserved rather than sorted because position in a
    retrieval response *is* the rank, and "returned but ranked 47th" is
    a different diagnosis from "returned first". Duplicates are dropped
    on first sighting. Non-JSON results yield an empty list — this is a
    best-effort index, not a parser contract.
    """
    text = _render_output(output)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []

    found: list[str] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if len(found) >= _CITATION_CAP:
            return
        if isinstance(node, dict):
            for key in _CITATION_KEYS:
                value = node.get(key)
                if isinstance(value, str) and value and value not in seen:
                    seen.add(value)
                    found.append(value)
                    if len(found) >= _CITATION_CAP:
                        return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(parsed)
    if len(found) >= _CITATION_CAP:
        found.append(f"+more (capped at {_CITATION_CAP})")
    return found
