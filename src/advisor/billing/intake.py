"""Consultant-style LLM intake detection (ABS-315).

The launch product (``docs/decisions/2026-06-priced-question-catalog.md``)
sells answers to priced questions. Each catalog question declares a
**required-input schema** (``Question.required_inputs``) — the facts the
engine needs before it can answer (an address, a proposed use, project
dimensions, …). The buy-an-answer flow (ABS-312) validates those inputs at
checkout and HARD-FAILS a purchase whose required inputs are missing
(``MissingRequiredInputsError``) — the "unworkable inputs" failed-question
case, caught before any charge.

This module SOFTENS that edge into a consultant conversation. Given a
selected question and whatever the user has said so far (free-form text +
any already-collected structured inputs), an LLM:

1. **Extracts** values for the question's inputs from the conversation
   (e.g. "Can I put a fourplex at 12 Pine St?" → ``address="12 Pine St"``,
   ``proposed_use="a fourplex"``).
2. Leaves the module to **decide completeness deterministically** against
   the schema — we never proceed (or charge) on an LLM that merely
   *claims* the inputs are complete.
3. When a required input is still missing, the module composes a
   **consultant-style follow-up** asking for exactly what's missing,
   optionally led by the LLM's natural-language phrasing.

The caller loops: show the prompt, collect the user's reply, re-run
``detect_intake`` with the accumulated conversation, and once
``IntakeResult.complete`` is True, hand the merged ``inputs`` to the
buy-an-answer checkout — which records them against the purchase and runs
the answer. Answer quality is bounded by input quality, so this pairs with
the failed-question rule: inputs that are present-but-unworkable still
void at answer time (no charge).

Design mirrors ``advisor.billing.quote``: a single tools-less LLM call,
robust by construction (a transport/parse error degrades to "nothing
extracted, ask for everything missing" rather than crashing), and gated
behind an ``__INTAKE_DETECT__`` marker so the e2e ``MockGateway`` resolves
a deterministic verdict.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from advisor.billing.questions import Question
from advisor.llm import LLMGateway, Message, TextBlock

logger = logging.getLogger(__name__)


# Marker embedded in the (tools-less) intake-detection prompt so a mock
# gateway can resolve a deterministic extraction in tests, mirroring the
# ``QUOTE_CLASSIFY_MARKER`` / ``FOLLOWUP_CLASSIFY_MARKER`` pattern.
INTAKE_MARKER = "__INTAKE_DETECT__"


@dataclass(frozen=True)
class IntakeResult:
    """The outcome of one consultant-style intake detection pass.

    Attributes:
        complete: True iff every *required* input now has a value —
            the deterministic gate the caller uses to decide whether to
            proceed to checkout. Never trust the LLM for this; it is
            recomputed from the schema.
        inputs: The merged input values (already-collected ⊕ freshly
            extracted), cleaned and keyed by ``InputField.name``. Only
            keys that belong to the question's schema appear here. This is
            what the buy-an-answer checkout records against the purchase.
        missing_required: Names of required inputs still missing. Empty
            iff ``complete``.
        missing_optional: Names of optional inputs still missing — surfaced
            so the consultant prompt can invite (not demand) them.
        prompt: The consultant-style follow-up asking for what's missing.
            Empty string when ``complete`` (nothing left to ask).
    """

    complete: bool
    inputs: dict[str, str]
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    prompt: str = ""


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _intake_prompt(
    question: Question, conversation: str, known: dict[str, str]
) -> str:
    """Build the tools-less extraction prompt for the LLM.

    Describes the question's input schema and the conversation so far, and
    asks the model to (a) extract any input values it can find and (b)
    phrase a friendly one-line follow-up for whatever is missing. The
    module — not the model — decides completeness, so the prompt only asks
    the model to extract and phrase, never to gate.
    """
    field_lines = "\n".join(
        f"- {f.name} ({'required' if f.required else 'optional'}): "
        f"{f.label} — {f.description}"
        for f in question.required_inputs
    )
    known_lines = (
        "\n".join(f"- {k}: {v}" for k, v in known.items() if v)
        or "(none yet)"
    )
    return (
        f"{INTAKE_MARKER}\n"
        "You are a land-use consultant doing intake for a paid by-law "
        f"answer. The customer wants: {question.display_name}.\n\n"
        "That answer needs these inputs:\n"
        f"{field_lines}\n\n"
        "Already collected:\n"
        f"{known_lines}\n\n"
        "The customer has said so far:\n"
        f"{conversation!r}\n\n"
        "Extract any of the above inputs you can confidently read from what "
        "the customer said. Do NOT invent values that are not stated. Then "
        "write one short, friendly sentence asking for whatever required "
        "input is still missing (or a brief acknowledgement if nothing is "
        "missing). Respond with ONLY compact JSON: "
        '{"inputs": {"<name>": "<value>", ...}, "message": "<one sentence>"}.'
    )


def build_consultant_prompt(
    question: Question,
    missing_required: list[str],
    missing_optional: list[str],
    *,
    lead_in: str = "",
) -> str:
    """Compose the deterministic consultant ask for the missing inputs.

    The blocking decision is driven entirely by ``missing_required``, so
    this prompt is composed from the schema (labels + descriptions) rather
    than trusted from the LLM — the LLM's natural phrasing is only an
    optional ``lead_in``. Missing *optional* inputs are invited, never
    demanded.
    """
    by_name = {f.name: f for f in question.required_inputs}
    parts: list[str] = []
    lead = lead_in.strip()
    if lead:
        parts.append(lead)
    parts.append(
        f"To prepare your {question.display_name}, I need a little more "
        "detail:"
    )
    for name in missing_required:
        f = by_name.get(name)
        if f is not None:
            parts.append(f"- {f.label}: {f.description}")
    if missing_optional:
        opt: list[str] = []
        for name in missing_optional:
            f = by_name.get(name)
            if f is not None:
                opt.append(f"- {f.label}: {f.description}")
        if opt:
            parts.append("It also helps (optional) if you can share:")
            parts.extend(opt)
    parts.append("Share those and I'll get your answer.")
    return "\n".join(parts)


async def _extract_inputs(
    gateway: LLMGateway,
    question: Question,
    conversation: str,
    known: dict[str, str],
    *,
    model: str | None = None,
) -> tuple[dict[str, str], str]:
    """Run the single tools-less extraction call. Returns (extracted, lead).

    Robust by construction: any transport/parse error degrades to "nothing
    extracted, no lead-in" (logged) so a classifier hiccup never blocks a
    customer — the module just asks for whatever the schema still needs.
    Only keys that belong to the question's schema are accepted; the LLM
    cannot smuggle in unknown fields.
    """
    from advisor.llm import CompletionRequest  # noqa: PLC0415

    valid = {f.name for f in question.required_inputs}
    request = CompletionRequest(
        model=model or _default_model(),
        system=(
            "You do intake for land-use by-law questions: extract the "
            "inputs an answer needs from what the customer says. Output "
            "only JSON; never invent values."
        ),
        messages=[
            Message(
                role="user",
                content=_intake_prompt(question, conversation, known),
            )
        ],
    )
    try:
        response = await gateway.complete(request)
        text = "".join(
            b.text for b in response.content if isinstance(b, TextBlock)
        )
        verdict = json.loads(text)
        raw_inputs = verdict.get("inputs") or {}
        extracted = {
            k: _clean(v)
            for k, v in raw_inputs.items()
            if k in valid and _clean(v)
        }
        lead = _clean(verdict.get("message"))
        return extracted, lead
    except Exception:  # noqa: BLE001 — never let intake crash the flow
        logger.warning(
            "intake extraction failed for question %s; asking for missing "
            "inputs from the schema",
            question.slug,
            exc_info=True,
        )
        return {}, ""


def _default_model() -> str:
    from advisor.llm.registry import get_settings  # noqa: PLC0415

    return get_settings().main_model


async def detect_intake(
    gateway: LLMGateway,
    question: Question,
    *,
    conversation: str = "",
    provided_inputs: dict[str, str] | None = None,
    model: str | None = None,
) -> IntakeResult:
    """Detect missing inputs for a question and compose a consultant ask.

    Merges already-collected ``provided_inputs`` with values the LLM
    extracts from ``conversation``, then decides completeness
    DETERMINISTICALLY against the question's required-input schema (the
    same gate as ``answers.validate_inputs``). When a required input is
    still missing, returns a consultant-style ``prompt`` asking for exactly
    what's needed; when everything required is present, returns
    ``complete=True`` with the merged ``inputs`` ready for checkout.

    Already-collected ``provided_inputs`` take precedence over freshly
    extracted values (they are confirmed). The LLM call is skipped entirely
    when there is no conversation to read — there is nothing to extract, so
    we go straight to asking for whatever the schema still needs.
    """
    provided = {
        f.name: _clean((provided_inputs or {}).get(f.name))
        for f in question.required_inputs
    }
    provided = {k: v for k, v in provided.items() if v}

    convo = (conversation or "").strip()
    extracted: dict[str, str] = {}
    lead = ""
    if convo:
        extracted, lead = await _extract_inputs(
            gateway, question, convo, provided, model=model
        )

    # Merge: extracted first, confirmed/provided inputs win on conflict.
    merged = {**extracted, **provided}

    missing_required = [
        f.name
        for f in question.required_inputs
        if f.required and not merged.get(f.name)
    ]
    missing_optional = [
        f.name
        for f in question.required_inputs
        if not f.required and not merged.get(f.name)
    ]

    if not missing_required:
        return IntakeResult(
            complete=True,
            inputs=merged,
            missing_required=[],
            missing_optional=missing_optional,
            prompt="",
        )

    prompt = build_consultant_prompt(
        question, missing_required, missing_optional, lead_in=lead
    )
    return IntakeResult(
        complete=False,
        inputs=merged,
        missing_required=missing_required,
        missing_optional=missing_optional,
        prompt=prompt,
    )
