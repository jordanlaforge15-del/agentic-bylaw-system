"""Tool-use orchestration: the loop that turns a chat turn into
zero or more tool round-trips before the assistant gives a final
answer."""
from __future__ import annotations

from typing import Any

import pytest

from advisor.llm import (
    CompletionRequest,
    LLMRole,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    run_tool_loop,
)
from advisor.llm.mock import MockGateway, text_response, tool_use_response
from advisor.llm.tool_loop import ToolLoopError, text_of


def _request_with_tool() -> CompletionRequest:
    return CompletionRequest(
        model="claude-opus-4-5",
        system="You are a planner.",
        messages=[Message(role=LLMRole.USER, content="what's the height at 6321 Quinpool?")],
        tools=[
            ToolDefinition(
                name="search_bylaw_evidence",
                description="Search the RCLUB.",
                input_schema={"type": "object"},
            )
        ],
    )


@pytest.mark.asyncio
async def test_zero_tool_calls_when_first_response_is_final():
    """If the LLM answers without asking for tools, the loop returns
    immediately with iterations=1 and no tool_calls."""
    gateway = MockGateway(scripted=[text_response("just a plain answer")])
    result = await run_tool_loop(gateway, request=_request_with_tool(), handlers={})
    assert result.iterations == 1
    assert result.tool_calls == []
    assert text_of(result.final_response) == "just a plain answer"


@pytest.mark.asyncio
async def test_single_tool_call_round_trip():
    """The classic loop: LLM asks, handler runs, LLM gets result and
    produces a final answer. The conversation should contain four
    messages: original user, assistant tool_use, user tool_result,
    assistant final."""
    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="search_bylaw_evidence",
                tool_input={"query": "max height", "civic_number": "6321"},
                preamble="Searching the bylaw.",
            ),
            text_response("Max height is 90m per Schedule 15."),
        ]
    )

    handler_inputs: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> str:
        handler_inputs.append(payload)
        return "Schedule 15 says max_height_m=90"

    result = await run_tool_loop(
        gateway,
        request=_request_with_tool(),
        handlers={"search_bylaw_evidence": handler},
    )

    assert result.iterations == 2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "search_bylaw_evidence"
    assert result.tool_calls[0].input == {"query": "max height", "civic_number": "6321"}
    assert result.tool_calls[0].error is None
    assert handler_inputs == [{"query": "max height", "civic_number": "6321"}]

    assert text_of(result.final_response) == "Max height is 90m per Schedule 15."

    # Conversation includes both round-trips:
    roles = [m.role for m in result.conversation]
    assert roles == [LLMRole.USER, LLMRole.ASSISTANT, LLMRole.USER, LLMRole.ASSISTANT]
    # The tool_result is the user-side block correlated by id:
    user_tool_result = result.conversation[2]
    assert isinstance(user_tool_result.content, list)
    blocks = user_tool_result.content
    assert isinstance(blocks[0], ToolResultBlock)
    assert blocks[0].tool_use_id == "tu_1"
    assert blocks[0].is_error is False


@pytest.mark.asyncio
async def test_handler_exception_becomes_tool_result_error():
    """A handler that raises must NOT crash the loop. The exception
    is rendered into a tool_result with is_error=True so the LLM can
    see the failure and recover."""
    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="search_bylaw_evidence",
                tool_input={"q": "x"},
            ),
            text_response("Sorry, I couldn't search."),
        ]
    )

    async def broken_handler(_payload: dict[str, Any]) -> str:
        raise RuntimeError("database is down")

    result = await run_tool_loop(
        gateway,
        request=_request_with_tool(),
        handlers={"search_bylaw_evidence": broken_handler},
    )

    assert result.tool_calls[0].error is not None
    assert "RuntimeError" in result.tool_calls[0].error
    assert "database is down" in result.tool_calls[0].error

    # The tool_result block must surface the error to the LLM:
    user_msg = result.conversation[2]
    assert isinstance(user_msg.content, list)
    block = user_msg.content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.is_error is True


@pytest.mark.asyncio
async def test_unknown_tool_name_reports_configuration_error():
    """If the LLM hallucinates a tool name we didn't register, the
    loop produces a clean tool_result error rather than a Python
    KeyError or silently ignoring it."""
    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="nonexistent_tool",
                tool_input={},
            ),
            text_response("Apologies."),
        ]
    )

    result = await run_tool_loop(gateway, request=_request_with_tool(), handlers={})

    assert result.tool_calls[0].error is not None
    assert "nonexistent_tool" in result.tool_calls[0].error
    assert "configuration" in result.tool_calls[0].error.lower()


@pytest.mark.asyncio
async def test_max_iterations_forces_text_synthesis_instead_of_raising():
    """A misbehaving model that keeps emitting tool_use forever must
    not hang the chat backend OR error out generically. The loop
    runs ``max_iterations`` tool rounds, then forces ONE more call
    with tools stripped so the model has to produce a real text
    answer from whatever it already retrieved.

    This is what makes "Can 2 COM lots be combined?" — a question
    the LUB doesn't answer — surface as a useful "the LUB doesn't
    cover this" reply instead of a generic agent-gave-up error.
    """
    n_calls = {"i": 0}

    def script(_request: CompletionRequest):
        n_calls["i"] += 1
        # First N calls: keep asking for the tool. The (N+1)th call
        # is the synthesis call — by then ``tools`` is [] in the
        # request, so we return a plain text response.
        if not _request.tools:
            return text_response(
                "The LUB does not cover lot consolidation; "
                "see the HRM Subdivision By-law."
            )
        return tool_use_response(
            tool_id=f"tu_{n_calls['i']}",
            tool_name="search_bylaw_evidence",
            tool_input={},
        )

    gateway = MockGateway(callable_=script)

    async def handler(_payload: dict[str, Any]) -> str:
        return "no relevant fragments"

    result = await run_tool_loop(
        gateway,
        request=_request_with_tool(),
        handlers={"search_bylaw_evidence": handler},
        max_iterations=3,
    )

    assert result.iterations == 3
    assert result.terminated_reason == "iteration_cap"
    assert len(result.tool_calls) == 3
    # Synthesis turn produced a real text answer.
    assert "Subdivision" in text_of(result.final_response)
    # The conversation ends with an assistant turn (the synthesis),
    # preceded by a user(tool_result + nudge) turn so the persistence
    # path can save the full audit trail.
    assert result.conversation[-1].role == LLMRole.ASSISTANT
    assert result.conversation[-2].role == LLMRole.USER


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_one_response_handled_in_order():
    """A single assistant turn may include several tool_use blocks
    (parallel tool calls). The loop must invoke each handler once
    and return all tool_results in a single user message."""
    gateway = MockGateway(
        scripted=[
            # First response: two tool_use blocks side by side.
            type(text_response("ignored"))(
                model="",
                content=[
                    TextBlock(text="Looking up two things."),
                    ToolUseBlock(id="tu_1", name="search_bylaw_evidence", input={"q": "first"}),
                    ToolUseBlock(id="tu_2", name="search_bylaw_evidence", input={"q": "second"}),
                ],
            ),
            text_response("Combined answer."),
        ]
    )

    seen_inputs: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> str:
        seen_inputs.append(payload)
        return f"result for {payload.get('q')}"

    result = await run_tool_loop(
        gateway,
        request=_request_with_tool(),
        handlers={"search_bylaw_evidence": handler},
    )

    assert len(result.tool_calls) == 2
    assert seen_inputs == [{"q": "first"}, {"q": "second"}]

    # Single user message holds both tool_results, in order:
    user_msg = result.conversation[2]
    assert isinstance(user_msg.content, list)
    assert len(user_msg.content) == 2
    assert all(isinstance(b, ToolResultBlock) for b in user_msg.content)
    ids = [b.tool_use_id for b in user_msg.content if isinstance(b, ToolResultBlock)]
    assert ids == ["tu_1", "tu_2"]


@pytest.mark.asyncio
async def test_cost_circuit_breaker_trips_on_oversized_request():
    """When the pre-flight estimator says the request exceeds the
    token budget, the loop strips tools and forces ONE synthesis turn
    — the same shape as the iteration-cap fallback. The trip is
    recorded on the result so the chat route can audit it.

    The test pins a tiny budget (50 tokens) so a request that's
    nowhere near a production runaway still trips reliably.
    """
    seen_requests: list[CompletionRequest] = []

    def script(req: CompletionRequest):
        seen_requests.append(req)
        if not req.tools:
            return text_response("Bounded synthesis answer.")
        # Without trip the loop would happily keep calling tools; the
        # estimator should fire before this scripted reply matters.
        return tool_use_response(
            tool_id="tu_1",
            tool_name="search_bylaw_evidence",
            tool_input={"q": "x"},
        )

    gateway = MockGateway(callable_=script)

    async def handler(_payload):
        return "ignored"

    result = await run_tool_loop(
        gateway,
        request=_request_with_tool(),
        handlers={"search_bylaw_evidence": handler},
        token_budget=10,
    )

    assert result.terminated_reason == "cost_circuit_trip"
    assert result.circuit_trip is not None
    assert result.circuit_trip.budget == 10
    assert result.circuit_trip.estimated_input_tokens > 10
    # The trip fires on the FIRST iteration here — the request as
    # built by ``_request_with_tool`` already exceeds 10 tokens.
    assert result.circuit_trip.iteration == 1
    # Synthesis call landed; tools were stripped:
    assert any(req.tools == [] for req in seen_requests)
    assert text_of(result.final_response) == "Bounded synthesis answer."


@pytest.mark.asyncio
async def test_cost_circuit_breaker_pass_through_under_budget():
    """With the default (large) budget a normal turn flows through
    untouched — no trip, no synthesis fallback, ``circuit_trip`` is
    ``None``."""
    gateway = MockGateway(scripted=[text_response("normal answer")])
    result = await run_tool_loop(
        gateway,
        request=_request_with_tool(),
        handlers={},
    )
    assert result.terminated_reason == "end_turn"
    assert result.circuit_trip is None


@pytest.mark.asyncio
async def test_cost_circuit_trip_after_tool_round():
    """When the budget is crossed AFTER a tool round has appended a
    bulky tool_result, the trip iteration is recorded as the iteration
    that would have ballooned past the cap — and the synthesis call
    still sees the retrieved evidence in the conversation."""
    calls = {"i": 0}

    def script(req: CompletionRequest):
        calls["i"] += 1
        if not req.tools:
            return text_response("answered from evidence above")
        return tool_use_response(
            tool_id=f"tu_{calls['i']}",
            tool_name="search_bylaw_evidence",
            tool_input={"q": "x"},
        )

    gateway = MockGateway(callable_=script)

    async def fat_handler(_payload):
        # A 2k-char tool_result blows past a small budget on the
        # NEXT iteration's pre-flight estimate.
        return "x" * 2000

    result = await run_tool_loop(
        gateway,
        request=_request_with_tool(),
        handlers={"search_bylaw_evidence": fat_handler},
        token_budget=400,
    )

    assert result.terminated_reason == "cost_circuit_trip"
    assert result.circuit_trip is not None
    assert result.circuit_trip.iteration >= 2
    assert text_of(result.final_response) == "answered from evidence above"


@pytest.mark.asyncio
async def test_total_usage_aggregates_across_iterations():
    """``total_usage`` sums ``CompletionResponse.usage`` from every
    iteration. The default MockGateway usage is 10/20 per call, so a
    two-iteration tool-use loop reports 20/40."""
    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="search_bylaw_evidence",
                tool_input={"q": "x"},
            ),
            text_response("done."),
        ]
    )

    async def handler(_payload: dict[str, Any]) -> str:
        return "ok"

    result = await run_tool_loop(
        gateway,
        request=_request_with_tool(),
        handlers={"search_bylaw_evidence": handler},
    )

    assert result.total_usage is not None
    assert result.total_usage.input_tokens == 20
    assert result.total_usage.output_tokens == 40
