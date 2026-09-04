"""AnthropicGateway request translation — focused on the prompt-cache
wiring, since that's the lever that controls API spend.

Real API calls are out of scope here; we drive ``_to_anthropic_params``
directly. The verification script at ``scripts/verify_prompt_cache.py``
exercises end-to-end against the live API."""
from __future__ import annotations

import inspect

from advisor.llm import (
    CompletionRequest,
    LLMRole,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from advisor.llm.anthropic_backend import AnthropicGateway


def _gateway() -> AnthropicGateway:
    # ``api_key`` is unused for the translation path — we never call
    # the client. Pass a dummy and skip constructing one ourselves.
    return AnthropicGateway(api_key="unused")


def test_no_cache_flags_passes_system_as_plain_string():
    """Default request shape (no caching) must remain bit-exactly the
    same as before — the Anthropic SDK still accepts ``system`` as a
    plain string and we shouldn't churn that for callers who haven't
    opted in."""
    gw = _gateway()
    req = CompletionRequest(
        model="claude-opus-4-5",
        system="You are a planner.",
        messages=[Message(role=LLMRole.USER, content="hi")],
    )
    params = gw._to_anthropic_params(req)
    assert params["system"] == "You are a planner."
    assert "tools" not in params


def test_cache_system_rewrites_system_as_block_list_with_cache_control():
    """Anthropic only honours ``cache_control`` on block-list system
    prompts, so the translation has to convert the string we keep on
    the request into the structured shape — and only when the caller
    asked for it."""
    gw = _gateway()
    req = CompletionRequest(
        model="claude-opus-4-5",
        system="You are a planner.",
        messages=[Message(role=LLMRole.USER, content="hi")],
        cache_system=True,
    )
    params = gw._to_anthropic_params(req)
    assert params["system"] == [
        {
            "type": "text",
            "text": "You are a planner.",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_cache_tools_marks_only_the_last_tool():
    """One cache breakpoint on the final tool caches the whole tools
    array; marking earlier tools too would waste breakpoints from the
    4-per-request budget."""
    gw = _gateway()
    req = CompletionRequest(
        model="claude-opus-4-5",
        messages=[Message(role=LLMRole.USER, content="hi")],
        tools=[
            ToolDefinition(name="a", description="d", input_schema={"type": "object"}),
            ToolDefinition(name="b", description="d", input_schema={"type": "object"}),
            ToolDefinition(name="c", description="d", input_schema={"type": "object"}),
        ],
        cache_tools=True,
    )
    params = gw._to_anthropic_params(req)
    tools = params["tools"]
    assert "cache_control" not in tools[0]
    assert "cache_control" not in tools[1]
    assert tools[2]["cache_control"] == {"type": "ephemeral"}


def test_per_block_cache_flag_emits_cache_control_in_message():
    """Block-level ``cache`` lets the chat layer place stable-prefix
    breakpoints on conversation milestones without the gateway needing
    a separate request-level switch for messages."""
    gw = _gateway()
    msg = Message(
        role=LLMRole.ASSISTANT,
        content=[
            TextBlock(text="thinking..."),
            ToolUseBlock(id="tu_1", name="search", input={"q": "x"}, cache=True),
        ],
    )
    req = CompletionRequest(
        model="claude-opus-4-5",
        messages=[Message(role=LLMRole.USER, content="hi"), msg],
    )
    params = gw._to_anthropic_params(req)
    asst_blocks = params["messages"][1]["content"]
    assert "cache_control" not in asst_blocks[0]
    assert asst_blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_tool_result_block_cache_flag_emits_cache_control():
    """The chat layer may mark a tool_result as a breakpoint when it's
    the last stable thing in a turn. The translation must honour that
    even though tool_result has a richer payload than plain text."""
    gw = _gateway()
    req = CompletionRequest(
        model="claude-opus-4-5",
        messages=[
            Message(role=LLMRole.USER, content="hi"),
            Message(
                role=LLMRole.USER,
                content=[
                    ToolResultBlock(
                        tool_use_id="tu_1", content="ok", cache=True
                    )
                ],
            ),
        ],
    )
    params = gw._to_anthropic_params(req)
    block = params["messages"][1]["content"][0]
    assert block["type"] == "tool_result"
    assert block["cache_control"] == {"type": "ephemeral"}


def test_per_tool_cache_flag_combines_with_request_level_flag():
    """A caller may have a single tool they want cached even when the
    request-level ``cache_tools`` flag is off — make sure both knobs
    independently produce cache_control on their target tools."""
    gw = _gateway()
    req = CompletionRequest(
        model="claude-opus-4-5",
        messages=[Message(role=LLMRole.USER, content="hi")],
        tools=[
            ToolDefinition(
                name="a", description="d", input_schema={"type": "object"}, cache=True
            ),
            ToolDefinition(name="b", description="d", input_schema={"type": "object"}),
        ],
    )
    params = gw._to_anthropic_params(req)
    assert params["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in params["tools"][1]


# ---------------------------------------------------------------------------
# SDK-compatibility guard (ABS-531)
# ---------------------------------------------------------------------------
#
# Prod broke on every case-open with:
#
#     AsyncMessages.create() got an unexpected keyword argument 'temperature'
#
# `pyproject.toml` declared `anthropic>=0.40` — a floor, not a pin — so the
# Docker build resolved anthropic 1.x, which removed the sampling parameters
# from the Messages API surface, while the dev venv kept the 0.x it had
# installed months earlier. Every test above passed, because every test above
# only inspects the dict we build; none of them ever asked whether the SDK we
# ship would accept it.
#
# These two tests close that gap. They cost no API call: `_to_anthropic_params`
# produces the kwargs, and `inspect.signature` says whether the *installed* SDK
# takes them. If a future bump removes or renames a parameter we send, this
# fails in CI instead of in a user's case.


def _maximal_request() -> CompletionRequest:
    """A request that exercises every branch of ``_to_anthropic_params``.

    Each optional field is populated so the resulting dict carries the widest
    key set the translation can emit — a narrower request would let a removed
    parameter slip through unnoticed.
    """
    return CompletionRequest(
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
        stop_sequences=["STOP"],
        metadata={"user_id": "abc"},
        cache_system=True,
        cache_tools=True,
    )


def _accepted_kwargs(method: object) -> set[str] | None:
    """The kwarg names ``method`` accepts, or ``None`` if it accepts anything.

    ``None`` means the signature carries ``**kwargs`` and so proves nothing —
    the caller must treat that as "cannot tell", not as "accepts none".
    """
    params = inspect.signature(method).parameters  # type: ignore[arg-type]
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    return {name for name in params if name != "self"}


def test_every_emitted_param_is_accepted_by_the_installed_sdk_create():
    """Guards against an SDK bump dropping a parameter we always send.

    This is the exact break that took prod down: the kwargs dict was fine, the
    installed SDK's signature was not.
    """
    from anthropic.resources.messages import AsyncMessages

    params = _gateway()._to_anthropic_params(_maximal_request())
    accepted = _accepted_kwargs(AsyncMessages.create)
    if accepted is None:
        return

    unsupported = sorted(set(params) - accepted)
    assert not unsupported, (
        f"AnthropicGateway sends {unsupported}, which the installed anthropic "
        f"SDK's AsyncMessages.create() does not accept. Passing these raises "
        f"TypeError at request time — in production, not in this suite. Either "
        f"pin the SDK back or update _to_anthropic_params for the new surface."
    )


def test_every_emitted_param_is_accepted_by_the_installed_sdk_stream():
    """``stream()`` takes the same dict as ``create()`` and is just as able to
    drift out from under it — AnthropicGateway.stream() shares
    ``_to_anthropic_params``, so a removed parameter breaks streaming chat too.
    """
    from anthropic.resources.messages import AsyncMessages

    params = _gateway()._to_anthropic_params(_maximal_request())
    accepted = _accepted_kwargs(AsyncMessages.stream)
    if accepted is None:
        return

    unsupported = sorted(set(params) - accepted)
    assert not unsupported, (
        f"AnthropicGateway sends {unsupported}, which the installed anthropic "
        f"SDK's AsyncMessages.stream() does not accept."
    )
