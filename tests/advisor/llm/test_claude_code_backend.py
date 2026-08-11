"""``claude -p`` transport + gateway class (ABS-455).

Every subprocess call here is faked: nothing in this file may reach a
real ``claude`` binary, which is why the suite is expected to stay green
under ``PATH=/nonexistent``. What the fake lets us pin down is the part
that costs money when it is wrong — the argv (the two flags that keep
the agentic loop on our side), the retry ladder, and the absence of any
path that quietly reaches for the metered API backend instead.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path

import pytest

from advisor.llm.base import (
    CompletionRequest,
    LLMGateway,
    LLMRole,
    Message,
    MessageStartEvent,
    MessageStopEvent,
    ToolDefinition,
    ToolUseBlock,
)
from advisor.llm.claude_code_backend import (
    ARGV_PROMPT_LIMIT_BYTES,
    AUTOCOMPACT_THRESHOLD,
    DISALLOWED_TOOLS,
    ClaudeCodeGateway,
    ClaudeCodeGatewayError,
)

MODEL = "claude-opus-4-5"


# -- fixtures ----------------------------------------------------------------


def _tool() -> ToolDefinition:
    return ToolDefinition(
        name="lookup_bylaw",
        description="Look up a bylaw section.",
        input_schema={
            "type": "object",
            "required": ["section"],
            "properties": {"section": {"type": "string"}},
        },
    )


def _request(**overrides) -> CompletionRequest:
    kwargs = {
        "model": MODEL,
        "system": "You are a municipal bylaw advisor.",
        "messages": [Message(role=LLMRole.USER, content="Is a shed allowed?")],
        "tools": [_tool()],
    }
    kwargs.update(overrides)
    return CompletionRequest(**kwargs)


def _payload(structured: dict | None = None, **extra) -> str:
    payload = {
        "uuid": "5f1c2f7e-0d1a-4c0e-9a11-2b3c4d5e6f70",
        "usage": {"input_tokens": 120, "output_tokens": 34},
    }
    if structured is not None:
        payload["structured_output"] = structured
    payload.update(extra)
    return json.dumps(payload)


def _final_answer(text: str = "A shed under 10 m2 is permitted.") -> str:
    return _payload({"action": "final_answer", "text": text})


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        delay: float = 0.0,
    ) -> None:
        self._stdout = stdout.encode("utf-8")
        self._stderr = stderr.encode("utf-8")
        self.returncode = returncode
        self._delay = delay
        self.killed = False
        self.stdin_written: bytes | None = None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_written = input
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


class _Spawner:
    """Stands in for ``asyncio.create_subprocess_exec``.

    Records every argv it is handed and replays ``results`` in order,
    repeating the last one once the script runs out, so a test can say
    "always fail" without counting attempts.
    """

    def __init__(self, results: list[_FakeProcess]) -> None:
        self._results = results
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    async def __call__(self, *argv, **kwargs) -> _FakeProcess:
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        index = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[index]

    # -- argv accessors used by the assertions below
    def argv(self, index: int = 0) -> list[str]:
        return self.calls[index]

    def flag(self, name: str, index: int = 0) -> str:
        argv = self.argv(index)
        return argv[argv.index(name) + 1]

    def prompt(self, index: int = 0) -> str:
        return self.flag("-p", index)


@pytest.fixture
def spawn(monkeypatch):
    """Install a scriptable fake spawner; returns the installer."""

    def _install(*results: _FakeProcess) -> _Spawner:
        spawner = _Spawner(list(results) or [_FakeProcess(stdout=_final_answer())])
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
        return spawner

    return _install


def _ok(stdout: str) -> _FakeProcess:
    return _FakeProcess(stdout=stdout)


# -- DoD 1: protocol conformance ---------------------------------------------


def test_gateway_satisfies_the_llm_gateway_protocol():
    """DoD 1. Bare class, no base — conformance has to be structural,
    the same way the other two backends get it."""
    gateway = ClaudeCodeGateway(cli_path="/usr/local/bin/claude")
    assert isinstance(gateway, LLMGateway)
    assert gateway.name == "claude_code"


# -- DoD 2-4: argv ------------------------------------------------------------


async def test_argv_carries_the_flags_that_keep_the_loop_on_our_side(spawn):
    """DoD 2. ``--disallowedTools`` is what demotes the CLI from agent to
    completion engine, and ``--autocompact`` keeps it from rewriting the
    history we bill and cite from. Both are load-bearing, so both are
    pinned here rather than left to review."""
    spawner = spawn(_ok(_final_answer()))
    await ClaudeCodeGateway(cli_path="claude").complete(_request())

    argv = spawner.argv()
    assert spawner.flag("--output-format") == "json"

    schema = json.loads(spawner.flag("--json-schema"))
    assert set(schema["properties"]["action"]["enum"]) == {
        "tool_calls",
        "final_answer",
    }

    disallowed = spawner.flag("--disallowedTools")
    assert set(re.split(r"[,\s]+", disallowed)) == set(DISALLOWED_TOOLS)
    for builtin in ("Bash", "Read", "Write", "Edit", "WebSearch", "Task"):
        assert builtin in disallowed

    assert spawner.flag("--autocompact") == "1000000"
    assert AUTOCOMPACT_THRESHOLD == 1_000_000
    # Well above the advisor's own ~165k cumulative-token breaker, so
    # ours trips first.
    assert AUTOCOMPACT_THRESHOLD > 165_000

    assert argv[0] == "claude"
    assert argv[1] == "-p"


async def test_request_model_is_the_value_of_the_model_flag(spawn):
    """DoD 3. The two-model split (main vs classifier) is per-request, so
    a gateway that pinned one model would silently bill Opus rates for
    the Haiku pre-flight."""
    spawner = spawn(_ok(_final_answer()))
    await ClaudeCodeGateway(cli_path="claude").complete(
        _request(model="claude-haiku-4-5")
    )
    assert spawner.flag("--model") == "claude-haiku-4-5"


async def test_system_prompt_travels_on_append_system_prompt_once(spawn):
    """DoD 4. It goes in the CLI's system slot, and *only* there — the
    rendered body drops it so a large advisor persona isn't paid for
    twice on every turn."""
    spawner = spawn(_ok(_final_answer()))
    system = "You are a municipal bylaw advisor."
    await ClaudeCodeGateway(cli_path="claude").complete(_request(system=system))

    assert spawner.flag("--append-system-prompt") == system
    assert system not in spawner.prompt()
    # The tool menu and protocol rules still ride in the prompt body.
    assert "lookup_bylaw" in spawner.prompt()


async def test_tool_less_request_omits_the_system_flag(spawn):
    """A request with no system prompt must not pass an empty string —
    the CLI would take it as an instruction to append nothing useful."""
    spawner = spawn(_ok(_final_answer()))
    await ClaudeCodeGateway(cli_path="claude").complete(
        _request(system=None, tools=[])
    )
    assert "--append-system-prompt" not in spawner.argv()


async def test_a_normal_prompt_rides_on_the_p_operand(spawn):
    """The specced command line, and the one live validation will
    exercise first: prompt in argv, nothing on stdin."""
    process = _ok(_final_answer())
    spawner = spawn(process)
    await ClaudeCodeGateway(cli_path="claude").complete(_request())

    assert spawner.prompt() != ""
    assert spawner.kwargs[0]["stdin"] is None
    assert process.stdin_written is None


async def test_an_oversized_prompt_moves_to_stdin(spawn):
    """Linux caps a single argv entry at 128 KiB and fails the exec with
    E2BIG past it — about 32k tokens, well inside what the advisor's
    165k cumulative cap permits. Left in argv, a normal research
    conversation would die with an OSError that says nothing about
    prompts."""
    process = _ok(_final_answer())
    spawner = spawn(process)
    huge = "The lot line is disputed. " * 6_000  # ~150 KB
    await ClaudeCodeGateway(cli_path="claude").complete(
        _request(messages=[Message(role=LLMRole.USER, content=huge)])
    )

    argv = spawner.argv()
    assert "-p" in argv
    # The operand is gone — every remaining entry is a flag or a small
    # value, so no single one can trip the ceiling.
    assert argv[argv.index("-p") + 1] == "--model"
    assert max(len(a.encode()) for a in argv) < ARGV_PROMPT_LIMIT_BYTES

    assert spawner.kwargs[0]["stdin"] is not None
    assert process.stdin_written is not None
    assert huge[:200] in process.stdin_written.decode("utf-8")


# -- success paths ------------------------------------------------------------


async def test_final_answer_envelope_becomes_an_end_turn_response(spawn):
    spawn(_ok(_final_answer("A shed under 10 m2 is permitted.")))
    response = await ClaudeCodeGateway(cli_path="claude").complete(_request())

    assert response.stop_reason == "end_turn"
    assert response.model == MODEL
    assert [b.text for b in response.content] == [
        "A shed under 10 m2 is permitted."
    ]
    assert response.usage is not None
    assert response.usage.input_tokens == 120


async def test_tool_calls_envelope_becomes_tool_use_blocks(spawn):
    spawn(
        _ok(
            _payload(
                {
                    "action": "tool_calls",
                    "tool_calls": [
                        {"name": "lookup_bylaw", "input": {"section": "4.2"}}
                    ],
                }
            )
        )
    )
    response = await ClaudeCodeGateway(cli_path="claude").complete(_request())

    assert response.stop_reason == "tool_use"
    block = response.content[0]
    assert isinstance(block, ToolUseBlock)
    assert block.name == "lookup_bylaw"
    assert block.input == {"section": "4.2"}


# -- DoD 5-8: failure ladder --------------------------------------------------


async def test_non_zero_exit_is_retried_then_raises(spawn):
    """DoD 5."""
    spawner = spawn(_FakeProcess(returncode=1, stderr="boom"))
    gateway = ClaudeCodeGateway(cli_path="claude", max_retries=3)

    with pytest.raises(ClaudeCodeGatewayError) as excinfo:
        await gateway.complete(_request())

    assert len(spawner.calls) == 3
    assert "after 3 attempts" in str(excinfo.value)
    assert "boom" in str(excinfo.value)


async def test_invalid_json_stdout_is_retried_then_raises(spawn):
    """DoD 6. A CLI that prints a banner or a bare error line looks
    exactly like this, and it is worth one more shot before failing."""
    spawner = spawn(_ok("Welcome to Claude Code!\nnot json"))
    gateway = ClaudeCodeGateway(cli_path="claude", max_retries=2)

    with pytest.raises(ClaudeCodeGatewayError) as excinfo:
        await gateway.complete(_request())

    assert len(spawner.calls) == 2
    assert "not valid JSON" in str(excinfo.value)


async def test_payload_missing_structured_output_raises(spawn):
    """DoD 7. ``--json-schema`` is what puts the field there; a payload
    without it means the envelope contract was not honoured at all."""
    spawner = spawn(_ok(_payload(None, result="here is some prose")))
    gateway = ClaudeCodeGateway(cli_path="claude", max_retries=2)

    with pytest.raises(ClaudeCodeGatewayError) as excinfo:
        await gateway.complete(_request())

    assert len(spawner.calls) == 2
    assert "structured_output" in str(excinfo.value)


async def test_payload_is_error_raises(spawn):
    """DoD 8. Exit code 0 with ``is_error: true`` is how the CLI reports
    an upstream API failure; treating it as success would feed the tool
    loop an empty turn."""
    spawner = spawn(
        _ok(
            _payload(
                {"action": "final_answer", "text": "ignored"},
                is_error=True,
                result="Credit balance too low",
            )
        )
    )
    gateway = ClaudeCodeGateway(cli_path="claude", max_retries=1)

    with pytest.raises(ClaudeCodeGatewayError) as excinfo:
        await gateway.complete(_request())

    assert len(spawner.calls) == 1
    assert "Credit balance too low" in str(excinfo.value)


async def test_invalid_envelope_is_retried_then_raises(spawn):
    """A hallucinated tool name is model output, not a dead transport,
    so it takes the same self-repair ladder as a malformed reply."""
    spawner = spawn(
        _ok(
            _payload(
                {
                    "action": "tool_calls",
                    "tool_calls": [{"name": "rm_rf", "input": {}}],
                }
            )
        )
    )
    gateway = ClaudeCodeGateway(cli_path="claude", max_retries=2)

    with pytest.raises(ClaudeCodeGatewayError) as excinfo:
        await gateway.complete(_request())

    assert len(spawner.calls) == 2
    assert "rm_rf" in str(excinfo.value)


async def test_a_later_attempt_can_succeed(spawn):
    """The retry ladder exists to recover, not just to fail slower."""
    spawner = spawn(_ok("not json"), _ok(_final_answer("recovered")))
    response = await ClaudeCodeGateway(cli_path="claude", max_retries=3).complete(
        _request()
    )

    assert len(spawner.calls) == 2
    assert response.content[0].text == "recovered"


async def test_missing_cli_binary_fails_immediately_without_retrying(monkeypatch):
    """A missing binary is the same on attempt 3 as on attempt 1 — and a
    slow triple failure would look like a model problem in the logs."""
    calls: list[int] = []

    async def _boom(*_argv, **_kwargs):
        calls.append(1)
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    gateway = ClaudeCodeGateway(cli_path="/nonexistent/claude", max_retries=3)

    with pytest.raises(ClaudeCodeGatewayError) as excinfo:
        await gateway.complete(_request())

    assert len(calls) == 1
    assert "not found" in str(excinfo.value)


async def test_timeout_kills_the_subprocess_and_retries(spawn):
    """A hung CLI holds a subscription slot; leaving it running would
    stack one zombie per attempt."""
    hung = _FakeProcess(stdout=_final_answer(), delay=5)
    spawner = spawn(hung)
    gateway = ClaudeCodeGateway(cli_path="claude", max_retries=2, timeout_s=0.01)

    with pytest.raises(ClaudeCodeGatewayError) as excinfo:
        await gateway.complete(_request())

    assert hung.killed is True
    assert len(spawner.calls) == 2
    assert "timed out" in str(excinfo.value)


# -- DoD 9: self-repair prompt ------------------------------------------------


async def test_retry_appends_the_prior_error_to_the_prompt(spawn):
    """DoD 9. Strictly additive: the model sees everything it saw last
    time plus what went wrong, which is what makes the second attempt
    worth spending."""
    spawner = spawn(_ok("not json at all"))
    gateway = ClaudeCodeGateway(cli_path="claude", max_retries=2)

    with pytest.raises(ClaudeCodeGatewayError):
        await gateway.complete(_request())

    first, second = spawner.prompt(0), spawner.prompt(1)
    assert second.startswith(first)
    assert len(second) > len(first)
    suffix = second[len(first) :]
    assert "PREVIOUS ATTEMPT FAILED" in suffix
    assert "not valid JSON" in suffix


# -- DoD 10: no fallback to the metered backend -------------------------------


def test_module_never_reaches_for_the_metered_api_backend():
    """DoD 10. Silent fallback is the surprise-charge failure mode this
    whole backend exists to prevent, so the transport is not merely
    told not to fall back — it has no way to."""
    import advisor.llm.claude_code_backend as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    lowered = source.lower()
    for banned in ("anthropicgateway", "asyncanthropic", "anthropic"):
        assert banned not in lowered, f"{banned!r} leaked into the transport"
    assert not re.search(r"^\s*(from|import)\s+anthropic", source, re.MULTILINE)


def test_module_imports_with_the_sdk_unavailable(monkeypatch):
    """DoD 10, the other half: a source grep can be fooled by an
    indirect import. Load the module fresh with ``anthropic`` poisoned
    in ``sys.modules`` — anything reaching it, at any depth, raises."""
    import advisor.llm.claude_code_backend as module

    monkeypatch.setitem(sys.modules, "anthropic", None)
    spec = importlib.util.spec_from_file_location(
        "_claude_code_backend_isolated", module.__file__
    )
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)  # raises ImportError if it touches the SDK

    assert isolated.ClaudeCodeGateway.name == "claude_code"


# -- DoD 11: the event loop stays free ----------------------------------------


async def test_complete_does_not_block_the_event_loop(spawn):
    """DoD 11. This is why the sync layer-1 helper is mirrored rather
    than imported: ``subprocess.run`` would freeze every other request
    in the worker for the whole 5-30s model turn."""
    spawn(_FakeProcess(stdout=_final_answer(), delay=0.05))

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.001)
            ticks += 1

    ticker = asyncio.create_task(_ticker())
    try:
        response = await ClaudeCodeGateway(cli_path="claude").complete(_request())
    finally:
        ticker.cancel()

    assert response.stop_reason == "end_turn"
    assert ticks > 1, "no other task ran while complete() was awaiting the CLI"


# -- dropped fields -----------------------------------------------------------


async def test_dropped_fields_are_warned_once_each(spawn, caplog):
    """Dropped-but-documented. A caller who set ``temperature=0`` for a
    deterministic classifier is not getting it, and finding that out
    from a wrong answer is much more expensive than a log line."""
    spawn(_ok(_final_answer()), _ok(_final_answer()))
    gateway = ClaudeCodeGateway(cli_path="claude")
    request = _request(
        max_tokens=64_000,
        temperature=0.0,
        stop_sequences=["STOP"],
        metadata={"user_id": "u1"},
        cache_system=True,
        cache_tools=True,
    )

    with caplog.at_level(logging.WARNING, logger="advisor.llm.claude_code_backend"):
        await gateway.complete(request)
        await gateway.complete(request)

    warned = [r.getMessage() for r in caplog.records if "drops request." in r.getMessage()]
    for field in (
        "max_tokens",
        "temperature",
        "stop_sequences",
        "metadata",
        "cache_system",
        "cache_tools",
    ):
        hits = [m for m in warned if f"request.{field}=" in m]
        assert len(hits) == 1, f"expected exactly one warning for {field}, got {hits}"


async def test_untouched_defaults_do_not_warn(spawn, caplog):
    """Only fields the caller actually moved are reported — warning on
    the default ``temperature=0.7`` would make the signal worthless."""
    spawn(_ok(_final_answer()))
    with caplog.at_level(logging.WARNING, logger="advisor.llm.claude_code_backend"):
        await ClaudeCodeGateway(cli_path="claude").complete(_request())

    assert not [r for r in caplog.records if "drops request." in r.getMessage()]


# -- stream -------------------------------------------------------------------


async def test_stream_synthesises_the_standard_event_order(spawn):
    """Protocol completeness only — production synthesises its SSE
    downstream of the tool loop. Still has to emit the shape streaming
    consumers expect, or it is a trap for the first caller who tries."""
    spawn(_ok(_final_answer("permitted")))
    events = [
        event
        async for event in ClaudeCodeGateway(cli_path="claude").stream(_request())
    ]

    assert isinstance(events[0], MessageStartEvent)
    assert isinstance(events[-1], MessageStopEvent)
    assert [e.type for e in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[2].text_delta == "permitted"
    assert events[-2].stop_reason == "end_turn"
    assert events[-2].usage is not None


async def test_stream_carries_tool_use_input_as_json_delta(spawn):
    spawn(
        _ok(
            _payload(
                {
                    "action": "tool_calls",
                    "tool_calls": [
                        {"name": "lookup_bylaw", "input": {"section": "4.2"}}
                    ],
                }
            )
        )
    )
    events = [
        event
        async for event in ClaudeCodeGateway(cli_path="claude").stream(_request())
    ]

    start = next(e for e in events if e.type == "content_block_start")
    assert isinstance(start.content_block, ToolUseBlock)
    delta = next(e for e in events if e.type == "content_block_delta")
    assert json.loads(delta.input_json_delta) == {"section": "4.2"}


# -- construction -------------------------------------------------------------


def test_max_retries_must_allow_at_least_one_attempt():
    with pytest.raises(ValueError):
        ClaudeCodeGateway(max_retries=0)


def test_cli_path_defaults_to_the_resolved_binary(monkeypatch):
    """Resolution happens once at construction; a PATH-less environment
    still yields a usable argv[0] rather than an exception at import."""
    monkeypatch.setattr(
        "advisor.llm.claude_code_backend.shutil.which", lambda _name: None
    )
    assert ClaudeCodeGateway()._cli_path == "claude"

    monkeypatch.setattr(
        "advisor.llm.claude_code_backend.shutil.which",
        lambda _name: "/opt/homebrew/bin/claude",
    )
    assert ClaudeCodeGateway()._cli_path == "/opt/homebrew/bin/claude"
