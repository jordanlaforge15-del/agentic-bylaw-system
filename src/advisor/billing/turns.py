"""Token ↔ turn conversion and wallet env parameters (ABS-380).

The beta pivot presents the prepaid token wallet to users as "~N turns".
Turns conversion is **backend-owned** (design spec D6): the frontend never
divides tokens itself — it renders ``approx_turns_remaining`` /
``tokens_per_turn`` straight off the wire. That keeps a re-calibration of
the per-turn token size a single backend concern.

These parameters are read **directly from the environment at call time**,
NOT through a cached ``pydantic_settings`` object, so an operator can
retune ``ADVISOR_TOKENS_PER_TURN`` (or the grant / floor / warn threshold)
with a config flip and no process restart — the same convention
``_conversation_entry_enabled`` uses for ``ADVISOR_CONVERSATION_ENTRY_ENABLED``.
The acceptance test "factor change effective without restart" pins this.

Defaults come from the design spec's business-parameters table; they are
placeholders pending the transcript-replay calibration recorded on ABS-380.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Design-spec defaults (2026-07-beta-pivot-turn-wallet-gated-reports.md).
DEFAULT_TOKENS_PER_TURN = 2_500
DEFAULT_SIGNUP_TOKEN_GRANT = 25_000
DEFAULT_CHAT_MIN_BALANCE_TOKENS = 0
DEFAULT_LOW_BALANCE_WARN_TOKENS = 5_000


def _read_int(name: str, default: int, *, minimum: int | None = None) -> int:
    """Parse ``os.environ[name]`` as an int, falling back to ``default``.

    A missing, empty, or unparsable value returns ``default`` rather than
    raising — a misconfigured env var must not 500 a balance read. When
    ``minimum`` is set, a parsed value below it also falls back to
    ``default`` (used to keep ``tokens_per_turn`` at least 1 so the
    turns division can't hit ZeroDivisionError).
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("%s=%r is not an integer; using default %d", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning(
            "%s=%d is below the minimum %d; using default %d",
            name,
            value,
            minimum,
            default,
        )
        return default
    return value


def tokens_per_turn() -> int:
    """Tokens attributed to one "turn" for display conversion.

    Clamped to at least 1 so ``approx_turns_remaining`` can't divide by
    zero if the env var is misconfigured to 0 or a negative value.
    """
    return _read_int(
        "ADVISOR_TOKENS_PER_TURN", DEFAULT_TOKENS_PER_TURN, minimum=1
    )


def signup_token_grant() -> int:
    """Tokens granted once to a brand-new user's wallet on first sign-in."""
    return _read_int(
        "ADVISOR_SIGNUP_TOKEN_GRANT", DEFAULT_SIGNUP_TOKEN_GRANT, minimum=0
    )


def chat_min_balance_tokens() -> int:
    """Pre-flight floor: chat is refused at ``balance <= floor`` (ABS-383).

    Read here so the wallet read API can surface it as ``floor_tokens`` and
    derive ``chat_enabled`` consistently with the pre-flight check.
    """
    return _read_int(
        "ADVISOR_CHAT_MIN_BALANCE_TOKENS", DEFAULT_CHAT_MIN_BALANCE_TOKENS
    )


def low_balance_warn_tokens() -> int:
    """Warn threshold: ``low_balance`` flips true at ``balance <= warn``."""
    return _read_int(
        "ADVISOR_LOW_BALANCE_WARN_TOKENS",
        DEFAULT_LOW_BALANCE_WARN_TOKENS,
        minimum=0,
    )


def approx_turns_remaining(balance_tokens: int) -> int:
    """``floor(balance / tokens_per_turn)``, floored at 0 for display.

    Negative balances (overdraw) present as 0 turns remaining — the UI
    never shows a negative turn count.
    """
    if balance_tokens <= 0:
        return 0
    return balance_tokens // tokens_per_turn()
