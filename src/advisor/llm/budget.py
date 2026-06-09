"""Per-turn input-token budget for the chat backend.

The circuit breaker is a safety net against runaway turns. On
2026-05-11 a single user turn consumed ~849k input tokens at ~$12.93,
and another ~611k at ~$9.30 — both on Opus 4.5 ($15/M input). They
went through silently because the tool loop has no per-turn cost
ceiling. This module supplies the estimator and the default budget
the loop uses to detect those turns BEFORE submitting them.

Estimator choice
----------------
We use a character-based heuristic (~``_CHARS_PER_TOKEN`` chars per
token) instead of Anthropic's ``messages.count_tokens`` endpoint:

- ``count_tokens`` is a billable network round-trip on the hot path;
  paying it on every iteration to MAYBE avoid a larger call is the
  wrong shape for a safety net.
- The estimator only has to be accurate enough to catch ~150k+ token
  requests. At that scale a 25% error margin still trips the breaker
  on every runaway turn we've observed and doesn't false-positive on
  normal usage (single-digit thousands of tokens per turn).
- A pure-Python heuristic means no extra dependency, no tokenizer
  download, no provider-specific shim.

The 4-chars-per-token ratio is the industry rule of thumb for
English-heavy prompts; it under-estimates Claude's BPE on technical
content (URLs, code, JSON), which is the right direction for a
safety net — slightly conservative.

Cache discounting (ABS-291)
---------------------------
After WI-1 (ABS-285) lands the rolling intra-loop cache breakpoint,
most tokens on deep-question turns are cache reads at ~10% of the
input rate. Without discounting, the cost-circuit breaker trips at
the same raw-token count as before — prematurely forcing synthesis
and degrading answers even though the actual billed cost is far lower.

``estimate_request_input_tokens`` now returns a *billed-equivalent*
token count instead of a raw token count.  Cache-read regions are
weighted at ``_CACHE_READ_WEIGHT`` (~10%); the budget is therefore in
billed-equivalent rather than raw terms.  The default (150 000) still
represents a turn cap of ~$2.25 at Opus rates, but the effective raw
token headroom is ~10× larger for cached turns — allowing the tool
loop to run to its natural conclusion rather than hitting the breaker.

Configuration
-------------
The default budget is read once from the ``ADVISOR_TURN_INPUT_TOKEN_BUDGET``
env var (falling back to ``_DEFAULT_TURN_INPUT_TOKEN_BUDGET``) by
``default_token_budget()``. Callers — chiefly ``ChatSession`` — read
the default at construction time so a test can override it by passing
an explicit value without touching the environment.

The default (150,000) is a deliberate safety-net level, NOT a primary
cost lever:

- At Opus 4.5's $15/M input rate, 150k billed-equivalent tokens caps
  one turn at ~$2.25.
- The 95th-percentile turn on 2026-05-11 was 611k tokens; 4 of the
  10 recorded events that day were over 150k.
- Parallel workstreams (prompt caching, retrieval-payload trimming,
  history compaction) all drop expected per-turn token counts. The
  threshold is chosen to leave headroom AFTER those land — not to
  squeeze current numbers.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

from advisor.llm.base import (
    CompletionRequest,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

# Average characters per token for English-heavy prompts. Claude's BPE
# is denser on natural language and sparser on JSON/code; this average
# under-estimates code-heavy prompts (the breaker trips earlier on
# them), which is the right bias for a safety net.
_CHARS_PER_TOKEN = 4

# Weight applied to tokens inside a cached prefix region. Anthropic
# charges ~10% of the normal input-token rate for cache reads; we use
# the same fraction so the estimator tracks *billed-equivalent* rather
# than raw token counts (ABS-291).
_CACHE_READ_WEIGHT = 0.1

# Default safety-net cap: 150k billed-equivalent input tokens per turn.
# At Opus 4.5's $15/M input rate this caps a single turn at ~$2.25.
# After ABS-291 the budget is in billed-equivalent terms; cached turns
# have ~10× the raw-token headroom before the breaker fires.
_DEFAULT_TURN_INPUT_TOKEN_BUDGET = 150_000


@dataclass(frozen=True)
class CircuitTripInfo:
    """Records a cost-circuit trip so callers can audit it.

    ``estimated_input_tokens`` is the pre-flight estimate that crossed
    the budget. ``budget`` is the value the loop was configured with;
    persisted alongside so a future threshold change doesn't make old
    trip records ambiguous. ``iteration`` is the loop iteration the
    trip happened on — useful for distinguishing "first prompt was
    huge" (iteration 1) from "tool results accumulated past the cap"
    (later iterations).
    """

    estimated_input_tokens: int
    budget: int
    iteration: int


def case_budget_for(tier: str) -> int:
    """Return the per-case cumulative token budget for ``tier``.

    Layer 1 of the case-credit enforcement model — the hard cap that
    bounds total tokens (input + output) across every session sharing
    the same case. Reads from the ``advisor.billing.packs`` catalog so
    the source of truth stays in one place.

    Returns the safety-net default for tiers we don't recognise (e.g.
    a future tier added to the catalog but not yet plumbed through
    the chat layer); this is a soft fallback rather than a crash so a
    catalog change can land without immediately breaking chat.
    """
    # Lazy import: ``advisor.billing.packs`` would otherwise create a
    # circular import path through ``advisor.billing.__init__``, which
    # imports the router, which imports ``cases.py``, which imports
    # this module's ``CircuitTripInfo``.
    from advisor.billing.packs import TIERS  # noqa: PLC0415

    tier_def = TIERS.get(tier)
    if tier_def is None:
        return _DEFAULT_TURN_INPUT_TOKEN_BUDGET
    return tier_def.token_budget


@lru_cache(maxsize=1)
def default_token_budget() -> int:
    """Return the env-configured default turn budget.

    Cached so the env-var read happens once per process. Tests that
    need a fresh read can call ``default_token_budget.cache_clear()``.
    Read failures (non-integer value, zero, negative) fall back to the
    module default with a warning rather than crashing the chat layer.
    """
    raw = os.environ.get("ADVISOR_TURN_INPUT_TOKEN_BUDGET")
    if raw is None:
        return _DEFAULT_TURN_INPUT_TOKEN_BUDGET
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "ADVISOR_TURN_INPUT_TOKEN_BUDGET=%r is not an integer; "
            "falling back to default %d",
            raw,
            _DEFAULT_TURN_INPUT_TOKEN_BUDGET,
        )
        return _DEFAULT_TURN_INPUT_TOKEN_BUDGET
    if value <= 0:
        logger.warning(
            "ADVISOR_TURN_INPUT_TOKEN_BUDGET=%d is non-positive; "
            "falling back to default %d",
            value,
            _DEFAULT_TURN_INPUT_TOKEN_BUDGET,
        )
        return _DEFAULT_TURN_INPUT_TOKEN_BUDGET
    return value


def estimate_request_input_tokens(request: CompletionRequest) -> int:
    """Estimate the *billed-equivalent* input-token cost of ``request``.

    Sums character counts across every payload field the provider
    serializes (system prompt, tool definitions, message history) and
    divides by ``_CHARS_PER_TOKEN``. Returns an integer estimate; the
    caller compares it to a budget in billed-equivalent terms.

    **Cache discounting (ABS-291):** tokens inside cached prefix regions
    are weighted at ``_CACHE_READ_WEIGHT`` (~10%), matching Anthropic's
    cache-read pricing:

    * ``cache_system=True`` → system prompt is already in the provider's
      cache (stable across all iterations); charged at 10%.
    * ``cache_tools=True`` → all tool definitions are cached; charged at
      10%.
    * The last ``cache=True`` content block in ``request.messages`` marks
      a rolling intra-loop cache breakpoint (ABS-285). Every message
      *before* the message that contains this block was written to cache
      on the previous iteration and is now a cache read; charged at 10%.
      The breakpoint message itself and any content after it are new
      (uncached) and charged at full price.

    Deliberately ignores: ``max_tokens`` (output cap), ``temperature``,
    ``stop_sequences``, and ``metadata`` (negligible contribution).
    """
    chars = 0
    if request.system:
        weight = _CACHE_READ_WEIGHT if request.cache_system else 1.0
        chars += int(len(request.system) * weight)
    if request.tools:
        tools_chars = 0
        for tool in request.tools:
            tools_chars += len(tool.name)
            tools_chars += len(tool.description)
            tools_chars += _json_chars(tool.input_schema)
        weight = _CACHE_READ_WEIGHT if request.cache_tools else 1.0
        chars += int(tools_chars * weight)
    chars += _message_list_billed_chars(request.messages)
    return chars // _CHARS_PER_TOKEN


def _message_list_billed_chars(messages: list[Message]) -> int:
    """Character count across all messages with cache-read discounting.

    Locates the last message that contains any block with ``cache=True``.
    Every message *before* that message is treated as a cache read
    (multiplied by ``_CACHE_READ_WEIGHT``); the breakpoint message and
    any messages after it are charged at full price.

    Returns the unscaled character total when no cache-marked block is
    found (i.e. the request is fully uncached).

    Background: the rolling intra-loop breakpoint (ABS-285) places
    ``cache=True`` on the last block of the most recent tool_result turn.
    Everything before that message was written to cache on the previous
    loop iteration and is served as a cache read on this one.
    Session-level milestone markers (``_mark_conversation_cache_milestones``
    in ``advisor.chat.session``) also use ``cache=True`` on early assistant
    turns; they appear before the rolling marker, so they are covered by
    the same discount region.
    """
    last_cache_msg: int | None = None
    for i, message in enumerate(messages):
        if isinstance(message.content, list):
            for block in message.content:
                if getattr(block, "cache", False):
                    last_cache_msg = i

    if last_cache_msg is None:
        return sum(_message_chars(m) for m in messages)

    cached_chars = 0
    uncached_chars = 0
    for i, message in enumerate(messages):
        if i < last_cache_msg:
            cached_chars += _message_chars(message)
        else:
            uncached_chars += _message_chars(message)
    return int(cached_chars * _CACHE_READ_WEIGHT) + uncached_chars


def _message_chars(message: Message) -> int:
    if isinstance(message.content, str):
        return len(message.content)
    total = 0
    for block in message.content:
        total += _block_chars(block)
    return total


def _block_chars(block: object) -> int:
    """Count payload chars on one content block.

    Mirrors the fields the provider serializes — ``text`` for text
    blocks, the JSON-encoded ``input`` for tool_use blocks, and either
    raw string or recursively-counted blocks for tool_result content.
    Unknown block types fall back to ``repr`` length so a future block
    kind doesn't silently contribute zero to the estimate.
    """
    if isinstance(block, TextBlock):
        return len(block.text)
    if isinstance(block, ToolUseBlock):
        return len(block.name) + _json_chars(block.input)
    if isinstance(block, ToolResultBlock):
        if isinstance(block.content, str):
            return len(block.content)
        return sum(_block_chars(b) for b in block.content)
    return len(repr(block))


def _json_chars(payload: object) -> int:
    """Best-effort character count of a JSON-serializable payload.

    Falls back to ``repr`` length when ``json.dumps`` can't handle the
    value (e.g. a custom object); the estimate doesn't need to match
    the wire format byte-for-byte, just be in the right ballpark.
    """
    try:
        return len(json.dumps(payload, default=str))
    except (TypeError, ValueError):
        return len(repr(payload))
