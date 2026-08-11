"""Translation layer for the ``claude -p`` gateway (ABS-454).

Pure functions only — nothing here spawns a CLI or touches the network.
The cases that matter most are the ones where the CLI's shape and the
gateway's shape disagree: the payload's own ``stop_reason`` (which lies,
see ``test_payload_stop_reason_never_overrides_final_answer``) and the
absence of any provider-side validation of tool inputs.
"""
from __future__ import annotations

import json

import pytest

from advisor.llm.base import (
    CompletionRequest,
    LLMRole,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from advisor.llm.claude_code_translation import (
    EnvelopeValidationError,
    ToolInputValidationError,
    UnknownToolError,
    build_envelope_schema,
    envelope_to_response,
    render_prompt,
)

MODEL = "claude-opus-4-5"


def _lookup_tool() -> ToolDefinition:
    return ToolDefinition(
        name="lookup_bylaw",
        description="Look up a bylaw section by municipality and section number.",
        input_schema={
            "type": "object",
            "required": ["municipality", "section"],
            "properties": {
                "municipality": {"type": "string"},
                "section": {"type": "string"},
            },
        },
    )


def _measure_tool() -> ToolDefinition:
    return ToolDefinition(
        name="measure_setback",
        description="Compute the setback distance for a lot edge.",
        input_schema={
            "type": "object",
            "required": ["edge"],
            "properties": {"edge": {"type": "string"}},
        },
    )


def _tools() -> list[ToolDefinition]:
    return [_lookup_tool(), _measure_tool()]


def _request(**overrides) -> CompletionRequest:
    kwargs = {
        "model": MODEL,
        "system": "You are a municipal planning analyst.",
        "messages": [Message(role=LLMRole.USER, content="Is a shed allowed?")],
        "tools": _tools(),
    }
    kwargs.update(overrides)
    return CompletionRequest(**kwargs)


def _payload(**overrides) -> dict:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "0f9c1d2e-3a4b-5c6d-7e8f-901234567890",
        "uuid": "abcd1234-5678-90ab-cdef-1234567890ab",
    }
    payload.update(overrides)
    return payload


# -- envelope -> response ----------------------------------------------------


def test_final_answer_yields_single_text_block_and_end_turn():
    """DoD 1. A ``final_answer`` envelope is the loop's exit condition:
    one TextBlock, ``end_turn``, and crucially *no* ToolUseBlock — the
    loop keys off the absence of tool_use to stop iterating."""
    response = envelope_to_response(
        {"action": "final_answer", "text": "A shed under 20 m² is permitted."},
        _payload(),
        MODEL,
        tools=_tools(),
    )
    assert len(response.content) == 1
    block = response.content[0]
    assert isinstance(block, TextBlock)
    assert block.text == "A shed under 20 m² is permitted."
    assert response.stop_reason == "end_turn"
    assert response.model == MODEL
    assert response.role == LLMRole.ASSISTANT


def test_single_tool_call_yields_one_tool_use_block_and_tool_use_stop():
    """DoD 2. The single-call case — the shape ``tool_loop`` sees on
    almost every iteration."""
    response = envelope_to_response(
        {
            "action": "tool_calls",
            "tool_calls": [
                {
                    "name": "lookup_bylaw",
                    "input": {"municipality": "Halifax", "section": "4.2"},
                }
            ],
        },
        _payload(),
        MODEL,
        tools=_tools(),
    )
    assert len(response.content) == 1
    block = response.content[0]
    assert isinstance(block, ToolUseBlock)
    assert block.name == "lookup_bylaw"
    assert block.input == {"municipality": "Halifax", "section": "4.2"}
    assert response.stop_reason == "tool_use"


def test_three_tool_calls_yield_three_blocks_with_unique_ids():
    """DoD 3. Parallel calls must survive translation as separate blocks
    with distinct ids — ``tool_loop`` correlates each ToolResultBlock
    back by ``tool_use_id``, so a duplicate id silently mis-routes a
    tool result to the wrong call."""
    response = envelope_to_response(
        {
            "action": "tool_calls",
            "tool_calls": [
                {
                    "name": "lookup_bylaw",
                    "input": {"municipality": "Halifax", "section": "4.2"},
                },
                {"name": "measure_setback", "input": {"edge": "front"}},
                {"name": "measure_setback", "input": {"edge": "rear"}},
            ],
        },
        _payload(),
        MODEL,
        tools=_tools(),
    )
    blocks = response.content
    assert len(blocks) == 3
    assert all(isinstance(b, ToolUseBlock) for b in blocks)
    assert [b.name for b in blocks] == [
        "lookup_bylaw",
        "measure_setback",
        "measure_setback",
    ]
    ids = [b.id for b in blocks]
    assert len(set(ids)) == 3
    assert response.stop_reason == "tool_use"


def test_usage_maps_all_four_fields_exactly():
    """DoD 4. The CLI reports the same four field names TokenUsage uses,
    so the mapping is 1:1 — this pins it against a silent rename."""
    response = envelope_to_response(
        {"action": "final_answer", "text": "done"},
        _payload(
            usage={
                "input_tokens": 1234,
                "output_tokens": 56,
                "cache_creation_input_tokens": 789,
                "cache_read_input_tokens": 1011,
                "service_tier": "standard",
            }
        ),
        MODEL,
        tools=_tools(),
    )
    assert response.usage is not None
    assert response.usage.input_tokens == 1234
    assert response.usage.output_tokens == 56
    assert response.usage.cache_creation_input_tokens == 789
    assert response.usage.cache_read_input_tokens == 1011


def test_absent_usage_yields_none_rather_than_zeroes():
    """DoD 5. ``None`` distinguishes "the CLI reported nothing" from "the
    turn genuinely cost zero" — the tool-loop metrics roll-up treats the
    two differently and must not crash on either."""
    response = envelope_to_response(
        {"action": "final_answer", "text": "done"},
        _payload(),
        MODEL,
        tools=_tools(),
    )
    assert response.usage is None


def test_payload_stop_reason_never_overrides_final_answer():
    """DoD 6 — the regression guard for the 2026-08-09 probe finding.

    ``--json-schema`` is implemented internally as a forced tool call, so
    the CLI payload reports ``stop_reason: "tool_use"`` even when the
    envelope is a semantically final answer. Passing it through would
    leave ``tool_loop`` waiting for tool calls that never come."""
    response = envelope_to_response(
        {"action": "final_answer", "text": "A shed under 20 m² is permitted."},
        _payload(stop_reason="tool_use"),
        MODEL,
        tools=_tools(),
    )
    assert response.stop_reason == "end_turn"
    assert all(not isinstance(b, ToolUseBlock) for b in response.content)


# -- prompt rendering --------------------------------------------------------


def test_rendered_prompt_contains_the_system_text():
    """DoD 7. ``claude -p`` takes one string; the system prompt has no
    dedicated slot, so it has to survive as prompt text or the persona
    is silently dropped."""
    request = _request(system="You are a municipal planning analyst.")
    prompt = render_prompt(request)
    assert "You are a municipal planning analyst." in prompt


def test_rendered_prompt_contains_every_tool_name_description_and_schema():
    """DoD 8. No tools array on the CLI — the menu only exists if we
    render it, and the model can't fill in a schema it never saw."""
    request = _request()
    prompt = render_prompt(request)
    for tool in request.tools:
        assert tool.name in prompt
        assert tool.description in prompt
        for prop in tool.input_schema["properties"]:
            assert prop in prompt
        assert json.dumps(tool.input_schema, sort_keys=True, indent=2) in prompt


def test_rendered_prompt_preserves_tool_use_then_tool_result_order():
    """DoD 9. A resumed loop replays prior turns as text; if the
    tool_result rendered before its tool_use the model would read the
    transcript as an answer arriving before the question."""
    request = _request(
        messages=[
            Message(role=LLMRole.USER, content="Is a shed allowed?"),
            Message(
                role=LLMRole.ASSISTANT,
                content=[
                    ToolUseBlock(
                        id="toolu_prior_1",
                        name="lookup_bylaw",
                        input={"municipality": "Halifax", "section": "4.2"},
                    )
                ],
            ),
            Message(
                role=LLMRole.USER,
                content=[
                    ToolResultBlock(
                        tool_use_id="toolu_prior_1",
                        content="Section 4.2: accessory buildings under 20 m².",
                    )
                ],
            ),
        ]
    )
    prompt = render_prompt(request)
    use_at = prompt.index("toolu_prior_1")
    result_at = prompt.index("Section 4.2: accessory buildings under 20 m².")
    question_at = prompt.index("Is a shed allowed?")
    assert question_at < use_at < result_at
    assert "tool_result" in prompt


def test_render_prompt_is_deterministic():
    """DoD 10. Prompt caching only pays off on a byte-stable prefix, and
    the transport keys its cache on this string — any dict-ordering or
    id churn here quietly doubles cost."""
    request = _request()
    assert render_prompt(request) == render_prompt(request)


# -- validation --------------------------------------------------------------


def test_unknown_tool_name_raises_typed_error():
    """DoD 11. A hallucinated tool name must fail loudly here so the
    transport can retry, rather than reaching ``tool_loop`` and being
    reported to the user as a dead-end tool error."""
    with pytest.raises(UnknownToolError) as excinfo:
        envelope_to_response(
            {
                "action": "tool_calls",
                "tool_calls": [{"name": "delete_bylaw", "input": {}}],
            },
            _payload(),
            MODEL,
            tools=_tools(),
        )
    assert isinstance(excinfo.value, EnvelopeValidationError)
    assert "delete_bylaw" in str(excinfo.value)


def test_tool_input_violating_schema_raises_typed_error():
    """DoD 12. The API validates tool_use inputs for us; the envelope
    path has no such guarantee, so an argument of the wrong type would
    otherwise blow up inside the handler mid-turn."""
    with pytest.raises(ToolInputValidationError) as excinfo:
        envelope_to_response(
            {
                "action": "tool_calls",
                "tool_calls": [
                    {
                        "name": "lookup_bylaw",
                        # 'section' declared as a string; missing entirely.
                        "input": {"municipality": "Halifax"},
                    }
                ],
            },
            _payload(),
            MODEL,
            tools=_tools(),
        )
    assert isinstance(excinfo.value, EnvelopeValidationError)
    assert "lookup_bylaw" in str(excinfo.value)


def test_wrong_typed_tool_input_raises_typed_error():
    """Sibling of the case above: present but wrong type. Both must be
    caught by the same schema check, not just the required-key one."""
    with pytest.raises(ToolInputValidationError):
        envelope_to_response(
            {
                "action": "tool_calls",
                "tool_calls": [
                    {
                        "name": "lookup_bylaw",
                        "input": {"municipality": "Halifax", "section": 42},
                    }
                ],
            },
            _payload(),
            MODEL,
            tools=_tools(),
        )


def test_unrecognised_action_raises_typed_error():
    """The discriminator is the whole contract; anything outside the
    enum means the envelope is unusable and the turn should be retried
    rather than guessed at."""
    with pytest.raises(EnvelopeValidationError):
        envelope_to_response(
            {"action": "think_harder", "text": "hmm"}, _payload(), MODEL, tools=_tools()
        )


def test_tool_calls_action_with_empty_list_raises_typed_error():
    """``action: tool_calls`` with nothing to call would translate to a
    zero-tool_use response, which ``tool_loop`` reads as ``end_turn`` and
    returns to the user as an empty answer."""
    with pytest.raises(EnvelopeValidationError):
        envelope_to_response(
            {"action": "tool_calls", "tool_calls": []},
            _payload(),
            MODEL,
            tools=_tools(),
        )


def test_final_answer_with_empty_text_raises_typed_error():
    """Mirror of the empty-tool_calls guard: a final answer with no text
    would end the loop and hand the user a blank response, so it is a
    malformed envelope the transport should retry."""
    with pytest.raises(EnvelopeValidationError):
        envelope_to_response(
            {"action": "final_answer", "text": "   "}, _payload(), MODEL, tools=_tools()
        )
    with pytest.raises(EnvelopeValidationError):
        envelope_to_response({"action": "final_answer"}, _payload(), MODEL, tools=_tools())


def test_text_alongside_tool_calls_is_preserved_before_the_calls():
    """The Messages API allows a text preamble on a tool-use turn; keep
    it, in order, so transcripts of a CLI-backed run read the same as an
    API-backed one."""
    response = envelope_to_response(
        {
            "action": "tool_calls",
            "text": "Checking the accessory-building rules.",
            "tool_calls": [{"name": "measure_setback", "input": {"edge": "front"}}],
        },
        _payload(),
        MODEL,
        tools=_tools(),
    )
    assert isinstance(response.content[0], TextBlock)
    assert isinstance(response.content[1], ToolUseBlock)
    assert response.stop_reason == "tool_use"


# -- envelope schema ---------------------------------------------------------


def test_envelope_schema_shape_matches_the_contract():
    """The schema is the only lever constraining CLI output; the shape
    below is what the transport passes to ``--json-schema``."""
    schema = build_envelope_schema(_tools())
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["action"]
    assert set(schema["properties"]["action"]["enum"]) == {
        "tool_calls",
        "final_answer",
    }
    calls = schema["properties"]["tool_calls"]
    assert calls["type"] == "array"
    assert calls["items"]["required"] == ["name", "input"]
    assert calls["items"]["properties"]["input"]["type"] == "object"
    assert schema["properties"]["text"]["type"] == "string"


def test_envelope_schema_narrows_tool_names_to_the_available_menu():
    """Constraining ``name`` to an enum prevents the hallucinated-tool
    failure at the source; the runtime check stays as defence in depth."""
    schema = build_envelope_schema(_tools())
    assert schema["properties"]["tool_calls"]["items"]["properties"]["name"][
        "enum"
    ] == ["lookup_bylaw", "measure_setback"]


def test_envelope_schema_without_tools_leaves_name_unconstrained():
    """A tool-less request (plain Q&A) still needs a valid schema — an
    empty ``enum`` is invalid JSON Schema and would fail the CLI."""
    schema = build_envelope_schema([])
    name_schema = schema["properties"]["tool_calls"]["items"]["properties"]["name"]
    assert name_schema == {"type": "string"}


def test_envelope_schema_validates_a_real_envelope():
    """Round-trip guard: the schema we ship must actually accept the
    envelopes this module is built to translate."""
    import jsonschema

    schema = build_envelope_schema(_tools())
    jsonschema.validate(
        {
            "action": "tool_calls",
            "tool_calls": [{"name": "measure_setback", "input": {"edge": "front"}}],
        },
        schema,
    )
    jsonschema.validate({"action": "final_answer", "text": "ok"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"action": "final_answer", "extra": 1}, schema)


# -- purity ------------------------------------------------------------------


def test_module_performs_no_io():
    """DoD 1. This layer must stay a pure translation: the transport
    that spawns ``claude -p`` lives elsewhere, and keeping I/O out is
    what lets every case above run without a subprocess."""
    from pathlib import Path

    import advisor.llm.claude_code_translation as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for banned in ("subprocess", "asyncio", "urllib", "requests", "open("):
        assert banned not in source, f"{banned!r} leaked into the translation layer"
