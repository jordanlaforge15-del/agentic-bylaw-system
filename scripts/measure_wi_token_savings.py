"""ABS-302: Measure the token savings actually delivered by WI-1 / WI-4.

Two measurement modes:

1. ``--from-transcripts <dir>`` (DEFAULT, ZERO API SPEND)
   Reads the per-turn ``tool_loop_metrics.total_usage`` already captured by
   ``scripts/run_test_prompts.py`` and computes WI-1's effect analytically:

       actual_cost     = (input * input_rate
                         + cache_create * cache_write_rate
                         + cache_read * cache_read_rate
                         + output * output_rate) / 1M
       projected_no_WI1 = ((input + cache_create + cache_read) * input_rate
                          + output * output_rate) / 1M
       WI-1 savings    = projected_no_WI1 - actual

   This is exact for WI-1's effect on what *was billed*. It uses real
   production data — no new model calls, no simulation, no API key.

2. ``--mock <config>`` (ZERO API SPEND)
   Drives ``run_tool_loop`` against ``MockGateway`` with realistic scripted
   tool calls. ``config`` is one of:

     - ``baseline``     — WI-1 OFF, WI-4 OFF
     - ``wi1``          — WI-1 ON,  WI-4 OFF
     - ``wi1+4``        — WI-1 ON,  WI-4 ON

   Captures every ``CompletionRequest`` the gateway receives and reports
   bytes per request partitioned by ``cache=True`` vs uncached. Useful as
   a sensitivity check on the analytical mode and the only way to measure
   WI-4 (in-loop compaction) — that one isn't visible in transcripts
   because it only affects what gets sent during the loop, not the
   tokens Anthropic returns to count.

The output is a JSON blob suitable for further analysis plus a human-readable
table printed to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Opus 4.5 published rates (USD per MTok).
RATES = {
    "input": 15.00,
    "output": 75.00,
    "cache_write": 18.75,
    "cache_read": 1.50,
}


# -- ANALYTICAL MODE (from transcripts) ---------------------------------------


def _cost_actual(usage: dict[str, int]) -> float:
    """Cost as Anthropic billed it: uncached input + cache_write + cache_read + output."""
    return (
        usage.get("input_tokens", 0) * RATES["input"]
        + usage.get("cache_creation_input_tokens", 0) * RATES["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * RATES["cache_read"]
        + usage.get("output_tokens", 0) * RATES["output"]
    ) / 1_000_000


def _cost_projected_no_wi1(usage: dict[str, int]) -> float:
    """Cost if WI-1 (rolling cache breakpoint) had not been in place.

    Without WI-1, the cache=True marker is never placed on tool_results,
    so the prefix is never cached. Every token Anthropic counted as
    cache_create + cache_read would instead have shipped as uncached input
    each iteration. Output tokens stay the same — they're per-iteration
    new content, unaffected by caching.
    """
    total_input = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    return (
        total_input * RATES["input"]
        + usage.get("output_tokens", 0) * RATES["output"]
    ) / 1_000_000


def analyse_transcript(path: Path) -> dict[str, Any]:
    """Per-case WI-1 effect from a TC-NNN.json transcript."""
    transcript = json.loads(path.read_text())
    case_actual = 0.0
    case_projected = 0.0
    turns_with_metrics = 0
    iters = 0
    for turn in transcript.get("turns", []):
        tlm = turn.get("tool_loop_metrics") or {}
        usage = tlm.get("total_usage")
        if not usage:
            continue
        case_actual += _cost_actual(usage)
        case_projected += _cost_projected_no_wi1(usage)
        iters += tlm.get("iterations", 0) or 0
        turns_with_metrics += 1
    saved = case_projected - case_actual
    pct = (saved / case_projected * 100.0) if case_projected else 0.0
    return {
        "id": transcript.get("id"),
        "title": transcript.get("title"),
        "complexity": transcript.get("complexity"),
        "model": transcript.get("model"),
        "turns_with_metrics": turns_with_metrics,
        "total_iterations": iters,
        "actual_usd": round(case_actual, 4),
        "projected_no_wi1_usd": round(case_projected, 4),
        "wi1_savings_usd": round(saved, 4),
        "wi1_savings_pct": round(pct, 2),
    }


def analyse_run_dir(run_dir: Path) -> list[dict[str, Any]]:
    files = sorted(run_dir.glob("TC-*.json"))
    if not files:
        raise SystemExit(f"No TC-*.json found in {run_dir}")
    return [analyse_transcript(p) for p in files]


# -- MOCKGATEWAY MODE (synthetic isolation) -----------------------------------


@dataclass
class IterMeasurement:
    iteration: int
    msgs_cached_chars: int
    msgs_uncached_chars: int
    system_chars: int
    tools_chars: int
    cache_system: bool
    cache_tools: bool


def _block_chars(block: Any) -> int:
    """Rough size of one block's serialised payload.

    The relative WI-1/WI-4 comparison only needs ratios; chars/4 ≈ tokens
    for English is a fine first-pass approximation. The final billing
    validation call (one TC-001 on real Anthropic) provides absolute
    ground truth.
    """
    from advisor.llm.base import TextBlock, ToolResultBlock, ToolUseBlock

    if isinstance(block, TextBlock):
        return len(block.text)
    if isinstance(block, ToolUseBlock):
        return len(block.name) + len(json.dumps(block.input))
    if isinstance(block, ToolResultBlock):
        if isinstance(block.content, str):
            return len(block.content)
        return sum(_block_chars(b) for b in block.content)
    return 0


def _measure_request(req: Any) -> IterMeasurement:
    """Partition one CompletionRequest's bytes by cache flag."""
    from advisor.llm.base import TextBlock

    cached = 0
    uncached = 0
    for msg in req.messages:
        blocks = (
            msg.content
            if isinstance(msg.content, list)
            else [TextBlock(text=msg.content)]
        )
        for block in blocks:
            size = _block_chars(block)
            if getattr(block, "cache", False):
                cached += size
            else:
                uncached += size
    sys_chars = len(req.system or "")
    tools_chars = sum(
        len(t.name) + len(t.description) + len(json.dumps(t.input_schema))
        for t in req.tools
    )
    return IterMeasurement(
        iteration=0,  # filled by caller
        msgs_cached_chars=cached,
        msgs_uncached_chars=uncached,
        system_chars=sys_chars,
        tools_chars=tools_chars,
        cache_system=req.cache_system,
        cache_tools=req.cache_tools,
    )


def _script_for_deep_loop(num_rounds: int) -> list[Any]:
    """Scripted assistant turns simulating a TC-005-shaped deep loop:
    several tool_use rounds followed by a synthesis answer.
    """
    from advisor.llm.base import (
        CompletionResponse,
        TextBlock,
        ToolUseBlock,
    )

    out: list[CompletionResponse] = []
    queries = [
        "maximum height in HR-2",
        "FAR limit in HR-2",
        "front and side setbacks in HR-2",
        "streetwall requirements in HR-2",
        "parking minimum for residential in HR-2",
        "lot coverage in HR-2",
        "bonus density triggers in HR-2",
        "open space requirement in HR-2",
    ]
    for i in range(num_rounds):
        q = queries[i % len(queries)]
        out.append(
            CompletionResponse(
                model="",
                content=[
                    TextBlock(text=f"Looking up: {q}"),
                    ToolUseBlock(
                        id=f"tu_{i+1}",
                        name="search_bylaw_evidence",
                        input={"q": q},
                    ),
                ],
                stop_reason="tool_use",
            )
        )
    out.append(
        CompletionResponse(
            model="",
            content=[
                TextBlock(text="Based on the evidence retrieved: ... (final answer).")
            ],
            stop_reason="end_turn",
        )
    )
    return out


async def _handler_realistic(_payload: dict[str, Any]) -> str:
    """Approximates a real ``search_bylaw_evidence`` tool_result.

    Size tuned to match what production retrieval returns: a handful of
    matches each with a short text excerpt + citation + cross-refs.
    """
    return (
        "Section 4.2 — Maximum height: 11 m for principal dwellings; "
        "additional height permitted under Section 5.1 only when bonus "
        "criteria are met. See Schedule B for zone-specific overlays. "
        "Cross-references: 4.1, 4.3, 5.1, 6.7."
    ) * 4  # ~1.2k chars — close to a real retrieval-match content size


def _initial_request() -> Any:
    from advisor.llm.base import (
        CompletionRequest,
        LLMRole,
        Message,
        ToolDefinition,
    )

    tool = ToolDefinition(
        name="search_bylaw_evidence",
        description="Search the bylaw for relevant passages.",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    )
    return CompletionRequest(
        model="claude-opus-4-5",
        system="You are a helpful bylaw assistant.",
        messages=[
            Message(
                role=LLMRole.USER,
                content="What are the rules for an HR-2 residential tower?",
            )
        ],
        tools=[tool],
        cache_system=True,
        cache_tools=True,
    )


async def run_mock_config(config: str, num_rounds: int = 6) -> dict[str, Any]:
    """One MockGateway run with the named WI configuration."""
    from advisor.llm import tool_loop as tool_loop_mod
    from advisor.llm.mock import MockGateway
    from advisor.llm.tool_loop import run_tool_loop

    wi1 = config in ("wi1", "wi1+4")
    wi4 = config in ("wi1+4",)

    # WI-1 toggle: monkey-patch the rolling-breakpoint marker.
    real_mark = tool_loop_mod._mark_rolling_cache_breakpoint
    if not wi1:
        tool_loop_mod._mark_rolling_cache_breakpoint = lambda messages: messages

    # WI-4 toggle: ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT<=0 disables compaction.
    # Default keeps WI-4 ON unless overridden by an env var; we explicitly set
    # it for the duration of this run and restore afterward.
    prior_env = os.environ.get("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT")
    if wi4:
        os.environ.pop("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT", None)
    else:
        os.environ["ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT"] = "0"

    try:
        gateway = MockGateway(scripted=_script_for_deep_loop(num_rounds))
        await run_tool_loop(
            gateway,
            request=_initial_request(),
            handlers={"search_bylaw_evidence": _handler_realistic},
            token_budget=100_000_000,  # disable cost circuit for clean comparison
            max_iterations=max(20, num_rounds + 5),
        )
        measurements: list[IterMeasurement] = []
        for i, call in enumerate(gateway.calls, 1):
            m = _measure_request(call)
            m.iteration = i
            measurements.append(m)
    finally:
        tool_loop_mod._mark_rolling_cache_breakpoint = real_mark
        if prior_env is None:
            os.environ.pop("ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT", None)
        else:
            os.environ["ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT"] = prior_env

    cached_total = sum(m.msgs_cached_chars for m in measurements)
    uncached_total = sum(m.msgs_uncached_chars for m in measurements)
    # chars -> tokens approximation: /4 for English.
    cached_tok = cached_total / 4
    uncached_tok = uncached_total / 4
    # Projected cost @ Opus rates assuming cache region bills at cache_read.
    # NOTE: This is a structural projection — it answers "how many bytes
    # are cache-eligible vs uncached," not "what would Anthropic bill."
    # The cache_write 1.25× premium isn't modelled here because we don't
    # know which bytes are first-time writes vs re-reads from the request
    # alone. Use the --from-transcripts mode for actual billing math.
    projected_cost = (
        uncached_tok * RATES["input"] + cached_tok * RATES["cache_read"]
    ) / 1_000_000
    return {
        "config": config,
        "rounds": num_rounds,
        "gateway_calls": len(measurements),
        "per_iteration": [asdict(m) for m in measurements],
        "totals": {
            "msgs_cached_chars": cached_total,
            "msgs_uncached_chars": uncached_total,
            "msgs_cached_tok_approx": round(cached_tok),
            "msgs_uncached_tok_approx": round(uncached_tok),
        },
        "projected_input_cost_usd": round(projected_cost, 4),
    }


# -- CLI ----------------------------------------------------------------------


def _print_analytical(rows: list[dict[str, Any]]) -> None:
    print("=" * 96)
    print("ANALYTICAL MODE — WI-1 effect from existing transcripts")
    print("=" * 96)
    print(
        f"  {'id':<8} {'iters':<7} {'actual $':<12} "
        f"{'projected no WI-1 $':<22} {'saved $':<12} {'saved %':<8}"
    )
    total_actual = total_projected = 0.0
    for r in rows:
        print(
            f"  {r['id']:<8} {r['total_iterations']:<7} "
            f"${r['actual_usd']:<11.4f} "
            f"${r['projected_no_wi1_usd']:<21.4f} "
            f"${r['wi1_savings_usd']:<11.4f} "
            f"{r['wi1_savings_pct']:<7.2f}%"
        )
        total_actual += r["actual_usd"]
        total_projected += r["projected_no_wi1_usd"]
    saved = total_projected - total_actual
    pct = (saved / total_projected * 100.0) if total_projected else 0.0
    print(
        f"\n  TOTAL across {len(rows)} cases: actual=${total_actual:.4f}  "
        f"projected_no_WI1=${total_projected:.4f}  "
        f"WI-1 saved=${saved:.4f} ({pct:.2f}%)"
    )


def _print_mock(result: dict[str, Any]) -> None:
    print("=" * 96)
    print(f"MOCKGATEWAY MODE — config={result['config']} rounds={result['rounds']}")
    print("=" * 96)
    print(
        f"  {'iter':<5} {'msgs_cached_chars':<19} "
        f"{'msgs_uncached_chars':<21} {'sys':<6} {'tools':<6}"
    )
    for m in result["per_iteration"]:
        print(
            f"  {m['iteration']:<5} {m['msgs_cached_chars']:<19} "
            f"{m['msgs_uncached_chars']:<21} {m['system_chars']:<6} {m['tools_chars']:<6}"
        )
    t = result["totals"]
    print(
        f"\n  totals: cached={t['msgs_cached_chars']} uncached={t['msgs_uncached_chars']} "
        f"(~{t['msgs_cached_tok_approx']} cached toks, "
        f"~{t['msgs_uncached_tok_approx']} uncached toks)"
    )
    print(f"  projected input cost @ Opus rates: ${result['projected_input_cost_usd']:.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    p_t = sub.add_parser("from-transcripts", help="Analytical from existing run dir")
    p_t.add_argument("run_dir", type=Path, help="evals/runs/<dir> with TC-*.json")
    p_t.add_argument("--out", type=Path, help="Optional JSON output path")

    p_m = sub.add_parser("mock", help="MockGateway synthetic measurement")
    p_m.add_argument("config", choices=["baseline", "wi1", "wi1+4"])
    p_m.add_argument("--rounds", type=int, default=6)
    p_m.add_argument("--out", type=Path, help="Optional JSON output path")

    args = parser.parse_args()

    # Make src/ importable for the mock mode.
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    if args.mode == "from-transcripts":
        rows = analyse_run_dir(args.run_dir)
        _print_analytical(rows)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(rows, indent=2))
            print(f"\nWrote: {args.out}")
        return 0

    if args.mode == "mock":
        result = asyncio.run(run_mock_config(args.config, num_rounds=args.rounds))
        _print_mock(result)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2))
            print(f"\nWrote: {args.out}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
