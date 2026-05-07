"""Tool-use orchestration.

The Anthropic Messages API expects callers to run a small loop: send
messages + tools, get a response, if the response contains
``tool_use`` blocks, execute the tools, append a USER message
containing matching ``tool_result`` blocks, and call the API again.
The loop terminates when the model returns a response without
tool_use (i.e. ``stop_reason='end_turn'`` or similar).

Doing this loop by hand at every chat-backend call site is tedious
and error-prone (forgetting tool_use_id correlation; mishandling
exceptions; not enforcing iteration limits). ``run_tool_loop``
encapsulates it.

A ``ToolHandler`` is a callable the loop invokes when the LLM asks
for a tool by name. The handler's signature is
``async def handler(input: dict) -> str | list[ContentBlock]``. The
return value becomes the ``content`` of the matching tool_result.
Handlers can raise; the loop converts an exception into a
``ToolResultBlock(is_error=True)`` so the LLM can see the failure
and recover, rather than the whole conversation aborting.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    LLMGateway,
    LLMRole,
    Message,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


# Async handler shape. The loop calls the handler with the LLM's
# input dict; the handler returns the tool's output as either a
# plain string (rendered as a single text block by the gateway) or
# a list of content blocks (for tools whose output is structured).
ToolHandler = Callable[[dict[str, Any]], Awaitable[str | list[ContentBlock]]]


@dataclass
class ToolLoopResult:
    """Outcome of a tool-use loop run.

    ``final_response`` is the last assistant turn (no further
    tool_use blocks). ``conversation`` is the complete message list,
    including all intermediate tool_use / tool_result exchanges, so
    the caller can persist it.

    ``tool_calls`` records each tool invocation in order — useful for
    audit, billing, and observability.
    """

    final_response: CompletionResponse
    conversation: list[Message]
    tool_calls: list["ToolInvocation"] = field(default_factory=list)
    iterations: int = 0
    # Sum of every per-iteration ``CompletionResponse.usage`` seen
    # during the loop. Set to ``None`` when the gateway didn't return
    # usage on any iteration (e.g. older MockGateway responses). The
    # aggregate matters because tool-use turns make multiple model
    # calls; the final response's ``usage`` only covers the last one.
    total_usage: TokenUsage | None = None


@dataclass
class ToolInvocation:
    """One tool call within the loop. Records what the LLM asked for
    and what the handler returned (or raised)."""

    tool_use_id: str
    tool_name: str
    input: dict[str, Any]
    output: str | list[ContentBlock] | None
    error: str | None = None


class ToolLoopError(Exception):
    """Raised when the loop hits its iteration limit without the LLM
    issuing a non-tool-use response. Suggests an infinite-loop bug in
    the model or handlers that can't make progress."""


async def run_tool_loop(
    gateway: LLMGateway,
    *,
    request: CompletionRequest,
    handlers: dict[str, ToolHandler],
    max_iterations: int = 10,
) -> ToolLoopResult:
    """Drive a Messages API conversation through any number of tool-use
    rounds and return when the LLM stops asking for tools.

    ``request.tools`` should declare every tool the LLM is permitted
    to call; ``handlers`` should supply an implementation for each
    name. A tool the LLM asks for that has no handler is reported as
    an error to the LLM (``is_error=True`` tool_result) so it can
    recover or apologise.

    ``max_iterations`` is a safety cap. Most chats settle in 1–3
    rounds; anything higher than 5 in practice usually means a
    handler is misbehaving.
    """
    conversation = list(request.messages)
    tool_calls: list[ToolInvocation] = []
    total_usage: TokenUsage | None = None

    for iteration in range(1, max_iterations + 1):
        current_request = request.model_copy(
            update={"messages": list(conversation)}
        )
        response = await gateway.complete(current_request)
        total_usage = _accumulate_usage(total_usage, response.usage)

        # Always append the assistant turn to the conversation, even
        # when it contains tool_use blocks — the next request needs
        # the assistant turn AND the user-side tool_result turn that
        # follows.
        conversation.append(
            Message(role=LLMRole.ASSISTANT, content=list(response.content))
        )

        tool_use_blocks = [b for b in response.content if isinstance(b, ToolUseBlock)]
        if not tool_use_blocks:
            return ToolLoopResult(
                final_response=response,
                conversation=conversation,
                tool_calls=tool_calls,
                iterations=iteration,
                total_usage=total_usage,
            )

        result_blocks: list[ContentBlock] = []
        for use_block in tool_use_blocks:
            invocation = await _run_one_handler(handlers, use_block)
            tool_calls.append(invocation)
            if invocation.error is not None:
                result_blocks.append(
                    ToolResultBlock(
                        tool_use_id=use_block.id,
                        content=invocation.error,
                        is_error=True,
                    )
                )
            else:
                result_blocks.append(
                    ToolResultBlock(
                        tool_use_id=use_block.id,
                        content=invocation.output or "",
                    )
                )

        conversation.append(Message(role=LLMRole.USER, content=result_blocks))

    raise ToolLoopError(
        f"tool-use loop exceeded max_iterations={max_iterations}; "
        f"likely a model or handler that can't terminate"
    )


async def _run_one_handler(
    handlers: dict[str, ToolHandler], block: ToolUseBlock
) -> ToolInvocation:
    """Execute one tool_use block. Captures handler errors instead of
    letting them propagate — the LLM gets to see the error string and
    can decide whether to retry, apologise, or work around it."""
    handler = handlers.get(block.name)
    if handler is None:
        message = (
            f"No handler registered for tool {block.name!r}. "
            "This is a server-side configuration bug."
        )
        logger.error(message)
        return ToolInvocation(
            tool_use_id=block.id,
            tool_name=block.name,
            input=dict(block.input),
            output=None,
            error=message,
        )
    try:
        output = await handler(block.input)
    except Exception as exc:  # noqa: BLE001 — surface the error to the LLM
        logger.exception("tool %r handler raised", block.name)
        return ToolInvocation(
            tool_use_id=block.id,
            tool_name=block.name,
            input=dict(block.input),
            output=None,
            error=f"{type(exc).__name__}: {exc}",
        )
    return ToolInvocation(
        tool_use_id=block.id,
        tool_name=block.name,
        input=dict(block.input),
        output=output,
    )


def _accumulate_usage(
    total: TokenUsage | None, delta: TokenUsage | None
) -> TokenUsage | None:
    """Sum two optional ``TokenUsage`` snapshots.

    Returns ``None`` only when both inputs are ``None`` (i.e. the
    gateway has never reported usage). Once we've seen any usage we
    keep accumulating into a real ``TokenUsage`` so a later
    ``None`` doesn't blank the running total.
    """
    if delta is None:
        return total
    if total is None:
        return delta.model_copy()
    return TokenUsage(
        input_tokens=total.input_tokens + delta.input_tokens,
        output_tokens=total.output_tokens + delta.output_tokens,
        cache_creation_input_tokens=(
            total.cache_creation_input_tokens + delta.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            total.cache_read_input_tokens + delta.cache_read_input_tokens
        ),
    )


def text_of(response: CompletionResponse) -> str:
    """Convenience: extract concatenated text from the response's text
    blocks. Handy for the simplest chat callers that don't care about
    structured content."""
    return "".join(b.text for b in response.content if isinstance(b, TextBlock))
