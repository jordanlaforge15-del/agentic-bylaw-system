"""Unit tests for the Claude Code headless backend shim.

All subprocess calls are mocked — no real ``claude -p`` invocations and
no network. The shim's contract is purely about request/response shape
translation between the Anthropic SDK interface the agents expect and
the ``claude -p --output-format json --json-schema`` invocation it
actually performs.
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from layer1._claude_code_client import (
    ClaudeCodeBackendError,
    ClaudeCodeClient,
)


# ---------------------------------------------------------------- helpers
def _success_payload(structured: dict) -> str:
    """The shape claude -p --output-format json returns on success."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "structured_output": structured,
            "session_id": "fake",
            "total_cost_usd": 0.0,
        }
    )


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    """Build a CompletedProcess-shaped mock."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


_DEMO_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def _call_create(client: ClaudeCodeClient, *, user_text: str = "hi"):
    """Wraps the agent-side call shape so each test doesn't repeat it."""
    return client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system="you are helpful",
        tools=[{"name": "report_answer", "input_schema": _DEMO_SCHEMA}],
        tool_choice={"type": "tool", "name": "report_answer"},
        messages=[{"role": "user", "content": user_text}],
    )


# ---------------------------------------------------------------- success
def test_translates_successful_response_to_tool_use_shape():
    """Happy path: claude -p returns structured_output → shim returns an
    object whose .content[0] mimics an Anthropic SDK tool_use block."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = _completed(_success_payload({"answer": "42"}))
        client = ClaudeCodeClient()
        response = _call_create(client)

    block = response.content[0]
    assert block.type == "tool_use"
    assert block.name == "report_answer"
    assert block.input == {"answer": "42"}


def test_cli_invocation_uses_required_flags():
    """The subprocess call must include --output-format json and the
    schema JSON via --json-schema, with the prompt as the second arg
    after `-p`."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = _completed(_success_payload({"answer": "ok"}))
        client = ClaudeCodeClient(cli_path="/usr/local/bin/claude")
        _call_create(client, user_text="what's the deal")

    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "/usr/local/bin/claude"
    assert cmd[1] == "-p"
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
    assert "--json-schema" in cmd
    schema_str = cmd[cmd.index("--json-schema") + 1]
    assert json.loads(schema_str) == _DEMO_SCHEMA


def test_prompt_includes_system_and_user_and_tool_name():
    """The combined prompt must carry the system text, the user text, and
    the tool name so the model knows what to fill out."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = _completed(_success_payload({"answer": "ok"}))
        client = ClaudeCodeClient()
        _call_create(client, user_text="USER_MARKER_42")

    prompt = mock_run.call_args.args[0][2]
    assert "you are helpful" in prompt
    assert "USER_MARKER_42" in prompt
    assert "report_answer" in prompt


def test_flattens_sdk_style_system_blocks_with_cache_control():
    """ABS-94 introduces system blocks like [{"type":"text","text":...,
    "cache_control":...}]. Shim should reduce to the joined text and
    silently drop cache_control (Claude Code does its own caching)."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = _completed(_success_payload({"answer": "ok"}))
        client = ClaudeCodeClient()
        client.messages.create(
            model="x", max_tokens=1,
            system=[
                {"type": "text", "text": "system part 1",
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "system part 2"},
            ],
            tools=[{"name": "report_answer", "input_schema": _DEMO_SCHEMA}],
            tool_choice={"type": "tool", "name": "report_answer"},
            messages=[{"role": "user", "content": "u"}],
        )

    prompt = mock_run.call_args.args[0][2]
    assert "system part 1" in prompt
    assert "system part 2" in prompt
    # cache_control should not leak into the prompt body
    assert "cache_control" not in prompt
    assert "ephemeral" not in prompt


# ---------------------------------------------------------------- failure
def test_loud_failure_on_nonzero_exit_after_retries():
    """Subprocess non-zero exit → retry up to max_retries → raise."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = _completed("", returncode=1, stderr="boom")
        client = ClaudeCodeClient(max_retries=3)
        with pytest.raises(ClaudeCodeBackendError, match=r"after 3 attempts"):
            _call_create(client)
    assert mock_run.call_count == 3, "Should have tried max_retries times"


def test_loud_failure_on_invalid_json_stdout():
    """If claude -p emits non-JSON stdout, raise without falling back."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = _completed("not even json{")
        client = ClaudeCodeClient(max_retries=1)
        with pytest.raises(ClaudeCodeBackendError, match=r"not valid JSON"):
            _call_create(client)


def test_loud_failure_on_missing_structured_output():
    """If the response is valid JSON but lacks structured_output, raise."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = _completed(json.dumps({
            "is_error": False, "result": "plain text response, no schema",
        }))
        client = ClaudeCodeClient(max_retries=1)
        with pytest.raises(ClaudeCodeBackendError, match=r"missing dict 'structured_output'"):
            _call_create(client)


def test_loud_failure_on_is_error_true():
    """is_error=true in the response → raise immediately on that attempt."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.return_value = _completed(json.dumps({
            "is_error": True, "result": "rate limited", "api_error_status": "429",
        }))
        client = ClaudeCodeClient(max_retries=1)
        with pytest.raises(ClaudeCodeBackendError, match=r"is_error=true"):
            _call_create(client)


def test_retry_succeeds_after_one_failure():
    """First attempt fails (non-zero exit), second succeeds. Result returned."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.side_effect = [
            _completed("", returncode=1, stderr="transient"),
            _completed(_success_payload({"answer": "recovered"})),
        ]
        client = ClaudeCodeClient(max_retries=3)
        response = _call_create(client)
    assert response.content[0].input == {"answer": "recovered"}
    assert mock_run.call_count == 2


def test_retry_prompt_appends_error_for_repair():
    """The second attempt's prompt should include the previous error so
    the model can self-correct."""
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.side_effect = [
            _completed("garbage{"),
            _completed(_success_payload({"answer": "ok"})),
        ]
        client = ClaudeCodeClient(max_retries=2)
        _call_create(client)
    second_prompt = mock_run.call_args_list[1].args[0][2]
    assert "PREVIOUS ATTEMPT FAILED" in second_prompt


def test_loud_failure_on_timeout():
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=5)
        client = ClaudeCodeClient(max_retries=1, timeout_s=5)
        with pytest.raises(ClaudeCodeBackendError, match=r"timed out"):
            _call_create(client)


def test_loud_failure_on_missing_cli():
    with patch("layer1._claude_code_client.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        client = ClaudeCodeClient(max_retries=1, cli_path="/nonexistent/claude")
        with pytest.raises(ClaudeCodeBackendError, match=r"not found"):
            _call_create(client)


# ---------------------------------------------------------------- validation
def test_rejects_multi_tool_request():
    """The shim only supports forced-single-tool calls (Anthropic
    tool_choice={'type':'tool','name':...}). Multi-tool requests would
    require schema synthesis we haven't implemented."""
    client = ClaudeCodeClient(max_retries=1)
    with pytest.raises(ClaudeCodeBackendError, match=r"exactly one tool"):
        client.messages.create(
            model="x", max_tokens=1, system="",
            tools=[
                {"name": "a", "input_schema": _DEMO_SCHEMA},
                {"name": "b", "input_schema": _DEMO_SCHEMA},
            ],
            tool_choice={"type": "tool", "name": "a"},
            messages=[{"role": "user", "content": "u"}],
        )


def test_rejects_unforced_tool_choice():
    """If tool_choice doesn't force the only tool, raise — we have no way
    to express 'maybe call this tool' in --json-schema mode."""
    client = ClaudeCodeClient(max_retries=1)
    with pytest.raises(ClaudeCodeBackendError, match=r"tool_choice must"):
        client.messages.create(
            model="x", max_tokens=1, system="",
            tools=[{"name": "a", "input_schema": _DEMO_SCHEMA}],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": "u"}],
        )


def test_rejects_multi_message_input():
    """The agents always send a single user message — multi-turn would
    require we maintain a session, which the headless backend doesn't
    cleanly support across subprocess invocations."""
    client = ClaudeCodeClient(max_retries=1)
    with pytest.raises(ClaudeCodeBackendError, match=r"single user message"):
        client.messages.create(
            model="x", max_tokens=1, system="",
            tools=[{"name": "a", "input_schema": _DEMO_SCHEMA}],
            tool_choice={"type": "tool", "name": "a"},
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "..."},
                {"role": "user", "content": "second"},
            ],
        )
