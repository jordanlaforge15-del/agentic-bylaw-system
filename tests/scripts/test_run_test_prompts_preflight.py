"""ABS-515: the eval runner's pre-flight, before a single turn is billed.

An eight-case sweep once billed ~$1.70 nobody had agreed to spend. Nothing
was broken — the runner drove whatever advisor was listening and had no way
to ask how that process was configured, so a metered advisor and a free one
were indistinguishable from the caller's side.

The fix is a handshake: ``GET /healthz`` reports the gateway the advisor
actually built (``advisor.api.app``) and whether it bills per token
(``advisor.llm.registry.is_metered``), and the runner refuses to start
against a metered one without ``--allow-metered``.

These cases pin the runner's half of it. The single most important
property is that BOTH unknowns fail closed: an advisor too old to report
``llm.metered``, and an advisor reporting a provider name nobody
recognises, are treated as metered. Guessing "free" spends real money on
the strength of a guess; guessing "metered" costs one flag.

No advisor, no database — the checks take a parsed health document.
"""
from __future__ import annotations

import pytest

from advisor.llm.registry import UNMETERED_PROVIDERS, is_metered
from scripts.run_test_prompts import (
    check_billing_precondition,
    check_model_precondition,
)


def _health(**llm: object) -> dict[str, object]:
    return {"status": "ok", "llm": llm}


BASE_URL = "http://127.0.0.1:8000"


# -- billing pre-flight -------------------------------------------------------


def test_metered_advisor_is_refused_without_the_flag() -> None:
    error = check_billing_precondition(
        _health(provider="anthropic", metered=True, main_model="claude-opus-4-5"),
        BASE_URL,
        allow_metered=False,
    )

    assert error is not None
    assert "anthropic" in error
    # The abort has to be actionable: name the flag and the reason.
    assert "--allow-metered" in error
    assert "metered" in error


def test_metered_advisor_runs_once_consent_is_explicit() -> None:
    assert (
        check_billing_precondition(
            _health(provider="anthropic", metered=True),
            BASE_URL,
            allow_metered=True,
        )
        is None
    )


def test_unmetered_advisor_needs_no_flag() -> None:
    """The mock gateway costs nothing; there is nothing to consent to."""
    assert (
        check_billing_precondition(
            _health(provider="mock", metered=False), BASE_URL, allow_metered=False
        )
        is None
    )


def test_advisor_that_does_not_report_metering_is_assumed_metered() -> None:
    """Fail closed on an old advisor — ABS-515's load-bearing case.

    ``llm.metered`` is absent on anything predating this change. Those are
    overwhelmingly real, metered advisors (the mock gateway only runs inside
    the e2e stack, which is always current), so "I don't know" must not
    resolve to "free".
    """
    error = check_billing_precondition(
        _health(main_model="claude-opus-4-5"), BASE_URL, allow_metered=False
    )

    assert error is not None
    assert "predates" in error
    assert "--allow-metered" in error


def test_health_document_without_an_llm_block_is_assumed_metered() -> None:
    error = check_billing_precondition({"status": "ok"}, BASE_URL, allow_metered=False)

    assert error is not None
    assert "--allow-metered" in error


def test_the_abort_says_there_is_no_cheaper_provider_to_switch_to() -> None:
    """Don't send the operator hunting for the backend that was removed.

    The obvious reading of "this run would spend metered credits" is "then
    point me at the free one". ABS-522 deleted it. Saying so in the abort is
    the difference between one flag and an afternoon.
    """
    error = check_billing_precondition(
        _health(provider="anthropic", metered=True), BASE_URL, allow_metered=False
    )

    assert error is not None
    assert "ABS-522" in error


def test_metered_flag_is_honoured_over_the_provider_name() -> None:
    """The server decides; the runner does not second-guess it.

    A gateway named ``anthropic`` reporting ``metered: false`` would be
    strange, but the server computed that answer from the gateway object it
    built. Re-deriving it here from the name would put two sources of truth
    in the system, and the runner's would be the worse-informed one.
    """
    assert (
        check_billing_precondition(
            _health(provider="anthropic", metered=False), BASE_URL, allow_metered=False
        )
        is None
    )


# -- model pre-flight (ABS-267, unchanged behaviour after the refactor) --------


def test_model_precondition_passes_on_a_match() -> None:
    assert (
        check_model_precondition(_health(main_model="claude-haiku-4-5"), "claude-haiku-4-5")
        is None
    )


def test_model_precondition_names_both_models_on_a_mismatch() -> None:
    error = check_model_precondition(
        _health(main_model="claude-opus-4-5"), "claude-haiku-4-5"
    )

    assert error is not None
    assert "claude-opus-4-5" in error
    assert "claude-haiku-4-5" in error
    assert "ADVISOR_LLM_MAIN_MODEL" in error


def test_model_precondition_fails_when_the_advisor_reports_no_model() -> None:
    error = check_model_precondition({"status": "ok"}, "claude-haiku-4-5")

    assert error is not None
    assert "None" in error


# -- the server-side half the runner depends on -------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("anthropic", True),
        ("mock", False),
        ("bedrock", True),  # unrecognised → fail closed
        ("claude_code", True),  # removed in ABS-522; never "free" again
        (None, True),  # gateway with no ``name`` attribute
    ],
)
def test_is_metered_fails_closed(provider: str | None, expected: bool) -> None:
    assert is_metered(provider) is expected


def test_only_the_mock_gateway_is_unmetered() -> None:
    """A new entry here makes eval runs free — it needs a reason in review."""
    assert UNMETERED_PROVIDERS == frozenset({"mock"})
