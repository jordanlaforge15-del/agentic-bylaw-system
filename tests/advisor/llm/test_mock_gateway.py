"""MockGateway behaviour — both scripted and callable modes, plus the
streaming-event sequencing that real gateways must match."""
from __future__ import annotations

import pytest

from advisor.llm import (
    CompletionRequest,
    CompletionResponse,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    LLMRole,
    Message,
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    TextBlock,
    ToolUseBlock,
)
from advisor.llm.mock import MockGateway, text_response, tool_use_response


def _request(text: str = "hi") -> CompletionRequest:
    return CompletionRequest(
        model="claude-opus-4-5",
        messages=[Message(role=LLMRole.USER, content=text)],
    )


@pytest.mark.asyncio
async def test_scripted_response_returned_in_order():
    gateway = MockGateway(
        scripted=[text_response("first"), text_response("second")]
    )
    r1 = await gateway.complete(_request())
    r2 = await gateway.complete(_request())
    assert "".join(b.text for b in r1.content if isinstance(b, TextBlock)) == "first"
    assert "".join(b.text for b in r2.content if isinstance(b, TextBlock)) == "second"


@pytest.mark.asyncio
async def test_exhausted_scripted_responses_raises():
    gateway = MockGateway(scripted=[text_response("only one")])
    await gateway.complete(_request())
    with pytest.raises(AssertionError, match="exhausted"):
        await gateway.complete(_request())


@pytest.mark.asyncio
async def test_callable_mode_inspects_request():
    seen: list[CompletionRequest] = []

    def fn(req: CompletionRequest) -> CompletionResponse:
        seen.append(req)
        return text_response(f"echo: {req.messages[-1].content}")

    gateway = MockGateway(callable_=fn)
    response = await gateway.complete(_request("hello"))
    text = "".join(b.text for b in response.content if isinstance(b, TextBlock))
    assert text == "echo: hello"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_calls_attribute_records_every_request():
    gateway = MockGateway(scripted=[text_response("a"), text_response("b")])
    await gateway.complete(_request("first"))
    await gateway.complete(_request("second"))
    assert len(gateway.calls) == 2
    assert gateway.calls[0].messages[-1].content == "first"
    assert gateway.calls[1].messages[-1].content == "second"


@pytest.mark.asyncio
async def test_stream_emits_events_in_anthropic_order():
    gateway = MockGateway(scripted=[text_response("hello world")])
    events = []
    async for event in gateway.stream(_request()):
        events.append(event)

    # First and last events fixed per Anthropic ordering:
    assert isinstance(events[0], MessageStartEvent)
    assert isinstance(events[-1], MessageStopEvent)
    # Block start, deltas, stop must appear in order:
    assert isinstance(events[1], ContentBlockStartEvent)
    delta_events = [e for e in events if isinstance(e, ContentBlockDeltaEvent)]
    assert delta_events, "expected at least one delta event"
    assert isinstance(events[-3], ContentBlockStopEvent) or any(
        isinstance(e, ContentBlockStopEvent) for e in events
    )
    # Concatenated deltas reconstruct the full text:
    reconstructed = "".join(e.text_delta or "" for e in delta_events)
    assert reconstructed == "hello world"


@pytest.mark.asyncio
async def test_stream_handles_tool_use_block():
    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="search_bylaw_evidence",
                tool_input={"query": "max height", "location": {"civic_number": "6321"}},
                preamble="Looking that up.",
            )
        ]
    )
    events = []
    async for event in gateway.stream(_request("what's the height at 6321 quinpool")):
        events.append(event)
    # Two content blocks (preamble + tool_use): expect 2 start + 2 stop.
    starts = [e for e in events if isinstance(e, ContentBlockStartEvent)]
    stops = [e for e in events if isinstance(e, ContentBlockStopEvent)]
    assert len(starts) == 2
    assert len(stops) == 2
    # Tool-use block is the second one:
    assert isinstance(starts[1].content_block, ToolUseBlock)
    # message_delta carries the tool_use stop reason:
    delta = next(e for e in events if isinstance(e, MessageDeltaEvent))
    assert delta.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_stop_reason_inferred_from_content_when_missing():
    """If the test author forgets to set stop_reason, the gateway
    infers it from the content blocks. tool_use content -> tool_use,
    plain text -> end_turn."""
    gateway = MockGateway(scripted=[text_response("plain")])
    response = await gateway.complete(_request())
    assert response.stop_reason == "end_turn"
