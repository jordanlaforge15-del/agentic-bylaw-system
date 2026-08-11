"""Unit tests for scripts/validate_claude_code_gateway.py (ABS-457).

The script's live half spends subscription turns and needs an
authenticated ``claude`` CLI, so none of that runs here. What is tested
is the half that decides pass/fail: the four pure verdict functions,
the two preconditions (API key set, opt-in absent), and the report
shape. Those are what a future reader will trust when they re-run the
validation, so a canned payload for each verdict — the real one it
returned on 2026-08-11 and the failure it is meant to catch — is worth
more than a live re-run nobody will pay for in CI.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from advisor.llm.base import (
    CompletionResponse,
    LLMRole,
    TextBlock,
    ToolUseBlock,
)
from advisor.llm.claude_code_backend import ClaudeCodeGateway
from scripts.validate_claude_code_gateway import (
    API_KEY_REFUSAL,
    ASSUMPTION_TITLES,
    OPT_IN_ENV_VAR,
    AssumptionResult,
    api_key_is_set,
    build_report,
    main,
    report_dir,
    usage_excerpt,
    verdict_autocompact,
    verdict_model_alias,
    verdict_round_trip,
    verdict_token_attribution,
)


def _usage(
    input_tokens: int = 10,
    output_tokens: int = 100,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> dict:
    return {
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            # Noise the excerpt must drop.
            "service_tier": "standard",
            "iterations": [{"input_tokens": input_tokens}],
        }
    }


# ---------------------------------------------------------------------------
# usage_excerpt
# ---------------------------------------------------------------------------


def test_usage_excerpt_keeps_only_the_four_counters():
    assert usage_excerpt(_usage(cache_creation=5)) == {
        "input_tokens": 10,
        "output_tokens": 100,
        "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 0,
    }


def test_usage_excerpt_of_payload_without_usage_is_empty():
    assert usage_excerpt({"is_error": False}) == {}


# ---------------------------------------------------------------------------
# Assumption 1: model alias resolution
# ---------------------------------------------------------------------------


def test_model_alias_passes_when_a_modelusage_entry_matches():
    # Shape observed live: the CLI bills its own scaffolding turn to a
    # second model, so a matching entry — not a sole entry — is the bar.
    payload = {
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"canonicalModel": "claude-haiku-4-5"},
            "claude-opus-4-5": {"canonicalModel": "claude-opus-4-5"},
        }
    }
    passed, detail, evidence = verdict_model_alias(payload, "claude-opus-4-5")
    assert passed
    assert "claude-opus-4-5" in detail
    assert evidence["modelUsage_canonicalModel"]["claude-opus-4-5"] == "claude-opus-4-5"


def test_model_alias_fails_when_the_alias_resolved_elsewhere():
    payload = {"modelUsage": {"claude-sonnet-4-5": {"canonicalModel": "claude-sonnet-4-5"}}}
    passed, detail, _ = verdict_model_alias(payload, "claude-opus-4-5")
    assert not passed
    assert "claude-sonnet-4-5" in detail


def test_model_alias_fails_when_modelusage_is_absent():
    passed, detail, _ = verdict_model_alias({"usage": {}}, "claude-opus-4-5")
    assert not passed
    assert "modelUsage" in detail


# ---------------------------------------------------------------------------
# Assumption 2: token attribution
# ---------------------------------------------------------------------------


def test_token_attribution_passes_when_the_prompt_lands_in_input_tokens():
    baseline = _usage(input_tokens=10)
    probe = _usage(input_tokens=1300)
    passed, detail, evidence = verdict_token_attribution(baseline, probe, 5000)
    assert passed
    assert evidence["delta"]["input_tokens"] == 1290
    assert "visible to the wallet_cap_trip breaker" in detail


def test_token_attribution_fails_when_the_prompt_lands_in_the_cache():
    """The verdict this run actually returned on 2026-08-11.

    input_tokens identical across both calls, the whole system prompt in
    cache_creation. The message must say so in the strongest terms —
    the breaker's input term is inert, not merely low.
    """
    baseline = _usage(input_tokens=10, cache_read=24419)
    probe = _usage(input_tokens=10, cache_creation=13717, cache_read=11691)
    passed, detail, evidence = verdict_token_attribution(baseline, probe, 5023)
    assert not passed
    assert evidence["delta"]["input_tokens"] == 0
    assert "not a function of prompt size" in detail
    assert "inert" in detail


def test_token_attribution_distinguishes_undercount_from_inert():
    """A small-but-nonzero delta is a different diagnosis."""
    baseline = _usage(input_tokens=10)
    probe = _usage(input_tokens=110)  # +100, under the 502 threshold
    passed, detail, _ = verdict_token_attribution(baseline, probe, 5023)
    assert not passed
    assert "under-counts" in detail
    assert "inert" not in detail


def test_token_attribution_fails_when_usage_is_missing():
    passed, detail, _ = verdict_token_attribution({}, _usage(), 5000)
    assert not passed
    assert "no usage object" in detail


# ---------------------------------------------------------------------------
# Assumption 3: autocompact pinning
# ---------------------------------------------------------------------------


def test_autocompact_passes_when_the_full_conversation_is_reported():
    # Live shape: input landed almost entirely in cache_creation, which
    # assumption 3 does not care about — it asks only whether the input
    # arrived at all.
    payload = _usage(input_tokens=10, cache_creation=51021, cache_read=11691)
    passed, detail, evidence = verdict_autocompact(payload, 200_971)
    assert passed
    assert evidence["reported_input_tokens_total"] == 62722
    assert evidence["compaction_markers"] == []
    assert "no compaction notice" in detail


def test_autocompact_fails_when_the_reported_input_is_a_fraction():
    payload = _usage(input_tokens=10, cache_creation=2000)
    passed, detail, _ = verdict_autocompact(payload, 200_000)
    assert not passed
    assert "silent compaction" in detail


def test_autocompact_catches_a_partial_compaction():
    """A summarise-to-half compaction must not read as "not compacted".

    This is the check's whole point, and a loose floor would miss it:
    25,000 tokens for a 200,000-character conversation is half the input
    gone, which clears a 40%-of-estimate bar but not the 80% one the
    threshold is actually set to.
    """
    payload = _usage(input_tokens=10, cache_creation=25_000)
    passed, detail, evidence = verdict_autocompact(payload, 200_000)
    assert not passed
    assert evidence["reported_input_threshold"] == 40_000
    assert "silent compaction" in detail


def test_autocompact_fails_on_a_compaction_notice_in_the_payload():
    payload = _usage(input_tokens=10, cache_creation=51021, cache_read=11691)
    payload["result"] = "Context low — conversation was compacted to continue."
    passed, detail, evidence = verdict_autocompact(payload, 200_971)
    assert not passed
    assert evidence["compaction_markers"] == ["result"]
    assert "compaction notice" in detail


def test_autocompact_marker_scan_ignores_key_names():
    """``autocompact`` as a *key* is our own flag, not a compaction event.

    Scanning keys would make every payload from a correctly-configured
    run look like a failure.
    """
    payload = _usage(input_tokens=10, cache_creation=51021, cache_read=11691)
    payload["autocompact_threshold"] = 1_000_000
    passed, _, evidence = verdict_autocompact(payload, 200_971)
    assert passed
    assert evidence["compaction_markers"] == []


def test_autocompact_fails_when_usage_is_missing():
    passed, detail, _ = verdict_autocompact({"is_error": False}, 200_000)
    assert not passed
    assert "no usage object" in detail


# ---------------------------------------------------------------------------
# Assumption 4: multi-iteration round trip
# ---------------------------------------------------------------------------


def _tool_use_response() -> CompletionResponse:
    return CompletionResponse(
        id="uuid-1",
        model="claude-opus-4-5",
        role=LLMRole.ASSISTANT,
        content=[
            ToolUseBlock(
                id="toolu_cc_abc_0",
                name="lookup_zone_standard",
                input={"zone": "HR-2", "standard": "max_height"},
            )
        ],
        stop_reason="tool_use",
    )


def _final_answer_response(text: str = "HR-2 permits 25 metres.") -> CompletionResponse:
    return CompletionResponse(
        id="uuid-2",
        model="claude-opus-4-5",
        role=LLMRole.ASSISTANT,
        content=[TextBlock(text=text)],
        stop_reason="end_turn",
    )


def test_round_trip_passes_when_both_halves_land():
    passed, detail, evidence = verdict_round_trip(
        _tool_use_response(), _final_answer_response(), "lookup_zone_standard"
    )
    assert passed
    assert evidence["first_tool_calls"][0]["input"]["zone"] == "HR-2"
    assert "final_answer" in detail


def test_round_trip_fails_when_turn_one_never_asks_for_a_tool():
    passed, detail, _ = verdict_round_trip(
        _final_answer_response(), _final_answer_response(), "lookup_zone_standard"
    )
    assert not passed
    assert "turn 1 did not request a tool call" in detail


def test_round_trip_fails_when_turn_one_asks_for_the_wrong_tool():
    first = _tool_use_response()
    first.content = [ToolUseBlock(id="toolu_cc_abc_0", name="some_other_tool", input={})]
    passed, detail, _ = verdict_round_trip(first, _final_answer_response(), "lookup_zone_standard")
    assert not passed
    assert "some_other_tool" in detail


def test_round_trip_fails_when_turn_two_asks_for_another_tool_instead():
    passed, detail, _ = verdict_round_trip(
        _tool_use_response(), _tool_use_response(), "lookup_zone_standard"
    )
    assert not passed
    assert "turn 2 did not return a final answer" in detail


def test_round_trip_fails_on_an_empty_final_answer():
    passed, detail, _ = verdict_round_trip(
        _tool_use_response(),
        _final_answer_response(text="   "),
        "lookup_zone_standard",
    )
    assert not passed
    assert "turn 2 did not return a final answer" in detail


def test_round_trip_fails_when_the_second_turn_never_happened():
    passed, detail, _ = verdict_round_trip(_tool_use_response(), None, "lookup_zone_standard")
    assert not passed
    assert "did not produce a response" in detail


# ---------------------------------------------------------------------------
# Preconditions and CLI behaviour
# ---------------------------------------------------------------------------


def test_api_key_is_set_reads_the_environment(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert api_key_is_set() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    assert api_key_is_set() is True


def test_api_key_is_set_treats_an_empty_value_as_unset():
    assert api_key_is_set({"ANTHROPIC_API_KEY": ""}) is False


def test_main_refuses_and_exits_2_when_the_api_key_is_set(monkeypatch, capsys):
    """DoD 2: the refusal must be loud, and must not depend on the opt-in.

    Exit 2 (precondition), not 1 (assumption failed) — a run that never
    started must not be readable as a run that found something.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setenv(OPT_IN_ENV_VAR, "1")
    assert main([]) == 2
    assert API_KEY_REFUSAL in capsys.readouterr().err


def test_main_skips_and_writes_nothing_without_the_opt_in(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(OPT_IN_ENV_VAR, raising=False)
    assert main(["--report-root", str(tmp_path)]) == 0
    assert "SKIPPED" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_the_literal_1_opts_in(monkeypatch, capsys, tmp_path, value):
    """Anything other than ``1`` skips, matching the haiku-smoke gate.

    A truthy-string gate would let a stray ``ABS_RUN_LIVE_CLAUDE_CODE=0``
    spend subscription turns.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv(OPT_IN_ENV_VAR, value)
    assert main(["--report-root", str(tmp_path)]) == 0
    assert "SKIPPED" in capsys.readouterr().out


def test_main_exits_2_when_the_cli_is_missing(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv(OPT_IN_ENV_VAR, "1")
    monkeypatch.setattr("scripts.validate_claude_code_gateway.shutil.which", lambda _: None)
    assert main(["--report-root", str(tmp_path)]) == 2
    assert "not on PATH" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_the_private_transport_hook_the_script_leans_on_still_exists():
    """Pin the one private-API dependency the live half has.

    ``_invoke`` calls ``ClaudeCodeGateway._invoke_once`` to get the raw
    payload, because ``complete()`` returns a translated response and
    three of the four assumptions are about fields the translation drops.
    That is a deliberate trade, but it means a transport refactor could
    break this script silently — the breakage would only surface on the
    next live run, months later, to whoever is depending on the result.
    Pinning the signature here turns that into a unit-test failure.
    """
    signature = inspect.signature(ClaudeCodeGateway._invoke_once)
    assert list(signature.parameters) == [
        "self",
        "prompt",
        "schema",
        "request",
        "attempt",
    ]
    assert signature.parameters["attempt"].kind is inspect.Parameter.KEYWORD_ONLY
    assert inspect.iscoroutinefunction(ClaudeCodeGateway._invoke_once)


def test_report_dir_is_a_utc_timestamp():
    stamped = report_dir(Path("evals/runs/x"), datetime(2026, 8, 11, 1, 6, 50, tzinfo=UTC))
    assert stamped.name == "20260811T010650Z"


def _result(key: str, passed: bool) -> AssumptionResult:
    return AssumptionResult(
        key=key,
        title=ASSUMPTION_TITLES[key],
        passed=passed,
        detail="detail",
        model="claude-opus-4-5",
        evidence={"usage": {"input_tokens": 10}},
    )


def test_build_report_is_json_serialisable_and_carries_every_assumption():
    results = [_result(key, True) for key in ASSUMPTION_TITLES]
    report = build_report(results, "claude-opus-4-5", "/nonexistent/claude")
    assert report["all_passed"] is True
    assert report["issue"] == "ABS-457"
    assert [a["key"] for a in report["assumptions"]] == list(ASSUMPTION_TITLES)
    # Every entry carries the evidence the verdict came from, per DoD 3.
    assert all(a["evidence"] for a in report["assumptions"])
    json.dumps(report)  # must not raise


def test_build_report_all_passed_is_false_when_any_assumption_failed():
    results = [
        _result("model_alias_resolution", True),
        _result("token_attribution", False),
    ]
    assert build_report(results, "claude-opus-4-5", "/nonexistent/claude")["all_passed"] is False


def test_build_report_survives_an_unrunnable_cli_path():
    """``--version`` is a diagnostic, not a precondition — a bad path
    must leave a null in the report rather than blowing up the write."""
    report = build_report([_result("token_attribution", True)], "m", "/nope/claude")
    assert report["cli_version"] is None
