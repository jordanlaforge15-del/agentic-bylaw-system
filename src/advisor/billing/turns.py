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
same factor for the same reason.

Grant sizing (ABS-404, 2026-08-08)
----------------------------------
ABS-416 left a cost note here putting a 175k turn at ~$0.96 and the
10-turn signup grant at ~$9.60 of API spend per account, against a
"~$0.55 USD / 100k wallet tokens" anchor. **That anchor was wrong by
~5x**, and this is the ticket that owns the number, so it is corrected
here rather than left to mislead the next person sizing the grant.

``docs/COST_MODEL.md`` measured it directly against the real API
(ABS-303, N=8, current prod config). The wallet counts
``input + output`` only; cache writes and reads are 35% of the dollar
cost and are invisible to it. So the honest denominator is cost per
*wallet-counted* token, and that is **~$28.9 / MTok USD** —
``$2.89 / 100k``, not ``$0.55 / 100k``.

At that rate:

===========================  ==================  ==================
Item                          Was believed         Actually
===========================  ==================  ==================
One 175k turn                 $0.96                ~$5.05
10-turn signup grant          $9.60                ~$50.50
===========================  ==================  ==================

Even the absolute floor — pretending every counted token is uncached
input at $15/MTok and cache costs nothing — puts a turn at $2.63 and a
10-turn grant at $26. There is no reading in which a free, no-card
signup should carry that.

``DEFAULT_SIGNUP_TOKEN_GRANT`` is therefore **3 turns** (525,000). Three
is enough to evaluate the product — the harm this ticket was filed for
was a new account locked out after *one* question — while cutting
per-account exposure to ~$15 at the measured rate. It stays a
no-restart env knob (``ADVISOR_SIGNUP_TOKEN_GRANT``), so trial-to-paid
conversion data can move it either way without a deploy.

The **top-up price ladder is deliberately NOT changed here**: at
$28.9/MTok every SKU sells turns for roughly a quarter of what they
cost, but list prices are a revenue decision, not an engineering one.
Recorded as a blocking item on the beta-pivot decision doc's open
questions instead — it must be settled before
``ADVISOR_PAYMENTS_ENABLED`` goes true, since selling below cost is only
harmless while nothing can actually be sold.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Calibrated against measured prod burn — see the module docstring.
DEFAULT_TOKENS_PER_TURN = 175_000
# ABS-404: 3 turns, not 10 — see "Grant sizing" above. Sized against the
# measured ~$28.9/MTok wallet-counted cost, not the ~$5.5/MTok anchor
# ABS-416 assumed.
DEFAULT_SIGNUP_TOKEN_GRANT = 3 * DEFAULT_TOKENS_PER_TURN  # 525,000
DEFAULT_CHAT_MIN_BALANCE_TOKENS = 0
# ABS-404: 1 turn, not 2. The threshold was sized in turns against a
# 10-turn grant, where 2 turns meant "20% left — time to act". Against
# the 3-turn grant above, 2 turns is 67% left: the warning would fire on
# every new user's first question and stop meaning anything. One turn
# still leaves room to ask something and then top up.
DEFAULT_LOW_BALANCE_WARN_TOKENS = 1 * DEFAULT_TOKENS_PER_TURN  # 175,000


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
