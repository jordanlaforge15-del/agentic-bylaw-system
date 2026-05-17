"""Pattern-based response dispatcher for the e2e test FastAPI server.

The `MockGateway` accepts a `callable_` that turns each
`CompletionRequest` into a `CompletionResponse`. This module supplies
that callable for the test stack so the same advisor backend can be
driven through end-to-end UI tests without an Anthropic API key.

Dispatch rules:

* **Classifier requests** (``tools=[]``, single user message that is
  JSON with an ``anchor_label`` field) — return a JSON text block
  that the classifier parser accepts. Default recommendation is
  ``standard`` with 0.85 confidence; the dispatcher inspects the
  ``anchor_label`` / ``message`` for hint keywords (``quick``,
  ``complex``) so tests can drive the classifier deterministically.

* **Chat requests, no prior tool_use** (turn just started) — return a
  preamble + a ``search_bylaw_evidence`` ``tool_use`` block. The chat
  session executes the tool, appends a ``tool_result``, and calls the
  gateway again.

* **Chat requests, prior tool_use seen** (follow-up turn) — return a
  final text answer mentioning the tool result. This is the
  "qualifying" turn that commits the reserved credit
  (see ``_turn_was_qualifying`` in ``advisor.api.app``).

Scenario keywords in the user message override the default rules:

* ``"MOCK_BUDGET_NEAR_END"`` — final text is large enough that the
  post-stream settlement emits ``case_budget_warning`` on the SSE
  stream (provided the chat session was opened on a small budget).

* ``"MOCK_REQUEST_UPGRADE"`` — the first tool_use response calls
  ``request_tier_upgrade`` instead of ``search_bylaw_evidence``,
  giving the UI a ``case_upgrade_offer`` SSE event to render.

* ``"MOCK_EMPTY_TURN"`` — the assistant returns an empty text block
  with no tool_use, exercising the "non-qualifying turn" refund path.

All responses are deterministic, so identical sessions produce
identical SSE traces — a hard requirement for screenshot-stable UI
tests.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from advisor.llm.mock import text_response, tool_use_response


_CLASSIFIER_PERSONA_SIGNAL = "anchor_label"
_DEFAULT_CITATION = (
    "RC-LUB §15.4(a) sets a minimum front yard setback of 3.0 m."
)


def build_dispatcher() -> Callable[[CompletionRequest], CompletionResponse]:
    """Return the dispatcher callable wired into ``MockGateway(callable_=...)``."""
    return _dispatch


def _dispatch(request: CompletionRequest) -> CompletionResponse:
    if not request.tools:
        # Either the pre-flight classifier or an unrelated tools-less
        # call. The classifier is the only such path we ship today.
        return _classifier_response(request)

    user_text = _latest_user_text(request)
    has_prior_tool_use = _has_assistant_tool_use(request)

    if "MOCK_EMPTY_TURN" in user_text:
        return text_response("")

    if has_prior_tool_use:
        return _final_answer_response(user_text)

    if "MOCK_REQUEST_UPGRADE" in user_text:
        return tool_use_response(
            tool_id="t-upgrade",
            tool_name="request_tier_upgrade",
            tool_input={
                "recommended_tier": "complex",
                "reason": (
                    "Question requires cross-bylaw reasoning beyond the "
                    "current tier's depth."
                ),
            },
            preamble="One moment — flagging a tier upgrade.",
        )

    return tool_use_response(
        tool_id="t-search-1",
        tool_name="search_bylaw_evidence",
        tool_input={
            "query": user_text[:120] or "front yard setback",
            "top_k": 4,
        },
        preamble="Searching the bylaw for relevant passages.",
        usage=TokenUsage(input_tokens=80, output_tokens=24),
    )


def _classifier_response(request: CompletionRequest) -> CompletionResponse:
    payload_text = _latest_user_text(request)
    tier = "standard"
    confidence = 0.85
    reasons = ["Single-anchor question of typical depth."]

    blob = payload_text.lower()
    if _CLASSIFIER_PERSONA_SIGNAL in blob:
        # Parse the structured user payload so scenario keywords on
        # anchor_label or message both drive the recommendation.
        try:
            data = json.loads(payload_text)
            blob = " ".join(
                str(data.get(k, "")) for k in ("anchor_label", "message")
            ).lower()
        except (ValueError, TypeError):
            pass

    if "mock_quick" in blob or "simple" in blob:
        tier, confidence = "quick", 0.92
        reasons = ["Single-parcel zoning lookup."]
    elif "mock_complex" in blob or "rezoning" in blob:
        tier, confidence = "complex", 0.9
        reasons = [
            "Multi-bylaw cross reference detected.",
            "Likely needs heritage + density analysis.",
        ]

    body = json.dumps(
        {"tier": tier, "confidence": confidence, "reasons": reasons}
    )
    return CompletionResponse(
        model=request.model,
        content=[TextBlock(text=body)],
        stop_reason="end_turn",
        usage=TokenUsage(input_tokens=60, output_tokens=40),
    )


def _final_answer_response(user_text: str) -> CompletionResponse:
    citation_line = (
        f"\n\nSource: {_DEFAULT_CITATION}"
        if "no_citation" not in user_text.lower()
        else ""
    )
    body = (
        f"Based on the bylaw evidence I just searched, here is the answer "
        f"to your question.{citation_line}"
    )
    return text_response(
        body,
        usage=TokenUsage(input_tokens=140, output_tokens=90),
        stop_reason="end_turn",
    )


def _latest_user_text(request: CompletionRequest) -> str:
    for message in reversed(request.messages):
        if message.role.value != "user":
            continue
        content = message.content
        if isinstance(content, str):
            return content
        for block in content:
            if isinstance(block, TextBlock):
                return block.text
    return ""


def _has_assistant_tool_use(request: CompletionRequest) -> bool:
    for message in request.messages:
        if message.role.value != "assistant":
            continue
        content = message.content
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, ToolUseBlock):
                return True
    return False
