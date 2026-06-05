#!/usr/bin/env python3
"""ABS-271 Phase 1B tuning study — direct Anthropic API runner.

Drives TC-005 with the Anthropic API directly (no HTTP server) using the
production database and the same tool definitions the advisor uses. Sweeps
limit ∈ {5, 10, 15, 20, 30} by setting ADVISOR_FORCE_SEARCH_LIMIT in-process
between runs.

Usage:
  export ANTHROPIC_API_KEY="..."
  python scripts/run_phase1b_tuning_direct.py \
      --out-dir evals/runs/20260604T000000Z-phase-1b-tuning
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import anthropic

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"

MODEL = "claude-haiku-4-5"
MAX_ITERATIONS = 15
# Haiku 4.5 pricing (USD per token)
INPUT_COST_PER_MTOK = 0.80 / 1_000_000
CACHE_READ_COST_PER_MTOK = 0.08 / 1_000_000
CACHE_WRITE_COST_PER_MTOK = 1.00 / 1_000_000
OUTPUT_COST_PER_MTOK = 4.00 / 1_000_000


def load_tc005() -> dict[str, Any]:
    with PROMPTS_FILE.open() as f:
        cases = json.load(f)
    match = next((c for c in cases if c["id"] == "TC-005"), None)
    if match is None:
        raise RuntimeError("TC-005 not found in regional_centre_test_prompts.json")
    return match


def _tool_def_to_anthropic(tool_def) -> dict[str, Any]:
    """Convert advisor ToolDefinition to Anthropic tools format."""
    return {
        "name": tool_def.name,
        "description": tool_def.description,
        "input_schema": tool_def.input_schema,
    }


def _estimate_cost(usage: Any) -> float:
    if usage is None:
        return 0.0
    inp = getattr(usage, "input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (
        (inp - cache_r) * INPUT_COST_PER_MTOK
        + cache_r * CACHE_READ_COST_PER_MTOK
        + cache_w * CACHE_WRITE_COST_PER_MTOK
        + out * OUTPUT_COST_PER_MTOK
    )


async def run_tc005_direct(
    tool_defs,
    handlers: dict[str, Any],
    tc005: dict[str, Any],
    limit: int,
    client: anthropic.Anthropic,
) -> dict[str, Any]:
    """Run TC-005 with the given limit setting and return metrics."""
    os.environ["ADVISOR_FORCE_SEARCH_LIMIT"] = str(limit)

    anthropic_tools = [_tool_def_to_anthropic(t) for t in tool_defs]
    conversation: list[dict[str, Any]] = []
    turns_out: list[dict[str, Any]] = []

    for turn_def in tc005["turns"]:
        user_msg = turn_def["message"]
        conversation.append({"role": "user", "content": user_msg})

        iterations = 0
        turn_tool_calls = 0
        turn_cost = 0.0
        t1_input = 0
        t1_output = 0
        stop_reason_final = "end_turn"
        assistant_text = ""

        turn_start = time.time()
        messages = list(conversation)

        while iterations < MAX_ITERATIONS:
            iterations += 1
            t0 = time.time()
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                tools=anthropic_tools,
                messages=messages,
            )
            latency_ms = int((time.time() - t0) * 1000)
            turn_cost += _estimate_cost(resp.usage)
            t1_input += getattr(resp.usage, "input_tokens", 0) or 0
            t1_output += getattr(resp.usage, "output_tokens", 0) or 0

            stop_reason_final = resp.stop_reason

            # Extract text and tool_use blocks
            assistant_content = []
            text_parts = []
            tool_uses = []
            for block in resp.content:
                if block.type == "text":
                    text_parts.append(block.text)
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    tool_uses.append(block)
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            assistant_text = " ".join(text_parts)
            messages.append({"role": "assistant", "content": assistant_content})

            if not tool_uses or resp.stop_reason == "end_turn":
                break

            # Execute tools
            tool_results = []
            for tu in tool_uses:
                turn_tool_calls += 1
                handler = handlers.get(tu.name)
                if handler is None:
                    result = f"Unknown tool: {tu.name}"
                    is_error = True
                else:
                    try:
                        result = await handler(tu.input)
                        is_error = False
                    except Exception as exc:
                        result = str(exc)
                        is_error = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                    **({"is_error": True} if is_error else {}),
                })
            messages.append({"role": "user", "content": tool_results})

        else:
            # Hit cap — force a synthesis turn
            stop_reason_final = "iteration_cap"
            messages.append({"role": "user", "content": "[SYSTEM: Max iterations reached. Synthesise from context.]"})
            resp = client.messages.create(
                model=MODEL, max_tokens=512, tools=[], messages=messages
            )
            turn_cost += _estimate_cost(resp.usage)
            for block in resp.content:
                if block.type == "text":
                    assistant_text = block.text

        wall_s = round(time.time() - turn_start, 1)
        turn_out = {
            "turn": turn_def["turn"],
            "iterations": iterations,
            "tool_calls": turn_tool_calls,
            "stop_reason": stop_reason_final,
            "wall_time_s": wall_s,
            "cost_usd": round(turn_cost, 6),
            "input_tokens": t1_input,
            "output_tokens": t1_output,
            "assistant_text_chars": len(assistant_text),
        }
        turns_out.append(turn_out)
        conversation.append({"role": "assistant", "content": assistant_text})
        print(f"    T{turn_def['turn']}: iters={iterations} tools={turn_tool_calls} reason={stop_reason_final} cost=${turn_cost:.4f} {wall_s}s")

    # Clean up the env var so next run starts fresh
    os.environ.pop("ADVISOR_FORCE_SEARCH_LIMIT", None)

    return {
        "limit": limit,
        "model": MODEL,
        "turns": turns_out,
        "total_cost_usd": round(sum(t["cost_usd"] for t in turns_out), 6),
        "total_wall_s": round(sum(t["wall_time_s"] for t in turns_out), 1),
    }


async def main_async(limits: list[int], out_dir: Path, db_url: str):
    # Import advisor internals here so we can set up the production service
    import sys
    sys.path.insert(0, str(REPO_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT / "mcp"))

    from advisor.chat.tools import build_bylaw_tools
    from bylaw_retrieval.retrieval import RetrievalService
    from layer1.db.session import session_scope

    tc005 = load_tc005()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    results: list[dict[str, Any]] = []

    for limit in limits:
        print(f"\n=== limit={limit} ===")
        with session_scope(db_url) as session:
            service = RetrievalService(session)
            tool_defs, handlers = build_bylaw_tools(service)

            try:
                run = await run_tc005_direct(tool_defs, handlers, tc005, limit, client)
                results.append({"status": "ok", **run})
                print(f"  total_cost=${run['total_cost_usd']:.4f} wall={run['total_wall_s']}s")
            except Exception as exc:
                print(f"  ERROR: {exc}")
                results.append({"limit": limit, "status": "error", "error": str(exc)})

    # Save raw results
    raw_path = out_dir / "raw_results.json"
    raw_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nRaw results: {raw_path}")

    # Generate and save report
    report = _generate_report(results)
    report_path = out_dir / "REPORT.md"
    report_path.write_text(report)
    print(f"Report: {report_path}")


def _generate_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 1B Tuning Study — TC-005 Haiku at varying `limit`",
        "",
        "**ABS-271:** Tunable result-set size on `search_bylaw_evidence`  ",
        f"**Model:** `{MODEL}`  ",
        "**Test case:** TC-005 (multi-dimensional HR-2 zoning question)  ",
        "**Method:** `ADVISOR_FORCE_SEARCH_LIMIT` env var; direct Anthropic API (no HTTP server)  ",
        "**Single run per condition** (AC-1B.6 scope: exploration)  ",
        "",
        "## Headline results",
        "",
        "| limit | T1 iters | T1 reason | T1 cost (USD) | Total cost (USD) | Wall (s) |",
        "|-------|---------|-----------|--------------|-----------------|---------|",
    ]

    for r in results:
        limit = r.get("limit", "?")
        if r.get("status") != "ok":
            lines.append(f"| {limit} | ERROR | — | — | — | — |")
            continue
        turns = r.get("turns", [])
        t1 = next((t for t in turns if t["turn"] == 1), None)
        if t1 is None:
            lines.append(f"| {limit} | N/A | — | — | — | — |")
            continue
        t1_iters = t1.get("iterations", "?")
        t1_reason = t1.get("stop_reason", "?")
        t1_cost = t1.get("cost_usd", 0)
        total_cost = r.get("total_cost_usd", 0)
        wall = r.get("total_wall_s", 0)
        lines.append(f"| {limit} | {t1_iters} | {t1_reason} | ${t1_cost:.4f} | ${total_cost:.4f} | {wall} |")

    lines += [
        "",
        "## Per-turn breakdown",
        "",
    ]

    for r in results:
        limit = r.get("limit", "?")
        if r.get("status") != "ok":
            lines.append(f"### limit={limit}: ERROR — {r.get('error', '')}")
            lines.append("")
            continue
        lines.append(f"### limit={limit}")
        lines.append("")
        lines.append("| Turn | Iterations | Tool calls | Stop reason | Cost (USD) | Wall (s) | Answer chars |")
        lines.append("|------|-----------|------------|-------------|-----------|---------|-------------|")
        for t in r.get("turns", []):
            lines.append(
                f"| T{t['turn']} | {t['iterations']} | {t['tool_calls']} | {t['stop_reason']} "
                f"| ${t['cost_usd']:.4f} | {t['wall_time_s']} | {t['assistant_text_chars']} |"
            )
        lines.append("")

    lines += [
        "## Analysis",
        "",
        "### Does higher limit reduce T1 iteration count?",
        "",
        "From the ABS-267 Haiku baseline (limit=5), T1 hits `iteration_cap` at 10",
        "iterations. This study tests whether higher per-call context reduces that count.",
        "",
        "### Cost-efficiency tradeoff",
        "",
        "Higher limits increase per-call payload size (billed on every subsequent turn).",
        "The optimal limit minimises `T1_cost + downstream_cost`.",
        "",
        "### Candidate default for AC-1B.7",
        "",
        "*(Select the limit where T1 first reaches `end_turn` without cap, or the",
        "lowest limit where further increases provide diminishing returns.)*",
    ]

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="ABS-271 Phase 1B tuning study (direct API)")
    parser.add_argument("--out-dir", default="evals/runs/phase-1b-tuning")
    parser.add_argument("--limits", nargs="+", type=int, default=[5, 10, 15, 20, 30])
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL", "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"),
    )
    args = parser.parse_args()

    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(main_async(args.limits, out_dir, args.db_url))


if __name__ == "__main__":
    main()
