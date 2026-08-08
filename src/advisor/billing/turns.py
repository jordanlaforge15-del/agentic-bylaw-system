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

Calibration (ABS-416, 2026-08-08)
---------------------------------
The original defaults came from the design spec's business-parameters
table and were explicit placeholders. They were wrong by ~70x: a turn was
assumed to cost 2,500 tokens, so the wallet advertised dozens-to-hundreds
of questions where it actually covered a handful.

Measured against production, queried 2026-08-08. In prod one
``advisor_usage_event`` row of ``event_type='llm_call'`` is one assistant
turn (the whole tool loop is aggregated into it) — verified by matching
each row against its ``advisor_token_transaction`` burn, which agrees
exactly (e.g. 243,820 in + 3,746 out == the -247,566 burn):

===============================================  ======  ========  ========
Sample                                                n    median      mean
===============================================  ======  ========  ========
Wallet burns (every burn ever recorded in prod)       3   103,014   120,022
Turns on the current case flow (2026-06 onward)       4   110,610   119,568
Full-research turns, all history (>= 50k)            12   192,724   271,650
===============================================  ======  ========  ========

Burn is strongly bimodal: intake / clarifying / short follow-up turns land
in the 300–25k band, while a real grounded research question lands in the
100k–250k band. The two full-research prod questions cited on ABS-416 are
247,566 and 103,014 tokens; ``DEFAULT_TOKENS_PER_TURN`` is set to 175,000,
their midpoint (175,290 rounded). That sits above the recent all-turn mean
(~120k), so the count we advertise errs toward *under*-promising — the
correct direction for a bug whose harm was over-promising.

Every other token-denominated parameter here was sized against the 2,500
assumption in units of turns, so all of them are rescaled by the same
factor (70x) and the turn counts the product promises are unchanged:

* signup grant       25,000 -> 1,750,000  (10 turns, as advertised)
* low-balance warn    5,000 ->   350,000  (2 turns, as before)
* chat floor              0 ->         0  (0 turns — unchanged)

The paid top-up SKUs in ``advisor.billing.topups`` are rescaled by the
same factor for the same reason. Cost note for ABS-404 (which owns grant
sizing / pricing strategy, not this ticket): at the ~$0.55 USD / 100k
wallet tokens anchor, a 175k turn costs ~$0.96 and the 10-turn signup
grant is ~$9.60 of API spend per new account. That is a live exposure —
dial ``ADVISOR_SIGNUP_TOKEN_GRANT`` down on prod if it needs to be
smaller before ABS-404 settles; no deploy is required.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Calibrated against measured prod burn — see the module docstring.
DEFAULT_TOKENS_PER_TURN = 175_000
DEFAULT_SIGNUP_TOKEN_GRANT = 10 * DEFAULT_TOKENS_PER_TURN  # 1,750,000
DEFAULT_CHAT_MIN_BALANCE_TOKENS = 0
DEFAULT_LOW_BALANCE_WARN_TOKENS = 2 * DEFAULT_TOKENS_PER_TURN  # 350,000


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
