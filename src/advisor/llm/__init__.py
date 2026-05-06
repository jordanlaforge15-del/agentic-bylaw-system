"""Provider-agnostic LLM gateway.

The chat backend talks to ``LLMGateway``; concrete implementations
adapt the unified types to/from a provider's wire format. Mirrors
Anthropic's content-block model closely so adding OpenAI / Bedrock /
local models later doesn't lose information.

Public surface:
- ``LLMGateway``: protocol implementations conform to.
- ``CompletionRequest`` / ``CompletionResponse``: unified shapes.
- ``Message`` / ``ContentBlock`` / ``TextBlock`` / ``ToolUseBlock`` /
  ``ToolResultBlock``: building blocks.
- ``ToolDefinition``: tool spec the LLM sees.
- ``StreamEvent`` and its concrete subtypes: streaming protocol.
- ``ToolHandler`` / ``run_tool_loop``: caller-side orchestration of
  the tool-use cycle (LLM emits tool_use, we execute, send result back).

Implementations live in sibling modules:
- ``advisor.llm.anthropic_backend.AnthropicGateway``
- ``advisor.llm.mock.MockGateway``
"""
from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    LLMGateway,
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
from advisor.llm.tool_loop import ToolHandler, ToolLoopResult, run_tool_loop

__all__ = [
    "CompletionRequest",
    "CompletionResponse",
    "ContentBlock",
    "ContentBlockDeltaEvent",
    "ContentBlockStartEvent",
    "ContentBlockStopEvent",
    "LLMGateway",
    "LLMRole",
    "Message",
    "MessageDeltaEvent",
    "MessageStartEvent",
    "MessageStopEvent",
    "StreamEvent",
    "TextBlock",
    "TokenUsage",
    "ToolDefinition",
    "ToolHandler",
    "ToolLoopResult",
    "ToolResultBlock",
    "ToolUseBlock",
    "run_tool_loop",
]
