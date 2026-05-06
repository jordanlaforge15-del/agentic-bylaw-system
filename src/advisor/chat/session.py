"""Per-conversation chat session.

A ``ChatSession`` carries everything needed to run one user's chat:
the system prompt, the message history, the tool registry, and the
target model. It does NOT hold the LLM gateway — that's passed in per
``send_user_message`` call so a single session can survive across
gateway swaps (test -> prod, model upgrade, etc.) without losing
state.

Streaming v1 design
-------------------
``send_user_message`` runs ``run_tool_loop`` (which is non-streaming
internally — Anthropic's tool-use API requires a complete assistant
turn to know whether tools were requested) and only AFTER the loop
finishes does it synthesise stream events from the final response.
This is intentional:

* The tool loop architecture is fundamentally non-streaming — partial
  tool_use blocks have no semantic meaning, and we cannot dispatch a
  handler until the full input is parsed.
* Synthetic streaming gives the frontend the same event shape it will
  see in v2, so the SSE plumbing can be built and tested now.
* True incremental token streaming during tool use (driving
  ``gateway.stream()`` directly inside the loop) is a larger refactor
  in ``advisor.llm`` and is deliberately deferred.

The synthetic stream uses ``MockGateway._stream_from_response``, which
emits the same event sequence (message_start, per-block start/delta/
stop, message_delta, message_stop) the real Anthropic stream does.
Frontends that consume this don't need to know it's synthetic.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from advisor.llm import (
    CompletionRequest,
    CompletionResponse,
    LLMGateway,
    LLMRole,
    Message,
    StreamEvent,
    TextBlock,
    ToolDefinition,
)
from advisor.llm.mock import MockGateway
from advisor.llm.tool_loop import ToolHandler, run_tool_loop


@dataclass
class ChatSession:
    """One user's conversation state.

    ``user_id`` is a placeholder for workstream 3's auth integration —
    in v1 it's just the value of the ``X-User-Id`` header.

    ``messages`` is the full Anthropic-shape conversation including
    every intermediate tool_use / tool_result round-trip. Persisting
    just this list across requests is enough to resume any session.
    """

    session_id: str
    user_id: str
    system_prompt: str
    messages: list[Message] = field(default_factory=list)
    tool_defs: list[ToolDefinition] = field(default_factory=list)
    tool_handlers: dict[str, ToolHandler] = field(default_factory=dict)
    model: str = "claude-opus-4-5"

    async def send_user_message_blocking(
        self, gateway: LLMGateway, text: str
    ) -> CompletionResponse:
        """Run one user turn and return the final assistant response.

        Use this in tests or non-streaming integrations. Mutates
        ``self.messages`` to include the user message and the full
        intermediate conversation (tool_use / tool_result blocks
        included) plus the final assistant turn.
        """
        # Append the user turn FIRST so the loop sees it as the
        # latest entry. We keep mutation here (not deferred to after
        # the loop) so a concurrent reader inspecting the session
        # mid-call sees the user's input rather than a stale state.
        self.messages.append(Message(role=LLMRole.USER, content=text))

        request = CompletionRequest(
            model=self.model,
            system=self.system_prompt,
            messages=list(self.messages),
            tools=list(self.tool_defs),
        )
        result = await run_tool_loop(
            gateway,
            request=request,
            handlers=self.tool_handlers,
        )

        # Replace our message list with the full conversation the loop
        # produced — that's the only way to capture intermediate
        # tool_use / tool_result turns. ``result.conversation``
        # already includes the original user message because we
        # appended it before building the request.
        self.messages = list(result.conversation)
        return result.final_response

    async def send_user_message(
        self, gateway: LLMGateway, text: str
    ) -> AsyncIterator[StreamEvent]:
        """Run one user turn and yield a synthetic stream of events.

        v1 streams synthetically AFTER the tool loop completes; true
        incremental streaming during tool use will land when we drive
        ``gateway.stream()`` directly inside the loop, which is not
        yet implemented. See module docstring for rationale.
        """
        final_response = await self.send_user_message_blocking(gateway, text)
        # Reuse the mock's chunker so the event sequence exactly
        # matches what a real Anthropic stream would emit. The
        # frontend can't tell the difference from a real stream.
        async for event in MockGateway._stream_from_response(final_response):
            yield event


def empty_assistant_message() -> Message:
    """Convenience: an empty assistant message for placeholder slots.

    Used by tests that need to seed a session with a known shape
    without driving a full round-trip through the gateway. Kept here
    rather than in ``advisor.llm`` because it's a chat-layer concern.
    """
    return Message(role=LLMRole.ASSISTANT, content=[TextBlock(text="")])
