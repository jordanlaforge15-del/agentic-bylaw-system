"""``claude -p`` transport + gateway class (ABS-455).

The translation half of this backend lives in
``advisor.llm.claude_code_translation`` (prompt in, envelope out, no
I/O). This module is the other half: it runs the CLI and assembles the
pieces into a class shaped like ``advisor.llm.base.LLMGateway``.

Why the CLI at all
------------------
The Messages API bills per token against an API key. ``claude -p``
bills against the operator's Claude Code subscription. Same model, same
structured output, different meter.

How the agentic loop stays on our side
--------------------------------------
``claude -p`` is itself an agent: given its built-in tools it will read
files, run commands, and iterate until it decides it is done, never
yielding control in between. The advisor needs the opposite — one
decision per call, so ``tool_loop.py`` can execute the handler, charge
the turn, and enforce the cost breakers between steps. Two flags buy
that back:

``--disallowedTools`` (every built-in, see :data:`DISALLOWED_TOOLS`)
    Leaves the CLI with nothing to act with, so the only move available
    to it is to answer in the envelope. The advisor's tools are
    described in the prompt text and run on our side.

``--autocompact 1000000``
    Pins the compaction threshold an order of magnitude above the
    advisor's own ~165k cumulative-token breaker, so our breaker always
    trips first. Left at the default, the CLI would silently rewrite
    conversation history mid-turn and the transcript we bill and cite
    from would no longer be the transcript the model saw.

Failure policy
--------------
On failure this retries (appending the previous error to the prompt for
self-repair, the pattern proven in ``src/layer1/_claude_code_client.py``)
and then raises :class:`ClaudeCodeGatewayError`. It **never** falls back
to the API-key backend. A silent fallback would turn "the CLI is
misconfigured" into a surprise metered bill, which is the exact failure
this whole backend exists to avoid — so the transport is deliberately
unable to construct any other gateway.

Why the layer-1 helper is mirrored rather than imported
-------------------------------------------------------
``src/layer1/_claude_code_client.py`` is synchronous
(``subprocess.run``); calling it from ``complete()`` would block the
event loop for the whole 5-30s model turn, stalling every other request
in the FastAPI worker. This module uses
``asyncio.create_subprocess_exec`` instead. The advisor also has no
business importing from layer 1 — that dependency edge doesn't exist
anywhere else and shouldn't start here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from typing import Any

from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    LLMRole,
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
)
from advisor.llm.claude_code_translation import (
    ClaudeCodeTranslationError,
    build_envelope_schema,
    envelope_to_response,
    render_prompt,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ARGV_PROMPT_LIMIT_BYTES",
    "AUTOCOMPACT_THRESHOLD",
    "CLAUDE_CLI",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_S",
    "DISALLOWED_TOOLS",
    "ClaudeCodeCLINotFoundError",
    "ClaudeCodeGateway",
    "ClaudeCodeGatewayError",
]

CLAUDE_CLI = "claude"
DEFAULT_TIMEOUT_S = 300
DEFAULT_MAX_RETRIES = 3

# Far above the advisor's own ~165k cumulative-token breaker, so ours
# fires first and the CLI never compacts a conversation out from under
# a turn we are mid-way through billing.
AUTOCOMPACT_THRESHOLD = 1_000_000

# Linux caps a single argv entry at 128 KiB (MAX_ARG_STRLEN, 32 pages)
# and fails the whole exec with E2BIG past it — roughly 32k tokens,
# well under the 165k the advisor is allowed to accumulate, so a real
# research conversation reaches it. Stay clear of the cliff and hand
# anything larger to the CLI on stdin instead.
ARGV_PROMPT_LIMIT_BYTES = 100_000

# Every built-in tool the CLI ships with. Disallowing the lot is what
# demotes ``claude -p`` from an agent to a completion engine; the
# advisor's own tools travel in the prompt and execute in
# ``tool_loop.py``. If a future CLI release adds a built-in, add it
# here — an unlisted tool is one the model may decide to use, and a
# turn that acts on its own is a turn we neither metered nor audited.
DISALLOWED_TOOLS = (
    "Bash",
    "BashOutput",
    "Edit",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "KillShell",
    "NotebookEdit",
    "Read",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Write",
)

# Request fields the CLI has no flag for. Carried to the backend by
# callers written against the API path, honoured by nobody here.
_DROPPED_REQUEST_FIELDS = (
    "max_tokens",
    "temperature",
    "stop_sequences",
    "metadata",
    "cache_system",
    "cache_tools",
)


class ClaudeCodeGatewayError(RuntimeError):
    """The CLI failed to produce a usable response.

    Distinct from the metered API backend's error types (and from
    :class:`~advisor.llm.claude_code_translation.ClaudeCodeTranslationError`,
    which is retryable model output rather than a dead transport) so
    callers can tell which backend broke.
    """


class ClaudeCodeCLINotFoundError(ClaudeCodeGatewayError):
    """The ``claude`` binary isn't on PATH.

    Split out because it is the one failure worth *not* retrying: a
    missing binary is the same on attempt 3 as on attempt 1, and three
    identical failures make a config problem read like a model problem
    in the logs. Subclasses the gateway error so callers catching the
    base still see it.
    """


class ClaudeCodeGateway:
    """``LLMGateway`` implementation backed by the ``claude -p`` CLI.

    Bare class, no base — like ``MockGateway`` and the metered API
    backend, it satisfies the Protocol structurally.

    ``cli_path`` defaults to whatever ``claude`` resolves to on PATH.
    ``max_retries`` counts total attempts, not retries-after-the-first:
    ``max_retries=3`` means at most three subprocess invocations — so
    the worst-case latency of one ``complete()`` is
    ``max_retries * timeout_s``, which is what the defaults are sized
    around.
    """

    name = "claude_code"

    def __init__(
        self,
        *,
        cli_path: str | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._cli_path = cli_path or shutil.which(CLAUDE_CLI) or CLAUDE_CLI
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        # Per-instance so the warning is one line per dropped field per
        # process (the registry hands out a single cached gateway), not
        # one per request.
        self._warned_fields: set[str] = set()

    # -- LLMGateway ----------------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Run one turn through the CLI and translate the envelope back.

        Awaits the subprocess rather than blocking on it, so a turn in
        flight doesn't hold the event loop hostage.
        """
        self._warn_dropped_fields(request)
        prompt, schema = _render(request)

        last_error: str | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                payload = await self._invoke_once(
                    prompt, schema, request, attempt=attempt
                )
                structured = payload.get("structured_output")
                if not isinstance(structured, dict):
                    raise ClaudeCodeGatewayError(
                        "`claude -p` response missing dict 'structured_output'. "
                        f"Got keys: {sorted(payload)}"
                    )
                return envelope_to_response(
                    structured, payload, request.model, tools=request.tools
                )
            except ClaudeCodeCLINotFoundError:
                raise
            except (ClaudeCodeGatewayError, ClaudeCodeTranslationError) as exc:
                last_error = str(exc)
                logger.warning(
                    "ClaudeCodeGateway attempt %d/%d failed: %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                if attempt < self._max_retries:
                    prompt = _prompt_with_repair_hint(prompt, last_error)

        raise ClaudeCodeGatewayError(
            f"`claude -p` failed after {self._max_retries} attempts. "
            f"Last error: {last_error}"
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        """Synthesise a stream from the completed response.

        The CLI's ``--output-format json`` path is all-or-nothing: the
        envelope only parses once it is whole, so there is nothing to
        emit incrementally. Provided for Protocol completeness — the
        chat surface builds its SSE stream downstream of the tool loop
        (``session.py``), which calls :meth:`complete`, so nothing in
        production reaches this method.
        """
        response = await self.complete(request)
        for event in _synthesise_stream(response):
            yield event

    # -- transport -----------------------------------------------------------

    def build_argv(self, request: CompletionRequest) -> list[str]:
        """The exact command line a first attempt would run.

        Public because the flags are the contract: ``--disallowedTools``
        and ``--autocompact`` are what keep the loop (and the meter) on
        our side, and auditing them shouldn't require spawning a CLI or
        reaching into a private method. Retries re-render only the
        prompt; every other element is what you see here.
        """
        prompt, schema = _render(request)
        argv, _stdin = self._build_invocation(prompt, schema, request)
        return argv

    def _build_invocation(
        self, prompt: str, schema: dict[str, Any], request: CompletionRequest
    ) -> tuple[list[str], bytes | None]:
        """argv plus whatever should be written to the CLI's stdin.

        The prompt normally travels as the ``-p`` operand. Past
        :data:`ARGV_PROMPT_LIMIT_BYTES` it moves to stdin instead,
        because Linux caps a *single* argv entry at 128 KiB
        (``MAX_ARG_STRLEN``) and rejects the whole exec with ``E2BIG``
        past it. That ceiling is roughly 32k tokens — far below the
        advisor's own 165k cumulative cap, so a normal research
        conversation would hit it, and it would surface as an
        unexplained OSError rather than as anything about prompts.

        Under the limit the command line is exactly the specced one, so
        the common path stays the path live validation exercises.
        """
        encoded = prompt.encode("utf-8")
        oversized = len(encoded) > ARGV_PROMPT_LIMIT_BYTES
        argv = [
            self._cli_path,
            "-p",
            *([] if oversized else [prompt]),
            "--model",
            request.model,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
        ]
        if request.system:
            argv += ["--append-system-prompt", request.system]
        argv += [
            "--disallowedTools",
            ",".join(DISALLOWED_TOOLS),
            "--autocompact",
            str(AUTOCOMPACT_THRESHOLD),
        ]
        if oversized:
            logger.info(
                "ClaudeCodeGateway: prompt is %d bytes, over the %d-byte argv "
                "ceiling — sending it on stdin instead of as the -p operand.",
                len(encoded),
                ARGV_PROMPT_LIMIT_BYTES,
            )
        return argv, encoded if oversized else None

    async def _invoke_once(
        self,
        prompt: str,
        schema: dict[str, Any],
        request: CompletionRequest,
        *,
        attempt: int,
    ) -> dict[str, Any]:
        """One subprocess invocation. Returns the CLI's parsed payload."""
        argv, stdin_bytes = self._build_invocation(prompt, schema, request)
        logger.debug(
            "ClaudeCodeGateway: spawning %s (model=%s, attempt=%d, %d prompt chars)",
            self._cli_path,
            request.model,
            attempt,
            len(prompt),
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE if stdin_bytes else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ClaudeCodeCLINotFoundError(
                f"`claude` CLI not found at {self._cli_path!r}. "
                f"Install Claude Code or pass cli_path explicitly."
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=stdin_bytes), timeout=self._timeout_s
            )
        except TimeoutError as exc:  # asyncio.TimeoutError aliases this on 3.11+
            await _terminate(process)
            raise ClaudeCodeGatewayError(
                f"`claude -p` timed out after {self._timeout_s}s (attempt {attempt})"
            ) from exc

        if process.returncode != 0:
            raise ClaudeCodeGatewayError(
                f"`claude -p` exited {process.returncode}: "
                f"stderr={_decode(stderr).strip()[:500]!r}"
            )

        text = _decode(stdout)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ClaudeCodeGatewayError(
                f"`claude -p` stdout was not valid JSON: {exc}. "
                f"Stdout prefix: {text[:300]!r}"
            ) from exc

        if not isinstance(payload, dict):
            raise ClaudeCodeGatewayError(
                f"`claude -p` stdout was not a JSON object, got "
                f"{type(payload).__name__}"
            )

        if payload.get("is_error"):
            raise ClaudeCodeGatewayError(
                "`claude -p` reported is_error=true: "
                f"{payload.get('result') or payload.get('api_error_status') or 'no details'}"
            )

        return payload

    # -- dropped fields ------------------------------------------------------

    def _warn_dropped_fields(self, request: CompletionRequest) -> None:
        """Say out loud which request knobs the CLI cannot honour.

        Only fields the caller actually moved off their default are
        reported: a request carrying ``temperature=0.7`` never asked for
        anything, while ``temperature=0.0`` did and is not getting it.
        Dropped-but-documented — never silent, never per-request spam.
        """
        for field in _DROPPED_REQUEST_FIELDS:
            if field in self._warned_fields:
                continue
            value = getattr(request, field)
            if value == _request_field_default(field):
                continue
            self._warned_fields.add(field)
            logger.warning(
                "ClaudeCodeGateway drops request.%s=%r: `claude -p` has no "
                "equivalent flag. This will not be applied to the turn.",
                field,
                value,
            )


# -- helpers -----------------------------------------------------------------


def _render(request: CompletionRequest) -> tuple[str, dict[str, Any]]:
    """Prompt text + envelope schema for a request.

    The system prompt rides on ``--append-system-prompt`` (the CLI's own
    system slot) and is stripped from the rendered body so it isn't sent
    twice — advisor personas are large enough that duplicating one is a
    measurable per-turn cost.
    """
    prompt = render_prompt(request.model_copy(update={"system": None}))
    return prompt, build_envelope_schema(request.tools)


def _request_field_default(field: str) -> Any:
    return CompletionRequest.model_fields[field].get_default(
        call_default_factory=True
    )


def _prompt_with_repair_hint(prompt: str, error: str) -> str:
    """Append the previous failure so the next attempt can self-repair.

    Strictly additive: the retry prompt is the previous prompt plus the
    error, so a run's prompts nest and a transcript shows exactly what
    the model was told each time.
    """
    return (
        f"{prompt}\n\n"
        f"--- PREVIOUS ATTEMPT FAILED ---\n"
        f"Your last response was rejected: {error}\n"
        f"Respond again with a single JSON object matching the required "
        f"envelope schema. No prose, no markdown fences."
    )


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Kill a timed-out CLI and reap it, so we don't leak a zombie."""
    try:
        process.kill()
    except ProcessLookupError:  # already gone
        return
    try:
        await process.wait()
    except Exception:  # pragma: no cover - best-effort reap
        logger.debug("ClaudeCodeGateway: reaping timed-out CLI failed", exc_info=True)


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


def _synthesise_stream(response: CompletionResponse) -> list[StreamEvent]:
    """Fan a finished response out into the event order real streams use:
    message_start -> per-block start/delta/stop -> message_delta -> stop."""
    events: list[StreamEvent] = [
        MessageStartEvent(
            message_id=response.id, model=response.model, role=LLMRole.ASSISTANT
        )
    ]
    for index, block in enumerate(response.content):
        events.append(ContentBlockStartEvent(index=index, content_block=block))
        if isinstance(block, TextBlock) and block.text:
            events.append(
                ContentBlockDeltaEvent(index=index, text_delta=block.text)
            )
        elif isinstance(block, ToolUseBlock):
            events.append(
                ContentBlockDeltaEvent(
                    index=index, input_json_delta=json.dumps(block.input)
                )
            )
        events.append(ContentBlockStopEvent(index=index))
    events.append(
        MessageDeltaEvent(
            stop_reason=response.stop_reason,
            stop_sequence=response.stop_sequence,
            usage=response.usage,
        )
    )
    events.append(MessageStopEvent())
    return events
