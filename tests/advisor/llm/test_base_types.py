"""Unified LLM types: serialization, discrimination, the contract the
rest of the app depends on."""
from __future__ import annotations

import pytest

from advisor.llm import (
    CompletionRequest,
    CompletionResponse,
    LLMRole,
    Message,
    TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)


def test_message_accepts_plain_string_content():
    m = Message(role=LLMRole.USER, content="hello")
    assert m.content == "hello"


def test_message_accepts_block_list_content():
    m = Message(
        role=LLMRole.ASSISTANT,
        content=[
            TextBlock(text="thinking..."),
            ToolUseBlock(id="tu_1", name="search", input={"q": "barrington"}),
        ],
    )
    assert isinstance(m.content, list)
    assert len(m.content) == 2
    assert isinstance(m.content[0], TextBlock)
    assert isinstance(m.content[1], ToolUseBlock)


def test_tool_use_block_input_defaults_to_empty():
    b = ToolUseBlock(id="tu_1", name="search")
    assert b.input == {}


def test_tool_result_can_carry_nested_blocks():
    """A tool can return structured content (for example, a search
    result with mixed text + image blocks). The recursive content
    annotation must accept block lists."""
    block = ToolResultBlock(
        tool_use_id="tu_1",
        content=[TextBlock(text="result"), TextBlock(text="more")],
    )
    assert isinstance(block.content, list)
    assert len(block.content) == 2


def test_tool_result_is_error_defaults_false():
    b = ToolResultBlock(tool_use_id="tu_1", content="ok")
    assert b.is_error is False


def test_completion_request_round_trips():
    req = CompletionRequest(
        model="claude-opus-4-5",
        system="You are a planner.",
        messages=[Message(role=LLMRole.USER, content="hi")],
        tools=[
            ToolDefinition(
                name="search",
                description="search the bylaw",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            )
        ],
        max_tokens=1024,
        temperature=0.3,
    )
    dumped = req.model_dump()
    rebuilt = CompletionRequest.model_validate(dumped)
    assert rebuilt == req


def test_completion_response_default_role_is_assistant():
    resp = CompletionResponse(
        model="claude-opus-4-5",
        content=[TextBlock(text="answer")],
    )
    assert resp.role == LLMRole.ASSISTANT


def test_token_usage_cache_fields_default_to_zero():
    """Older provider responses don't carry cache fields. Defaults
    must let the chat backend sum fields without None handling."""
    u = TokenUsage(input_tokens=10, output_tokens=20)
    assert u.cache_creation_input_tokens == 0
    assert u.cache_read_input_tokens == 0


def test_invalid_role_rejected():
    with pytest.raises(ValueError):
        Message(role="system", content="oops")  # type: ignore[arg-type]
