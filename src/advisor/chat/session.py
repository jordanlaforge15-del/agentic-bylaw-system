"""Per-conversation chat session.

A ``ChatSession`` carries everything needed to run one user's chat:
the system prompt, the message history, the tool registry, and the
target model. It does NOT hold the LLM gateway — that's passed in per
``send_user_message`` call so a single session can survive across
gateway swaps (test -> prod, model upgrade, etc.) without losing
state.

Streaming v1 design
-------------------
``send_user_message`` runs ``run_tool_loop`` (which is non-streaming
internally — Anthropic's tool-use API requires a complete assistant
turn to know whether tools were requested) and only AFTER the loop
finishes does it synthesise stream events from the final response.
This is intentional:

* The tool loop architecture is fundamentally non-streaming — partial
  tool_use blocks have no semantic meaning, and we cannot dispatch a
  handler until the full input is parsed.
* Synthetic streaming gives the frontend the same event shape it will
  see in v2, so the SSE plumbing can be built and tested now.
* True incremental token streaming during tool use (driving
  ``gateway.stream()`` directly inside the loop) is a larger refactor
  in ``advisor.llm`` and is deliberately deferred.

The synthetic stream uses ``MockGateway._stream_from_response``, which
emits the same event sequence (message_start, per-block start/delta/
stop, message_delta, message_stop) the real Anthropic stream does.
Frontends that consume this don't need to know it's synthetic.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from advisor.chat.history_compaction import (
    compact_history_for_submission,
    resolve_keep_recent,
)
from advisor.llm import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    LLMGateway,
    LLMRole,
    Message,
    StreamEvent,
    TextBlock,
    TokenUsage,
    ToolDefinition,
)
from advisor.llm.budget import CircuitTripInfo, default_token_budget
from advisor.llm.mock import MockGateway
from advisor.llm.tool_loop import ToolHandler, run_tool_loop

# Anthropic supports up to 4 cache breakpoints per request. The chat
# session spends two on the request-level shared prefix (system,
# tools) and reserves the remaining two for stable conversation
# milestones — the first one or two assistant turns. Those turns are
# byte-stable for every subsequent turn in the session, so caching
# them turns multi-turn conversations into long prompt-cache reads
# instead of full-cost replays.
_MAX_CONVERSATION_CACHE_MILESTONES = 2


@dataclass
class ChatSession:
    """One user's conversation state.

    ``user_id`` is a placeholder for workstream 3's auth integration —
    in v1 it's just the value of the ``X-User-Id`` header.

    ``messages`` is the full Anthropic-shape conversation including
    every intermediate tool_use / tool_result round-trip. Persisting
    just this list across requests is enough to resume any session.
    """

    session_id: str
    user_id: str
    system_prompt: str
    messages: list[Message] = field(default_factory=list)
    tool_defs: list[ToolDefinition] = field(default_factory=list)
    tool_handlers: dict[str, ToolHandler] = field(default_factory=dict)
    model: str = "claude-opus-4-5"
    # Hook fired AFTER ``send_user_message_blocking`` finishes a turn —
    # i.e. after ``self.messages`` has been replaced with the full
    # post-turn conversation. The DB-backed ``SessionStore`` registers
    # this to persist newly-appended messages without the chat route
    # having to know anything about persistence. Default ``None`` keeps
    # the in-memory path's behaviour unchanged.
    on_turn_complete: Callable[["ChatSession"], None] | None = field(
        default=None, repr=False, compare=False
    )
    # Aggregate token usage from the most recent ``send_user_message_blocking``
    # call (sum of every per-iteration ``CompletionResponse.usage`` the
    # tool loop produced). Reset to ``None`` between turns. Persistence
    # hooks read this to attribute tokens to the final assistant row,
    # and the chat route reads it to update the up-front ``UsageEvent``.
    last_turn_usage: TokenUsage | None = field(
        default=None, repr=False, compare=False
    )
    # Per-turn input-token budget enforced by the cost-circuit breaker
    # in ``run_tool_loop``. The default reads from
    # ``ADVISOR_TURN_INPUT_TOKEN_BUDGET`` (falling back to a sane
    # safety-net cap); tests can pin a small value to exercise the
    # trip path without env-var manipulation.
    token_budget: int = field(default_factory=default_token_budget)
    # Set by ``send_user_message_blocking`` when the cost-circuit
    # breaker fires on the most recent turn — ``None`` for turns that
    # completed under budget. The chat route reads this to enrich the
    # UsageEvent metadata so trips are auditable in analytics.
    last_turn_circuit_trip: CircuitTripInfo | None = field(
        default=None, repr=False, compare=False
    )
    # Wall-clock of the most recent turn. Used by the sidebar to render
    # "2m ago" / "yesterday" — the in-memory store has no other notion
    # of recency, and the DB-backed store overwrites this on load with
    # the row's ``updated_at`` so both paths surface a consistent value.
    updated_at: datetime | None = field(default=None, compare=False)
    # How many recent user-prompt turns to keep intact when compacting
    # history for LLM submission. ``None`` defers to the
    # ``ADVISOR_CHAT_COMPACT_KEEP_RECENT`` env var (default 2). Older
    # turns get their tool_result block content replaced with a short
    # deterministic summary so we stop re-paying for full retrieval
    # payloads on every iteration of the tool loop. Persistence is
    # unaffected — ``self.messages`` keeps the full payload.
    compact_keep_recent: int | None = field(default=None, compare=False)

    async def send_user_message_blocking(
        self, gateway: LLMGateway, text: str
    ) -> CompletionResponse:
        """Run one user turn and return the final assistant response.

        Use this in tests or non-streaming integrations. Mutates
        ``self.messages`` to include the user message and the full
        intermediate conversation (tool_use / tool_result blocks
        included) plus the final assistant turn.
        """
        # Append the user turn FIRST so the loop sees it as the
        # latest entry. We keep mutation here (not deferred to after
        # the loop) so a concurrent reader inspecting the session
        # mid-call sees the user's input rather than a stale state.
        self.messages.append(Message(role=LLMRole.USER, content=text))

        # Compact older tool_result payloads into one-line summaries
        # before submission. ``self.messages`` itself is untouched —
        # this is a view-only transformation so persistence still
        # stores the full payload. ``prefix_len`` records how many
        # messages the loop starts with, so we can splice any newly-
        # appended messages (tool_use / tool_result / final assistant)
        # back into the FULL history after the loop returns rather
        # than overwriting older turns with their compacted variants.
        prefix_len = len(self.messages)
        submission_messages = compact_history_for_submission(
            self.messages,
            keep_recent=resolve_keep_recent(self.compact_keep_recent),
        )

        request = CompletionRequest(
            model=self.model,
            system=self.system_prompt,
            messages=_mark_conversation_cache_milestones(submission_messages),
            tools=list(self.tool_defs),
            # The system prompt and tools array are byte-stable for the
            # lifetime of the session — flip on prompt caching so the
            # gateway places ``cache_control`` markers on them. On every
            # call after the first, the provider reads those prefixes
            # from cache at ~10% of the input-token rate.
            cache_system=True,
            cache_tools=True,
        )
        result = await run_tool_loop(
            gateway,
            request=request,
            handlers=self.tool_handlers,
            token_budget=self.token_budget,
        )

        # Splice the loop's newly-appended messages back onto the
        # FULL prefix. ``result.conversation[:prefix_len]`` is the
        # compacted view we passed in — discard it; the messages it
        # represents are already present in ``self.messages`` in
        # their full-payload form. Anything beyond ``prefix_len`` is
        # what the loop added (assistant tool_use turns, our
        # tool_result turns built from real handler output, and the
        # final assistant text) and needs to be preserved verbatim.
        self.messages = list(self.messages) + list(
            result.conversation[prefix_len:]
        )
        # Stash the aggregate usage so the persistence hook (and the
        # chat route) can attribute real token counts. Reset before
        # we set so a turn with no reported usage clears the prior
        # value rather than carrying it forward.
        self.last_turn_usage = result.total_usage
        self.last_turn_circuit_trip = result.circuit_trip
        self.updated_at = datetime.now(timezone.utc)

        # Fire the post-turn hook AFTER messages are settled. The
        # callback receives ``self`` so it can read the new message
        # list and persist whatever it likes. Exceptions propagate —
        # a persistence failure should fail the chat turn loudly
        # rather than silently drop a message.
        if self.on_turn_complete is not None:
            self.on_turn_complete(self)

        return result.final_response

    async def send_user_message(
        self, gateway: LLMGateway, text: str
    ) -> AsyncIterator[StreamEvent]:
        """Run one user turn and yield a synthetic stream of events.

        v1 streams synthetically AFTER the tool loop completes; true
        incremental streaming during tool use will land when we drive
        ``gateway.stream()`` directly inside the loop, which is not
        yet implemented. See module docstring for rationale.
        """
        final_response = await self.send_user_message_blocking(gateway, text)
        # Reuse the mock's chunker so the event sequence exactly
        # matches what a real Anthropic stream would emit. The
        # frontend can't tell the difference from a real stream.
        async for event in MockGateway._stream_from_response(final_response):
            yield event


def _mark_conversation_cache_milestones(messages: list[Message]) -> list[Message]:
    """Mark cache breakpoints on the first stable assistant turns.

    The earliest assistant turns in a session are byte-stable for
    every subsequent turn: once the assistant has answered turn 1,
    that text never changes again, so caching it lets turn 2 onward
    read a long prefix from the provider's prompt cache. We mark the
    LAST block of each early assistant message (Anthropic caches up
    to and including the marked block) and stop once we've placed
    ``_MAX_CONVERSATION_CACHE_MILESTONES`` markers.

    Skipped intentionally:
    - User messages with plain-string content. Wrapping them in a
      block list to add a cache flag would change the request shape
      the rest of the chat layer observes (tests inspect raw
      ``request.messages[i].content``) for marginal gain — short
      user prompts contribute little to the cached prefix relative
      to system + tools + assistant turns.
    - Tool-result user turns. The tool loop rebuilds these per
      iteration; marking them here doesn't carry through.

    Returns a fresh list with fresh Message / block objects on the
    marked positions; unmarked messages are reused by reference.
    """
    out: list[Message] = []
    marked = 0
    for msg in messages:
        if (
            marked < _MAX_CONVERSATION_CACHE_MILESTONES
            and msg.role == LLMRole.ASSISTANT
            and isinstance(msg.content, list)
            and msg.content
        ):
            blocks: list[ContentBlock] = list(msg.content)
            blocks[-1] = blocks[-1].model_copy(update={"cache": True})
            out.append(msg.model_copy(update={"content": blocks}))
            marked += 1
        else:
            out.append(msg)
    return out


def empty_assistant_message() -> Message:
    """Convenience: an empty assistant message for placeholder slots.

    Used by tests that need to seed a session with a known shape
    without driving a full round-trip through the gateway. Kept here
    rather than in ``advisor.llm`` because it's a chat-layer concern.
    """
    return Message(role=LLMRole.ASSISTANT, content=[TextBlock(text="")])
