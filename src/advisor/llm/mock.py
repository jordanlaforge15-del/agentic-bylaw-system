"""Deterministic mock LLM gateway for tests.

Lets the chat backend, tool-loop, and streaming code be tested
end-to-end without an API key, network, or non-determinism. Two modes:

- ``MockGateway(scripted=[response, response, ...])``: each call to
  ``complete`` (or ``stream``) returns the next scripted response.
  Lets a test simulate a multi-turn conversation including tool-use.

- ``MockGateway(callable_=fn)``: ``fn(request) -> CompletionResponse``
  for tests that need to inspect the request and shape the reply.

The streaming implementation chunks each scripted response into events
that match the real Anthropic stream order: message_start ->
content_block_start/delta*/stop per block -> message_delta ->
message_stop. That way streaming-aware code is exercised the same
shape as production.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    LLMRole,
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    StreamEvent,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)


class MockGateway:
    """Mock LLMGateway implementation."""

    name = "mock"

    def __init__(
        self,
        *,
        scripted: list[CompletionResponse] | None = None,
        callable_: Callable[[CompletionRequest], CompletionResponse] | None = None,
        default_usage: TokenUsage | None = None,
    ) -> None:
        if scripted is None and callable_ is None:
            raise ValueError("MockGateway requires either scripted or callable_")
        if scripted is not None and callable_ is not None:
            raise ValueError("MockGateway: pass scripted OR callable_, not both")
        self._scripted = list(scripted or [])
        self._callable = callable_
        self._default_usage = default_usage or TokenUsage(
            input_tokens=10, output_tokens=20
        )
        self._calls: list[CompletionRequest] = []

    @property
    def calls(self) -> list[CompletionRequest]:
        """Every request the mock has received, in order. Tests assert
        on the structure of these to verify the chat backend built the
        right payload."""
        return list(self._calls)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self._calls.append(request)
        return self._next_response(request)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        self._calls.append(request)
        response = self._next_response(request)
        async for event in self._stream_from_response(response):
            yield event

    def _next_response(self, request: CompletionRequest) -> CompletionResponse:
        if self._callable is not None:
            response = self._callable(request)
            return self._fill_defaults(response, request)
        if not self._scripted:
            raise AssertionError(
                "MockGateway exhausted scripted responses; either pass more "
                "or use callable_ for dynamic shaping"
            )
        response = self._scripted.pop(0)
        return self._fill_defaults(response, request)

    def _fill_defaults(
        self, response: CompletionResponse, request: CompletionRequest
    ) -> CompletionResponse:
        # Reuse the scripted response but make sure model/usage match
        # the request shape so tests don't have to fill them every time.
        if response.model == "":
            response = response.model_copy(update={"model": request.model})
        if response.usage is None:
            response = response.model_copy(update={"usage": self._default_usage})
        if response.stop_reason is None:
            stop_reason = "tool_use" if any(
                isinstance(b, ToolUseBlock) for b in response.content
            ) else "end_turn"
            response = response.model_copy(update={"stop_reason": stop_reason})
        return response

    @staticmethod
    async def _stream_from_response(
        response: CompletionResponse,
    ) -> AsyncIterator[StreamEvent]:
        yield MessageStartEvent(
            message_id=response.id, model=response.model, role=LLMRole.ASSISTANT
        )
        for index, block in enumerate(response.content):
            yield ContentBlockStartEvent(index=index, content_block=block)
            if isinstance(block, TextBlock) and block.text:
                # Chunk text into a few deltas so streaming consumers
                # see multiple events per block (closer to real life
                # where the SDK emits dozens of small deltas).
                chunks = _chunk_text(block.text, max_chunks=4)
                for chunk in chunks:
                    yield ContentBlockDeltaEvent(index=index, text_delta=chunk)
            elif isinstance(block, ToolUseBlock):
                yield ContentBlockDeltaEvent(
                    index=index, input_json_delta=json.dumps(block.input)
                )
            yield ContentBlockStopEvent(index=index)
        yield MessageDeltaEvent(
            stop_reason=response.stop_reason,
            stop_sequence=response.stop_sequence,
            usage=response.usage,
        )
        yield MessageStopEvent()


def _chunk_text(text: str, *, max_chunks: int) -> list[str]:
    if not text:
        return []
    if len(text) <= max_chunks:
        return list(text)
    size = (len(text) + max_chunks - 1) // max_chunks
    return [text[i : i + size] for i in range(0, len(text), size)]


def text_response(text: str, **kwargs: Any) -> CompletionResponse:
    """Tiny helper for tests: build a single-text-block response."""
    return CompletionResponse(
        model=kwargs.pop("model", ""),
        content=[TextBlock(text=text)],
        **kwargs,
    )


def tool_use_response(
    *,
    tool_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    preamble: str | None = None,
    **kwargs: Any,
) -> CompletionResponse:
    """Build a response that emits a tool_use block. Optional preamble
    text precedes it (Anthropic models often think out loud before
    calling a tool).
    """
    blocks: list[Any] = []
    if preamble:
        blocks.append(TextBlock(text=preamble))
    blocks.append(ToolUseBlock(id=tool_id, name=tool_name, input=tool_input))
    return CompletionResponse(
        model=kwargs.pop("model", ""),
        content=blocks,
        stop_reason=kwargs.pop("stop_reason", "tool_use"),
        **kwargs,
    )
