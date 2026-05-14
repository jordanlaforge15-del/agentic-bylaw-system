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

Configuration
-------------
The default budget is read once from the ``ADVISOR_TURN_INPUT_TOKEN_BUDGET``
env var (falling back to ``_DEFAULT_TURN_INPUT_TOKEN_BUDGET``) by
``default_token_budget()``. Callers — chiefly ``ChatSession`` — read
the default at construction time so a test can override it by passing
an explicit value without touching the environment.

The default (150,000) is a deliberate safety-net level, NOT a primary
cost lever:

- At Opus 4.5's $15/M input rate, 150k tokens caps one turn at ~$2.25.
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

# Default safety-net cap: 150k input tokens per turn. At Opus 4.5's
# $15/M input rate this caps a single turn at ~$2.25. Tuned to leave
# headroom for normal multi-turn conversations while still catching
# the runaways we observed in production logs.
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
    """Estimate the input-token cost of ``request`` BEFORE sending it.

    Sums character counts across every payload field the provider
    serializes (system prompt, tool definitions, message history) and
    divides by ``_CHARS_PER_TOKEN``. Returns an integer estimate; the
    caller compares it to a budget.

    Deliberately ignores: ``max_tokens`` (output cap, not input),
    ``temperature``, ``stop_sequences``, and ``metadata`` (negligible
    contribution). Cache-related flags are also irrelevant — even a
    cached read still counts toward the per-turn input ceiling we
    care about here (cost on cache reads is ~10% of normal, but the
    safety net measures the prompt size, not the bill).
    """
    chars = 0
    if request.system:
        chars += len(request.system)
    for tool in request.tools:
        chars += len(tool.name)
        chars += len(tool.description)
        chars += _json_chars(tool.input_schema)
    for message in request.messages:
        chars += _message_chars(message)
    return chars // _CHARS_PER_TOKEN


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
