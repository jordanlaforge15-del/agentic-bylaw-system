#!/usr/bin/env python3
"""ABS-457: live-CLI validation of the ``claude_code`` gateway's runtime assumptions.

Four assumptions in the ABS-455/456 design could not be settled by
reading code. They are settled here, against the real ``claude`` CLI,
*before* anyone spends hours on the 20-case eval run. A failure does
not block the eval — it bounds how the eval's numbers may be read, and
must be written down (see "Known limitations" in
``src/advisor/llm/claude_code_backend.py``).

The four
--------
1. **Model alias resolution.** ``--model claude-opus-4-5`` actually
   resolves to Opus 4.5. ``claude-haiku-4-5`` was confirmed working on
   2026-08-09; Opus is the advisor's configured ``main_model`` and was
   never checked. Verdict from ``modelUsage[].canonicalModel``.

2. **Token attribution.** The advisor's ``wallet_cap_trip`` breaker
   (``tool_loop.py``, ``_measured_wallet_tokens``) counts
   ``input_tokens + output_tokens`` and deliberately excludes cache
   tokens, because the chat wallet does not charge for them. A probe on
   2026-08-09 showed ~22k of the CLI's *own* scaffolding landing in
   ``cache_creation_input_tokens`` — correctly invisible to the
   breaker. Where *our* prompt lands was never established. This check
   sends a >= 5,000-character system prompt and asserts the resulting
   tokens show up in ``usage.input_tokens``, not in the cache fields.
   A baseline call with a tiny system prompt runs first, so the verdict
   rests on the *delta* rather than on an absolute number that CLI
   scaffolding could swamp. **If this fails the wallet breaker
   under-counts and fires late.**

3. **Autocompact pinning.** With ``--autocompact 1000000`` a >= 200,000
   character conversation is not silently compacted. Claude Code
   compacting mid-turn would rewrite what the model sees without
   telling us — a divergence the API path cannot have, since it
   hard-errors at the context limit instead.

4. **Multi-iteration round trip.** One real ``complete()`` returning
   ``action:"tool_calls"``, then a second ``complete()`` fed the
   resulting ``ToolUseBlock`` plus a synthetic ``ToolResultBlock``,
   returning ``action:"final_answer"``. The single most load-bearing
   assumption in the design: it proves the loop closes through the real
   CLI.

Running it
----------
::

    ABS_RUN_LIVE_CLAUDE_CODE=1 env -u ANTHROPIC_API_KEY \\
        .venv/bin/python scripts/validate_claude_code_gateway.py

Opt-in by env var, following ``tests/integration/test_haiku_smoke.py``.
Without ``ABS_RUN_LIVE_CLAUDE_CODE=1`` the script prints a SKIPPED
banner, writes no report, and exits 0 — it is not a failure to decline
to spend a subscription turn, and this keeps the script harmless to any
suite or hook that happens to execute it.

``ANTHROPIC_API_KEY`` is a hard stop, not a warning. Set, it would
route these calls to the metered API — the exact surprise bill the
whole ``claude_code`` backend exists to avoid — so the script refuses
to start and tells you to re-run under ``env -u ANTHROPIC_API_KEY``.

Exit codes: ``0`` all four passed (or skipped), ``1`` at least one
assumption failed, ``2`` a precondition failed (API key set, CLI
missing).

Output
------
``evals/runs/claude-code-gateway-validation/<UTC-timestamp>/report.json``
— per assumption: pass/fail, the payload excerpt the verdict was
derived from, and the model used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:  # running from a checkout, no install
    sys.path.insert(0, str(REPO_ROOT / "src"))

from advisor.llm.base import (
    CompletionRequest,
    CompletionResponse,
    LLMRole,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from advisor.llm.claude_code_backend import ClaudeCodeGateway
from advisor.llm.claude_code_translation import (
    build_envelope_schema,
    render_prompt,
)

__all__ = [
    "API_KEY_REFUSAL",
    "ASSUMPTION_TITLES",
    "OPT_IN_ENV_VAR",
    "REPORT_ROOT",
    "AssumptionResult",
    "api_key_is_set",
    "build_report",
    "main",
    "report_dir",
    "usage_excerpt",
    "verdict_autocompact",
    "verdict_model_alias",
    "verdict_round_trip",
    "verdict_token_attribution",
]

OPT_IN_ENV_VAR = "ABS_RUN_LIVE_CLAUDE_CODE"
REPORT_ROOT = Path("evals/runs/claude-code-gateway-validation")

DEFAULT_MODEL = "claude-opus-4-5"

ASSUMPTION_TITLES = {
    "model_alias_resolution": "1. Model alias resolution",
    "token_attribution": "2. Token attribution (wallet breaker)",
    "autocompact_pinning": "3. Autocompact pinning",
    "multi_iteration_round_trip": "4. Multi-iteration round trip",
}

# Assumption 2 sizing. 5,000 characters is the ticket's floor; English
# prose runs ~4 chars/token, so a system prompt this size is worth
# ~1,250 tokens. We require the observed input_tokens delta to clear
# 40% of that estimate — generous enough to absorb tokeniser variance
# and CLI-side prompt assembly, tight enough that "the prompt went to
# the cache fields instead" (which would leave the delta near zero)
# cannot pass.
SYSTEM_PROMPT_MIN_CHARS = 5_000
_CHARS_PER_TOKEN = 4
_ATTRIBUTION_TOLERANCE = 0.4

# Assumption 3 sizing. Same chars/token estimate over a >= 200,000
# character conversation, applied to *total* reported input (input +
# cache creation + cache read) — assumption 3 asks whether the CLI
# silently dropped input, not which bucket it landed in. That is
# assumption 2's job.
#
# The floor is much tighter than assumption 2's for a reason. 40% of a
# 200k-character conversation is ~20k tokens, and a compaction that
# summarised the history down to 25k would clear that bar and be
# reported as "not compacted" — precisely the outcome this check exists
# to detect. Compaction's whole purpose is a large reduction, so
# requiring the reported input to stay within 20% of the estimate keeps
# the check sensitive to it. Measured headroom on the 2026-08-11 run:
# 62,772 reported against a 40,194 floor.
CONVERSATION_MIN_CHARS = 200_000
_COMPACTION_TOLERANCE = 0.8
_COMPACTION_MARKERS = ("compact", "summariz", "summaris", "truncat")


@dataclass
class AssumptionResult:
    """One assumption's verdict plus the evidence it was derived from."""

    key: str
    title: str
    passed: bool
    detail: str
    model: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pure verdict functions. No subprocess, no filesystem — the live
# runners below feed them real payloads, the unit tests feed them
# canned ones, and the logic that decides pass/fail is the same either
# way.
# ---------------------------------------------------------------------------


def usage_excerpt(payload: dict[str, Any]) -> dict[str, Any]:
    """The four token counters, flattened out of a CLI payload.

    The CLI's ``usage`` object also carries per-iteration breakdowns and
    service-tier noise that would bloat every report entry; the report
    is meant to be readable by whoever inherits this validation, so only
    the numbers a verdict can turn on are kept.
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        key: usage.get(key, 0)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    }


def verdict_model_alias(
    payload: dict[str, Any], expected_model: str
) -> tuple[bool, str, dict[str, Any]]:
    """Did ``--model <alias>`` resolve to the model we asked for?

    ``modelUsage`` is keyed by whatever string was billed and each entry
    carries a ``canonicalModel``. The CLI bills its own scaffolding
    turns (title generation and similar) to other models, so entries
    are matched rather than requiring a single one: the question is
    whether *our* model appears, not whether it was the only one.
    """
    model_usage = payload.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        return (
            False,
            ("payload carried no 'modelUsage' object, so the resolved model cannot be read at all"),
            {"modelUsage": model_usage},
        )

    canonicals = {
        key: entry.get("canonicalModel")
        for key, entry in model_usage.items()
        if isinstance(entry, dict)
    }
    matched = [key for key, canonical in canonicals.items() if canonical == expected_model]
    if matched:
        return (
            True,
            f"modelUsage[{matched[0]!r}].canonicalModel == {expected_model!r}",
            {"modelUsage_canonicalModel": canonicals},
        )
    return (
        False,
        (
            f"no modelUsage entry reports canonicalModel == {expected_model!r}; "
            f"saw {sorted({v for v in canonicals.values() if v})}"
        ),
        {"modelUsage_canonicalModel": canonicals},
    )


def verdict_token_attribution(
    baseline_payload: dict[str, Any],
    probe_payload: dict[str, Any],
    system_prompt_chars: int,
) -> tuple[bool, str, dict[str, Any]]:
    """Did a large system prompt land in ``input_tokens`` or in the cache?

    Verdict is on the *delta* between an otherwise-identical baseline
    call with a tiny system prompt and the probe call with the large
    one. An absolute reading cannot answer this: the CLI's own ~22k of
    scaffolding moves between the cache buckets run to run and would
    drown a 1,250-token signal either way.
    """
    baseline = usage_excerpt(baseline_payload)
    probe = usage_excerpt(probe_payload)
    if not baseline or not probe:
        return (
            False,
            ("one or both probes reported no usage object, so attribution cannot be determined"),
            {"baseline_usage": baseline, "probe_usage": probe},
        )

    delta = {key: probe.get(key, 0) - baseline.get(key, 0) for key in probe}
    expected_tokens = system_prompt_chars / _CHARS_PER_TOKEN
    threshold = int(expected_tokens * _ATTRIBUTION_TOLERANCE)
    observed = delta.get("input_tokens", 0)
    evidence = {
        "baseline_usage": baseline,
        "probe_usage": probe,
        "delta": delta,
        "system_prompt_chars": system_prompt_chars,
        "input_tokens_delta_threshold": threshold,
    }

    if observed >= threshold:
        return (
            True,
            (
                f"a {system_prompt_chars}-char system prompt moved input_tokens by "
                f"+{observed} (>= {threshold}), so our prompt is visible to the "
                "wallet_cap_trip breaker"
            ),
            evidence,
        )
    cache_delta = delta.get("cache_creation_input_tokens", 0) + delta.get(
        "cache_read_input_tokens", 0
    )
    # Worth distinguishing in the verdict text: a small-but-nonzero
    # delta means the breaker under-counts by a factor, while a delta of
    # exactly zero means input_tokens is not a function of prompt size
    # at all and the breaker's input term is dead weight. The two imply
    # very different mitigations.
    severity = (
        "input_tokens did not move at all, so it is not a function of prompt "
        "size and the breaker's input term is inert"
        if observed == 0
        else "the breaker under-counts our prompt"
    )
    return (
        False,
        (
            f"a {system_prompt_chars}-char system prompt moved input_tokens by only "
            f"+{observed} (< {threshold}); cache fields moved by +{cache_delta}. "
            f"{severity} — wallet_cap_trip will fire late or never."
        ),
        evidence,
    )


def verdict_autocompact(
    payload: dict[str, Any], conversation_chars: int
) -> tuple[bool, str, dict[str, Any]]:
    """Was a very large conversation passed through whole, uncompacted?

    Two independent signals, both required:

    * the reported input (all three input buckets summed) is in the
      ballpark of the conversation we sent — a compacted turn would
      report a small fraction of it;
    * no compaction notice anywhere in the payload.

    The token check alone would miss a compaction that happened to
    summarise verbosely; the marker scan alone would miss a silent one.
    """
    usage = usage_excerpt(payload)
    if not usage:
        return (
            False,
            "payload carried no usage object, so compaction cannot be ruled out",
            {"usage": usage},
        )

    reported = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    threshold = int(conversation_chars / _CHARS_PER_TOKEN * _COMPACTION_TOLERANCE)
    markers = _compaction_markers_in(payload)
    evidence = {
        "usage": usage,
        "reported_input_tokens_total": reported,
        "conversation_chars": conversation_chars,
        "reported_input_threshold": threshold,
        "compaction_markers": markers,
    }

    if reported < threshold:
        return (
            False,
            (
                f"a {conversation_chars}-char conversation reported only {reported} "
                f"input tokens (< {threshold}) — consistent with silent compaction"
            ),
            evidence,
        )
    if markers:
        return (
            False,
            f"payload contains compaction notice(s): {markers}",
            evidence,
        )
    return (
        True,
        (
            f"a {conversation_chars}-char conversation reported {reported} input "
            f"tokens (>= {threshold}) with no compaction notice in the payload"
        ),
        evidence,
    )


def _compaction_markers_in(payload: dict[str, Any]) -> list[str]:
    """Payload keys whose *value* reads like a compaction notice.

    Only string-valued leaves are scanned, and keys are excluded: the
    CLI's own schema has no compaction key today, but a future one would
    likely be a flag or a message, and matching on values keeps a stray
    key name like ``autocompact_threshold`` from reading as evidence of
    compaction having happened.
    """
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            lowered = node.lower()
            if any(marker in lowered for marker in _COMPACTION_MARKERS):
                found.append(path)

    walk(payload, "")
    return found


def verdict_round_trip(
    first: CompletionResponse | None,
    second: CompletionResponse | None,
    tool_name: str,
) -> tuple[bool, str, dict[str, Any]]:
    """Did the loop actually close: tool_calls out, final_answer back?

    Checks both halves, because either alone is uninformative. A first
    turn that never asks for a tool proves nothing about resumption; a
    second turn that answers without having been fed a tool result
    proves nothing about whether the CLI can read one.
    """
    evidence: dict[str, Any] = {
        "first_stop_reason": first.stop_reason if first else None,
        "first_content_types": [b.type for b in first.content] if first else None,
        "second_stop_reason": second.stop_reason if second else None,
        "second_content_types": [b.type for b in second.content] if second else None,
    }
    if first is None or second is None:
        return False, "one of the two turns did not produce a response", evidence

    tool_uses = [b for b in first.content if isinstance(b, ToolUseBlock)]
    evidence["first_tool_calls"] = [{"name": b.name, "input": b.input} for b in tool_uses]
    if first.stop_reason != "tool_use" or not tool_uses:
        return (
            False,
            (
                f"turn 1 did not request a tool call (stop_reason="
                f"{first.stop_reason!r}, blocks={evidence['first_content_types']})"
            ),
            evidence,
        )
    if not any(b.name == tool_name for b in tool_uses):
        return (
            False,
            f"turn 1 requested {[b.name for b in tool_uses]}, not {tool_name!r}",
            evidence,
        )

    texts = [b.text for b in second.content if isinstance(b, TextBlock)]
    evidence["second_text"] = " ".join(texts)[:1000]
    if second.stop_reason != "end_turn" or not any(t.strip() for t in texts):
        return (
            False,
            (
                f"turn 2 did not return a final answer (stop_reason="
                f"{second.stop_reason!r}, blocks={evidence['second_content_types']})"
            ),
            evidence,
        )
    return (
        True,
        (
            f"turn 1 returned action='tool_calls' for {tool_name!r}; turn 2, fed the "
            "tool result, returned action='final_answer' with text"
        ),
        evidence,
    )


# ---------------------------------------------------------------------------
# Live runners
# ---------------------------------------------------------------------------


async def _invoke(gateway: ClaudeCodeGateway, request: CompletionRequest) -> dict[str, Any]:
    """One CLI round trip, returning the raw payload.

    Deliberately reaches for the gateway's own render + invoke path
    rather than shelling out independently: a validation that built its
    own command line would prove things about a command line nothing in
    production runs.
    """
    prompt = render_prompt(request.model_copy(update={"system": None}))
    schema = build_envelope_schema(request.tools)
    return await gateway._invoke_once(prompt, schema, request, attempt=1)


def _tiny_request(model: str, system: str) -> CompletionRequest:
    return CompletionRequest(
        model=model,
        system=system,
        messages=[
            Message(
                role=LLMRole.USER,
                content=("Reply with the single word 'ok' as your final answer. Do not elaborate."),
            )
        ],
    )


def _filler(chars: int, label: str) -> str:
    """Deterministic prose padding of at least ``chars`` characters.

    Prose rather than repeated tokens: a long run of identical text
    compresses in ways real conversation does not, and both the
    attribution and the compaction check are reasoning about token
    counts.
    """
    template = (
        "Reference note {label}-{index}: municipal land-use regulation "
        "section records the permitted height, setback, and lot-coverage "
        "standards applicable to the zone under review, together with the "
        "conditions that attach to each permitted use. "
    )
    parts: list[str] = []
    total = 0
    index = 0
    while total < chars:
        part = template.format(label=label, index=index)
        parts.append(part)
        total += len(part)
        index += 1
    return "".join(parts)[:chars]


async def check_model_alias(gateway: ClaudeCodeGateway, model: str) -> AssumptionResult:
    payload = await _invoke(gateway, _tiny_request(model, "You are a test probe."))
    passed, detail, evidence = verdict_model_alias(payload, model)
    evidence["usage"] = usage_excerpt(payload)
    return AssumptionResult(
        key="model_alias_resolution",
        title=ASSUMPTION_TITLES["model_alias_resolution"],
        passed=passed,
        detail=detail,
        model=model,
        evidence=evidence,
    )


async def check_token_attribution(gateway: ClaudeCodeGateway, model: str) -> AssumptionResult:
    big_system = "You are a test probe.\n\n" + _filler(SYSTEM_PROMPT_MIN_CHARS, "SYS")
    baseline_payload = await _invoke(gateway, _tiny_request(model, "You are a test probe."))
    probe_payload = await _invoke(gateway, _tiny_request(model, big_system))
    passed, detail, evidence = verdict_token_attribution(
        baseline_payload, probe_payload, len(big_system)
    )
    return AssumptionResult(
        key="token_attribution",
        title=ASSUMPTION_TITLES["token_attribution"],
        passed=passed,
        detail=detail,
        model=model,
        evidence=evidence,
    )


async def check_autocompact(gateway: ClaudeCodeGateway, model: str) -> AssumptionResult:
    body = _filler(CONVERSATION_MIN_CHARS, "CONV")
    request = CompletionRequest(
        model=model,
        system="You are a test probe reading a long reference document.",
        messages=[
            Message(role=LLMRole.USER, content=body),
            Message(
                role=LLMRole.USER,
                content=(
                    "That was the reference document. As your final answer, "
                    "reply with the single word 'ok'."
                ),
            ),
        ],
    )
    # The prompt the CLI actually receives is what compaction would act
    # on, so the verdict is sized against the rendered length, not the
    # filler length.
    rendered_chars = len(render_prompt(request.model_copy(update={"system": None})))
    payload = await _invoke(gateway, request)
    passed, detail, evidence = verdict_autocompact(payload, rendered_chars)
    return AssumptionResult(
        key="autocompact_pinning",
        title=ASSUMPTION_TITLES["autocompact_pinning"],
        passed=passed,
        detail=detail,
        model=model,
        evidence=evidence,
    )


_ROUND_TRIP_TOOL = ToolDefinition(
    name="lookup_zone_standard",
    description=(
        "Look up a numeric standard (height, setback, lot coverage) for a "
        "zoning designation. The only way to obtain this information."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "zone": {"type": "string", "description": "Zone code, e.g. HR-2"},
            "standard": {
                "type": "string",
                "description": "Standard to look up, e.g. max_height",
            },
        },
        "required": ["zone", "standard"],
    },
)


async def check_round_trip(gateway: ClaudeCodeGateway, model: str) -> AssumptionResult:
    system = (
        "You are a bylaw research assistant. You have no knowledge of any "
        "municipality's numeric zoning standards and must obtain them with "
        "the provided tool before answering."
    )
    question = Message(
        role=LLMRole.USER,
        content="What is the maximum building height in the HR-2 zone?",
    )
    first_request = CompletionRequest(
        model=model, system=system, messages=[question], tools=[_ROUND_TRIP_TOOL]
    )
    first = await gateway.complete(first_request)

    tool_uses = [b for b in first.content if isinstance(b, ToolUseBlock)]
    second: CompletionResponse | None = None
    if tool_uses:
        call = tool_uses[0]
        second_request = CompletionRequest(
            model=model,
            system=system,
            messages=[
                question,
                Message(role=LLMRole.ASSISTANT, content=list(first.content)),
                Message(
                    role=LLMRole.USER,
                    content=[
                        ToolResultBlock(
                            tool_use_id=call.id,
                            content=("max_height: 25 metres (HR-2, synthetic test value)"),
                        )
                    ],
                ),
            ],
            tools=[_ROUND_TRIP_TOOL],
        )
        second = await gateway.complete(second_request)

    passed, detail, evidence = verdict_round_trip(first, second, _ROUND_TRIP_TOOL.name)
    return AssumptionResult(
        key="multi_iteration_round_trip",
        title=ASSUMPTION_TITLES["multi_iteration_round_trip"],
        passed=passed,
        detail=detail,
        model=model,
        evidence=evidence,
    )


# (assumption key, runner). Paired explicitly rather than derived from
# the function name so a crashed check can still be reported under the
# right assumption.
CHECKS = (
    ("model_alias_resolution", check_model_alias),
    ("token_attribution", check_token_attribution),
    ("autocompact_pinning", check_autocompact),
    ("multi_iteration_round_trip", check_round_trip),
)


# ---------------------------------------------------------------------------
# Preconditions, reporting, entry point
# ---------------------------------------------------------------------------


API_KEY_REFUSAL = (
    "REFUSING TO RUN: ANTHROPIC_API_KEY is set.\n"
    "This script validates the subscription-billed `claude` CLI. With an API "
    "key in the environment the run risks billing the metered Messages API "
    "instead — the exact surprise the claude_code backend exists to avoid.\n"
    "Re-run it as:\n"
    "  ABS_RUN_LIVE_CLAUDE_CODE=1 env -u ANTHROPIC_API_KEY "
    ".venv/bin/python scripts/validate_claude_code_gateway.py"
)


def api_key_is_set(env: dict[str, str] | None = None) -> bool:
    """Is ``ANTHROPIC_API_KEY`` present and non-empty?

    Mirrors ``advisor.llm.registry._assert_no_api_key_billing``: with a
    key present these turns could bill the metered API, which is the
    surprise this backend exists to prevent. A validation run that
    quietly cost money would also invalidate its own premise.

    Checked before the opt-in gate, so the refusal is visible even to
    someone who has not set ``ABS_RUN_LIVE_CLAUDE_CODE`` yet.
    """
    source = os.environ if env is None else env
    return bool(source.get("ANTHROPIC_API_KEY"))


def report_dir(root: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return root / stamp


def build_report(results: list[AssumptionResult], model: str, cli_path: str) -> dict[str, Any]:
    return {
        "issue": "ABS-457",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "model": model,
        "cli_path": cli_path,
        "cli_version": _cli_version(cli_path),
        "all_passed": all(r.passed for r in results),
        "assumptions": [r.as_dict() for r in results],
    }


def _cli_version(cli_path: str) -> str | None:
    import subprocess  # local: only this one diagnostic needs it

    try:
        out = subprocess.run(
            [cli_path, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,  # a version we can't read is a null field, not a failure
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


async def run_all(model: str, cli_path: str | None) -> list[AssumptionResult]:
    gateway = ClaudeCodeGateway(cli_path=cli_path)
    results: list[AssumptionResult] = []
    for key, check in CHECKS:
        try:
            result = await check(gateway, model)
        except Exception as exc:  # noqa: BLE001
            # A crashed check is a failed assumption, not a failed run:
            # the remaining three still carry information, and the
            # report is more useful with one error entry than with none.
            result = AssumptionResult(
                key=key,
                title=ASSUMPTION_TITLES[key],
                passed=False,
                detail=f"check raised {type(exc).__name__}: {exc}",
                model=model,
                evidence={"exception": repr(exc)},
            )
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.title}\n        {result.detail}\n", flush=True)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model alias to validate (default: {DEFAULT_MODEL})",
    )
    parser.add_argument("--cli-path", default=None, help="Path to the `claude` binary.")
    parser.add_argument(
        "--report-root",
        default=None,
        type=Path,
        help=f"Where to write the report (default: {REPORT_ROOT})",
    )
    args = parser.parse_args(argv)

    if api_key_is_set():
        print(API_KEY_REFUSAL, file=sys.stderr)
        return 2

    if os.environ.get(OPT_IN_ENV_VAR) != "1":
        print(
            f"SKIPPED — live validation is opt-in. Set {OPT_IN_ENV_VAR}=1 to "
            "run it:\n"
            f"  {OPT_IN_ENV_VAR}=1 env -u ANTHROPIC_API_KEY "
            ".venv/bin/python scripts/validate_claude_code_gateway.py\n"
            "No report written."
        )
        return 0

    cli_path = args.cli_path or shutil.which("claude")
    if not cli_path:
        print(
            "PRECONDITION FAILED: the `claude` CLI is not on PATH. Install "
            "Claude Code and authenticate it against a Claude subscription, "
            "or pass --cli-path.",
            file=sys.stderr,
        )
        return 2

    print(
        f"ABS-457 live validation — model={args.model} cli={cli_path}\n"
        "Four assumptions, four to six subscription-billed CLI turns.\n",
        flush=True,
    )
    results = asyncio.run(run_all(args.model, cli_path))

    root = args.report_root or (REPO_ROOT / REPORT_ROOT)
    out_dir = report_dir(Path(root))
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(results, args.model, cli_path)
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    failed = [r for r in results if not r.passed]
    print(f"Report: {report_path}")
    if failed:
        print(
            "\nFAILED ASSUMPTIONS (record each in the ABS-457 Linear comment "
            "and in the 'Known limitations' section of "
            "src/advisor/llm/claude_code_backend.py):"
        )
        for result in failed:
            print(f"  - {result.title}: {result.detail}")
        return 1
    print("\nAll four assumptions hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
