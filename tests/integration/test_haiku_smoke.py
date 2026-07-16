"""Live-API smoke test for Haiku 4.5 + tool use.

Why this exists: ABS-267 introduces a cheap-model regression workflow
where development iteration runs against Haiku while merge gates run
against Opus. The shared ``AnthropicGateway`` is model-agnostic in
principle — both models flow through the same ``messages.create``
path — but per-model wire-format differences (header requirements,
tool-use schema quirks, token-limit defaults) have historically
broken assumptions. This test catches that class of regression
before it shows up as a $1+ wasted run.

Skipped unless ``ABS_RUN_LIVE_HAIKU_SMOKE=1`` is set in the
environment AND ``ANTHROPIC_API_KEY`` is available. Cost per run:
under $0.01 USD (single-turn, ~300 tokens in / ~50 out on Haiku).
"""
from __future__ import annotations

import os

import pytest

from advisor.llm import (
    CompletionRequest,
    LLMRole,
    Message,
    ToolDefinition,
    ToolUseBlock,
    run_tool_loop,
)
from advisor.llm.anthropic_backend import AnthropicGateway


_LIVE = os.environ.get("ABS_RUN_LIVE_HAIKU_SMOKE") == "1"
_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))


pytestmark = pytest.mark.skipif(
    not (_LIVE and _HAS_KEY),
    reason=(
        "Live Haiku smoke skipped — set ABS_RUN_LIVE_HAIKU_SMOKE=1 and "
        "ANTHROPIC_API_KEY in env to enable. Cost per run: <$0.01 USD."
    ),
)


@pytest.mark.asyncio
async def test_haiku_runs_a_single_tool_use_round_trip():
    """One tool round-trip on Haiku 4.5: ask a question that needs a
    tool, run the handler with a stub return value, verify the model
    produces a final text answer. If this fails the cheap-model
    regression workflow can't be trusted."""
    gateway = AnthropicGateway(api_key=os.environ["ANTHROPIC_API_KEY"])

    request = CompletionRequest(
        model="claude-haiku-4-5",
        system=(
            "You are a tiny test assistant. When the user asks for a "
            "single piece of information about Halifax bylaws, call "
            "the fake_lookup tool exactly once with the user's query, "
            "then summarise the tool's response in one short sentence."
        ),
        messages=[
            Message(
                role=LLMRole.USER,
                content=(
                    "What is the max height in HR-2? Use the fake_lookup "
                    "tool."
                ),
            )
        ],
        tools=[
            ToolDefinition(
                name="fake_lookup",
                description="Return a stubbed bylaw fact for testing.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        ],
        max_tokens=200,
        temperature=0.0,
    )

    handler_calls: list[dict] = []

    async def handler(payload: dict) -> str:
        handler_calls.append(payload)
        return "HR-2 max height is 25 metres (stub)."

    result = await run_tool_loop(
        gateway,
        request=request,
        handlers={"fake_lookup": handler},
        max_iterations=4,  # plenty for a one-call test
    )

    # The model called the tool at least once with a sensible query.
    assert len(handler_calls) >= 1, (
        "Haiku did not call the tool — model behaviour regression "
        "or system-prompt instruction failure"
    )
    # Loop terminated naturally (no cap, no circuit trip).
    assert result.terminated_reason == "end_turn"
    # Final response contains text content (the summary turn).
    text_blocks = [
        b for b in result.final_response.content if b.type == "text"
    ]
    assert len(text_blocks) >= 1
    # Loop produced ABS-266 per-iteration metrics on the live path.
    assert len(result.per_iteration) >= 2
    assert all(m.usage is not None for m in result.per_iteration)
