"""Priced-question catalog: prices, input schema, Stripe Price lookup."""
from __future__ import annotations

from advisor.billing.questions import (
    QUESTION_DEVELOPMENT_STANDARDS,
    QUESTION_DUE_DILIGENCE,
    QUESTION_LEGAL_NONCONFORMING,
    QUESTION_ORDER,
    QUESTION_PERMITTED_USE,
    QUESTION_VARIANCE_JUSTIFICATION,
    QUESTIONS,
    all_questions,
    question_for,
    question_for_stripe_price_id,
)
from advisor.billing.settings import AdvisorBillingSettings


def test_catalog_has_the_five_launch_questions() -> None:
    assert set(QUESTIONS) == {
        QUESTION_PERMITTED_USE,
        QUESTION_DEVELOPMENT_STANDARDS,
        QUESTION_DUE_DILIGENCE,
        QUESTION_LEGAL_NONCONFORMING,
        QUESTION_VARIANCE_JUSTIFICATION,
    }
    # Menu order is cheapest → most expensive, as listed in the decision.
    assert QUESTION_ORDER == (
        QUESTION_PERMITTED_USE,
        QUESTION_DEVELOPMENT_STANDARDS,
        QUESTION_DUE_DILIGENCE,
        QUESTION_LEGAL_NONCONFORMING,
        QUESTION_VARIANCE_JUSTIFICATION,
    )


def test_launch_prices_match_the_decision() -> None:
    # Prices from docs/decisions/2026-06-priced-question-catalog.md,
    # in CAD cents. These are the load-bearing numbers — the e2e and
    # the Stripe Price amounts must match.
    assert QUESTIONS[QUESTION_PERMITTED_USE].price_cents == 7_900
    assert QUESTIONS[QUESTION_DEVELOPMENT_STANDARDS].price_cents == 14_900
    assert QUESTIONS[QUESTION_DUE_DILIGENCE].price_cents == 19_900
    assert QUESTIONS[QUESTION_LEGAL_NONCONFORMING].price_cents == 19_900
    assert QUESTIONS[QUESTION_VARIANCE_JUSTIFICATION].price_cents == 29_900


def test_development_standards_carries_an_elevated_reasoning_budget() -> None:
    # ABS-360: the flagship $149 multi-standard report reliably out-grows
    # the flat default cumulative budget and tripped the ABS-305 breaker
    # before it could ground ("ran past its reasoning budget"). It now
    # carries a larger, margin-safe per-question ceiling so a routine
    # input completes.
    from advisor.llm.budget import default_cumulative_token_budget

    dev = QUESTIONS[QUESTION_DEVELOPMENT_STANDARDS]
    assert dev.cumulative_token_budget == 260_000
    # Strictly above the session default — that gap is the fix.
    assert dev.cumulative_token_budget > default_cumulative_token_budget()


def test_variance_and_due_diligence_carry_elevated_reasoning_budgets() -> None:
    # ABS-370: sibling fix to ABS-360. Replaying the ABS-305 estimator over
    # real run transcripts showed both SKUs' routine runs straddle the flat
    # 165k default (variance: captured at 131k / voided at 171.6k on the
    # same code; due-diligence: captured at 158.8k / voided at 193.0k), so
    # each now carries the same margin-safe 260k ceiling as the
    # development-standards report.
    from advisor.llm.budget import default_cumulative_token_budget

    for slug in (QUESTION_VARIANCE_JUSTIFICATION, QUESTION_DUE_DILIGENCE):
        q = QUESTIONS[slug]
        assert q.cumulative_token_budget == 260_000
        # Strictly above the session default — that gap is the fix.
        assert q.cumulative_token_budget > default_cumulative_token_budget()


def test_only_grounding_heavy_questions_override_the_budget() -> None:
    # Everything else runs under the shared default (``None`` = defer to
    # ``default_cumulative_token_budget``); only questions we've found to
    # trip the flat default carry an explicit override.
    overridden = {
        q.slug for q in all_questions() if q.cumulative_token_budget is not None
    }
    assert overridden == {
        QUESTION_DEVELOPMENT_STANDARDS,
        QUESTION_DUE_DILIGENCE,
        QUESTION_VARIANCE_JUSTIFICATION,
    }
    # Any override that IS set stays within the off-menu tier ladder's top
    # (``exceptional`` = 330k, quote.py) so a catalog question can never be
    # granted more reasoning headroom than the most expensive off-menu band.
    for q in all_questions():
        if q.cumulative_token_budget is not None:
            assert 0 < q.cumulative_token_budget <= 330_000


def test_all_questions_returns_menu_order() -> None:
    questions = all_questions()
    assert [q.slug for q in questions] == list(QUESTION_ORDER)
    # Prices are non-decreasing across the menu order.
    prices = [q.price_cents for q in questions]
    assert prices == sorted(prices)


def test_each_question_binds_to_existing_engine_tools() -> None:
    # The backing calls must be real chat-engine tool names; a question
    # that names a non-existent tool is ungroundable.
    valid_tools = {
        "get_address_profile",
        "get_zone_profile",
        "evaluate_submission_against_bylaws",
        "search_bylaw_evidence",
    }
    for question in all_questions():
        assert question.backing_calls, f"{question.slug} has no backing calls"
        assert set(question.backing_calls) <= valid_tools, question.slug


def test_question_for_known_and_unknown() -> None:
    assert question_for(QUESTION_PERMITTED_USE).slug == QUESTION_PERMITTED_USE
    try:
        question_for("not_a_question")
    except KeyError:
        pass
    else:  # pragma: no cover - guard
        raise AssertionError("expected KeyError for unknown slug")


def test_prompt_template_placeholders_match_required_inputs() -> None:
    # Every input field name must appear as a {placeholder} in the
    # prompt template so the buy-an-answer flow can fill it; and the
    # template must not reference a placeholder with no backing input.
    import string

    for question in all_questions():
        field_names = {f.name for f in question.required_inputs}
        placeholders = {
            name
            for _, name, _, _ in string.Formatter().parse(
                question.prompt_template
            )
            if name
        }
        assert placeholders == field_names, question.slug
        # Every question needs at least one required input (an address).
        assert any(f.required for f in question.required_inputs), question.slug


def test_required_inputs_include_an_address() -> None:
    for question in all_questions():
        names = {f.name for f in question.required_inputs}
        assert "address" in names, question.slug


def test_stripe_price_env_var_naming_convention() -> None:
    # Convention: STRIPE_PRICE_QUESTION_<SLUG>, all uppercase.
    assert (
        question_for(QUESTION_PERMITTED_USE).stripe_price_env_var
        == "STRIPE_PRICE_QUESTION_PERMITTED_USE"
    )
    assert (
        question_for(QUESTION_VARIANCE_JUSTIFICATION).stripe_price_env_var
        == "STRIPE_PRICE_QUESTION_VARIANCE_JUSTIFICATION"
    )


def test_question_for_stripe_price_id_round_trips(monkeypatch) -> None:
    monkeypatch.setenv(
        "STRIPE_PRICE_QUESTION_DUE_DILIGENCE", "price_q_test_123"
    )
    settings = AdvisorBillingSettings()
    question = question_for_stripe_price_id("price_q_test_123", settings)
    assert question is not None
    assert question.slug == QUESTION_DUE_DILIGENCE


def test_question_for_stripe_price_id_returns_none_for_unknown() -> None:
    settings = AdvisorBillingSettings()
    assert question_for_stripe_price_id("price_missing", settings) is None
    assert question_for_stripe_price_id("", settings) is None


def test_settings_expose_a_price_field_per_question() -> None:
    # Every question's env-var convention must map to a real settings
    # attribute, otherwise the reverse-lookup silently never matches.
    settings = AdvisorBillingSettings()
    for question in all_questions():
        attr = question.stripe_price_env_var.lower()
        assert hasattr(settings, attr), attr
