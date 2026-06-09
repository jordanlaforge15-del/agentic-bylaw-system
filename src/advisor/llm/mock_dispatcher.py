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

* ``"MOCK_FEASIBILITY"`` — the final answer is a feasibility-grade reply
  that stacks several built-form dimensions (height, FAR, coverage,
  setback, parking) with no hedging language. The ABS-263 hedge injector
  in ``advisor.chat.session`` appends a verify-with-a-planner qualifier
  before the SSE stream is built, so tests can assert the hedge appears
  without depending on live-model phrasing.

* ``"MOCK_WITH_LOCATION"`` — the ``search_bylaw_evidence`` call
  includes a ``location`` slot (``civic_number="1234"``,
  ``street="Elm St"``) so the parcel pane renders with zone data and
  the "CITED THIS THREAD" panel is populated from the tool result.
  Use this keyword to test UI that depends on the right-pane being
  non-empty.

* ``"MOCK_DEEP_SEARCH"`` — instead of answering after one search, the
  dispatcher fans the turn out into ``_DEEP_SEARCH_ROUNDS`` (5) serial
  ``search_bylaw_evidence`` rounds before the final answer. This is the
  only path that drives more in-turn tool rounds than the WI-4 (ABS-290)
  in-loop compaction window, so the older rounds' tool_results get
  rewritten to one-line summaries inside the live loop. The e2e guard
  then confirms the deep loop still completes and answers — i.e. that
  rewriting bytes mid-loop didn't break the tool_use ↔ tool_result
  pairing the Messages API requires.

* ``"MOCK_FAN_OUT"`` — the first-turn response contains TWO
  ``search_bylaw_evidence`` ``tool_use`` blocks in a single assistant
  message (parallel fan-out). The tool loop executes both in parallel
  and returns both results as one ``tool_result`` turn, then the
  dispatcher answers on the follow-up call. Used by ABS-289 / WI-3 to
  guard the parallel-execution path that the persona's fan-out
  instruction now relies on.

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
# Serial search rounds a ``MOCK_DEEP_SEARCH`` turn fans out into before
# answering. Chosen above the WI-4 in-loop compaction window (default 3)
# so the oldest rounds are summarised inside the live loop, and below the
# tool-loop ``max_iterations`` cap (10) so the turn answers organically.
_DEEP_SEARCH_ROUNDS = 5


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

    if "MOCK_DEEP_SEARCH" in user_text:
        # Keep issuing search rounds until we've done _DEEP_SEARCH_ROUNDS,
        # then answer. Drives a single turn past the WI-4 compaction
        # window so the older tool_results are summarised in-loop.
        rounds_done = _count_search_rounds(request)
        if rounds_done < _DEEP_SEARCH_ROUNDS:
            return tool_use_response(
                tool_id=f"t-deep-{rounds_done + 1}",
                tool_name="search_bylaw_evidence",
                tool_input={"query": f"{user_text[:80]} (round {rounds_done + 1})"},
                preamble=f"Searching the bylaw (round {rounds_done + 1}).",
                usage=TokenUsage(input_tokens=80, output_tokens=24),
            )
        return _final_answer_response(user_text)

    if "MOCK_FAN_OUT" in user_text:
        if has_prior_tool_use:
            return _final_answer_response(user_text)
        # Emit two tool_use blocks in a single response (ABS-289 / WI-3).
        # The tool loop executes both in parallel, returns both results as
        # one tool_result turn, then the gateway is called once more and
        # answers. Guards the parallel-execution path the persona now relies on.
        return CompletionResponse(
            model="",
            content=[
                TextBlock(text="I'll look up height and FAR in parallel."),
                ToolUseBlock(
                    id="t-fanout-1",
                    name="search_bylaw_evidence",
                    input={"query": "maximum building height"},
                ),
                ToolUseBlock(
                    id="t-fanout-2",
                    name="search_bylaw_evidence",
                    input={"query": "maximum floor area ratio"},
                ),
            ],
            stop_reason="tool_use",
            usage=TokenUsage(input_tokens=80, output_tokens=40),
        )

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

    tool_input: dict = {
        "query": user_text[:120] or "front yard setback",
        "top_k": 4,
    }
    if "MOCK_WITH_LOCATION" in user_text:
        tool_input["location"] = {"civic_number": "1234", "street": "Elm St"}

    return tool_use_response(
        tool_id="t-search-1",
        tool_name="search_bylaw_evidence",
        tool_input=tool_input,
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
    if "MOCK_FEASIBILITY" in user_text:
        # A feasibility-grade answer that stacks several built-form
        # dimensions (height, FAR, coverage, setback, parking) and
        # deliberately omits any hedging language. The real ChatSession
        # pipeline runs the ABS-263 hedge injector over this turn, so the
        # SSE stream the e2e decodes should carry the appended
        # verify-with-a-planner qualifier even though the mock never wrote
        # one. This exercises product code, not a hard-coded hedge string.
        body = (
            "Feasibility envelope for the site:\n\n"
            "- Max height: 25.0 m\n"
            "- Max FAR: 2.0\n"
            "- Lot coverage: 65%\n"
            "- Front setback: 3.0 m\n"
            "- Parking: 1 space per dwelling unit\n\n"
            "Source: RC-LUB Table 1A, §15.4, Table 3."
        )
        return text_response(
            body,
            usage=TokenUsage(input_tokens=160, output_tokens=120),
            stop_reason="end_turn",
        )

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


def _count_search_rounds(request: CompletionRequest) -> int:
    """Count assistant ``search_bylaw_evidence`` tool_use blocks already
    in the conversation — i.e. how many search rounds the loop has run
    so far this turn. Used by the ``MOCK_DEEP_SEARCH`` fan-out to decide
    whether to issue another round or finally answer."""
    count = 0
    for message in request.messages:
        if message.role.value != "assistant":
            continue
        content = message.content
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, ToolUseBlock)
                and block.name == "search_bylaw_evidence"
            ):
                count += 1
    return count
