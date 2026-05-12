"""Compact older conversation history before LLM submission.

Even with tool_result payloads already trimmed (see ``compact.py``),
long sessions still re-bill every byte of history on every turn —
the Messages API resends the full conversation on each iteration of
the tool-use loop. Once the model has used a tool_result to produce
an answer, the model no longer needs the full payload to understand
"what was retrieved here"; a short deterministic summary suffices.

This module produces a "compacted view" of the message list for the
LLM submission path. The persisted ``ChatMessage`` rows still carry
the full payload — compaction is view-only.

Rules
-----
- Keep the most recent N user-prompt turns intact (default N=2,
  configurable via the ``ADVISOR_CHAT_COMPACT_KEEP_RECENT`` env var
  or the ``compact_keep_recent`` field on ``ChatSession``).
- Replace the ``content`` of tool_result blocks in older turns with
  a short, deterministic one-line summary derived from the original
  payload. The ``ToolResultBlock`` itself stays — Anthropic requires
  every tool_use to have a matching tool_result, so we never drop
  the block.
- Preserve assistant text and tool_use blocks verbatim. Assistant
  text is cheap (it's the model's own output) and signals the prior
  reasoning; tool_use blocks document what was called and feed the
  summarizer the query / arguments context.

Determinism
-----------
Anthropic prompt caching keys on byte-stable prefixes, so the same
conversation state must produce the same compacted bytes every
time. The summary functions read only fields from the (already
deterministic) tool_use input and tool_result JSON; numeric values
are formatted with a fixed-precision ``:.2f``; citation lists keep
the upstream (score-ranked) order and are capped at a fixed count.
"""
from __future__ import annotations

import json
import os
from typing import Any

from advisor.llm.base import (
    ContentBlock,
    LLMRole,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)


# Tunables. Kept module-private so callers don't reach in and tweak
# them mid-session — that would re-shape the byte-stable prefix and
# pop the prompt cache.

_KEEP_RECENT_ENV = "ADVISOR_CHAT_COMPACT_KEEP_RECENT"
_DEFAULT_KEEP_RECENT = 2

# Citation paths are listed in match (score-ranked) order. Cap so a
# very wide search doesn't expand the summary unboundedly.
_MAX_CITATIONS_LISTED = 8

# Fallback truncation for tool payloads we don't know how to project
# (custom tools, malformed JSON, etc.). Keeps the worst-case bounded.
_FALLBACK_MAX_CHARS = 200


def resolve_keep_recent(field_value: int | None) -> int:
    """Resolve the ``keep_recent`` value with the precedence:
    explicit field > env var > module default.

    A non-positive resolved value clamps to 1 — keeping at least the
    in-flight turn intact is required (the model needs the live
    tool_result to answer the user's current question).
    """
    if field_value is not None:
        return max(1, int(field_value))
    raw = os.environ.get(_KEEP_RECENT_ENV)
    if raw is not None:
        try:
            return max(1, int(raw))
        except ValueError:
            return _DEFAULT_KEEP_RECENT
    return _DEFAULT_KEEP_RECENT


def compact_history_for_submission(
    messages: list[Message], *, keep_recent: int
) -> list[Message]:
    """Return a new message list with older tool_result content
    summarised. ``messages`` is left untouched.

    The most recent ``keep_recent`` user-prompt turns (boundaries
    identified by a USER message with plain-string content) stay
    intact. Anything before the cutoff has its tool_result block
    content replaced with a one-line summary; assistant text and
    tool_use blocks pass through unchanged.
    """
    boundaries = [
        i
        for i, m in enumerate(messages)
        if m.role == LLMRole.USER and isinstance(m.content, str)
    ]
    if len(boundaries) <= keep_recent:
        # Not enough completed turns to compact anything yet.
        return list(messages)

    cutoff = boundaries[-keep_recent]
    tool_uses = _index_tool_uses(messages[:cutoff])

    out: list[Message] = []
    for i, msg in enumerate(messages):
        if i >= cutoff or not isinstance(msg.content, list):
            out.append(msg)
            continue
        new_blocks: list[ContentBlock] = []
        rewritten = False
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                name, tool_input = tool_uses.get(
                    block.tool_use_id, ("(unknown_tool)", {})
                )
                summary = _summarize_tool_result(
                    name, tool_input, block.content, block.is_error
                )
                new_blocks.append(
                    ToolResultBlock(
                        tool_use_id=block.tool_use_id,
                        content=summary,
                        is_error=block.is_error,
                    )
                )
                rewritten = True
            else:
                new_blocks.append(block)
        if rewritten:
            out.append(Message(role=msg.role, content=new_blocks))
        else:
            out.append(msg)
    return out


def _index_tool_uses(
    messages: list[Message],
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Build a ``tool_use_id -> (name, input)`` lookup so a
    tool_result can be summarised with its calling context."""
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for msg in messages:
        if msg.role != LLMRole.ASSISTANT or not isinstance(msg.content, list):
            continue
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                out[block.id] = (block.name, dict(block.input))
    return out


def _summarize_tool_result(
    tool_name: str,
    tool_input: dict[str, Any],
    content: str | list[ContentBlock],
    is_error: bool,
) -> str:
    """Project a tool_result payload to a one-line text summary.

    The shape is tool-specific because the LLM-useful signal differs
    by tool: search results care about citation paths and confidence,
    a citation lookup cares about the resolved fragment, a doc list
    just needs counts. Unknown tools fall back to a length-bounded
    excerpt so the summary is always small.
    """
    text = _flatten_content_to_text(content)
    if is_error:
        return f"[{tool_name}: error: {_truncate(text, _FALLBACK_MAX_CHARS)}]"

    payload = _try_parse_json(text)
    if payload is None:
        return f"[{tool_name}: {_truncate(text, _FALLBACK_MAX_CHARS)}]"

    if tool_name == "search_bylaw_evidence":
        return _summarize_search(tool_input, payload)
    if tool_name == "lookup_citation":
        return _summarize_citation_lookup(tool_input, payload)
    if tool_name == "get_document_outline":
        return _summarize_outline(tool_input, payload)
    if tool_name == "list_documents":
        return _summarize_document_list(payload)

    # Generic fallback for tools we don't have a tailored projection
    # for — fold the JSON down to its top-level keys so the model can
    # still see something structural without paying for the body.
    if isinstance(payload, dict):
        keys = ",".join(payload.keys())
        return f"[{tool_name}: keys={keys}]"
    return f"[{tool_name}: {_truncate(text, _FALLBACK_MAX_CHARS)}]"


def _summarize_search(
    tool_input: dict[str, Any], payload: dict[str, Any]
) -> str:
    query = str(tool_input.get("query", "")).strip()
    matches = payload.get("matches") or []
    total = payload.get("total_matches")
    if not isinstance(total, int):
        total = len(matches)

    citations: list[str] = []
    scores: list[float] = []
    confidences: list[float] = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        cp = m.get("citation_path")
        if isinstance(cp, str) and cp:
            citations.append(cp)
        s = m.get("score")
        if isinstance(s, (int, float)):
            scores.append(float(s))
        for ds in m.get("linked_datasets") or []:
            if not isinstance(ds, dict):
                continue
            c = ds.get("location_confidence")
            if isinstance(c, (int, float)):
                confidences.append(float(c))

    match_word = "match" if total == 1 else "matches"
    parts = [f"retrieved: {total} {match_word} for {query!r}"]

    loc_label = _location_label(tool_input.get("location"))
    if loc_label:
        parts.append(f"location {loc_label}")

    if citations:
        head = citations[:_MAX_CITATIONS_LISTED]
        cit_str = "/".join(head)
        if len(citations) > _MAX_CITATIONS_LISTED:
            cit_str += f"/+{len(citations) - _MAX_CITATIONS_LISTED}"
        parts.append(f"citations {cit_str}")

    if confidences:
        parts.append(f"max-confidence {max(confidences):.2f}")
    if scores:
        parts.append(f"max-score {max(scores):.2f}")

    if payload.get("truncation_note"):
        parts.append("more results available")

    return "[" + ", ".join(parts) + "]"


def _summarize_citation_lookup(
    tool_input: dict[str, Any], payload: dict[str, Any]
) -> str:
    cp_in = str(tool_input.get("citation_path", "")).strip()
    cp_out = payload.get("citation_path") or cp_in or "(unknown)"
    parts = [f"lookup_citation {cp_out!r}"]
    bylaw = payload.get("bylaw_name")
    municipality = payload.get("municipality")
    if bylaw and municipality:
        parts.append(f"{municipality} / {bylaw}")
    elif bylaw:
        parts.append(str(bylaw))
    page_start = payload.get("page_start")
    page_end = payload.get("page_end")
    if isinstance(page_start, int):
        if isinstance(page_end, int) and page_end != page_start:
            parts.append(f"p.{page_start}-{page_end}")
        else:
            parts.append(f"p.{page_start}")
    text = payload.get("text")
    if isinstance(text, str):
        parts.append(f"{len(text)} chars")
    return "[" + ", ".join(parts) + "]"


def _summarize_outline(
    tool_input: dict[str, Any], payload: dict[str, Any]
) -> str:
    doc = payload.get("document") or {}
    fragments = payload.get("fragments") or []
    bits = []
    doc_id = doc.get("id") if isinstance(doc, dict) else None
    if doc_id is None:
        doc_id = tool_input.get("document_id")
    if doc_id is not None:
        bits.append(f"doc={doc_id}")
    if isinstance(doc, dict):
        bylaw = doc.get("bylaw_name")
        if bylaw:
            bits.append(str(bylaw))
    bits.append(f"{len(fragments)} fragments")
    return "[get_document_outline: " + ", ".join(bits) + "]"


def _summarize_document_list(payload: dict[str, Any]) -> str:
    docs = payload.get("documents") or []
    municipalities: dict[str, int] = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        muni = d.get("municipality")
        if isinstance(muni, str):
            municipalities[muni] = municipalities.get(muni, 0) + 1
    parts = [f"list_documents: {len(docs)} documents"]
    if municipalities:
        # Insertion order is upstream order (deterministic).
        breakdown = ", ".join(
            f"{name}={count}" for name, count in municipalities.items()
        )
        parts.append(breakdown)
    return "[" + "; ".join(parts) + "]"


def _location_label(location: Any) -> str | None:
    """Render the search ``location`` slot into a short label.

    Priority mirrors the slot's documented disambiguation: explicit
    parcel/intersection beats civic address beats named place beats
    geometry. Only the first matching shape is rendered.
    """
    if not isinstance(location, dict):
        return None
    civic = location.get("civic_number")
    street = location.get("street")
    if civic and street:
        unit = location.get("unit")
        base = f"{civic} {street}"
        return f"{base} (unit {unit})" if unit else base
    parcel = location.get("parcel_id")
    if parcel:
        return f"PID {parcel}"
    intersection = location.get("intersection_streets")
    if isinstance(intersection, list) and intersection:
        return " & ".join(str(s) for s in intersection)
    named = location.get("named_place")
    if named:
        return str(named)
    if location.get("geometry"):
        return "GeoJSON"
    return None


def _flatten_content_to_text(content: str | list[ContentBlock]) -> str:
    if isinstance(content, str):
        return content
    # Nested block lists are rare in current handlers (everything
    # round-trips via ``json.dumps`` of a dict) but defend against
    # them so the summariser doesn't crash on a future tool.
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _try_parse_json(text: str) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1].rstrip()}..."
