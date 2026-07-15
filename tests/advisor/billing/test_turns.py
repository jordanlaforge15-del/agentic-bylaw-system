"""Token ↔ turn conversion parameters (ABS-380).

``advisor.billing.turns`` reads every parameter from the environment at
call time so a launch-day re-calibration takes effect without a restart —
these tests pin that behaviour and the flooring rules.
"""
from __future__ import annotations

from advisor.billing import turns


def test_defaults_match_design_spec() -> None:
    assert turns.tokens_per_turn() == 2_500
    assert turns.signup_token_grant() == 25_000
    assert turns.chat_min_balance_tokens() == 0
    assert turns.low_balance_warn_tokens() == 5_000


def test_approx_turns_floors_division(monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "2500")
    assert turns.approx_turns_remaining(25_000) == 10
    assert turns.approx_turns_remaining(24_999) == 9
    assert turns.approx_turns_remaining(2_499) == 0


def test_approx_turns_floors_negative_to_zero(monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "2500")
    assert turns.approx_turns_remaining(-1) == 0
    assert turns.approx_turns_remaining(-99_999) == 0
    assert turns.approx_turns_remaining(0) == 0


def test_factor_change_effective_without_restart(monkeypatch) -> None:
    # No cached settings object: a re-set env var changes the conversion
    # on the very next call.
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "5000")
    assert turns.tokens_per_turn() == 5_000
    assert turns.approx_turns_remaining(25_000) == 5
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "1000")
    assert turns.approx_turns_remaining(25_000) == 25


def test_misconfigured_tokens_per_turn_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "0")
    assert turns.tokens_per_turn() == 2_500  # min-guard prevents div-by-zero
    monkeypatch.setenv("ADVISOR_TOKENS_PER_TURN", "not-a-number")
    assert turns.tokens_per_turn() == 2_500


def test_env_overrides_thresholds(monkeypatch) -> None:
    monkeypatch.setenv("ADVISOR_SIGNUP_TOKEN_GRANT", "9000")
    monkeypatch.setenv("ADVISOR_CHAT_MIN_BALANCE_TOKENS", "500")
    monkeypatch.setenv("ADVISOR_LOW_BALANCE_WARN_TOKENS", "3000")
    assert turns.signup_token_grant() == 9_000
    assert turns.chat_min_balance_tokens() == 500
    assert turns.low_balance_warn_tokens() == 3_000
