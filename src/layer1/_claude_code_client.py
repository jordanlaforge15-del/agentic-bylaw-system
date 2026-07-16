"""Drop-in replacement for ``anthropic.Anthropic()`` that routes calls through
the Claude Code CLI in headless mode (``claude -p``).

Why this exists
---------------
The three learning-pipeline agents (Discovery, StructureAnalyst,
SemanticMapper) call ``client.messages.create(...)`` with the Anthropic
SDK. That bills at API per-token rates regardless of whether the user
holds a Claude Max subscription. The SDK has no path to authenticate
against a subscription — only API keys.

The Claude Code CLI, however, runs against the user's subscription. By
exec'ing ``claude -p`` with the same prompt + an equivalent JSON schema,
we get the same structured output, billed against the subscription.

What this class is
------------------
A facade exposing the same ``client.messages.create(...)`` interface the
agents already use. Internally:

1. Translate the Anthropic API request into a ``claude -p`` invocation
   with ``--output-format json --json-schema <schema>``.
2. Run the subprocess.
3. Parse the ``structured_output`` field from the JSON response back
   into an object whose shape (``.content[0].type == "tool_use"``,
   ``.content[0].name``, ``.content[0].input``) matches what the
   Anthropic SDK would have returned for a forced tool call.

What this class is NOT
----------------------
* Not a transparent fallback. On any failure (non-zero exit code,
  malformed JSON, missing ``structured_output``, schema mismatch) it
  raises :class:`ClaudeCodeBackendError` after the configured number of
  retry attempts. It NEVER falls back to the Anthropic SDK. The user
  asked for loud failure — the design goal is "no surprise API charges."
* Not a full Anthropic SDK reimplementation. Only the call shape used
  by the three learning-pipeline agents is supported:
  ``messages.create(model=, max_tokens=, system=, tools=[one schema],
  tool_choice={"type":"tool","name":...}, messages=[{"role":"user",...}])``

Caveats
-------
* **June 15, 2026.** Anthropic has announced that on subscription plans,
  Agent SDK / ``claude -p`` usage will draw from a separate monthly
  "Agent SDK credit" rather than the shared interactive subscription
  pool. Until then, headless usage counts against the normal allocation.
  After that date, capacity may be more constrained. See ABS-107.
* **Prompt caching.** ABS-94's ``cache_control`` annotations on the
  Anthropic SDK system blocks may not propagate identically through the
  subprocess boundary. Claude Code does its own caching internally
  (evidenced by ``cache_read_input_tokens`` in the JSON response), but
  whether sequential calls hit the same cache as they would on the SDK
  path is unverified.
* **Latency.** Each call spends 5-30 seconds in subprocess + model time
  (vs 3-10s for a direct SDK call). A full SemanticMapper run goes from
  ~3 min to ~10-15 min. Free, but slower.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)


CLAUDE_CLI = "claude"
DEFAULT_TIMEOUT_S = 300
DEFAULT_MAX_RETRIES = 3


class ClaudeCodeBackendError(RuntimeError):
    """Raised when the Claude Code headless backend fails to produce a valid
    structured response. Carries the last subprocess stderr / parse error for
    diagnostics. Intentionally distinct from :class:`anthropic.APIError`
    subtypes so callers can tell the two backends apart.
    """


@dataclass
class _ToolUseBlock:
    """Mimics the shape of an Anthropic SDK tool_use content block."""

    type: str
    name: str
    input: dict[str, Any]


@dataclass
class _Response:
    """Mimics the minimal shape of an Anthropic SDK response that the agents
    consume — ``response.content`` is iterated for ``tool_use`` blocks.
    """

    content: list[_ToolUseBlock]


class _MessagesNamespace:
    """Mirrors ``client.messages`` on the Anthropic SDK."""

    def __init__(self, owner: "ClaudeCodeClient") -> None:
        self._owner = owner

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: Any,
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
        messages: list[dict[str, Any]],
        **_ignored: Any,
    ) -> _Response:
        """Forced-tool-call create. Same surface as Anthropic SDK.

        ``model`` is currently advisory — ``claude -p`` picks the model
        from the user's Claude Code config and routes Haiku/Opus internally.
        We log a debug line so requests don't silently lose their model
        intent, but we don't enforce it.

        ``max_tokens`` is currently ignored: ``claude -p`` doesn't expose
        an equivalent flag in headless mode. The agents' existing 2048-4096
        ceilings exist to bound API cost; on subscription that's not the
        primary constraint.

        ``system`` accepts either the SDK's list-of-blocks shape (with
        ``cache_control`` annotations from ABS-94) or a plain string. We
        flatten to a string for ``claude -p``; ``cache_control`` is
        silently dropped — Claude Code does its own caching.

        ``tools`` must contain exactly one tool whose ``input_schema``
        becomes the ``--json-schema`` argument. ``tool_choice`` must force
        that one tool. Anything else raises.
        """
        if len(tools) != 1:
            raise ClaudeCodeBackendError(
                f"ClaudeCodeClient requires exactly one tool, got {len(tools)}"
            )
        tool = tools[0]
        tool_name = tool.get("name")
        if not tool_name:
            raise ClaudeCodeBackendError("Tool definition missing 'name'")
        if tool_choice.get("type") != "tool" or tool_choice.get("name") != tool_name:
            raise ClaudeCodeBackendError(
                f"tool_choice must be {{type:tool,name:{tool_name!r}}} — "
                f"the headless backend only supports forced-tool-call mode."
            )
        input_schema = tool.get("input_schema")
        if not isinstance(input_schema, dict):
            raise ClaudeCodeBackendError(
                f"Tool {tool_name!r} missing dict 'input_schema'"
            )

        system_text = self._flatten_system(system)
        user_text = self._flatten_messages(messages)
        prompt = self._build_prompt(system_text, user_text, tool_name, input_schema)

        logger.debug(
            "ClaudeCodeClient: calling claude -p for tool %r (model %s, ~%d prompt chars)",
            tool_name, model, len(prompt),
        )

        structured = self._owner._invoke_with_retry(prompt, input_schema)
        return _Response(
            content=[
                _ToolUseBlock(type="tool_use", name=tool_name, input=structured)
            ]
        )

    # --------------------------------------------------------- helpers
    @staticmethod
    def _flatten_system(system: Any) -> str:
        """SDK ``system`` may be a string or a list of content blocks
        (with optional ``cache_control``). Reduce to a single string."""
        if isinstance(system, str):
            return system
        if isinstance(system, list):
            parts: list[str] = []
            for block in system:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return str(system)

    @staticmethod
    def _flatten_messages(messages: list[dict[str, Any]]) -> str:
        """The agents only ever send a single user message with string
        content. Anything else is unexpected — raise so we catch surprises
        early rather than silently dropping content."""
        if len(messages) != 1:
            raise ClaudeCodeBackendError(
                f"Expected single user message, got {len(messages)}"
            )
        msg = messages[0]
        if msg.get("role") != "user":
            raise ClaudeCodeBackendError(
                f"Expected role=user, got {msg.get('role')!r}"
            )
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        raise ClaudeCodeBackendError(
            f"Unsupported user content shape: {type(content).__name__}"
        )

    @staticmethod
    def _build_prompt(
        system_text: str, user_text: str, tool_name: str, schema: dict[str, Any],
    ) -> str:
        """Combine the system prompt + user message into a single ``claude -p``
        prompt. Re-state the tool name in the user content so the model
        understands which "tool" it should be filling out — the SDK had
        ``tool_choice`` to force this, we don't.
        """
        return (
            f"{system_text}\n\n"
            f"You are being asked to fill out the {tool_name!r} tool.\n"
            f"Produce a single JSON object matching the tool's input schema.\n\n"
            f"--- INPUT ---\n{user_text}"
        )


class ClaudeCodeClient:
    """Drop-in replacement for ``anthropic.Anthropic()`` that routes through
    ``claude -p``. See the module docstring for the full rationale.

    .. code-block:: python

        # Before (Anthropic API, per-token billing):
        client = anthropic.Anthropic()

        # After (Claude Code headless, subscription billing):
        client = ClaudeCodeClient()

        # The rest of the call shape is unchanged:
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": user_text}],
        )

    Switching back to the Anthropic SDK requires editing that one line.
    There is intentionally no env-var toggle and no auto-detection — the
    design goal is "no surprise API charges."
    """

    def __init__(
        self,
        *,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        cli_path: str | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.cli_path = cli_path or shutil.which(CLAUDE_CLI) or CLAUDE_CLI
        self.messages = _MessagesNamespace(self)

    # ----------------------------------------------- subprocess invocation
    def _invoke_with_retry(
        self, prompt: str, schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Delegate to module-level :func:`call_claude_p_with_schema` —
        kept as an instance method so existing tests that patch this
        method continue to work."""
        return call_claude_p_with_schema(
            prompt, schema,
            timeout_s=self.timeout_s,
            max_retries=self.max_retries,
            cli_path=self.cli_path,
        )


# --------------------------------------------------------------------- public helper
def call_claude_p_with_schema(
    prompt: str,
    schema: dict[str, Any],
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    cli_path: str | None = None,
) -> dict[str, Any]:
    """Call ``claude -p --output-format json --json-schema <schema>`` and
    return the parsed ``structured_output`` dict.

    Use this when you have a flat prompt + a JSON schema and just want
    the model to fill it in — i.e. the call site doesn't need the
    Anthropic-SDK-shaped :class:`ClaudeCodeClient` wrapper because it
    isn't impersonating the SDK for an existing agent.

    Retries up to ``max_retries`` times; each retry appends the previous
    error to the prompt for self-correction. On exhaustion raises
    :class:`ClaudeCodeBackendError` — NEVER falls back to any other
    LLM provider.
    """
    resolved_cli = cli_path or shutil.which(CLAUDE_CLI) or CLAUDE_CLI
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return _invoke_claude_p_once(
                prompt, schema,
                cli_path=resolved_cli,
                timeout_s=timeout_s,
                attempt=attempt,
            )
        except ClaudeCodeBackendError as exc:
            last_error = str(exc)
            logger.warning(
                "call_claude_p_with_schema attempt %d/%d failed: %s",
                attempt, max_retries, exc,
            )
            if attempt < max_retries:
                prompt = (
                    f"{prompt}\n\n"
                    f"--- PREVIOUS ATTEMPT FAILED ---\n"
                    f"Your last response was invalid: {exc}\n"
                    f"Respond again with a JSON object matching the schema. "
                    f"No prose, no markdown fences."
                )
    raise ClaudeCodeBackendError(
        f"Claude Code backend failed after {max_retries} attempts. "
        f"Last error: {last_error}"
    )


def _invoke_claude_p_once(
    prompt: str,
    schema: dict[str, Any],
    *,
    cli_path: str,
    timeout_s: int,
    attempt: int,
) -> dict[str, Any]:
    """Single subprocess invocation. Returns parsed structured_output on
    success, raises :class:`ClaudeCodeBackendError` otherwise.
    """
    cmd = [
        cli_path, "-p", prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeBackendError(
            f"`claude -p` timed out after {timeout_s}s (attempt {attempt})"
        ) from exc
    except FileNotFoundError as exc:
        raise ClaudeCodeBackendError(
            f"`claude` CLI not found at {cli_path!r}. "
            f"Install Claude Code or pass cli_path explicitly."
        ) from exc

    if result.returncode != 0:
        raise ClaudeCodeBackendError(
            f"`claude -p` exited {result.returncode}: "
            f"stderr={result.stderr.strip()[:500]!r}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCodeBackendError(
            f"`claude -p` stdout was not valid JSON: {exc}. "
            f"Stdout prefix: {result.stdout[:300]!r}"
        ) from exc

    if payload.get("is_error"):
        raise ClaudeCodeBackendError(
            f"`claude -p` reported is_error=true: "
            f"{payload.get('result') or payload.get('api_error_status') or 'no details'}"
        )

    structured = payload.get("structured_output")
    if not isinstance(structured, dict):
        raise ClaudeCodeBackendError(
            "`claude -p` response missing dict 'structured_output' field. "
            f"Got keys: {list(payload.keys())}"
        )
    return structured


# --------------------------------------------------------------------- plain helper
def call_claude_p_plain(
    prompt: str,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    cli_path: str | None = None,
) -> str:
    """Call ``claude -p --output-format json`` without ``--json-schema`` and
    return the raw ``result`` string from the response.

    Use this when the caller expects free-form text (not structured JSON) —
    e.g. the Layer 2 RAG pipeline where the prompt asks for JSON but the
    protocol contract is ``generate(...) -> str``.

    Retries up to ``max_retries`` times. On exhaustion raises
    :class:`ClaudeCodeBackendError` — NEVER falls back to any other provider.
    """
    resolved_cli = cli_path or shutil.which(CLAUDE_CLI) or CLAUDE_CLI
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return _invoke_claude_p_plain_once(
                prompt,
                cli_path=resolved_cli,
                timeout_s=timeout_s,
                attempt=attempt,
            )
        except ClaudeCodeBackendError as exc:
            last_error = str(exc)
            logger.warning(
                "call_claude_p_plain attempt %d/%d failed: %s",
                attempt, max_retries, exc,
            )
    raise ClaudeCodeBackendError(
        f"Claude Code backend (plain) failed after {max_retries} attempts. "
        f"Last error: {last_error}"
    )


def _invoke_claude_p_plain_once(
    prompt: str,
    *,
    cli_path: str,
    timeout_s: int,
    attempt: int,
) -> str:
    """Single subprocess invocation without --json-schema. Returns the
    ``result`` field as a string."""
    cmd = [
        cli_path, "-p", prompt,
        "--output-format", "json",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeBackendError(
            f"`claude -p` timed out after {timeout_s}s (attempt {attempt})"
        ) from exc
    except FileNotFoundError as exc:
        raise ClaudeCodeBackendError(
            f"`claude` CLI not found at {cli_path!r}. "
            f"Install Claude Code or pass cli_path explicitly."
        ) from exc

    if result.returncode != 0:
        raise ClaudeCodeBackendError(
            f"`claude -p` exited {result.returncode}: "
            f"stderr={result.stderr.strip()[:500]!r}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCodeBackendError(
            f"`claude -p` stdout was not valid JSON: {exc}. "
            f"Stdout prefix: {result.stdout[:300]!r}"
        ) from exc

    if payload.get("is_error"):
        raise ClaudeCodeBackendError(
            f"`claude -p` reported is_error=true: "
            f"{payload.get('result') or payload.get('api_error_status') or 'no details'}"
        )

    text = payload.get("result")
    if not isinstance(text, str):
        raise ClaudeCodeBackendError(
            "`claude -p` response missing string 'result' field. "
            f"Got keys: {list(payload.keys())}"
        )
    return text
