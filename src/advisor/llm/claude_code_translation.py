"""Pure request/response translation for the ``claude -p`` gateway.

Why this exists: the advisor's tool loop (``advisor.llm.tool_loop``)
needs the model to emit a ``tool_use`` block and *stop*, so we can run
the handler, charge the turn, enforce the cost breakers, and loop.
``claude -p`` with MCP tools attached owns its own agentic loop and
never yields control between steps, so it cannot be dropped in as a
gateway that way. Instead we run it as a **tool-less completion
engine**: the tools are described in the prompt text, the reply shape
is pinned by a JSON Schema envelope (``--json-schema``), and the real
handlers still run on our side.

This module is the translation half of that: prompt in, envelope out.
It is deliberately free of process spawning, concurrency, filesystem and
network code — the transport that actually runs the CLI lives in a
separate module so this layer stays trivially testable and reusable.
The ban is enforced by a test, so keep the vocabulary out of this file
entirely, comments included.

Envelope contract (see :func:`build_envelope_schema`)::

    {"action": "tool_calls", "tool_calls": [{"name": ..., "input": {...}}]}
    {"action": "final_answer", "text": "..."}

``tool_calls`` is a *list* because the Messages API emits parallel
``tool_use`` blocks and ``tool_loop.py`` iterates them; collapsing to a
single call would silently serialise work the model asked to do at once.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as _JSONSchemaValidationError

from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    LLMRole,
    Message,
    TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    "ACTION_FINAL_ANSWER",
    "ACTION_TOOL_CALLS",
    "ClaudeCodeTranslationError",
    "EnvelopeValidationError",
    "ToolInputValidationError",
    "UnknownToolError",
    "build_envelope_schema",
    "envelope_to_response",
    "render_prompt",
    "usage_from_payload",
]

ACTION_TOOL_CALLS = "tool_calls"
ACTION_FINAL_ANSWER = "final_answer"


class ClaudeCodeTranslationError(Exception):
    """Base class for every failure raised by this module."""


class EnvelopeValidationError(ClaudeCodeTranslationError):
    """The model's envelope doesn't satisfy the contract.

    Always caused by model output, never by caller code, so the
    transport layer treats it as retryable: re-run the prompt rather
    than failing the turn.
    """


class UnknownToolError(EnvelopeValidationError):
    """The envelope asked for a tool that isn't on the request's menu."""

    def __init__(self, name: str, available: Sequence[str]) -> None:
        self.name = name
        self.available = list(available)
        known = ", ".join(self.available) or "<none>"
        super().__init__(
            f"envelope requested unknown tool {name!r}; available tools: {known}"
        )


class ToolInputValidationError(EnvelopeValidationError):
    """A tool call's ``input`` violates that tool's ``input_schema``."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"input for tool {name!r} failed schema validation: {reason}")


# -- schema ------------------------------------------------------------------


def build_envelope_schema(tools: list[ToolDefinition]) -> dict[str, Any]:
    """Build the JSON Schema handed to ``claude -p --json-schema``.

    The envelope is intentionally tiny: one discriminator (``action``)
    plus the two payload shapes. We do NOT inline each tool's
    ``input_schema`` into a per-tool ``oneOf`` — the CLI's structured
    output path is a forced tool call under the hood and large unions
    degrade its compliance. Per-call input validation happens on our
    side instead (:func:`envelope_to_response`), which also gives the
    transport a precise, retryable error.

    ``name`` is narrowed to an ``enum`` of the available tool names when
    the request carries any, so the common failure (a hallucinated tool)
    is prevented rather than merely detected.
    """
    name_schema: dict[str, Any] = {"type": "string"}
    if tools:
        name_schema["enum"] = [tool.name for tool in tools]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {"enum": [ACTION_TOOL_CALLS, ACTION_FINAL_ANSWER]},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "input"],
                    "properties": {
                        "name": name_schema,
                        "input": {"type": "object"},
                    },
                },
            },
            "text": {"type": "string"},
        },
    }


# -- prompt rendering --------------------------------------------------------

_PROTOCOL_INSTRUCTIONS = """\
You are running as a completion engine, not as an agent. Do not use any
of your own tools, do not read or write files, and do not attempt to
carry out the task yourself. Your entire job is to decide the single
next step and report it in the required JSON envelope.

Reply with exactly one JSON object:

- To call one or more of the tools listed above, set
  "action": "tool_calls" and provide "tool_calls" as a list of
  {"name": <tool name>, "input": <arguments object matching that tool's
  input_schema>}. List every tool call you want run in parallel; they
  are all executed before you are called again with the results.
- To answer the user, set "action": "final_answer" and put the complete
  answer in "text".

Never invent a tool name that is not listed above. Never emit an empty
"tool_calls" list — answer instead."""


def render_prompt(request: CompletionRequest) -> str:
    """Flatten a :class:`CompletionRequest` into ``claude -p`` prompt text.

    The CLI takes one string: no system-prompt slot we can trust to
    behave like the API's, no structured message list, no tools array.
    So system prompt, tool menu, protocol rules, and the full
    conversation (including prior ``tool_use`` / ``tool_result`` turns)
    are rendered into labelled sections in that order.

    Output is a pure function of ``request`` — byte-identical across
    calls — because the transport caches on it and prompt caching only
    pays off on a stable prefix. Every dict rendered here goes through
    ``json.dumps(..., sort_keys=True)`` for that reason.
    """
    sections: list[str] = []

    if request.system:
        sections.append(f"# System instructions\n\n{request.system}")

    if request.tools:
        sections.append(_render_tool_menu(request.tools))

    sections.append(f"# Response protocol\n\n{_PROTOCOL_INSTRUCTIONS}")
    sections.append(_render_conversation(request.messages))

    return "\n\n".join(sections)


def _render_tool_menu(tools: Sequence[ToolDefinition]) -> str:
    parts = [
        (
            "# Available tools\n\n"
            "You cannot execute these yourself. Request them through the\n"
            "envelope below and the caller will run them and report back."
        )
    ]
    for tool in tools:
        parts.append(
            f"## {tool.name}\n\n"
            f"{tool.description}\n\n"
            f"input_schema:\n{_dumps(tool.input_schema)}"
        )
    return "\n\n".join(parts)


def _render_conversation(messages: Sequence[Message]) -> str:
    parts = ["# Conversation"]
    for message in messages:
        label = "User" if message.role == LLMRole.USER else "Assistant"
        parts.append(f"## {label}\n\n{_render_content(message.content)}")
    return "\n\n".join(parts)


def _render_content(content: str | list[ContentBlock]) -> str:
    if isinstance(content, str):
        return content
    return "\n\n".join(_render_block(block) for block in content)


def _render_block(block: ContentBlock) -> str:
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ToolUseBlock):
        # Rendered in envelope shape so the transcript the model reads
        # back is the same shape it is asked to produce.
        return (
            f"[tool_call id={block.id}]\n"
            f"{_dumps({'name': block.name, 'input': block.input})}"
        )
    if isinstance(block, ToolResultBlock):
        status = "error" if block.is_error else "ok"
        return (
            f"[tool_result id={block.tool_use_id} status={status}]\n"
            f"{_render_content(block.content)}"
        )
    raise TypeError(f"unknown content block type: {type(block).__name__}")


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)


# -- response translation ----------------------------------------------------


def envelope_to_response(
    structured_output: Mapping[str, Any],
    payload: Mapping[str, Any] | None,
    model: str,
    *,
    tools: Sequence[ToolDefinition] = (),
) -> CompletionResponse:
    """Translate a validated envelope + CLI payload into a response.

    ``structured_output`` is the envelope the model produced;
    ``payload`` is the CLI's own result JSON (usage, session id, and —
    see below — a ``stop_reason`` we must ignore).

    **``stop_reason`` is derived from ``action``, never read from the
    payload.** Probe 2026-08-09: the CLI reported
    ``stop_reason: "tool_use"`` for a semantically final answer, because
    ``--json-schema`` is implemented internally as a forced tool call.
    Passing it through would make ``tool_loop`` wait forever for tool
    calls that a ``final_answer`` envelope never contains.

    ``tools`` is the menu the envelope is checked against; a call naming
    a tool that isn't on it, or carrying input that violates that tool's
    ``input_schema``, raises :class:`EnvelopeValidationError`.
    """
    if not isinstance(structured_output, Mapping):
        raise EnvelopeValidationError(
            f"envelope must be a JSON object, got {type(structured_output).__name__}"
        )

    action = structured_output.get("action")
    if action == ACTION_FINAL_ANSWER:
        text = _coerce_text(structured_output.get("text"))
        if not text.strip():
            # Same failure mode as an empty tool_calls list: the loop
            # would terminate on it and hand the user a blank answer.
            # Better to let the transport retry the turn.
            raise EnvelopeValidationError(
                "envelope action is 'final_answer' but 'text' is missing or empty"
            )
        content: list[ContentBlock] = [TextBlock(text=text)]
        stop_reason = "end_turn"
    elif action == ACTION_TOOL_CALLS:
        content = list(
            _to_tool_use_blocks(structured_output, payload, tools=tools)
        )
        preamble = _coerce_text(structured_output.get("text"))
        if preamble:
            # Mirrors the API shape, where a tool-use turn may open with
            # a text block explaining the plan.
            content.insert(0, TextBlock(text=preamble))
        stop_reason = "tool_use"
    else:
        raise EnvelopeValidationError(
            f"envelope action must be one of "
            f"{ACTION_TOOL_CALLS!r} / {ACTION_FINAL_ANSWER!r}, got {action!r}"
        )

    payload_map: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    return CompletionResponse(
        id=_response_id(payload_map),
        model=model,
        role=LLMRole.ASSISTANT,
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=usage_from_payload(payload_map),
    )


def usage_from_payload(payload: Mapping[str, Any] | None) -> TokenUsage | None:
    """Map the CLI payload's ``usage`` object onto :class:`TokenUsage`.

    1:1 — the CLI reports the same four field names the gateway types
    use. Absent or malformed usage yields ``None`` rather than a zeroed
    record, so cost attribution can tell "nothing reported" apart from
    "genuinely free".
    """
    if not isinstance(payload, Mapping):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    return TokenUsage(
        input_tokens=_coerce_int(usage.get("input_tokens")),
        output_tokens=_coerce_int(usage.get("output_tokens")),
        cache_creation_input_tokens=_coerce_int(
            usage.get("cache_creation_input_tokens")
        ),
        cache_read_input_tokens=_coerce_int(usage.get("cache_read_input_tokens")),
    )


def _to_tool_use_blocks(
    envelope: Mapping[str, Any],
    payload: Mapping[str, Any] | None,
    *,
    tools: Sequence[ToolDefinition],
) -> list[ToolUseBlock]:
    raw_calls = envelope.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise EnvelopeValidationError(
            "envelope action is 'tool_calls' but 'tool_calls' is missing or empty"
        )

    by_name = {tool.name: tool for tool in tools}
    correlation = _correlation_token(envelope, payload)
    blocks: list[ToolUseBlock] = []
    for index, raw in enumerate(raw_calls):
        if not isinstance(raw, Mapping):
            raise EnvelopeValidationError(
                f"tool_calls[{index}] must be an object, got {type(raw).__name__}"
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise EnvelopeValidationError(
                f"tool_calls[{index}] is missing a 'name' string"
            )
        tool = by_name.get(name)
        if tool is None:
            raise UnknownToolError(name, list(by_name))
        tool_input = raw.get("input")
        if not isinstance(tool_input, Mapping):
            raise ToolInputValidationError(
                name, f"'input' must be an object, got {type(tool_input).__name__}"
            )
        tool_input = dict(tool_input)
        _validate_tool_input(tool, tool_input)
        blocks.append(
            ToolUseBlock(
                id=f"toolu_cc_{correlation}_{index}",
                name=name,
                input=tool_input,
            )
        )
    return blocks


def _validate_tool_input(tool: ToolDefinition, tool_input: dict[str, Any]) -> None:
    """Check one call's arguments against the tool's declared schema.

    The API validates ``tool_use`` inputs for us; the CLI envelope path
    has no such guarantee, so a bad payload would otherwise reach the
    handler as a ``TypeError`` mid-turn. Catching it here keeps the
    failure retryable.
    """
    try:
        Draft202012Validator(tool.input_schema).validate(tool_input)
    except _JSONSchemaValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path)
        reason = f"{path}: {exc.message}" if path else exc.message
        raise ToolInputValidationError(tool.name, reason) from exc
    except Exception as exc:  # malformed input_schema — a caller bug
        raise ClaudeCodeTranslationError(
            f"tool {tool.name!r} has an invalid input_schema: {exc}"
        ) from exc


def _correlation_token(
    envelope: Mapping[str, Any], payload: Mapping[str, Any] | None
) -> str:
    """Stable per-response id fragment.

    Uniqueness *within* a response comes from the call index; this
    fragment keeps ids from colliding across iterations of the same
    tool loop, which would make a transcript ambiguous. Derived rather
    than random so the same envelope always renders the same ids —
    replaying a captured payload reproduces the transcript byte for
    byte.
    """
    if isinstance(payload, Mapping):
        for key in ("uuid", "session_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value.replace("-", "")[:16]
    digest = hashlib.sha256(
        json.dumps(envelope, sort_keys=True, default=str).encode("utf-8")
    )
    return digest.hexdigest()[:16]


def _response_id(payload: Mapping[str, Any]) -> str | None:
    for key in ("uuid", "session_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _coerce_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _coerce_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
