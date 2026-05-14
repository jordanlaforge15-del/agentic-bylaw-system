"""Layer-2 pre-flight tier classifier.

A cheap (~$0.001 per call) Haiku call that runs BEFORE a credit is
reserved on case-open. It reads the user's anchor (property /
project ref) and first message, and returns a structured
recommendation:

    {"tier": "quick"|"standard"|"complex",
     "confidence": 0..1,
     "reasons": ["...", ...]}

The recommendation is rendered as a banner on the case-open form.
The user can dismiss / override — this is **not** a hard gate.

Why a separate pre-flight endpoint and not SSE metadata
-------------------------------------------------------
By the time SSE starts a credit is already reserved. Recommending a
different tier mid-stream would force an upgrade-or-abandon decision
while tokens burn. Pre-flight keeps the choice in the user's hands
*before* spending starts. The latency penalty is small (~1-2s for
Haiku) and the user experiences it as part of the case-open click.

Robustness
----------
The Haiku output is parsed permissively:
* Pure-JSON response → straightforward parse.
* JSON-with-prose response (model added a preamble despite the
  prompt) → we extract the first ``{...}`` block via regex.
* Any other failure → return ``ClassifierResult.fallback()`` which
  recommends ``standard`` (the middle tier) with low confidence and
  a reason explaining the parser failure.

This is conservative on purpose: the recommendation is a hint, and a
silent default to the middle tier is better than crashing the
case-open click.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from advisor.chat.persona import load_classifier_persona
from advisor.llm import (
    CompletionRequest,
    LLMGateway,
    LLMRole,
    Message,
    TextBlock,
)

logger = logging.getLogger(__name__)


_VALID_TIERS = ("quick", "standard", "complex")

# Bound on how many input tokens we send to the classifier. The whole
# point is "cheap" — if the user's first message is enormous we
# truncate rather than spend Standard-tier money on a triage call.
_MAX_MESSAGE_CHARS = 4_000


class ClassifierResult(BaseModel):
    """Structured tier recommendation returned to the case-open flow."""

    tier: Literal["quick", "standard", "complex"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)

    @field_validator("reasons")
    @classmethod
    def _strip_reasons(cls, v: list[str]) -> list[str]:
        # Keep reasons short and trim accidental whitespace; cap to 5
        # to bound the banner size on the frontend.
        return [r.strip() for r in v if r and r.strip()][:5]

    @classmethod
    def fallback(cls, reason: str) -> "ClassifierResult":
        """Safe default when classification fails.

        Returns a low-confidence ``standard`` recommendation. The
        frontend can read ``confidence`` and choose whether to surface
        the recommendation banner or just open the user-selected tier
        without commentary.
        """
        return cls(
            tier="standard",
            confidence=0.0,
            reasons=[f"classifier_fallback: {reason}"],
        )


@dataclass(frozen=True)
class ClassifierInput:
    """The minimal input the classifier needs to recommend a tier.

    ``anchor_label`` is what the user typed on the case-open form
    (verbatim, not normalised — the classifier sees the same text the
    user is about to confirm). ``anchor_kind`` tells the model whether
    they're looking at an address, a project ref, or a DA number.
    ``message`` is the user's first chat message, truncated to a
    sensible length to keep the call cheap.
    """

    anchor_label: str
    anchor_kind: str
    message: str


async def classify_query(
    gateway: LLMGateway,
    *,
    anchor_label: str,
    anchor_kind: str,
    message: str,
    classifier_model: str,
) -> ClassifierResult:
    """Run a single Haiku turn and parse its recommendation.

    Errors (LLM failure, malformed JSON, validation failure) are
    swallowed and returned as ``ClassifierResult.fallback`` so the
    case-open flow never crashes on a bad classifier response.

    Args:
        gateway: An ``LLMGateway`` instance (production
            ``AnthropicGateway`` or test ``MockGateway``).
        anchor_label: Verbatim user input.
        anchor_kind: One of ``address`` / ``project_ref`` /
            ``development_application``.
        message: User's first message; truncated to
            ``_MAX_MESSAGE_CHARS`` to bound classifier cost.
        classifier_model: Model identifier from
            ``AdvisorLLMSettings.classifier_model`` (default
            ``claude-haiku-4-5``).
    """
    if not message or not message.strip():
        return ClassifierResult.fallback("empty_message")

    truncated = message.strip()[:_MAX_MESSAGE_CHARS]
    user_payload = json.dumps(
        {
            "anchor_label": anchor_label,
            "anchor_kind": anchor_kind,
            "message": truncated,
        },
        ensure_ascii=False,
    )

    try:
        persona = load_classifier_persona()
    except FileNotFoundError as exc:
        logger.warning("classifier persona missing: %s", exc)
        return ClassifierResult.fallback("persona_missing")

    request = CompletionRequest(
        model=classifier_model,
        system=persona,
        messages=[
            Message(
                role=LLMRole.USER,
                content=[TextBlock(text=user_payload)],
            )
        ],
        tools=[],
        max_tokens=400,
    )

    try:
        response = await gateway.complete(request)
    except Exception:  # noqa: BLE001 — classifier failure shouldn't crash case-open
        logger.exception("classifier call failed")
        return ClassifierResult.fallback("gateway_error")

    text = _extract_text(response)
    if not text:
        return ClassifierResult.fallback("empty_response")

    parsed = _parse_json_block(text)
    if parsed is None:
        return ClassifierResult.fallback("malformed_json")

    try:
        return ClassifierResult.model_validate(parsed)
    except ValidationError as exc:
        logger.info("classifier returned invalid shape: %s", exc)
        return ClassifierResult.fallback("invalid_shape")


def _extract_text(response: object) -> str:
    """Pull the assistant's text content out of a ``CompletionResponse``.

    The classifier is a single-turn JSON-mode call; we want the first
    text block's content. Returns ``""`` when the response is empty or
    contains no text blocks (which itself is a fallback signal).
    """
    blocks = getattr(response, "content", None)
    if not blocks:
        return ""
    chunks: list[str] = []
    for block in blocks:
        if hasattr(block, "text"):
            chunks.append(block.text)
    return "".join(chunks).strip()


_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _parse_json_block(text: str) -> dict | None:
    """Parse the first JSON object found in ``text``.

    Tries ``json.loads(text)`` first; on failure, extracts the first
    ``{...}`` block via regex and tries again. Returns ``None`` when
    no parseable JSON is found.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    return value
