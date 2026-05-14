"""Pre-flight Layer-2 tier classifier — happy path + parse fallbacks."""
from __future__ import annotations

import pytest

from advisor.chat.classifier import (
    ClassifierResult,
    classify_query,
)
from advisor.llm import (
    CompletionResponse,
    TextBlock,
    TokenUsage,
)
from advisor.llm.mock import MockGateway


def _gateway_returning(text: str) -> MockGateway:
    """MockGateway that returns a single text-only assistant message."""
    return MockGateway(
        scripted=[
            CompletionResponse(
                model="claude-haiku-4-5",
                content=[TextBlock(text=text)],
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=10, output_tokens=20),
            )
        ]
    )


@pytest.mark.asyncio
async def test_pure_json_response_is_parsed() -> None:
    gateway = _gateway_returning(
        '{"tier": "complex", "confidence": 0.92, '
        '"reasons": ["multi-property inquiry"]}'
    )
    result = await classify_query(
        gateway,
        anchor_label="123 Main St",
        anchor_kind="address",
        message="What's the rezoning process for two adjacent lots?",
        classifier_model="claude-haiku-4-5",
    )
    assert result.tier == "complex"
    assert result.confidence == 0.92
    assert result.reasons == ["multi-property inquiry"]


@pytest.mark.asyncio
async def test_json_with_prose_preamble_is_extracted() -> None:
    gateway = _gateway_returning(
        'Sure! Here is my recommendation:\n\n'
        '{"tier": "standard", "confidence": 0.7, "reasons": ["overlay zone"]}\n'
        'Hope that helps.'
    )
    result = await classify_query(
        gateway,
        anchor_label="addr",
        anchor_kind="address",
        message="Question",
        classifier_model="claude-haiku-4-5",
    )
    assert result.tier == "standard"
    assert result.confidence == 0.7


@pytest.mark.asyncio
async def test_invalid_tier_falls_back() -> None:
    gateway = _gateway_returning(
        '{"tier": "deluxe", "confidence": 0.5, "reasons": []}'
    )
    result = await classify_query(
        gateway,
        anchor_label="addr",
        anchor_kind="address",
        message="Question",
        classifier_model="claude-haiku-4-5",
    )
    assert result.tier == "standard"  # the safe default
    assert result.confidence == 0.0
    assert "classifier_fallback" in result.reasons[0]


@pytest.mark.asyncio
async def test_malformed_response_falls_back() -> None:
    gateway = _gateway_returning("not json at all")
    result = await classify_query(
        gateway,
        anchor_label="addr",
        anchor_kind="address",
        message="Question",
        classifier_model="claude-haiku-4-5",
    )
    assert result.tier == "standard"
    assert result.confidence == 0.0
    assert "malformed_json" in result.reasons[0]


@pytest.mark.asyncio
async def test_empty_message_returns_fallback_without_calling_gateway() -> None:
    # No scripted responses needed — the classifier short-circuits on
    # empty input before ever calling the gateway.
    gateway = MockGateway(scripted=[])
    result = await classify_query(
        gateway,
        anchor_label="addr",
        anchor_kind="address",
        message="   ",
        classifier_model="claude-haiku-4-5",
    )
    assert result.tier == "standard"
    assert "empty_message" in result.reasons[0]


@pytest.mark.asyncio
async def test_gateway_exception_falls_back() -> None:
    class BoomGateway:
        name = "boom"

        async def complete(self, request):
            raise RuntimeError("classifier api outage")

        async def stream(self, request):  # pragma: no cover — unused
            raise NotImplementedError

    result = await classify_query(
        BoomGateway(),  # type: ignore[arg-type]
        anchor_label="addr",
        anchor_kind="address",
        message="Question",
        classifier_model="claude-haiku-4-5",
    )
    assert result.tier == "standard"
    assert "gateway_error" in result.reasons[0]


def test_classifier_result_fallback_has_zero_confidence() -> None:
    fb = ClassifierResult.fallback("test")
    assert fb.tier == "standard"
    assert fb.confidence == 0.0
    assert fb.reasons == ["classifier_fallback: test"]


def test_classifier_result_caps_reasons_to_five() -> None:
    result = ClassifierResult(
        tier="quick",
        confidence=0.5,
        reasons=[f"reason {i}" for i in range(10)],
    )
    assert len(result.reasons) == 5
