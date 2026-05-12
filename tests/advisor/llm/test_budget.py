"""Per-turn input-token budget: estimator + env-configurable default.

Verifies that the heuristic counts every payload that contributes to
the provider's input-token bill (system, tools, messages including
nested tool_result blocks), that ``default_token_budget`` reads from
the env var with safe fallbacks on bad input, and that the cache is
respected.
"""
from __future__ import annotations

import pytest

from advisor.llm import (
    CompletionRequest,
    LLMRole,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from advisor.llm.budget import (
    default_token_budget,
    estimate_request_input_tokens,
)


def _request(**overrides) -> CompletionRequest:
    base = dict(
        model="claude-opus-4-5",
        messages=[Message(role=LLMRole.USER, content="hello")],
    )
    base.update(overrides)
    return CompletionRequest(**base)


def test_estimator_counts_system_prompt():
    """The system prompt is the largest stable contributor to most
    requests; the estimator must include its length in the total."""
    short = _request(system="x" * 100)
    long_ = _request(system="x" * 400)
    assert estimate_request_input_tokens(long_) > estimate_request_input_tokens(short)


def test_estimator_counts_message_content():
    """User-message content (plain string or block list) contributes
    to the estimate. A long user prompt should dominate the count."""
    req = _request(
        messages=[Message(role=LLMRole.USER, content="x" * 4000)]
    )
    # ~4 chars/token, so 4000 chars => ~1000 tokens.
    estimate = estimate_request_input_tokens(req)
    assert 900 <= estimate <= 1100


def test_estimator_counts_tool_definitions():
    """Tool name + description + JSON-encoded input_schema all hit the
    wire on every call. The estimator factors them in so a request
    with chunky tool defs reads heavier than a tool-free one."""
    tool = ToolDefinition(
        name="search_bylaw_evidence",
        description="x" * 200,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    with_tools = _request(tools=[tool])
    without_tools = _request()
    assert (
        estimate_request_input_tokens(with_tools)
        > estimate_request_input_tokens(without_tools)
    )


def test_estimator_counts_tool_result_block_payload():
    """tool_result blocks carry the bulk of the cost on retrieval-heavy
    turns — the runaway turns on 2026-05-11 were dominated by these.
    The estimator must descend into the blocks rather than counting
    only the wrapper."""
    big_result = ToolResultBlock(
        tool_use_id="tu_1", content="x" * 4000
    )
    req = _request(
        messages=[
            Message(role=LLMRole.USER, content="q"),
            Message(role=LLMRole.ASSISTANT, content=[
                ToolUseBlock(id="tu_1", name="search", input={"q": "x"}),
            ]),
            Message(role=LLMRole.USER, content=[big_result]),
        ]
    )
    estimate = estimate_request_input_tokens(req)
    assert estimate >= 1000  # the 4k-char payload alone is ~1k tokens


def test_estimator_handles_nested_tool_result_block_list():
    """tool_result.content can itself be a list of blocks; the
    estimator must recurse into them or it under-counts."""
    nested = ToolResultBlock(
        tool_use_id="tu_1",
        content=[TextBlock(text="x" * 800)],
    )
    req = _request(
        messages=[Message(role=LLMRole.USER, content=[nested])]
    )
    estimate = estimate_request_input_tokens(req)
    assert estimate >= 200


def test_default_token_budget_falls_back_when_env_unset(monkeypatch):
    """No env var => the module's safety-net default. The default is
    deliberately not asserted by exact value here so a future tweak
    doesn't break the test, only that it's a sane positive integer."""
    monkeypatch.delenv("ADVISOR_TURN_INPUT_TOKEN_BUDGET", raising=False)
    default_token_budget.cache_clear()
    value = default_token_budget()
    assert isinstance(value, int)
    assert value > 0


def test_default_token_budget_reads_env(monkeypatch):
    """An ops override via env var is honored."""
    monkeypatch.setenv("ADVISOR_TURN_INPUT_TOKEN_BUDGET", "50000")
    default_token_budget.cache_clear()
    assert default_token_budget() == 50000


@pytest.mark.parametrize("bad", ["abc", "", "0", "-1"])
def test_default_token_budget_falls_back_on_bad_env_value(monkeypatch, bad):
    """Non-integer / non-positive env values fall back to the module
    default rather than crashing the chat layer at session-construction
    time."""
    monkeypatch.setenv("ADVISOR_TURN_INPUT_TOKEN_BUDGET", bad)
    default_token_budget.cache_clear()
    value = default_token_budget()
    assert value > 0
