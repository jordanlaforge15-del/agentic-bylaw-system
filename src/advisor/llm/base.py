"""Unified LLM gateway types.

The shapes mirror Anthropic's Messages API content-block model
because (a) we use Anthropic as the first backend and the round-trip
is lossless, and (b) the content-block model generalises well to any
modern provider that supports tool use plus mixed text/structured
output. Other providers' simpler shapes (e.g. plain string responses)
collapse to a single TextBlock.

Why a custom Protocol rather than just using anthropic types
directly: lock-in. The chat backend depends only on these types; the
moment we want to add OpenAI, Bedrock, or a local model, only the
backend implementation changes — no callers move.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class LLMRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class TextBlock(BaseModel):
    """Plain text content. The most common block type."""

    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """The LLM is asking the caller to run a tool.

    ``id`` correlates this request with the matching ``ToolResultBlock``
    that comes back from the caller. ``input`` is the tool's argument
    payload, conforming to the tool's declared input_schema.
    """

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    """Caller's response to a ``ToolUseBlock``.

    Attached to a USER-role message and sent back to the LLM. ``content``
    is the tool's return value rendered for the LLM (string or further
    blocks). ``is_error`` lets the LLM reason about partial failures
    without us hiding them in the prompt.
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list["ContentBlock"]
    is_error: bool = False


# Discriminated union of every content block kind. Adding a new block
# type means adding it here; the Protocol's complete() and stream()
# methods then propagate it through the layers without change.
ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


class Message(BaseModel):
    """A single turn in the conversation.

    ``content`` accepts either a plain string (auto-wrapped to
    ``[TextBlock(text=...)]``) or an explicit block list. The block list
    is required for assistant messages that include tool_use, and for
    user messages that include tool_result.
    """

    role: LLMRole
    content: str | list[ContentBlock]


class ToolDefinition(BaseModel):
    """A tool the LLM is permitted to invoke.

    ``input_schema`` is a JSON Schema dict. The LLM uses it to validate
    its own ``tool_use`` payloads before emitting them, and the gateway
    surfaces it in whatever format the provider expects.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


class CompletionRequest(BaseModel):
    """Single completion or stream invocation.

    ``system`` is the system prompt (persona). ``messages`` is the
    conversation history. ``tools`` is empty for plain Q&A or populated
    when running the tool-use loop. ``cache_breakpoint_count`` lets the
    backend insert prompt-cache markers per provider conventions.
    """

    model: str
    system: str | None = None
    messages: list[Message]
    tools: list[ToolDefinition] = Field(default_factory=list)
    max_tokens: int = 4096
    temperature: float = 0.7
    stop_sequences: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class CompletionResponse(BaseModel):
    """Non-streaming completion result.

    ``stop_reason`` follows Anthropic's vocabulary
    (``end_turn`` / ``tool_use`` / ``max_tokens`` / ``stop_sequence``)
    so the tool-use loop can dispatch on it without inspecting content.
    """

    id: str | None = None
    model: str
    role: LLMRole = LLMRole.ASSISTANT
    content: list[ContentBlock]
    stop_reason: str | None = None
    stop_sequence: str | None = None
    usage: TokenUsage | None = None


# Streaming events. Each event mirrors a stage of the unfolding
# response. We carry the full event taxonomy (rather than collapsing
# to "delta strings") because tool-use callers need to see
# content_block_start to know when a tool_use block begins.


class MessageStartEvent(BaseModel):
    type: Literal["message_start"] = "message_start"
    message_id: str | None = None
    model: str
    role: LLMRole = LLMRole.ASSISTANT


class ContentBlockStartEvent(BaseModel):
    type: Literal["content_block_start"] = "content_block_start"
    index: int
    content_block: ContentBlock


class ContentBlockDeltaEvent(BaseModel):
    """An incremental update to the block at ``index``.

    ``text_delta`` is set for text-block updates; ``input_json_delta``
    is the partial JSON for tool_use blocks (provider-specific format
    that the caller should accumulate and parse on stop).
    """

    type: Literal["content_block_delta"] = "content_block_delta"
    index: int
    text_delta: str | None = None
    input_json_delta: str | None = None


class ContentBlockStopEvent(BaseModel):
    type: Literal["content_block_stop"] = "content_block_stop"
    index: int


class MessageDeltaEvent(BaseModel):
    type: Literal["message_delta"] = "message_delta"
    stop_reason: str | None = None
    stop_sequence: str | None = None
    usage: TokenUsage | None = None


class MessageStopEvent(BaseModel):
    type: Literal["message_stop"] = "message_stop"


StreamEvent = (
    MessageStartEvent
    | ContentBlockStartEvent
    | ContentBlockDeltaEvent
    | ContentBlockStopEvent
    | MessageDeltaEvent
    | MessageStopEvent
)


@runtime_checkable
class LLMGateway(Protocol):
    """Provider-agnostic LLM interface.

    Concrete implementations live in ``advisor.llm.anthropic_backend``,
    ``advisor.llm.mock``, and (eventually) other providers. Test code
    uses ``MockGateway``; production wires the right backend via
    settings + a small factory in ``advisor.llm.registry``.
    """

    name: str  # provider id, for logging / billing attribution

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Issue a non-streaming completion."""

    async def stream(
        self, request: CompletionRequest
    ) -> AsyncIterator[StreamEvent]:
        """Issue a streaming completion. Yields events in order until
        ``MessageStopEvent``. Implementations must close their underlying
        provider resources on iteration completion or generator close.
        """


# Forward ref resolution for the recursive ToolResultBlock.content.
ToolResultBlock.model_rebuild()
