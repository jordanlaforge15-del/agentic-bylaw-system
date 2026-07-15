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

* ``"MOCK_MONOLOGUE_REPORT"`` (ABS-359) — grounds once, then answers with
  the exact chain-of-thought shape a report SKU produces: a first-person
  planning monologue fenced off by a ``---`` rule, decorative ``---``
  between sections, and a conversational apology/sign-off on the final
  section. ``build_report`` must scrub all of it. Drive it via a report
  question's free-form input so the sentinel rides the rendered prompt.

* ``"MOCK_FEASIBILITY"`` — the final answer is a feasibility-grade reply
  that stacks several built-form dimensions (height, FAR, coverage,
  setback, parking) with no hedging language. The ABS-263 hedge injector
  in ``advisor.chat.session`` appends a verify-with-a-planner qualifier
  before the SSE stream is built, so tests can assert the hedge appears
  without depending on live-model phrasing.

* ``"MOCK_ADJACENT_ZONING"`` (ABS-375) — round one calls the new
  ``get_adjacent_zoning`` tool (instead of ``search_bylaw_evidence``);
  round two answers with a DEFINITIVE side/rear setback verdict that
  cites the resolved abutting zone, exercising the adjacent-parcel
  zoning lookup plumbing end-to-end (tool → grounding → captured answer).
  ``get_adjacent_zoning`` is in ``GROUNDING_TOOLS``, so a turn that only
  calls it still commits the reserved credit. Drive it via a report
  question's free-form input so the sentinel rides the rendered prompt.

* ``"MOCK_WITH_LOCATION"`` — the ``search_bylaw_evidence`` call
  includes a ``location`` slot (``civic_number="1234"``,
  ``street="Elm St"``) so the parcel pane renders with zone data and
  the "CITED THIS THREAD" panel is populated from the tool result.
  Use this keyword to test UI that depends on the right-pane being
  non-empty.

* ``"MOCK_CUMULATIVE_TRIP"`` (ABS-305) — every round returns a
  ``search_bylaw_evidence`` ``tool_use`` whose preamble is a large
  filler block, so each request's billed-equivalent estimate climbs
  round over round while staying UNDER the per-request cost ceiling.
  After a handful of rounds the *cumulative* per-turn breaker in
  ``run_tool_loop`` crosses its ceiling and forces synthesis with
  ``terminated_reason="cumulative_cost_trip"``. Use on a Complex-tier
  case (``max_iterations=55``) so the iteration cap can't fire first.

* ``"MOCK_FAN_OUT"`` — the first-turn response contains TWO
  ``search_bylaw_evidence`` ``tool_use`` blocks in a single assistant
  message (parallel fan-out). The tool loop executes both in parallel
  and returns both results as one ``tool_result`` turn, then the
  dispatcher answers on the follow-up call. Used by ABS-289 / WI-3 to
  guard the parallel-execution path that the persona's fan-out
  instruction now relies on.

* ``"MOCK_REFINEMENT_HOLD"`` (ABS-317) — on the *first* turn, behaves
  normally (tool call → cited answer). On a *follow-up* turn that
  already has prior tool_use, returns a response that demonstrates the
  EVIDENCE INTEGRITY guardrail: the model restates its grounded
  determination and explicitly declines to change it. The response
  includes the original citation so citation-preservation tests can
  assert it survived the refinement turn.

* ``"MOCK_ANTI_NEW_REPORT"`` (ABS-317) — on the *first* turn, behaves
  normally (tool call → cited answer). On a *follow-up* turn that
  already has prior tool_use, returns the ANTI-NEW-REPORT guardrail
  response: model declines to answer the new question and directs the
  user to purchase a new question from the question menu.

* ``"MOCK_INPUT[<field>]=<value>|"`` (ABS-315) — recognised inside the
  tools-less ``__INTAKE_DETECT__`` consultant-intake prompt. Each
  sentinel is parsed into an extracted input value, so a spec can drive
  the intake detector deterministically: omit a required field's
  sentinel to force a "needs more input" consultant prompt, then add it
  on the next call to complete the intake.

All responses are deterministic, so identical sessions produce
identical SSE traces — a hard requirement for screenshot-stable UI
tests.
"""
from __future__ import annotations

import json
import re
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
# Rounds used by ``MOCK_COMPLEX_DEEP``. Above the old hardcoded cap (10)
# and below the Standard-tier cap (20) — exercises the Complex-tier path
# where per-tier max_iterations (55) allows the turn to finish organically
# rather than forcing an early synthesis at iteration 10.
_COMPLEX_DEEP_ROUNDS = 12

# ABS-305 cumulative-trip scenario. Each round's preamble carries this
# many filler characters (~80k chars ≈ 20k billed-equivalent tokens at
# the 4-chars/token heuristic). The conversation grows by roughly this
# much per round, so the running cumulative estimate crosses the default
# 165k cumulative budget after ~5 rounds — well before the Complex-tier
# iteration cap (55) and below the per-request cap (150k), so the
# CUMULATIVE breaker is the one that fires. Sized large enough that the
# trip is deterministic regardless of system-prompt length.
_CUMULATIVE_TRIP_PREAMBLE_CHARS = 80_000
# Hard stop so a regression that disables the breaker surfaces as a
# normal answer (visible test failure) rather than an unbounded loop.
_CUMULATIVE_TRIP_MAX_ROUNDS = 40

# ABS-372: retry-a-cost_ceiling-trip scenario (``MOCK_COST_CEILING_ONCE``).
# The buy-an-answer engine retries a nondeterministic cost_ceiling trip on a
# fresh turn before voiding (advisor.billing.answers). This mock reproduces
# exactly one such trip: the FIRST attempt piles on heavy grounded rounds
# until the ABS-305 cumulative breaker forces synthesis (a cost_ceiling
# void), and every RETRY grounds cheaply and answers (a captured deliverable).
# It therefore drives the whole retry-then-capture path end-to-end.
#
# Attempts are counted per test via a nonce carried in the sentinel
# (``MOCK_COST_CEILING_ONCE[<nonce>]``), because the e2e server shares one
# dispatcher across every request for the process' lifetime. run_answer
# builds a fresh ChatSession per attempt, so attempt N's ONLY round-0 request
# (no prior assistant tool_use) marks the start of that attempt — that is
# where we increment. Later rounds within the attempt, and the tools-less
# forced-synthesis call, never touch the counter.
_COST_CEILING_ONCE_ATTEMPTS: dict[str, int] = {}
_COST_CEILING_ONCE_NONCE_RE = re.compile(r"MOCK_COST_CEILING_ONCE\[([^\]]+)\]")


def _cost_ceiling_once_nonce(user_text: str) -> str:
    """The per-test nonce embedded in a ``MOCK_COST_CEILING_ONCE[<nonce>]``
    sentinel, or a shared default when the bare sentinel is used."""
    match = _COST_CEILING_ONCE_NONCE_RE.search(user_text)
    return match.group(1) if match else "_default"


def build_dispatcher() -> Callable[[CompletionRequest], CompletionResponse]:
    """Return the dispatcher callable wired into ``MockGateway(callable_=...)``."""
    return _dispatch


def _dispatch(request: CompletionRequest) -> CompletionResponse:
    if not request.tools:
        # ABS-312: persona-gated new-question classifier (tools-less).
        # The buy-an-answer refinement gate sends a one-shot
        # classification prompt carrying this marker. Resolve a
        # deterministic verdict: a new question iff the embedded
        # follow-up text carries the MOCK_NEW_QUESTION sentinel.
        classify_text = _latest_user_text(request)
        if "__FOLLOWUP_CLASSIFY__" in classify_text:
            verdict = "MOCK_NEW_QUESTION" in classify_text
            return text_response(json.dumps({"new_question": verdict}))
        if "__QUOTE_DIFFICULTY__" in classify_text:
            # ABS-316: off-menu price-quote classifier (tools-less). Resolve
            # a deterministic difficulty tier from a MOCK_QUOTE_<TIER>
            # sentinel embedded in the question; default to mid-band
            # 'moderate' when no sentinel is present.
            return text_response(json.dumps(_quote_verdict(classify_text)))
        if "__INTAKE_DETECT__" in classify_text:
            # ABS-315: consultant-style intake extractor (tools-less).
            # Resolve a deterministic extraction from MOCK_INPUT[<field>]=
            # <value>| sentinels embedded in the conversation; the module
            # decides completeness against the schema, so the mock only
            # needs to return what the "customer said".
            return text_response(json.dumps(_intake_verdict(classify_text)))
        # Two tools-less shapes reach the gateway:
        #   * the pre-flight classifier — a fresh request whose only
        #     message is the JSON anchor payload (no assistant turn yet);
        #   * a forced-synthesis call — ``run_tool_loop`` strips tools for
        #     the one-more answer turn after the iteration cap or a cost
        #     breaker trips, so the conversation already holds an
        #     assistant ``tool_use`` turn.
        # Distinguish by whether a prior assistant tool_use exists, so the
        # synthesis turn returns a real answer instead of a tier blob.
        if _has_assistant_tool_use(request):
            return _final_answer_response(_latest_user_text(request))
        return _classifier_response(request)

    user_text = _latest_user_text(request)
    has_prior_tool_use = _has_assistant_tool_use(request)

    if "MOCK_EMPTY_TURN" in user_text:
        return text_response("")

    if "MOCK_NUL_BYTES" in user_text:
        # ABS-332: drive a NUL byte (0x00) — the one character Postgres
        # text/jsonb cannot store — through the persisted answer, mirroring
        # real bylaw evidence extracted from municipal PDFs that carries
        # stray NULs. Round one grounds with a NUL inside the tool_use input
        # (→ transcript_json JSONB); round two answers with a NUL inside the
        # final text (→ answer_text TEXT). Both writes happen in db.flush()
        # OUTSIDE run_answer's engine-error guard, so without the
        # scrub_text/json_safe sanitizers from advisor.db.jsonsafe each
        # raises a DataError → HTTP 500 on a grounded, paid-for answer.
        nul = chr(0)
        if has_prior_tool_use:
            return text_response(
                f"The permitted use is confirmed.{nul} See the cited section.",
                usage=TokenUsage(input_tokens=40, output_tokens=20),
                stop_reason="end_turn",
            )
        return tool_use_response(
            tool_id="t-nul-1",
            tool_name="search_bylaw_evidence",
            tool_input={"query": f"permitted use{nul} setback"},
            preamble="Looking up the bylaw evidence.",
            usage=TokenUsage(input_tokens=40, output_tokens=12),
        )

    if "MOCK_MONOLOGUE_REPORT" in user_text:
        # ABS-359: emit the exact chain-of-thought shape the real engine
        # produces for a report SKU — a first-person planning monologue
        # fenced off from the report by a `---` rule, decorative `---`
        # dividers between sections, and a conversational apology/sign-off
        # tacked onto the final section. Round one grounds (a search
        # tool_use) so the answer path runs the full loop; round two writes
        # this monologue-laden markdown as the captured answer. build_report
        # (advisor.billing.report.parse_blocks) must scrub ALL of it — the
        # bug was chain-of-thought surfacing in the paid, exportable
        # deliverable. Exercised end-to-end by abs359-report-parser.spec.ts.
        if has_prior_tool_use:
            body = (
                "I have gathered the key provisions. Now I have sufficient "
                "information to prepare the variance justification package. "
                "Let me compile the findings:\n\n"
                "---\n\n"
                "# Variance Justification Package\n\n"
                "The requested rear-yard variance is supportable on all three "
                "statutory tests.\n\n"
                "## Statutory Test 1 — Minor in nature\n\n"
                "The 0.4 m encroachment is minor relative to the required "
                "setback.\n\n"
                "---\n\n"
                "## Conclusion\n\n"
                "The variance is supportable, subject to conditions.\n\n"
                "I apologize that I cannot provide a definitive citation-"
                "grounded answer for this specific query. Would you like me to "
                "research a different property or provision?\n\n"
                f"Source: {_DEFAULT_CITATION}"
            )
            return text_response(
                body,
                usage=TokenUsage(input_tokens=140, output_tokens=90),
                stop_reason="end_turn",
            )
        return tool_use_response(
            tool_id="t-monologue-1",
            tool_name="search_bylaw_evidence",
            tool_input={"query": "variance statutory criteria setback"},
            preamble="Looking up the variance provisions.",
            usage=TokenUsage(input_tokens=40, output_tokens=12),
        )

    if "MOCK_ADJACENT_ZONING" in user_text:
        # ABS-375: exercise the get_adjacent_zoning grounding tool. Round one
        # calls it; round two answers with a DEFINITIVE setback verdict that
        # names the resolved abutting zone — the shape a report should now
        # produce instead of "uncertain — depends on adjacent zoning".
        if has_prior_tool_use:
            body = (
                "The side-yard setback is definitively resolved. The abutting "
                "parcel to the east is zoned DH-1 (Downtown), so under the "
                "shared-downtown-abutment rule the required side yard is "
                "0.0 m — the proposed 0.0 m side setback PASSES, and no "
                "variance is required.\n\n"
                f"Source: {_DEFAULT_CITATION}"
            )
            return text_response(
                body,
                usage=TokenUsage(input_tokens=140, output_tokens=90),
                stop_reason="end_turn",
            )
        return tool_use_response(
            tool_id="t-adjzone-1",
            tool_name="get_adjacent_zoning",
            tool_input={"address": "1250 Robie Street"},
            preamble="Resolving the zoning of the abutting parcels.",
            usage=TokenUsage(input_tokens=40, output_tokens=12),
        )

    if "MOCK_COMPLEX_DEEP" in user_text:
        # Run _COMPLEX_DEEP_ROUNDS (12) serial search rounds before
        # answering. 12 > old hardcoded cap (10), so on a Standard- or
        # Quick-tier session this turn would have been forced into early
        # synthesis at iteration 10. On a Complex-tier session (cap=55)
        # it runs to completion. Used by ABS-287 to guard that the
        # per-tier max_iterations wiring is end-to-end correct.
        rounds_done = _count_search_rounds(request)
        if rounds_done < _COMPLEX_DEEP_ROUNDS:
            return tool_use_response(
                tool_id=f"t-cdeep-{rounds_done + 1}",
                tool_name="search_bylaw_evidence",
                tool_input={
                    "query": f"{user_text[:80]} (complex round {rounds_done + 1})"
                },
                preamble=f"Deep complex research (round {rounds_done + 1}).",
                usage=TokenUsage(input_tokens=80, output_tokens=24),
            )
        return _final_answer_response(user_text)

    if "MOCK_COST_CEILING_ONCE" in user_text:
        # ABS-372: exactly one cost_ceiling trip, then success on retry —
        # exercises run_answer's retry-before-void path end-to-end.
        nonce = _cost_ceiling_once_nonce(user_text)
        if not has_prior_tool_use:
            # Round 0 of a fresh turn = the start of a new attempt (a new
            # ChatSession per attempt). Count it here and nowhere else.
            _COST_CEILING_ONCE_ATTEMPTS[nonce] = (
                _COST_CEILING_ONCE_ATTEMPTS.get(nonce, 0) + 1
            )
        attempt = _COST_CEILING_ONCE_ATTEMPTS.get(nonce, 1)
        if attempt <= 1:
            # First attempt: heap on grounded rounds with a big filler
            # preamble until the ABS-305 cumulative breaker forces synthesis
            # (terminated_reason == cumulative_cost_trip → cost_ceiling void).
            rounds_done = _count_search_rounds(request)
            if rounds_done < _CUMULATIVE_TRIP_MAX_ROUNDS:
                return tool_use_response(
                    tool_id=f"t-ceilonce-{rounds_done + 1}",
                    tool_name="search_bylaw_evidence",
                    tool_input={"query": f"ceiling round {rounds_done + 1}"},
                    preamble="C" * _CUMULATIVE_TRIP_PREAMBLE_CHARS,
                    usage=TokenUsage(input_tokens=80, output_tokens=24),
                )
            return _final_answer_response(user_text)
        # Retry (attempt ≥ 2): ground once cheaply, then answer → captures.
        if has_prior_tool_use:
            return _final_answer_response(user_text)
        return tool_use_response(
            tool_id="t-ceilonce-retry",
            tool_name="search_bylaw_evidence",
            tool_input={"query": "ceiling retry grounding"},
            preamble="Searching the bylaw for relevant passages.",
            usage=TokenUsage(input_tokens=80, output_tokens=24),
        )

    if "MOCK_CUMULATIVE_TRIP" in user_text:
        # Keep issuing search rounds with a big filler preamble so the
        # turn's cumulative billed-equivalent estimate climbs until the
        # ABS-305 cumulative breaker forces synthesis. The hard cap is a
        # safety net: if the breaker is broken the loop answers here and
        # the e2e assertion (terminated_reason == cumulative_cost_trip)
        # fails loudly instead of running away.
        rounds_done = _count_search_rounds(request)
        if rounds_done < _CUMULATIVE_TRIP_MAX_ROUNDS:
            return tool_use_response(
                tool_id=f"t-cumtrip-{rounds_done + 1}",
                tool_name="search_bylaw_evidence",
                tool_input={"query": f"cumulative round {rounds_done + 1}"},
                preamble="C" * _CUMULATIVE_TRIP_PREAMBLE_CHARS,
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

    if "MOCK_UNGROUNDABLE" in user_text and not has_prior_tool_use:
        # ABS-312 failed-question scenario: answer with NO grounding tool
        # call (zero-evidence synthesis). The buy-an-answer classifier
        # treats this as ungroundable and VOIDS the card authorization.
        return text_response(
            "I'm not able to ground an answer to this question in the "
            "by-law evidence available. The specific provision that would "
            "govern it does not appear in the corpus I can search.",
            usage=TokenUsage(input_tokens=60, output_tokens=30),
        )

    if has_prior_tool_use:
        return _final_answer_response(user_text)

    # ABS-317 guardrail scenarios: on the first turn they behave normally
    # (search → cited answer). The mock keyword is preserved in the user
    # text so _final_answer_response can detect it on the follow-up call.
    if "MOCK_REFINEMENT_HOLD" in user_text or "MOCK_ANTI_NEW_REPORT" in user_text:
        return tool_use_response(
            tool_id="t-guardrail-search",
            tool_name="search_bylaw_evidence",
            tool_input={"query": user_text[:80]},
            preamble="Searching the bylaw for relevant passages.",
            usage=TokenUsage(input_tokens=80, output_tokens=24),
        )

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


def _quote_verdict(classify_text: str) -> dict[str, str]:
    """Resolve a deterministic off-menu difficulty verdict (ABS-316).

    The quote classifier prompt embeds the question, which in e2e carries
    a ``MOCK_QUOTE_<TIER>`` sentinel. Map it to the matching difficulty
    tier so the priced amount is deterministic; absent any sentinel,
    default to the mid-band ``moderate`` tier.
    """
    for tier in ("simple", "moderate", "involved", "complex", "exceptional"):
        if f"MOCK_QUOTE_{tier.upper()}" in classify_text:
            return {
                "difficulty": tier,
                "rationale": f"Mock-classified as {tier}.",
            }
    return {"difficulty": "moderate", "rationale": "Mock default mid-band."}


def _intake_verdict(classify_text: str) -> dict[str, object]:
    """Resolve a deterministic intake extraction (ABS-315).

    The intake prompt embeds the conversation, which in e2e carries
    ``MOCK_INPUT[<field>]=<value>|`` sentinels for whatever the customer
    "said". Parse them into the extracted-inputs map the real module
    expects; the module then merges with already-collected inputs and
    decides completeness from the schema. Absent any sentinel, nothing is
    extracted (the module asks for everything required).
    """
    extracted = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"MOCK_INPUT\[([a-z_]+)\]=([^|]*)\|", classify_text)
    }
    return {
        "inputs": extracted,
        "message": "Happy to help — could you share a couple more details?",
    }


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

    if "MOCK_DEV_STANDARDS_REPORT" in user_text:
        # ABS-376: a dev-standards report that leads with the mandatory
        # use-permission evaluation BEFORE the built-form standards table —
        # the section the ABS-375 prompt refocus silently dropped (a $149
        # report evaluated *how* the building complies while skipping
        # *whether* the use is allowed). The go/no-go threshold names
        # Section 43 / Schedule 9 for multi-unit dwelling use in the INS
        # zone, and flags that the Schedule 9 landmark dataset is absent
        # from the corpus (the ingestion gap). ``build_report`` turns the
        # "Use-permission determination" heading into a ``finding`` block
        # whose body preserves the Section 43 / Schedule 9 citation; the
        # built-form table follows.
        body = (
            "The proposal's use permission is the decisive threshold and is "
            "evaluated first, before built-form compliance.\n\n"
            "## Use-permission determination\n\n"
            "Multi-unit dwelling use in the INS (Institutional) zone is not "
            "permitted as-of-right. Under Section 43 it is conditionally "
            "permitted only on a Schedule 9 landmark site. Whether the "
            "subject parcel is a designated Schedule 9 landmark site cannot "
            "be resolved: the Schedule 9 landmark dataset is absent from the "
            "corpus (see docs/data-gaps/schedule-9-18-ingestion.md). Until "
            "that landmark status is confirmed with HRM, the entire proposal "
            "is contingent on this use-permission gate, regardless of "
            "built-form compliance.\n\n"
            "## Built-form standards\n\n"
            "| Standard | Required | Proposed | Result |\n"
            "| --- | --- | --- | --- |\n"
            "| Height | 20.0 m | 14.5 m | PASS |\n"
            "| Front setback | 3.0 m | 3.0 m | PASS |\n"
            "| Lot coverage | 60% | 48% | PASS |\n\n"
            f"Source: {_DEFAULT_CITATION}"
        )
        return text_response(
            body,
            usage=TokenUsage(input_tokens=200, output_tokens=180),
            stop_reason="end_turn",
        )

    if "MOCK_REFINEMENT_HOLD" in user_text:
        # ABS-317 EVIDENCE INTEGRITY: model holds its grounded determination
        # and restates the original citation rather than capitulating to
        # pressure. Citation preserved verbatim so the spec can assert it.
        body = (
            "I understand you are looking for a different answer, but the "
            "bylaw evidence does not support that conclusion. The "
            "determination remains unchanged: the setback requirement applies "
            "as stated.\n\n"
            f"Source: {_DEFAULT_CITATION}\n\n"
            "If you have new evidence or a changed proposal, please open a "
            "new question so it can be evaluated on its own merits."
        )
        return text_response(
            body,
            usage=TokenUsage(input_tokens=140, output_tokens=90),
            stop_reason="end_turn",
        )

    if "MOCK_ANTI_NEW_REPORT" in user_text:
        # ABS-317 ANTI-NEW-REPORT: model declines to answer a materially
        # different question and directs the user to purchase a new report.
        body = (
            "That follow-up is asking about a different property and use "
            "from the question you purchased. Answering it would constitute "
            "a separate bylaw report.\n\n"
            "To get an answer, please purchase a new question from the "
            "question menu."
        )
        return text_response(
            body,
            usage=TokenUsage(input_tokens=140, output_tokens=90),
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
    so far this turn. Used by the ``MOCK_COMPLEX_DEEP`` fan-out to decide
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
