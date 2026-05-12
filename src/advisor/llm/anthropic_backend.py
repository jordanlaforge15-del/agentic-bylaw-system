"""Anthropic backend for the LLM gateway.

Wraps ``anthropic.AsyncAnthropic`` and translates between the unified
gateway types and the Anthropic Messages API. The translation is mostly
a 1:1 mapping because the unified types were modelled on Anthropic's
content-block shapes; other providers will require more work.

Notes for future maintenance:
- Prompt caching: cache breakpoints are written as
  ``cache_control={"type": "ephemeral"}`` on system / messages /
  tools entries. The unified-types layer carries provider-agnostic
  ``cache`` flags (per block / tool) and ``cache_system`` /
  ``cache_tools`` flags on the request; the translation in this
  module is where they become Anthropic-specific markers. Caller
  policy (where to place breakpoints) lives in the chat layer.
- Vision: image blocks aren't in the unified types yet. When we add
  them, mirror Anthropic's ``image`` block shape.
- Web search / computer use: provider-specific tools that don't fit
  the generic ToolDefinition shape. Out of scope for v1.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    LLMRole,
    Message,
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    StreamEvent,
    TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

# Single shared cache_control payload. Anthropic's "ephemeral" tier is
# the only one the API supports today; we don't expose tier choice on
# the unified types because providers vary too much for a single knob
# to be useful.
_EPHEMERAL_CACHE: dict[str, str] = {"type": "ephemeral"}


class AnthropicGateway:
    """LLMGateway implementation backed by anthropic.AsyncAnthropic."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._client = client or AsyncAnthropic(api_key=api_key)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        params = self._to_anthropic_params(request)
        response = await self._client.messages.create(**params)
        return self._from_anthropic_response(response, request.model)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        params = self._to_anthropic_params(request)
        # The Anthropic SDK's stream() context manager yields events.
        # We translate each provider event into our unified StreamEvent
        # and re-yield, preserving order.
        async with self._client.messages.stream(**params) as stream:
            async for event in stream:
                translated = self._translate_stream_event(event, request.model)
                if translated is not None:
                    yield translated

    # -- request translation -------------------------------------------------

    def _to_anthropic_params(self, request: CompletionRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": request.model,
            "messages": [self._to_anthropic_message(m) for m in request.messages],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.system:
            # When the caller flagged the system prompt as cacheable we
            # send it as Anthropic's block-list shape so we can attach
            # cache_control; otherwise pass the plain string the SDK
            # accepts unchanged.
            if request.cache_system:
                params["system"] = [
                    {
                        "type": "text",
                        "text": request.system,
                        "cache_control": _EPHEMERAL_CACHE,
                    }
                ]
            else:
                params["system"] = request.system
        if request.tools:
            tool_dicts = [self._to_anthropic_tool(t) for t in request.tools]
            # A single cache breakpoint on the LAST tool caches the
            # whole tools array (Anthropic caches the prefix up to and
            # including the breakpoint). Per-tool ``tool.cache`` flags
            # set in _to_anthropic_tool stack on top of this if any
            # caller asks for finer control.
            if request.cache_tools and tool_dicts:
                tool_dicts[-1] = {**tool_dicts[-1], "cache_control": _EPHEMERAL_CACHE}
            params["tools"] = tool_dicts
        if request.stop_sequences:
            params["stop_sequences"] = request.stop_sequences
        if request.metadata:
            # Anthropic accepts ``metadata.user_id`` for billing /
            # abuse-tracking attribution. Other keys are ignored upstream
            # but we forward them so logs are complete.
            params["metadata"] = request.metadata
        return params

    @staticmethod
    def _to_anthropic_message(message: Message) -> dict[str, Any]:
        if isinstance(message.content, str):
            return {"role": message.role.value, "content": message.content}
        return {
            "role": message.role.value,
            "content": [
                AnthropicGateway._to_anthropic_block(b) for b in message.content
            ],
        }

    @staticmethod
    def _to_anthropic_block(block: ContentBlock) -> dict[str, Any]:
        payload: dict[str, Any]
        if isinstance(block, TextBlock):
            payload = {"type": "text", "text": block.text}
        elif isinstance(block, ToolUseBlock):
            payload = {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
        elif isinstance(block, ToolResultBlock):
            content: Any
            if isinstance(block.content, str):
                content = block.content
            else:
                content = [AnthropicGateway._to_anthropic_block(c) for c in block.content]
            payload = {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": content,
            }
            if block.is_error:
                payload["is_error"] = True
        else:
            raise TypeError(f"unknown content block type: {type(block).__name__}")
        if block.cache:
            payload["cache_control"] = _EPHEMERAL_CACHE
        return payload

    @staticmethod
    def _to_anthropic_tool(tool: ToolDefinition) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        if tool.cache:
            payload["cache_control"] = _EPHEMERAL_CACHE
        return payload

    # -- response translation -----------------------------------------------

    @staticmethod
    def _from_anthropic_response(response: Any, model: str) -> CompletionResponse:
        content = [
            AnthropicGateway._from_anthropic_block(block) for block in response.content
        ]
        usage = AnthropicGateway._from_anthropic_usage(getattr(response, "usage", None))
        return CompletionResponse(
            id=getattr(response, "id", None),
            model=getattr(response, "model", model) or model,
            role=LLMRole.ASSISTANT,
            content=content,
            stop_reason=getattr(response, "stop_reason", None),
            stop_sequence=getattr(response, "stop_sequence", None),
            usage=usage,
        )

    @staticmethod
    def _from_anthropic_block(block: Any) -> ContentBlock:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            return TextBlock(text=block.text)
        if block_type == "tool_use":
            return ToolUseBlock(
                id=block.id,
                name=block.name,
                input=dict(block.input or {}),
            )
        if block_type == "tool_result":
            return ToolResultBlock(
                tool_use_id=block.tool_use_id,
                content=block.content,
                is_error=bool(getattr(block, "is_error", False)),
            )
        raise TypeError(f"unknown anthropic block type: {block_type!r}")

    @staticmethod
    def _from_anthropic_usage(usage: Any) -> TokenUsage | None:
        if usage is None:
            return None
        return TokenUsage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(
                usage, "cache_creation_input_tokens", 0
            ) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

    # -- stream translation -------------------------------------------------

    @staticmethod
    def _translate_stream_event(event: Any, model: str) -> StreamEvent | None:
        """Convert an anthropic SDK stream event into our unified event.

        Returns None for events we deliberately drop (e.g. provider-
        specific noise that doesn't change the conversation state).
        """
        event_type = getattr(event, "type", None)
        if event_type == "message_start":
            message = getattr(event, "message", None)
            return MessageStartEvent(
                message_id=getattr(message, "id", None) if message else None,
                model=getattr(message, "model", model) if message else model,
                role=LLMRole.ASSISTANT,
            )
        if event_type == "content_block_start":
            return ContentBlockStartEvent(
                index=event.index,
                content_block=AnthropicGateway._from_anthropic_block(event.content_block),
            )
        if event_type == "content_block_delta":
            delta = event.delta
            delta_type = getattr(delta, "type", None)
            if delta_type == "text_delta":
                return ContentBlockDeltaEvent(index=event.index, text_delta=delta.text)
            if delta_type == "input_json_delta":
                return ContentBlockDeltaEvent(
                    index=event.index, input_json_delta=delta.partial_json
                )
            # Unknown delta types — drop quietly. The block_stop event
            # still fires and the caller has the final block from
            # message_stop.
            return None
        if event_type == "content_block_stop":
            return ContentBlockStopEvent(index=event.index)
        if event_type == "message_delta":
            delta = getattr(event, "delta", None)
            return MessageDeltaEvent(
                stop_reason=getattr(delta, "stop_reason", None) if delta else None,
                stop_sequence=getattr(delta, "stop_sequence", None) if delta else None,
                usage=AnthropicGateway._from_anthropic_usage(
                    getattr(event, "usage", None)
                ),
            )
        if event_type == "message_stop":
            return MessageStopEvent()
        return None
