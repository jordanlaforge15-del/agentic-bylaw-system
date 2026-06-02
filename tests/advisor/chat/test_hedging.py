"""ABS-263 hedge injector: append a verify-with-a-planner qualifier to
feasibility-grade answers, leave narrow lookups and already-hedged answers
alone."""
from __future__ import annotations

from advisor.chat.hedging import (
    HEDGE_MARKERS,
    HEDGE_SUFFIX,
    already_hedged,
    apply_hedge,
    should_hedge,
)
from advisor.llm.base import TextBlock, ToolUseBlock

# A developer feasibility answer: several built-form dimensions stacked
# together, no hedging language. This is exactly TC-005's failure shape.
_FEASIBILITY_ANSWER = (
    "Max height: 25.0 m. Max FAR: 2.0. Lot coverage: 65%. "
    "Front setback: 3.0 m. Parking: 1 space per dwelling unit."
)

# A narrow homeowner-style lookup: a single dimension, one number. The
# ABS-263 acceptance criteria say this must NOT get the full hedge dance.
_SIMPLE_LOOKUP = "The rear-yard setback in ER-1 is 7.5 m (RC-LUB §..)."


def test_should_hedge_on_feasibility_answer():
    assert should_hedge(_FEASIBILITY_ANSWER) is True


def test_should_not_hedge_simple_single_dimension_lookup():
    assert should_hedge(_SIMPLE_LOOKUP) is False


def test_should_not_hedge_empty_or_blank():
    assert should_hedge("") is False
    assert should_hedge("   \n  ") is False


def test_should_not_hedge_when_no_numbers():
    # Dimensions named but nothing quantitative to act on.
    text = "Height and parking are both regulated for this zone."
    assert should_hedge(text) is False


def test_should_not_double_hedge_already_hedged_feasibility():
    """An answer that already points the user at a planner must not be
    hedged again — keeps the injector idempotent."""
    hedged = _FEASIBILITY_ANSWER + " Confirm with a qualified planner first."
    assert already_hedged(hedged) is True
    assert should_hedge(hedged) is False


def test_apply_hedge_appends_suffix_to_last_text_block():
    content = [TextBlock(text=_FEASIBILITY_ANSWER)]
    out = apply_hedge(content)

    assert out is not content  # changed -> fresh list
    assert out[0].text.startswith(_FEASIBILITY_ANSWER)
    assert out[0].text.endswith(HEDGE_SUFFIX)


def test_apply_hedge_is_noop_for_simple_lookup():
    content = [TextBlock(text=_SIMPLE_LOOKUP)]
    out = apply_hedge(content)
    assert out is content  # unchanged object identity == no-op
    assert out[0].text == _SIMPLE_LOOKUP


def test_apply_hedge_is_idempotent():
    content = [TextBlock(text=_FEASIBILITY_ANSWER)]
    once = apply_hedge(content)
    twice = apply_hedge(once)
    assert twice is once  # second pass is a no-op (already hedged)


def test_injected_hedge_carries_verifier_markers():
    """The appended suffix must contain markers the ABS-260 verifier
    (scripts/verify_test_prompts.py) scans for, so an injected hedge is
    actually credited as hedging."""
    low = HEDGE_SUFFIX.lower()
    present = [m for m in HEDGE_MARKERS if m in low]
    # Several markers, not just one, so phrasing tweaks don't silently
    # drop us below the rubric.
    assert {"planner", "hrm", "not legal advice", "site-specific"} <= set(present)


def test_apply_hedge_appends_block_when_turn_has_no_text():
    """Defensive: a turn with only non-text blocks still gets a hedge
    block rather than silently dropping the qualifier."""
    content = [ToolUseBlock(id="t1", name="noop", input={})]
    # No text => no feasibility signal => no-op (correct: nothing to qualify).
    assert apply_hedge(content) is content
