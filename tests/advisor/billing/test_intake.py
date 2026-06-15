"""Consultant-style LLM intake detection (ABS-315).

When a selected question is missing required inputs, an LLM extracts what
it can from the conversation and the module decides completeness against
the question's required-input schema — never on the LLM's say-so. These
tests prove: extraction merges with already-collected inputs, completeness
is gated on REQUIRED inputs only, the consultant prompt names the missing
fields, optional inputs are invited not demanded, and a misbehaving
extractor degrades to "ask for the missing inputs" rather than crashing.

The gateway is the e2e ``MockGateway`` + dispatcher, which resolves a
deterministic extraction from ``MOCK_INPUT[<field>]=<value>|`` sentinels.
"""
from __future__ import annotations

import pytest

from advisor.billing.intake import (
    IntakeResult,
    build_consultant_prompt,
    detect_intake,
)
from advisor.billing.questions import (
    QUESTION_DUE_DILIGENCE,
    QUESTION_LEGAL_NONCONFORMING,
    QUESTION_PERMITTED_USE,
    question_for,
)
from advisor.llm.base import CompletionResponse, TextBlock, TokenUsage
from advisor.llm.mock import MockGateway
from advisor.llm.mock_dispatcher import build_dispatcher


def _gateway() -> MockGateway:
    return MockGateway(callable_=build_dispatcher())


@pytest.mark.asyncio
async def test_missing_required_input_triggers_consultant_prompt() -> None:
    # permitted_use needs address + proposed_use. The conversation only
    # supplies proposed_use → intake must ask for the address.
    result = await detect_intake(
        _gateway(),
        question_for(QUESTION_PERMITTED_USE),
        conversation=(
            "Can I build a fourplex on my lot? "
            "MOCK_INPUT[proposed_use]=a four-unit dwelling|"
        ),
    )
    assert result.complete is False
    assert result.missing_required == ["address"]
    assert result.inputs["proposed_use"] == "a four-unit dwelling"
    # The consultant prompt names the missing field by its human label.
    assert "Property address" in result.prompt


@pytest.mark.asyncio
async def test_all_required_inputs_present_completes() -> None:
    result = await detect_intake(
        _gateway(),
        question_for(QUESTION_PERMITTED_USE),
        conversation=(
            "Is a duplex allowed at 12 Pine Street? "
            "MOCK_INPUT[address]=12 Pine Street|"
            "MOCK_INPUT[proposed_use]=a duplex|"
        ),
    )
    assert result.complete is True
    assert result.missing_required == []
    assert result.inputs == {
        "address": "12 Pine Street",
        "proposed_use": "a duplex",
    }
    assert result.prompt == ""


@pytest.mark.asyncio
async def test_provided_inputs_merge_with_extracted() -> None:
    # proposed_use was confirmed in an earlier intake turn; the new
    # conversation supplies the address. Merge → complete.
    result = await detect_intake(
        _gateway(),
        question_for(QUESTION_PERMITTED_USE),
        conversation="It's at 12 Pine Street. MOCK_INPUT[address]=12 Pine Street|",
        provided_inputs={"proposed_use": "a four-unit dwelling"},
    )
    assert result.complete is True
    assert result.inputs == {
        "address": "12 Pine Street",
        "proposed_use": "a four-unit dwelling",
    }


@pytest.mark.asyncio
async def test_provided_inputs_win_over_extracted_on_conflict() -> None:
    # The user confirmed one address earlier; the conversation mentions a
    # different one. The confirmed (provided) value wins.
    result = await detect_intake(
        _gateway(),
        question_for(QUESTION_PERMITTED_USE),
        conversation=(
            "MOCK_INPUT[address]=99 Other Ave|"
            "MOCK_INPUT[proposed_use]=a duplex|"
        ),
        provided_inputs={"address": "12 Pine Street"},
    )
    assert result.inputs["address"] == "12 Pine Street"


@pytest.mark.asyncio
async def test_optional_input_does_not_block_completion() -> None:
    # legal_nonconforming requires address + existing_use_or_structure;
    # establishment_date is OPTIONAL. Supplying just the two required ones
    # completes intake, and the optional field is surfaced (not demanded).
    result = await detect_intake(
        _gateway(),
        question_for(QUESTION_LEGAL_NONCONFORMING),
        conversation=(
            "MOCK_INPUT[address]=5 King St|"
            "MOCK_INPUT[existing_use_or_structure]=a corner store|"
        ),
    )
    assert result.complete is True
    assert "establishment_date" in result.missing_optional


@pytest.mark.asyncio
async def test_no_conversation_skips_llm_and_asks_for_everything() -> None:
    calls: list = []

    class _Spy(MockGateway):
        async def complete(self, request):  # noqa: ANN001
            calls.append(request)
            raise AssertionError("intake must not call the LLM with no text")

    result = await detect_intake(
        _Spy(callable_=build_dispatcher()),
        question_for(QUESTION_PERMITTED_USE),
        conversation="   ",
    )
    assert calls == []  # nothing to extract → no LLM call
    assert result.complete is False
    assert set(result.missing_required) == {"address", "proposed_use"}


@pytest.mark.asyncio
async def test_extractor_failure_degrades_to_asking() -> None:
    class _Broken(MockGateway):
        async def complete(self, request):  # noqa: ANN001
            return CompletionResponse(
                model="m",
                content=[TextBlock(text="not json at all")],
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )

    # A parse failure must not crash — fall back to asking for the missing
    # required inputs from the schema.
    result = await detect_intake(
        _Broken(callable_=build_dispatcher()),
        question_for(QUESTION_PERMITTED_USE),
        conversation="Something the extractor will choke on.",
    )
    assert result.complete is False
    assert set(result.missing_required) == {"address", "proposed_use"}
    assert "Property address" in result.prompt


@pytest.mark.asyncio
async def test_extracted_unknown_fields_are_ignored() -> None:
    # The LLM cannot smuggle in fields outside the question's schema.
    result = await detect_intake(
        _gateway(),
        question_for(QUESTION_DUE_DILIGENCE),
        conversation=(
            "MOCK_INPUT[address]=7 Bishop St|"
            "MOCK_INPUT[secret_field]=ignore me|"
        ),
    )
    assert result.complete is True  # due_diligence needs only address
    assert "secret_field" not in result.inputs
    assert result.inputs == {"address": "7 Bishop St"}


def test_consultant_prompt_lists_required_and_optional() -> None:
    question = question_for(QUESTION_LEGAL_NONCONFORMING)
    prompt = build_consultant_prompt(
        question,
        missing_required=["address", "existing_use_or_structure"],
        missing_optional=["establishment_date"],
        lead_in="Happy to help with that.",
    )
    assert prompt.startswith("Happy to help with that.")
    assert "Property address" in prompt
    assert "Existing use or structure" in prompt
    assert "optional" in prompt.lower()
    assert "When it was established" in prompt


def test_intake_result_defaults() -> None:
    result = IntakeResult(complete=True, inputs={"address": "x"})
    assert result.missing_required == []
    assert result.missing_optional == []
    assert result.prompt == ""
