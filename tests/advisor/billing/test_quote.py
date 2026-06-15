"""Off-menu "Other" price quote (ABS-316).

The quote step is ALWAYS FREE — these tests prove the LLM difficulty
classification maps to an anchored, in-band price, that an empty question
is rejected before any LLM call, and that a misbehaving classifier
degrades to a fair mid-band default rather than crashing a checkout. The
gateway is the e2e ``MockGateway`` + dispatcher, which resolves a
deterministic tier from a ``MOCK_QUOTE_<TIER>`` sentinel.
"""
from __future__ import annotations

import pytest

from advisor.billing.quote import (
    BAND_HIGH_CENTS,
    BAND_LOW_CENTS,
    TIER_ORDER,
    TIERS,
    EmptyQuestionError,
    quote_question,
    tier_for,
)
from advisor.llm.base import CompletionResponse, TextBlock, TokenUsage
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher


def _gateway() -> MockGateway:
    return MockGateway(callable_=build_dispatcher())


@pytest.mark.asyncio
async def test_simple_question_quotes_at_the_floor_price() -> None:
    quote = await quote_question(
        _gateway(),
        "Is a duplex allowed at 1234 Elm St? MOCK_QUOTE_SIMPLE",
    )
    assert quote.difficulty == "simple"
    assert quote.price_cents == 7_900  # $79 — anchored to the menu floor
    assert quote.currency == "CAD"
    assert quote.rationale  # an explanation travels with the price


@pytest.mark.asyncio
async def test_complex_question_quotes_at_the_band_ceiling() -> None:
    quote = await quote_question(
        _gateway(),
        "Draft a full variance justification package. MOCK_QUOTE_COMPLEX",
    )
    assert quote.difficulty == "complex"
    assert quote.price_cents == 29_900  # $299 — top of the launch band


@pytest.mark.asyncio
async def test_exceptional_question_quotes_above_the_band() -> None:
    quote = await quote_question(
        _gateway(),
        "Assemble three parcels, rezone, and stack overlays. "
        "MOCK_QUOTE_EXCEPTIONAL",
    )
    assert quote.difficulty == "exceptional"
    # Clearly-more-complex questions are allowed above the $79–$299 band.
    assert quote.price_cents > BAND_HIGH_CENTS


@pytest.mark.asyncio
async def test_default_question_lands_mid_band() -> None:
    # No MOCK_QUOTE_* sentinel → mock defaults to the mid-band tier.
    quote = await quote_question(_gateway(), "A general zoning question.")
    assert quote.difficulty == "moderate"
    assert BAND_LOW_CENTS <= quote.price_cents <= BAND_HIGH_CENTS


@pytest.mark.asyncio
async def test_every_in_band_tier_stays_within_the_launch_envelope() -> None:
    for key in ("simple", "moderate", "involved", "complex"):
        assert BAND_LOW_CENTS <= TIERS[key].price_cents <= BAND_HIGH_CENTS


@pytest.mark.asyncio
async def test_empty_question_rejected_before_any_llm_call() -> None:
    calls: list = []

    class _Spy(MockGateway):
        async def complete(self, request):  # noqa: ANN001
            calls.append(request)
            raise AssertionError("quote must not call the LLM for empty input")

    with pytest.raises(EmptyQuestionError):
        await quote_question(_Spy(callable_=build_dispatcher()), "   ?  ")
    assert calls == []


@pytest.mark.asyncio
async def test_classifier_failure_degrades_to_mid_band_default() -> None:
    class _Broken(MockGateway):
        async def complete(self, request):  # noqa: ANN001
            return CompletionResponse(
                model="m",
                content=[TextBlock(text="not json at all")],
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )

    quote = await quote_question(
        _Broken(callable_=build_dispatcher()), "A real question."
    )
    # A parse failure must not crash the checkout — fair mid-band price.
    assert quote.difficulty == "moderate"
    assert quote.price_cents == TIERS["moderate"].price_cents


def test_cost_envelope_increases_with_difficulty() -> None:
    budgets = [TIERS[k].cumulative_token_budget for k in TIER_ORDER]
    # Harder, pricier questions get more reasoning headroom — but the
    # ordering is monotonic so the envelope tracks the price.
    assert budgets == sorted(budgets)
    assert len(set(budgets)) == len(budgets)


def test_tier_for_unknown_label_falls_back_to_moderate() -> None:
    assert tier_for("nonsense").key == "moderate"
    assert tier_for("").key == "moderate"
    assert tier_for("SIMPLE").key == "simple"  # case-insensitive
