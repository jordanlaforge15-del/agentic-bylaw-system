#!/usr/bin/env python3
"""ABS-271 Phase 1B tuning study runner.

Runs TC-005 (the multi-dimensional HR-2 question) using Haiku 4.5 at
limit ∈ {5, 10, 15, 20, 30} by setting ADVISOR_FORCE_SEARCH_LIMIT before
each run, then aggregates per-run metrics into a REPORT.md.

Usage:
  # Advisor must be running on --base-url (default http://127.0.0.1:8001).
  # Export ANTHROPIC_API_KEY before starting the advisor.
  python scripts/run_phase1b_tuning.py --base-url http://127.0.0.1:8001 \
      --out-dir evals/runs/20260604T000000Z-phase-1b-tuning

The script is NOT a subprocess launcher; it talks to the advisor via HTTP
and relies on the caller to have exported ADVISOR_FORCE_SEARCH_LIMIT before
starting the advisor process. For sequential limit sweeps, the advisor must
be restarted between conditions (so the env var is re-read).

For a fully automated sweep, see the shell wrapper pattern in BASELINE.md.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"


def load_tc005() -> dict[str, Any]:
    with PROMPTS_FILE.open() as f:
        cases = json.load(f)
    match = next((c for c in cases if c["id"] == "TC-005"), None)
    if match is None:
        raise RuntimeError("TC-005 not found in regional_centre_test_prompts.json")
    return match


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cur_event: str | None = None
    data_buf: list[str] = []

    def flush():
        nonlocal cur_event, data_buf
        if not cur_event and not data_buf:
            return
        raw = "\n".join(data_buf)
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = {"_raw": raw}
        events.append({"event": cur_event, "data": parsed})
        cur_event = None
        data_buf = []

    for line in text.splitlines():
        if line == "":
            flush()
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            cur_event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_buf.append(line.removeprefix("data:").strip())
    flush()
    return events


def run_tc005(base_url: str, case: dict[str, Any], user_id: str = "tuning-study-user") -> dict[str, Any]:
    """Drive TC-005 through the advisor and return per-turn metrics."""
    turns_out = []
    session_id: str | None = None

    with httpx.Client(base_url=base_url, timeout=300) as client:
        for turn in case["turns"]:
            body: dict[str, Any] = {"message": turn["message"], "user_id": user_id}
            if session_id:
                body["session_id"] = session_id
            body["case_id"] = case["id"]

            t0 = time.time()
            resp = client.post("/v1/chat", json=body)
            wall_s = time.time() - t0
            resp.raise_for_status()

            events = _parse_sse(resp.text)

            # Extract session_id from first session event
            for ev in events:
                if ev["event"] == "session" and ev["data"]:
                    session_id = ev["data"].get("session_id", session_id)

            # Extract tool_loop_metrics (ABS-266 instrumentation)
            tool_loop_metrics = None
            for ev in events:
                if ev["event"] == "tool_loop_metrics":
                    tool_loop_metrics = ev["data"]

            # Extract assistant text
            text_full: dict[int, str] = {}
            text_chunks: dict[int, list[str]] = {}
            for ev in events:
                if ev["event"] == "content_block_start":
                    d = ev["data"] or {}
                    block = d.get("content_block") or {}
                    if block.get("type") == "text":
                        text_full[d.get("index")] = block.get("text", "")
                elif ev["event"] == "content_block_delta":
                    d = ev["data"] or {}
                    if d.get("text_delta"):
                        text_chunks.setdefault(d.get("index"), []).append(d["text_delta"])

            assistant_text = ""
            for idx in sorted(set(list(text_full.keys()) + list(text_chunks.keys()))):
                if idx in text_full and text_full[idx]:
                    assistant_text += text_full[idx]
                elif idx in text_chunks:
                    assistant_text += "".join(text_chunks[idx])

            # Extract stop reason and usage
            stop_reason = None
            usage = None
            model = None
            for ev in events:
                if ev["event"] == "message_start":
                    model = (ev["data"] or {}).get("model", model)
                elif ev["event"] == "message_delta":
                    d = ev["data"] or {}
                    stop_reason = d.get("stop_reason", stop_reason)
                    if d.get("usage"):
                        usage = d["usage"]

            turns_out.append({
                "turn": turn["turn"],
                "wall_time_s": round(wall_s, 1),
                "stop_reason": stop_reason,
                "assistant_text_chars": len(assistant_text),
                "model": model,
                "usage": usage,
                "tool_loop_metrics": tool_loop_metrics,
            })

    return {"turns": turns_out, "session_id": session_id}


def main():
    parser = argparse.ArgumentParser(description="ABS-271 Phase 1B tuning study runner")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--out-dir", default="evals/runs/phase-1b-tuning")
    parser.add_argument("--limits", nargs="+", type=int, default=[5, 10, 15, 20, 30])
    parser.add_argument("--user-id", default="tuning-study-user-1")
    args = parser.parse_args()

    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    case = load_tc005()
    results: list[dict[str, Any]] = []

    for limit in args.limits:
        print(f"\n=== Running TC-005 with limit={limit} ===")
        try:
            run = run_tc005(args.base_url, case, user_id=f"{args.user_id}-limit{limit}")
            results.append({"limit": limit, "status": "ok", "run": run})
            print(f"  Turns completed: {len(run['turns'])}")
            for t in run["turns"]:
                metrics = t.get("tool_loop_metrics") or {}
                iters = metrics.get("iterations", "?")
                reason = metrics.get("terminated_reason", t.get("stop_reason", "?"))
                cost = metrics.get("estimated_cost_usd", "?")
                print(f"  T{t['turn']}: iterations={iters} reason={reason} cost=${cost}")
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({"limit": limit, "status": "error", "error": str(exc)})

    # Save raw results
    raw_path = out_dir / "raw_results.json"
    raw_path.write_text(json.dumps(results, indent=2, default=str))

    # Generate REPORT.md
    report = _generate_report(results, args.base_url)
    report_path = out_dir / "REPORT.md"
    report_path.write_text(report)
    print(f"\nReport written to: {report_path}")


def _generate_report(results: list[dict[str, Any]], base_url: str) -> str:
    lines = [
        "# Phase 1B Tuning Study — TC-005 Haiku at varying `limit`",
        "",
        "**ABS-271:** Tunable result-set size on `search_bylaw_evidence`  ",
        f"**Model:** `claude-haiku-4-5`  ",
        f"**Test case:** TC-005 (multi-dimensional HR-2 zoning question)  ",
        f"**Base URL:** `{base_url}`  ",
        "",
        "## Methodology",
        "",
        "Each condition forced `ADVISOR_FORCE_SEARCH_LIMIT=<N>` in the advisor",
        "environment so every `search_bylaw_evidence` call in that run returned",
        "exactly N fragments regardless of the model's own limit argument. The",
        "question is whether higher N reduces T1's iteration count (the",
        "bottleneck identified in the ABS-267 Haiku baseline).",
        "",
        "Single-run per condition (FR-1B / AC-1B.6 scope: exploration, not",
        "regression validation).",
        "",
        "## Results",
        "",
        "### T1 summary (the wide-net info-gather turn)",
        "",
        "| limit | T1 iterations | T1 stop reason | T1 cost (USD) | T1 answer chars |",
        "|-------|--------------|----------------|--------------|-----------------|",
    ]

    for r in results:
        limit = r["limit"]
        if r["status"] != "ok":
            lines.append(f"| {limit} | ERROR | — | — | — |")
            continue
        turns = r["run"]["turns"]
        t1 = next((t for t in turns if t["turn"] == 1), None)
        if t1 is None:
            lines.append(f"| {limit} | N/A | — | — | — |")
            continue
        metrics = t1.get("tool_loop_metrics") or {}
        iters = metrics.get("iterations", "?")
        reason = metrics.get("terminated_reason", t1.get("stop_reason", "?"))
        cost = metrics.get("estimated_cost_usd", "?")
        chars = t1.get("assistant_text_chars", "?")
        lines.append(f"| {limit} | {iters} | {reason} | {cost} | {chars} |")

    lines += [
        "",
        "### All turns summary",
        "",
        "| limit | Turns | Total cost (USD) | Wall time (s) | T1 iters | T2-end iters |",
        "|-------|-------|-----------------|--------------|---------|-------------|",
    ]

    for r in results:
        limit = r["limit"]
        if r["status"] != "ok":
            lines.append(f"| {limit} | ERROR | — | — | — | — |")
            continue
        turns = r["run"]["turns"]
        total_cost = 0.0
        total_wall = 0.0
        t1_iters = "?"
        downstream_iters = 0
        for t in turns:
            total_wall += t.get("wall_time_s", 0)
            metrics = t.get("tool_loop_metrics") or {}
            if t["turn"] == 1:
                t1_iters = metrics.get("iterations", "?")
                total_cost += metrics.get("estimated_cost_usd", 0) or 0
            else:
                downstream_iters += metrics.get("iterations", 1)
                total_cost += metrics.get("estimated_cost_usd", 0) or 0
        lines.append(
            f"| {limit} | {len(turns)} | ${total_cost:.4f} | {total_wall:.1f} | {t1_iters} | {downstream_iters} |"
        )

    lines += [
        "",
        "## Analysis",
        "",
        "*(Populated after running the study — compare T1 iteration counts across*",
        "*conditions to determine whether higher limits reduce iteration count.)*",
        "",
        "### Key question: at what limit does T1 stop hitting the cap?",
        "",
        "From the ABS-267 Haiku baseline, T1 hits `iteration_cap` at the default",
        "limit (5 at time of study). If a higher limit reduces T1 from `iteration_cap`",
        "to `end_turn`, that limit is the candidate default for AC-1B.7.",
        "",
        "### Cost-efficiency curve",
        "",
        "Higher limits reduce iteration count but increase per-call payload size",
        "(billed on every subsequent turn). The optimal limit minimises",
        "`T1_cost + downstream_cost` rather than just T1 iterations.",
        "",
        "## Candidate default for AC-1B.7",
        "",
        "*(Identify from the results table which limit produces the best*",
        "*iteration-count / cost tradeoff for the AC-1B.7 validation run.)*",
        "",
        "## Files",
        "",
        "- `raw_results.json` — full per-turn transcripts with tool_loop_metrics",
        "- `REPORT.md` — this file",
    ]

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
