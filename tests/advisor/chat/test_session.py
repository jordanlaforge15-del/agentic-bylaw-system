"""ChatSession orchestration: drives the tool-use loop and produces
a synthetic stream from the final response."""
from __future__ import annotations

from typing import Any

import pytest

from advisor.chat.session import ChatSession
from advisor.llm import (
    LLMRole,
    Message,
    MessageStartEvent,
    MessageStopEvent,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from advisor.llm.mock import MockGateway, text_response, tool_use_response


def _empty_session() -> ChatSession:
    return ChatSession(
        session_id="sess_test_1",
        user_id="user_42",
        system_prompt="You are a senior urban planner.",
        model="claude-opus-4-5",
    )


@pytest.mark.asyncio
async def test_send_user_message_blocking_simple_round_trip():
    """The simplest case: gateway answers in one shot. Session ends
    up with a user message and an assistant message — no tool
    intermediates."""
    session = _empty_session()
    gateway = MockGateway(scripted=[text_response("Halifax permits R-1 in some areas.")])

    response = await session.send_user_message_blocking(
        gateway, "What zones exist in Halifax?"
    )

    assert response.content[0].text == "Halifax permits R-1 in some areas."
    assert len(session.messages) == 2
    assert session.messages[0].role == LLMRole.USER
    assert session.messages[1].role == LLMRole.ASSISTANT


@pytest.mark.asyncio
async def test_send_user_message_blocking_with_tool_use():
    """Classic tool-use loop: assistant asks for a tool, handler
    runs, assistant replies. After the loop the session should
    contain four messages: user -> assistant(tool_use) ->
    user(tool_result) -> assistant(final). This is the same shape
    the run_tool_loop tests assert."""
    session = _empty_session()
    session.tool_defs = [
        ToolDefinition(
            name="search_bylaw_evidence",
            description="search",
            input_schema={"type": "object"},
        )
    ]

    captured: list[dict[str, Any]] = []

    async def search_handler(payload: dict[str, Any]) -> str:
        captured.append(payload)
        return '{"matches": []}'

    session.tool_handlers = {"search_bylaw_evidence": search_handler}

    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="search_bylaw_evidence",
                tool_input={"query": "max height"},
            ),
            text_response("No matches found."),
        ]
    )

    response = await session.send_user_message_blocking(
        gateway, "What's the max height in HRM?"
    )

    assert response.content[0].text == "No matches found."
    assert captured == [{"query": "max height"}]
    assert len(session.messages) == 4

    # Message 1: original user prompt.
    assert session.messages[0].role == LLMRole.USER
    # Message 2: assistant tool_use turn.
    assert session.messages[1].role == LLMRole.ASSISTANT
    use_blocks = [
        b for b in session.messages[1].content if isinstance(b, ToolUseBlock)
    ]
    assert len(use_blocks) == 1
    # Message 3: user tool_result turn.
    assert session.messages[2].role == LLMRole.USER
    result_blocks = [
        b for b in session.messages[2].content if isinstance(b, ToolResultBlock)
    ]
    assert len(result_blocks) == 1
    assert result_blocks[0].tool_use_id == "tu_1"
    # Message 4: final assistant answer.
    assert session.messages[3].role == LLMRole.ASSISTANT


@pytest.mark.asyncio
async def test_send_user_message_streams_in_correct_order():
    """The synthetic stream must start with MessageStartEvent and end
    with MessageStopEvent — that's the contract the SSE frontend
    relies on for opening and closing render frames."""
    session = _empty_session()
    gateway = MockGateway(scripted=[text_response("hello there")])

    events = [
        event
        async for event in session.send_user_message(gateway, "hi")
    ]

    assert isinstance(events[0], MessageStartEvent)
    assert isinstance(events[-1], MessageStopEvent)
    # And the user message landed on the session even though we
    # consumed via the streaming path:
    assert any(
        m.role == LLMRole.USER and m.content == "hi"
        for m in session.messages
    )


@pytest.mark.asyncio
async def test_session_request_includes_system_prompt_and_tools():
    """The session should forward its system prompt and tool defs to
    every gateway call. We capture the gateway's incoming request
    and assert on the shape — without this, a typo in the session's
    request-building code could silently degrade tool use."""
    session = _empty_session()
    session.tool_defs = [
        ToolDefinition(
            name="search_bylaw_evidence",
            description="d",
            input_schema={"type": "object"},
        )
    ]
    gateway = MockGateway(scripted=[text_response("ok")])

    await session.send_user_message_blocking(gateway, "hey")

    assert len(gateway.calls) == 1
    request = gateway.calls[0]
    assert request.system == "You are a senior urban planner."
    assert request.model == "claude-opus-4-5"
    assert len(request.tools) == 1
    assert request.tools[0].name == "search_bylaw_evidence"


@pytest.mark.asyncio
async def test_messages_preserved_across_turns():
    """A multi-turn conversation should accumulate history in the
    session — turn 2 must see turn 1's messages in its request to
    the gateway."""
    session = _empty_session()
    gateway = MockGateway(
        scripted=[
            text_response("first reply"),
            text_response("second reply"),
        ]
    )

    await session.send_user_message_blocking(gateway, "first question")
    await session.send_user_message_blocking(gateway, "second question")

    # On the second call the gateway should have seen 3 messages:
    # turn1 user, turn1 assistant, turn2 user.
    second_request = gateway.calls[1]
    assert len(second_request.messages) == 3
    assert second_request.messages[0].content == "first question"
    assert isinstance(second_request.messages[1].content, list)
    assert second_request.messages[2].content == "second question"


@pytest.mark.asyncio
async def test_streaming_yields_text_delta_events_for_text_blocks():
    """Synthetic streaming must include at least one delta event
    between block start and stop — the frontend relies on deltas
    to render incremental tokens. A v1 'just emit start/stop'
    optimisation would break that UX."""
    session = _empty_session()
    gateway = MockGateway(
        scripted=[text_response("a longer reply that should be chunked")]
    )

    events = [
        event
        async for event in session.send_user_message(gateway, "hi")
    ]

    delta_events = [e for e in events if e.type == "content_block_delta"]
    assert len(delta_events) >= 1


def test_session_constructed_with_default_model():
    """The dataclass default should be the current production model,
    so callers that don't override it pick up the latest available
    Claude Opus version automatically."""
    session = ChatSession(
        session_id="s1",
        user_id="u1",
        system_prompt="you are a planner",
    )
    assert session.model.startswith("claude-")


@pytest.mark.asyncio
async def test_user_message_appended_before_run():
    """Even before the gateway responds, the session's message list
    should already include the user turn — concurrent inspectors
    (e.g. a sidebar showing 'thinking...') depend on that."""
    session = _empty_session()
    seen_messages: list[Message] = []

    def capture(_request):
        seen_messages.extend(_request.messages)
        return text_response("ok")

    gateway = MockGateway(callable_=capture)
    await session.send_user_message_blocking(gateway, "what")
    # The capture callback ran during the gateway call, AFTER we
    # appended the user message. So the captured list includes it.
    user_msgs = [m for m in seen_messages if m.role == LLMRole.USER]
    assert any(
        m.content == "what" or (
            isinstance(m.content, list)
            and any(isinstance(b, TextBlock) and b.text == "what" for b in m.content)
        )
        for m in user_msgs
    )
