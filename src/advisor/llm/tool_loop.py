"""Tool-use orchestration.

The Anthropic Messages API expects callers to run a small loop: send
messages + tools, get a response, if the response contains
``tool_use`` blocks, execute the tools, append a USER message
containing matching ``tool_result`` blocks, and call the API again.
The loop terminates when the model returns a response without
tool_use (i.e. ``stop_reason='end_turn'`` or similar).

Doing this loop by hand at every chat-backend call site is tedious
and error-prone (forgetting tool_use_id correlation; mishandling
exceptions; not enforcing iteration limits). ``run_tool_loop``
encapsulates it.

A ``ToolHandler`` is a callable the loop invokes when the LLM asks
for a tool by name. The handler's signature is
``async def handler(input: dict) -> str | list[ContentBlock]``. The
return value becomes the ``content`` of the matching tool_result.
Handlers can raise; the loop converts an exception into a
``ToolResultBlock(is_error=True)`` so the LLM can see the failure
and recover, rather than the whole conversation aborting.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    IterationMetric,
    LLMGateway,
    LLMRole,
    Message,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from advisor.llm.budget import (
    CircuitTripInfo,
    default_token_budget,
    estimate_request_input_tokens,
)

# NOTE: ``advisor.chat.history_compaction`` is imported lazily inside
# ``run_tool_loop`` (not at module load) to break the import cycle —
# ``advisor.chat`` re-exports ``ChatSession``, which imports
# ``run_tool_loop`` from this module. By the time the loop actually
# runs, both packages are fully initialised.

logger = logging.getLogger(__name__)


# Async handler shape. The loop calls the handler with the LLM's
# input dict; the handler returns the tool's output as either a
# plain string (rendered as a single text block by the gateway) or
# a list of content blocks (for tools whose output is structured).
ToolHandler = Callable[[dict[str, Any]], Awaitable[str | list[ContentBlock]]]


@dataclass
class ToolLoopResult:
    """Outcome of a tool-use loop run.

    ``final_response`` is the last assistant turn (no further
    tool_use blocks). ``conversation`` is the complete message list,
    including all intermediate tool_use / tool_result exchanges, so
    the caller can persist it.

    ``tool_calls`` records each tool invocation in order — useful for
    audit, billing, and observability.

    ``terminated_reason`` is ``"end_turn"`` when the model produced a
    natural text response, ``"iteration_cap"`` when we hit
    ``max_iterations`` and forced a text-only synthesis turn, or
    ``"cost_circuit_trip"`` when the pre-flight token estimator caught
    a runaway turn before it shipped. Callers can surface a UI hint
    when the answer was forced rather than organic, and persist the
    distinction in the audit trail.

    ``circuit_trip`` carries the estimate and budget when the breaker
    fired, so the chat route can record both in the UsageEvent
    metadata. ``None`` when the turn terminated normally.
    """

    final_response: CompletionResponse
    conversation: list[Message]
    tool_calls: list["ToolInvocation"] = field(default_factory=list)
    iterations: int = 0
    # Sum of every per-iteration ``CompletionResponse.usage`` seen
    # during the loop. Set to ``None`` when the gateway didn't return
    # usage on any iteration (e.g. older MockGateway responses). The
    # aggregate matters because tool-use turns make multiple model
    # calls; the final response's ``usage`` only covers the last one.
    total_usage: TokenUsage | None = None
    terminated_reason: str = "end_turn"
    circuit_trip: CircuitTripInfo | None = None
    # ABS-266: One entry per gateway round, in dispatch order. Carries
    # the round's reported ``usage`` and wall-clock ``latency_ms`` so
    # callers can reconstruct the staircase of context growth instead
    # of seeing only the aggregate. Includes the forced-synthesis round
    # when the loop terminated on ``iteration_cap`` or
    # ``cost_circuit_trip``.
    per_iteration: list[IterationMetric] = field(default_factory=list)


@dataclass
class ToolInvocation:
    """One tool call within the loop. Records what the LLM asked for
    and what the handler returned (or raised).

    ``latency_ms`` is the handler's wall-clock execution time, NOT
    including the gateway round-trip that requested the tool. Used by
    ABS-266 to expose per-tool perf in ``ToolLoopMetricsEvent``.
    """

    tool_use_id: str
    tool_name: str
    input: dict[str, Any]
    output: str | list[ContentBlock] | None
    error: str | None = None
    latency_ms: int = 0


class ToolLoopError(Exception):
    """Reserved for unrecoverable loop errors (e.g. gateway crash).

    The iteration cap no longer raises — see ``run_tool_loop`` for
    the synthesis-fallback path. Kept around for future use and to
    avoid breaking callers that catch it.
    """


# Nudge appended to the last tool_result message when we hit the
# iteration cap. Lives next to the loop so the wording stays close
# to the prompt-engineering decision it represents.
_ITERATION_CAP_NUDGE = (
    "You have used your full tool budget for this turn. Stop calling "
    "tools and answer now using ONLY the evidence already retrieved "
    "above. If the user's question genuinely cannot be answered from "
    "what was retrieved, say so plainly in one paragraph and name the "
    "specific bylaw section, schedule, or external dataset that would "
    "actually contain the answer (for example: 'Lot consolidation is "
    "governed by the HRM Subdivision By-law, not the Land Use By-law'). "
    "Never claim a generic 'I couldn't find an answer' — be specific "
    "about what was missing."
)

# Nudge appended when the per-turn input-token budget would be
# exceeded on the next gateway call. Phrased as a hard ceiling rather
# than an iteration cap because the cause is request size, not loop
# count — the model may otherwise interpret "you used your budget"
# as iteration exhaustion and emit a different apology shape.
_COST_CIRCUIT_NUDGE = (
    "This turn has reached its input-token cost ceiling. Stop calling "
    "tools and answer now using ONLY the evidence already retrieved "
    "above. If the retrieved evidence is insufficient to answer the "
    "user's question, say so plainly in one paragraph and name the "
    "specific bylaw section, schedule, or external dataset that would "
    "actually contain the answer. Never claim a generic 'I couldn't "
    "find an answer' — be specific about what was missing."
)


async def run_tool_loop(
    gateway: LLMGateway,
    *,
    request: CompletionRequest,
    handlers: dict[str, ToolHandler],
    max_iterations: int = 10,
    token_budget: int | None = None,
) -> ToolLoopResult:
    """Drive a Messages API conversation through any number of tool-use
    rounds and return when the LLM stops asking for tools.

    ``request.tools`` should declare every tool the LLM is permitted
    to call; ``handlers`` should supply an implementation for each
    name. A tool the LLM asks for that has no handler is reported as
    an error to the LLM (``is_error=True`` tool_result) so it can
    recover or apologise.

    ``max_iterations`` is a per-tier safety cap. ``ChatSession``
    derives it from the active ``Tier.max_iterations`` (Quick=8,
    Standard=20, Complex=55) so the cap matches each tier's advertised
    reasoning-step range. The default of 10 is only a fallback for
    non-tier sessions (tests, legacy paths). When the cap is hit we
    don't raise — instead we make one more model call with tools
    stripped, forcing a text-only synthesis from whatever evidence was
    already retrieved. That converts a hard error ("agent gave up")
    into a real answer ("the LUB doesn't cover this; see the
    Subdivision By-law"), and keeps the partial conversation
    persistable so the audit trail isn't lost.

    ``token_budget`` is the cost-circuit ceiling on input tokens for
    the whole turn. Each iteration's request is estimated (cheap
    char-based heuristic — see ``advisor.llm.budget``) before being
    submitted; if the estimate exceeds the budget the loop takes the
    same synthesis-fallback path as the iteration cap, with a
    different nudge. ``None`` reads the default from
    ``default_token_budget()`` (env-overridable). The breaker is
    always on; tests pin a small budget to exercise the trip.

    NOTE: WI-1 (rolling cache breakpoint placement) and WI-4 (in-flight
    tool_result compaction) were reverted in ABS-304 after ABS-303
    showed they were net-negative in production. The function no longer
    places ``cache_control`` markers on tool_result blocks nor compacts
    older tool_result turns inside the loop. See
    ``evals/token_savings/20260610-ABS303-real-api-validation/ROLLUP.md``
    and the post-mortem in ``docs/TOKEN_COST_REDUCTION_FINDINGS.md``
    for the evidence.
    """
    budget = token_budget if token_budget is not None else default_token_budget()
    conversation = list(request.messages)
    tool_calls: list[ToolInvocation] = []
    total_usage: TokenUsage | None = None
    # ABS-266: capture per-round metrics in dispatch order.
    per_iteration: list[IterationMetric] = []

    for iteration in range(1, max_iterations + 1):
        # Snapshot the live conversation into the per-iteration request so
        # later appends (the assistant turn from this round, the next
        # tool_result turn) don't retroactively mutate the sent payload.
        current_request = request.model_copy(update={"messages": list(conversation)})

        estimated = estimate_request_input_tokens(current_request)
        if estimated > budget:
            trip = CircuitTripInfo(
                estimated_input_tokens=estimated,
                budget=budget,
                iteration=iteration,
            )
            logger.warning(
                "cost-circuit breaker tripped: estimated %d input tokens "
                "exceeds budget %d on iteration %d; forcing synthesis turn",
                estimated,
                budget,
                iteration,
            )
            return await _force_synthesis(
                gateway,
                request=request,
                conversation=conversation,
                tool_calls=tool_calls,
                total_usage=total_usage,
                iterations=iteration - 1,
                nudge=_COST_CIRCUIT_NUDGE,
                terminated_reason="cost_circuit_trip",
                circuit_trip=trip,
                per_iteration=per_iteration,
            )

        gateway_t0 = time.monotonic()
        response = await gateway.complete(current_request)
        gateway_latency_ms = int((time.monotonic() - gateway_t0) * 1000)
        total_usage = _accumulate_usage(total_usage, response.usage)

        # Always append the assistant turn to the conversation, even
        # when it contains tool_use blocks — the next request needs
        # the assistant turn AND the user-side tool_result turn that
        # follows.
        conversation.append(
            Message(role=LLMRole.ASSISTANT, content=list(response.content))
        )

        tool_use_blocks = [b for b in response.content if isinstance(b, ToolUseBlock)]
        per_iteration.append(
            IterationMetric(
                iteration=iteration,
                usage=response.usage,
                latency_ms=gateway_latency_ms,
                tool_call_count=len(tool_use_blocks),
            )
        )
        if not tool_use_blocks:
            return ToolLoopResult(
                final_response=response,
                conversation=conversation,
                tool_calls=tool_calls,
                iterations=iteration,
                total_usage=total_usage,
                terminated_reason="end_turn",
                per_iteration=per_iteration,
            )

        result_blocks: list[ContentBlock] = []
        for use_block in tool_use_blocks:
            invocation = await _run_one_handler(handlers, use_block)
            tool_calls.append(invocation)
            if invocation.error is not None:
                result_blocks.append(
                    ToolResultBlock(
                        tool_use_id=use_block.id,
                        content=invocation.error,
                        is_error=True,
                    )
                )
            else:
                result_blocks.append(
                    ToolResultBlock(
                        tool_use_id=use_block.id,
                        content=invocation.output or "",
                    )
                )

        conversation.append(Message(role=LLMRole.USER, content=result_blocks))

    logger.warning(
        "tool-use loop hit max_iterations=%d; forced synthesis turn",
        max_iterations,
    )
    return await _force_synthesis(
        gateway,
        request=request,
        conversation=conversation,
        tool_calls=tool_calls,
        total_usage=total_usage,
        per_iteration=per_iteration,
        iterations=max_iterations,
        nudge=_ITERATION_CAP_NUDGE,
        terminated_reason="iteration_cap",
        circuit_trip=None,
    )


async def _force_synthesis(
    gateway: LLMGateway,
    *,
    request: CompletionRequest,
    conversation: list[Message],
    tool_calls: list["ToolInvocation"],
    total_usage: TokenUsage | None,
    iterations: int,
    nudge: str,
    terminated_reason: str,
    circuit_trip: CircuitTripInfo | None,
    per_iteration: list[IterationMetric] | None = None,
) -> ToolLoopResult:
    """Tack a stop-and-answer nudge onto the last user message and
    make one final tools-stripped call.

    Shared between the iteration-cap and cost-circuit paths because
    the recovery shape is identical (mutate-last-user-message +
    strip-tools + one synthesis call); only the nudge wording and the
    audit fields differ. Anthropic forbids consecutive same-role
    turns, so the trailing message MUST already be a user turn —
    which it is on both paths: the iteration cap arrives right after
    appending a tool_result user turn, and the cost-circuit trip
    fires before any new gateway call so the conversation's tail
    matches the structure the previous iteration left.

    The synthesis call itself is NOT re-checked against the budget.
    Stripping tools and dropping their definitions usually brings the
    request well under the cap; on the rare case it doesn't (huge
    conversation history) we still send it — the user is owed an
    answer, and the call is the bounded one-more, not a runaway loop.
    """
    if not conversation or conversation[-1].role != LLMRole.USER:
        # First-turn trip: no prior user/tool_result to mutate, so we
        # don't have a synthesis-fallback shape that satisfies
        # Anthropic's same-role rule. Surface the original request as
        # the synthesis attempt — the conversation already ends with
        # the original user message in that case, and the nudge is
        # informational only.
        pass
    else:
        last_user = conversation[-1]
        if isinstance(last_user.content, str):
            nudged_content: list[ContentBlock] = [
                TextBlock(text=last_user.content),
                TextBlock(text=nudge),
            ]
        else:
            nudged_content = list(last_user.content) + [TextBlock(text=nudge)]
        conversation[-1] = Message(role=LLMRole.USER, content=nudged_content)

    synthesis_request = request.model_copy(
        update={"messages": list(conversation), "tools": []}
    )
    syn_t0 = time.monotonic()
    final_response = await gateway.complete(synthesis_request)
    syn_latency_ms = int((time.monotonic() - syn_t0) * 1000)
    total_usage = _accumulate_usage(total_usage, final_response.usage)
    conversation.append(
        Message(role=LLMRole.ASSISTANT, content=list(final_response.content))
    )
    metrics = list(per_iteration) if per_iteration else []
    # The forced-synthesis call is itself a billable round; record it
    # under ``iteration = iterations + 1`` so per_iteration[].iteration
    # remains monotonic even when the loop terminated abnormally.
    metrics.append(
        IterationMetric(
            iteration=iterations + 1,
            usage=final_response.usage,
            latency_ms=syn_latency_ms,
            tool_call_count=0,  # tools were stripped for synthesis
        )
    )
    return ToolLoopResult(
        final_response=final_response,
        conversation=conversation,
        tool_calls=tool_calls,
        iterations=iterations,
        total_usage=total_usage,
        terminated_reason=terminated_reason,
        circuit_trip=circuit_trip,
        per_iteration=metrics,
    )


async def _run_one_handler(
    handlers: dict[str, ToolHandler], block: ToolUseBlock
) -> ToolInvocation:
    """Execute one tool_use block. Captures handler errors instead of
    letting them propagate — the LLM gets to see the error string and
    can decide whether to retry, apologise, or work around it."""
    handler = handlers.get(block.name)
    if handler is None:
        message = (
            f"No handler registered for tool {block.name!r}. "
            "This is a server-side configuration bug."
        )
        logger.error(message)
        return ToolInvocation(
            tool_use_id=block.id,
            tool_name=block.name,
            input=dict(block.input),
            output=None,
            error=message,
        )
    t0 = time.monotonic()
    try:
        output = await handler(block.input)
    except Exception as exc:  # noqa: BLE001 — surface the error to the LLM
        logger.exception("tool %r handler raised", block.name)
        return ToolInvocation(
            tool_use_id=block.id,
            tool_name=block.name,
            input=dict(block.input),
            output=None,
            error=f"{type(exc).__name__}: {exc}",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    return ToolInvocation(
        tool_use_id=block.id,
        tool_name=block.name,
        input=dict(block.input),
        output=output,
        latency_ms=int((time.monotonic() - t0) * 1000),
    )


def _accumulate_usage(
    total: TokenUsage | None, delta: TokenUsage | None
) -> TokenUsage | None:
    """Sum two optional ``TokenUsage`` snapshots.

    Returns ``None`` only when both inputs are ``None`` (i.e. the
    gateway has never reported usage). Once we've seen any usage we
    keep accumulating into a real ``TokenUsage`` so a later
    ``None`` doesn't blank the running total.
    """
    if delta is None:
        return total
    if total is None:
        return delta.model_copy()
    return TokenUsage(
        input_tokens=total.input_tokens + delta.input_tokens,
        output_tokens=total.output_tokens + delta.output_tokens,
        cache_creation_input_tokens=(
            total.cache_creation_input_tokens + delta.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            total.cache_read_input_tokens + delta.cache_read_input_tokens
        ),
    )


def text_of(response: CompletionResponse) -> str:
    """Convenience: extract concatenated text from the response's text
    blocks. Handy for the simplest chat callers that don't care about
    structured content."""
    return "".join(b.text for b in response.content if isinstance(b, TextBlock))
