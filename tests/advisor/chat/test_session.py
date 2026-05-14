"""ChatSession orchestration: drives the tool-use loop and produces
a synthetic stream from the final response."""
from __future__ import annotations

import json
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
async def test_session_requests_enable_prompt_cache_for_system_and_tools():
    """Every gateway call the session makes must opt into prompt
    caching for the persona and tool defs — that's the load-bearing
    cost lever (Anthropic's ephemeral cache cuts those tokens by
    ~90%). A regression here silently re-bills the full rate."""
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

    request = gateway.calls[0]
    assert request.cache_system is True
    assert request.cache_tools is True


@pytest.mark.asyncio
async def test_session_marks_first_assistant_turns_as_cache_milestones():
    """First-assistant turns are byte-stable across the rest of the
    session; marking the LAST block of up to two of them gives turn 3+
    a long stable prefix to read from cache. Without this, the only
    cached prefix on later turns is system + tools."""
    session = _empty_session()
    gateway = MockGateway(
        scripted=[
            text_response("first answer"),
            text_response("second answer"),
            text_response("third answer"),
        ]
    )
    await session.send_user_message_blocking(gateway, "q1")
    await session.send_user_message_blocking(gateway, "q2")
    await session.send_user_message_blocking(gateway, "q3")

    third = gateway.calls[2]
    # Assistant turns sit at indices 1 and 3 after two completed turns.
    asst1 = third.messages[1]
    asst2 = third.messages[3]
    assert isinstance(asst1.content, list)
    assert isinstance(asst2.content, list)
    assert asst1.content[-1].cache is True
    assert asst2.content[-1].cache is True
    # Plain user-string messages stay untouched — wrapping them would
    # change the shape the rest of the chat layer observes.
    assert third.messages[0].content == "q1"
    assert third.messages[2].content == "q2"


@pytest.mark.asyncio
async def test_compaction_summarises_older_tool_results_in_submission():
    """Run three tool-use turns and verify the FINAL gateway call
    saw the first turn's tool_result content summarised, while
    ``session.messages`` still carries the full JSON payload (the
    persistence path reads from there)."""
    session = _empty_session()
    session.tool_defs = [
        ToolDefinition(
            name="search_bylaw_evidence",
            description="search",
            input_schema={"type": "object"},
        )
    ]

    # Big payload — the load-bearing thing compaction protects us
    # from. Each turn re-sends every earlier tool_result verbatim
    # without compaction, so this would re-bill on turn 3.
    turn1_payload = json.dumps(
        {
            "total_matches": 3,
            "matches": [
                {
                    "citation_path": "4.2.1",
                    "score": 0.94,
                    "text": "x" * 800,
                    "linked_datasets": [{"location_confidence": 0.94}],
                },
                {"citation_path": "4.2.3", "score": 0.91, "text": "y" * 800},
                {"citation_path": "5.1.7", "score": 0.80, "text": "z" * 800},
            ],
        }
    )
    turn2_payload = json.dumps({"total_matches": 0, "matches": []})
    turn3_payload = json.dumps({"total_matches": 0, "matches": []})

    payloads = iter([turn1_payload, turn2_payload, turn3_payload])

    async def handler(_payload: dict[str, Any]) -> str:
        return next(payloads)

    session.tool_handlers = {"search_bylaw_evidence": handler}

    gateway = MockGateway(
        scripted=[
            # Turn 1
            tool_use_response(
                tool_id="tu_1",
                tool_name="search_bylaw_evidence",
                tool_input={"query": "height limit ER-2"},
            ),
            text_response("first answer"),
            # Turn 2
            tool_use_response(
                tool_id="tu_2",
                tool_name="search_bylaw_evidence",
                tool_input={"query": "lot coverage"},
            ),
            text_response("second answer"),
            # Turn 3
            tool_use_response(
                tool_id="tu_3",
                tool_name="search_bylaw_evidence",
                tool_input={"query": "bedrooms"},
            ),
            text_response("third answer"),
        ]
    )

    await session.send_user_message_blocking(gateway, "q1")
    await session.send_user_message_blocking(gateway, "q2")
    await session.send_user_message_blocking(gateway, "q3")

    # Final gateway call (the synthesis step of turn 3) should have
    # received the turn-1 tool_result with summarised content. Older
    # tool_use blocks must remain intact for traceability.
    final_request = gateway.calls[-1]
    submitted = final_request.messages
    # Locate the turn-1 tool_result message (user-role list containing
    # a ToolResultBlock with tool_use_id="tu_1").
    submitted_turn1_result = next(
        m
        for m in submitted
        if m.role == LLMRole.USER
        and isinstance(m.content, list)
        and any(
            isinstance(b, ToolResultBlock) and b.tool_use_id == "tu_1"
            for b in m.content
        )
    )
    summarised_block = next(
        b
        for b in submitted_turn1_result.content
        if isinstance(b, ToolResultBlock) and b.tool_use_id == "tu_1"
    )
    assert isinstance(summarised_block.content, str)
    assert summarised_block.content.startswith(
        "[retrieved: 3 matches for 'height limit ER-2',"
    )
    assert len(summarised_block.content) < 300

    # Turn-1 tool_use block stays intact (traceability).
    submitted_turn1_use = next(
        m
        for m in submitted
        if m.role == LLMRole.ASSISTANT
        and isinstance(m.content, list)
        and any(
            isinstance(b, ToolUseBlock) and b.id == "tu_1" for b in m.content
        )
    )
    use_block = next(
        b
        for b in submitted_turn1_use.content
        if isinstance(b, ToolUseBlock) and b.id == "tu_1"
    )
    assert use_block.input == {"query": "height limit ER-2"}

    # session.messages — the persistence-bound copy — still carries
    # the full turn-1 payload. This is the load-bearing guarantee:
    # compaction is view-only.
    persisted_turn1_result = next(
        m
        for m in session.messages
        if m.role == LLMRole.USER
        and isinstance(m.content, list)
        and any(
            isinstance(b, ToolResultBlock) and b.tool_use_id == "tu_1"
            for b in m.content
        )
    )
    persisted_block = next(
        b
        for b in persisted_turn1_result.content
        if isinstance(b, ToolResultBlock) and b.tool_use_id == "tu_1"
    )
    assert persisted_block.content == turn1_payload


@pytest.mark.asyncio
async def test_compaction_keep_recent_field_overrides_default():
    """``compact_keep_recent=1`` should compact turn 1 even when only
    two completed turns are in history — the default of 2 would leave
    both intact."""
    session = _empty_session()
    session.compact_keep_recent = 1
    session.tool_defs = [
        ToolDefinition(
            name="search_bylaw_evidence",
            description="search",
            input_schema={"type": "object"},
        )
    ]

    payload = json.dumps(
        {"total_matches": 1, "matches": [{"citation_path": "1.1", "score": 0.8}]}
    )

    async def handler(_payload: dict[str, Any]) -> str:
        return payload

    session.tool_handlers = {"search_bylaw_evidence": handler}

    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="search_bylaw_evidence",
                tool_input={"query": "a"},
            ),
            text_response("first answer"),
            tool_use_response(
                tool_id="tu_2",
                tool_name="search_bylaw_evidence",
                tool_input={"query": "b"},
            ),
            text_response("second answer"),
        ]
    )

    await session.send_user_message_blocking(gateway, "q1")
    await session.send_user_message_blocking(gateway, "q2")

    submitted = gateway.calls[-1].messages
    turn1_result = next(
        m
        for m in submitted
        if m.role == LLMRole.USER
        and isinstance(m.content, list)
        and any(
            isinstance(b, ToolResultBlock) and b.tool_use_id == "tu_1"
            for b in m.content
        )
    )
    block = next(
        b
        for b in turn1_result.content
        if isinstance(b, ToolResultBlock) and b.tool_use_id == "tu_1"
    )
    assert isinstance(block.content, str)
    assert block.content.startswith("[retrieved:")


@pytest.mark.asyncio
async def test_compaction_is_byte_stable_across_repeated_submissions():
    """Anthropic prompt caching keys on byte-stable prefixes. Running
    the same conversation through ``send_user_message_blocking`` twice
    in a row (different sessions, identical inputs) must produce
    byte-identical submitted-message payloads up through the compacted
    prefix."""

    async def _run_three_turns() -> list[Message]:
        session = _empty_session()
        session.tool_defs = [
            ToolDefinition(
                name="search_bylaw_evidence",
                description="search",
                input_schema={"type": "object"},
            )
        ]
        payload = json.dumps(
            {
                "total_matches": 2,
                "matches": [
                    {
                        "citation_path": "4.2.1",
                        "score": 0.94,
                        "linked_datasets": [{"location_confidence": 0.94}],
                    },
                    {"citation_path": "4.2.3", "score": 0.91},
                ],
            }
        )

        async def handler(_payload: dict[str, Any]) -> str:
            return payload

        session.tool_handlers = {"search_bylaw_evidence": handler}

        gateway = MockGateway(
            scripted=[
                tool_use_response(
                    tool_id="tu_1",
                    tool_name="search_bylaw_evidence",
                    tool_input={"query": "max height"},
                ),
                text_response("first"),
                tool_use_response(
                    tool_id="tu_2",
                    tool_name="search_bylaw_evidence",
                    tool_input={"query": "lot coverage"},
                ),
                text_response("second"),
                tool_use_response(
                    tool_id="tu_3",
                    tool_name="search_bylaw_evidence",
                    tool_input={"query": "bedrooms"},
                ),
                text_response("third"),
            ]
        )
        await session.send_user_message_blocking(gateway, "q1")
        await session.send_user_message_blocking(gateway, "q2")
        await session.send_user_message_blocking(gateway, "q3")
        return list(gateway.calls[-1].messages)

    first = await _run_three_turns()
    second = await _run_three_turns()
    a = json.dumps([m.model_dump(mode="json") for m in first], sort_keys=True)
    b = json.dumps([m.model_dump(mode="json") for m in second], sort_keys=True)
    assert a == b


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


@pytest.mark.asyncio
async def test_case_anchor_injected_into_system_prompt():
    """When ``case_anchor_label`` is set on the session, the request's
    system prompt must include the anchor block. Without this, the LLM
    has no way to know the address the user opened the case for and
    asks them to repeat it on every turn."""
    session = _empty_session()
    session.case_anchor_label = "1234 Main St, Halifax"
    session.case_anchor_kind = "address"

    seen_systems: list[str] = []

    def capture(request):
        seen_systems.append(request.system)
        return text_response("ok")

    gateway = MockGateway(callable_=capture)
    await session.send_user_message_blocking(gateway, "What's the max height?")

    assert len(seen_systems) == 1
    system_text = seen_systems[0]
    # Persona must still be present (we append, never replace).
    assert "senior urban planner" in system_text
    # Anchor block carries the label so the LLM can use it.
    assert "1234 Main St, Halifax" in system_text
    # And carries the kind label so the model knows it's an address.
    assert "civic address" in system_text


@pytest.mark.asyncio
async def test_no_case_anchor_leaves_system_prompt_unchanged():
    """Sessions without a case anchor (legacy / test path) must send
    the persona unmodified — no surprise suffix."""
    session = _empty_session()

    seen_systems: list[str] = []

    def capture(request):
        seen_systems.append(request.system)
        return text_response("ok")

    gateway = MockGateway(callable_=capture)
    await session.send_user_message_blocking(gateway, "anything")

    assert seen_systems == ["You are a senior urban planner."]
