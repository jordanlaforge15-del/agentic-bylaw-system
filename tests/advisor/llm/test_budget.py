"""Per-turn input-token budget: estimator + env-configurable default.

Verifies that the heuristic counts every payload that contributes to
the provider's input-token bill (system, tools, messages including
nested tool_result blocks), that ``default_token_budget`` reads from
the env var with safe fallbacks on bad input, and that cache-read
regions are discounted correctly (ABS-291).
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
    default_cumulative_token_budget,
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


# ---------------------------------------------------------------------------
# ABS-305: cumulative per-turn budget default
# ---------------------------------------------------------------------------


def test_default_cumulative_budget_falls_back_when_env_unset(monkeypatch):
    """No env var => a sane positive integer (the module safety-net
    default). Exact value isn't pinned so a future tweak doesn't break
    the test."""
    monkeypatch.delenv("ADVISOR_TURN_CUMULATIVE_TOKEN_BUDGET", raising=False)
    default_cumulative_token_budget.cache_clear()
    value = default_cumulative_token_budget()
    assert isinstance(value, int)
    assert value > 0


def test_default_cumulative_budget_is_a_distinct_env_var(monkeypatch):
    """The cumulative breaker reads its OWN env var; overriding the
    per-request budget must not move the cumulative default (they bound
    different things and must stay independently tunable)."""
    monkeypatch.setenv("ADVISOR_TURN_INPUT_TOKEN_BUDGET", "12345")
    monkeypatch.delenv("ADVISOR_TURN_CUMULATIVE_TOKEN_BUDGET", raising=False)
    default_token_budget.cache_clear()
    default_cumulative_token_budget.cache_clear()
    assert default_token_budget() == 12345
    assert default_cumulative_token_budget() != 12345


def test_default_cumulative_budget_reads_env(monkeypatch):
    """An ops override via env var is honored."""
    monkeypatch.setenv("ADVISOR_TURN_CUMULATIVE_TOKEN_BUDGET", "200000")
    default_cumulative_token_budget.cache_clear()
    assert default_cumulative_token_budget() == 200000


@pytest.mark.parametrize("bad", ["abc", "", "0", "-1"])
def test_default_cumulative_budget_falls_back_on_bad_env_value(monkeypatch, bad):
    """Non-integer / non-positive env values fall back to the module
    default rather than crashing the chat layer at session-construction
    time."""
    monkeypatch.setenv("ADVISOR_TURN_CUMULATIVE_TOKEN_BUDGET", bad)
    default_cumulative_token_budget.cache_clear()
    value = default_cumulative_token_budget()
    assert value > 0


# ---------------------------------------------------------------------------
# ABS-291: cache-read discounting
# ---------------------------------------------------------------------------


def test_estimator_no_cache_flags_unchanged():
    """With no cache flags and no cache=True blocks the estimate is
    identical to pre-ABS-291 behaviour (backward compatibility)."""
    req = _request(
        system="s" * 400,
        messages=[Message(role=LLMRole.USER, content="q" * 400)],
    )
    # 800 chars total / 4 chars-per-token = 200 tokens
    assert estimate_request_input_tokens(req) == 200


def test_estimator_discounts_cached_system_prompt():
    """cache_system=True weights the system prompt at _CACHE_READ_WEIGHT
    (~10%), drastically lowering the billed-equivalent estimate."""
    req_uncached = _request(system="x" * 4000, cache_system=False)
    req_cached = _request(system="x" * 4000, cache_system=True)
    uncached = estimate_request_input_tokens(req_uncached)
    cached = estimate_request_input_tokens(req_cached)
    # Cached estimate must be at most 15% of the uncached estimate.
    assert cached <= uncached * 0.15


def test_estimator_discounts_cached_tools():
    """cache_tools=True weights all tool definitions at _CACHE_READ_WEIGHT."""
    tool = ToolDefinition(
        name="search_bylaw_evidence",
        description="x" * 200,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    req_uncached = _request(tools=[tool], cache_tools=False)
    req_cached = _request(tools=[tool], cache_tools=True)
    uncached = estimate_request_input_tokens(req_uncached)
    cached = estimate_request_input_tokens(req_cached)
    assert cached <= uncached * 0.15


def test_estimator_discounts_messages_before_rolling_breakpoint():
    """Messages before the last cache=True block are weighted at the
    cache-read rate.  This covers the ABS-285 rolling intra-loop
    breakpoint: prior tool_result turns have been written to cache and
    are served back as reads on the current iteration.

    Scenario mirrors iteration 3 of the tool loop:
      msg[0] user question
      msg[1] assistant tool_use
      msg[2] user tool_result (large, previously cached)
      msg[3] assistant tool_use
      msg[4] user tool_result, cache=True on last block  ← rolling marker
    """
    prior_result = ToolResultBlock(tool_use_id="tu_1", content="x" * 4000)
    new_result = ToolResultBlock(
        tool_use_id="tu_2", content="x" * 1000, cache=True
    )
    messages_cached = [
        Message(role=LLMRole.USER, content="original question"),
        Message(
            role=LLMRole.ASSISTANT,
            content=[ToolUseBlock(id="tu_1", name="search", input={"q": "x"})],
        ),
        Message(role=LLMRole.USER, content=[prior_result]),
        Message(
            role=LLMRole.ASSISTANT,
            content=[ToolUseBlock(id="tu_2", name="search", input={"q": "y"})],
        ),
        Message(role=LLMRole.USER, content=[new_result]),
    ]
    messages_uncached = [
        Message(role=LLMRole.USER, content="original question"),
        Message(
            role=LLMRole.ASSISTANT,
            content=[ToolUseBlock(id="tu_1", name="search", input={"q": "x"})],
        ),
        # Same 4000-char result but NO cache flag
        Message(
            role=LLMRole.USER,
            content=[ToolResultBlock(tool_use_id="tu_1", content="x" * 4000)],
        ),
        Message(
            role=LLMRole.ASSISTANT,
            content=[ToolUseBlock(id="tu_2", name="search", input={"q": "y"})],
        ),
        Message(
            role=LLMRole.USER,
            content=[ToolResultBlock(tool_use_id="tu_2", content="x" * 1000)],
        ),
    ]
    est_cached = estimate_request_input_tokens(
        _request(messages=messages_cached)
    )
    est_uncached = estimate_request_input_tokens(
        _request(messages=messages_uncached)
    )
    # The 4000-char prior result (~1000 tokens) should be discounted to
    # ~100 billed-equivalent tokens, so the cached estimate is materially
    # lower than the uncached one.
    assert est_cached < est_uncached * 0.5


def test_estimator_breakpoint_message_is_full_price():
    """The message that contains the cache=True block (the current
    iteration's tool_result turn — a cache WRITE, not a read) is charged
    at full price, not at the cache-read rate."""
    # One large message with cache=True — it IS the breakpoint message.
    result = ToolResultBlock(tool_use_id="tu_1", content="x" * 4000, cache=True)
    req = _request(
        messages=[Message(role=LLMRole.USER, content=[result])]
    )
    # 4000 chars / 4 chars-per-token = 1000 tokens (no discount applied
    # because there is nothing *before* the breakpoint message).
    assert estimate_request_input_tokens(req) == 1000


def test_estimator_only_messages_before_breakpoint_are_discounted():
    """Verify the boundary: messages[0..N-1] discounted, messages[N+] full."""
    large_cached = ToolResultBlock(tool_use_id="tu_0", content="y" * 4000)
    breakpoint_result = ToolResultBlock(
        tool_use_id="tu_1", content="z" * 400, cache=True
    )
    req = _request(
        messages=[
            # msg 0: 4000 chars — before breakpoint → discounted
            Message(role=LLMRole.USER, content=[large_cached]),
            # msg 1: 400 chars — breakpoint message → full price
            Message(role=LLMRole.USER, content=[breakpoint_result]),
        ]
    )
    # cached: int(4000 * 0.1) = 400 chars; uncached: 400 chars
    # total chars = 800; 800 / 4 = 200 tokens
    assert estimate_request_input_tokens(req) == 200


def test_estimator_all_cache_flags_combine():
    """system + tools + message cache discounts all apply independently."""
    tool = ToolDefinition(
        name="t",
        description="x" * 800,  # 800 chars tools
        input_schema={},
    )
    prior_result = ToolResultBlock(tool_use_id="tu_1", content="x" * 4000)
    breakpoint_result = ToolResultBlock(
        tool_use_id="tu_2", content="x" * 400, cache=True
    )
    req = _request(
        system="s" * 4000,  # 4000 chars system → discounted
        tools=[tool],
        cache_system=True,
        cache_tools=True,
        messages=[
            Message(role=LLMRole.USER, content=[prior_result]),  # 4000 → discounted
            Message(role=LLMRole.USER, content=[breakpoint_result]),  # 400 → full
        ],
    )
    # system:  int(4000 * 0.1) = 400 chars
    # tools:   int(800 + 1 + 2 chars * 0.1) ≈ int(810 * 0.1) = 81 chars
    # (tool name "t" = 1 char, description = 800, schema "{}" ≈ 2 chars)
    # messages cached: int(4000 * 0.1) = 400 chars
    # messages uncached: 400 chars
    # total chars ≈ 400 + 81 + 400 + 400 = 1281; tokens ≈ 320
    # uncached equivalent: 4000 + 803 + 4000 + 400 = 9203 chars → 2300 tokens
    req_uncached = _request(
        system="s" * 4000,
        tools=[tool],
        cache_system=False,
        cache_tools=False,
        messages=[
            Message(role=LLMRole.USER, content=[ToolResultBlock(tool_use_id="tu_1", content="x" * 4000)]),
            Message(role=LLMRole.USER, content=[ToolResultBlock(tool_use_id="tu_2", content="x" * 400)]),
        ],
    )
    est_cached = estimate_request_input_tokens(req)
    est_uncached = estimate_request_input_tokens(req_uncached)
    assert est_cached <= est_uncached * 0.2
