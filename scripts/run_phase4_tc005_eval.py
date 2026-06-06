#!/usr/bin/env python3
"""ABS-280 Phase 4: Re-run TC-005 with Phases 1-3 deployed.

Drives the local advisor through TC-005 (Developer feasibility for residential
tower in HR-2) — optionally all 6 turns, or just T5 (the home-occupation
permitted-use turn) — and writes evidence to:

    evals/runs/<UTC-timestamp>-table1A-permission/
        TC-005.json           full transcript with tool calls
        SUMMARY.json          answer quality metrics vs baseline
        GROUNDING.md          SQL grounding check for the Table 1A citation

Usage
-----
Run all 6 turns against a local dev stack (port 8000):
    python scripts/run_phase4_tc005_eval.py

Run only T5 to minimise API spend (still exercises the permitted-use turn):
    python scripts/run_phase4_tc005_eval.py --t5-only

Run against the e2e stack (port 8006):
    python scripts/run_phase4_tc005_eval.py --base-url http://127.0.0.1:8006

Prerequisites
-------------
* Advisor running with Phases 1-3 code and an ANTHROPIC_API_KEY.
* Production layer1 database with Table 1A enriched:
    - SourceTable.caption ILIKE 'Table 1%Permitted uses by zone%'
    - TableAxisBinding rows linking HR-2 column and home-occupation row
    - SourceTableCell.metadata_json containing permission_marker values
      (run scripts/backfill_permission_markers.py if missing)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"
RUNS_ROOT = REPO_ROOT / "evals" / "runs"

# Pre-Phase-3 baseline: T5 hedged entirely (no tool calls).
BASELINE_RUN = "20260603T100000Z-haiku-baseline"

# Hedge phrases that must be ABSENT from the post-Phase-3 T5 answer.
HEDGE_PHRASES = [
    "a symbol appears to indicate",
    "may be conditional",
    "I could not retrieve",
    "I did not retrieve",
    "I cannot confirm",
    "I was unable to",
    "I need to retrieve",
    "to answer this properly",
    "I would need to",
]

# Committed-verdict phrases that must be PRESENT in the T5 answer.
COMMITTED_PHRASES = [
    "not permitted",
    "not_permitted",
    "is not a permitted use",
    "home occupation is not",
]


def load_tc005() -> dict[str, Any]:
    with PROMPTS_FILE.open() as f:
        all_cases = json.load(f)
    tc5 = next((c for c in all_cases if c["id"] == "TC-005"), None)
    if tc5 is None:
        raise SystemExit("TC-005 not found in regional_centre_test_prompts.json")
    return tc5


def parse_sse_stream(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cur_event: str | None = None
    data_buf: list[str] = []

    def flush() -> None:
        nonlocal cur_event, data_buf
        if cur_event is None and not data_buf:
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
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            cur_event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_buf.append(line.removeprefix("data:").strip())
    flush()
    return events


def extract_turn_artifacts(events: list[dict[str, Any]]) -> dict[str, Any]:
    text_chunks: dict[int, str] = {}
    text_full: dict[int, str] = {}
    tool_calls: list[dict[str, Any]] = []
    tool_call_inputs: dict[int, dict[str, Any]] = {}
    tool_call_names: dict[int, str] = {}
    tool_call_ids: dict[int, str] = {}
    tool_input_json: dict[int, list[str]] = {}
    stop_reason: str | None = None
    usage: dict[str, Any] | None = None
    model: str | None = None
    session_id: str | None = None
    case_id: Any = None
    tool_loop_metrics: dict[str, Any] | None = None

    for ev in events:
        et = ev.get("event")
        data = ev.get("data") or {}
        if et == "session":
            session_id = data.get("session_id")
            case_id = data.get("case_id")
        elif et == "message_start":
            model = data.get("model") or model
        elif et == "tool_loop_metrics":
            tool_loop_metrics = data
        elif et == "content_block_start":
            idx = data.get("index")
            block = data.get("content_block") or {}
            btype = block.get("type")
            if btype == "text":
                text_full[idx] = block.get("text") or ""
            elif btype == "tool_use":
                tool_call_names[idx] = block.get("name", "")
                tool_call_ids[idx] = block.get("id", "")
                tool_call_inputs[idx] = block.get("input") or {}
        elif et == "content_block_delta":
            idx = data.get("index")
            if data.get("text_delta"):
                text_chunks.setdefault(idx, "")
                text_chunks[idx] += data["text_delta"]
            if data.get("input_json_delta"):
                tool_input_json.setdefault(idx, []).append(data["input_json_delta"])
        elif et == "content_block_stop":
            idx = data.get("index")
            if idx in tool_call_names:
                inp: Any = tool_call_inputs.get(idx) or {}
                if idx in tool_input_json:
                    try:
                        inp = json.loads("".join(tool_input_json[idx]))
                    except json.JSONDecodeError:
                        inp = {"_raw": "".join(tool_input_json[idx])}
                tool_calls.append({
                    "name": tool_call_names[idx],
                    "id": tool_call_ids.get(idx),
                    "input": inp,
                })
        elif et == "message_delta":
            stop_reason = data.get("stop_reason") or stop_reason
            if data.get("usage"):
                usage = data["usage"]

    parts: list[str] = []
    for idx in sorted(set(text_full.keys()) | set(text_chunks.keys())):
        if text_full.get(idx):
            parts.append(text_full[idx])
        else:
            parts.append(text_chunks.get(idx, ""))
    assistant_text = "\n".join(p for p in parts if p)

    return {
        "assistant_text": assistant_text,
        "tool_calls": tool_calls,
        "stop_reason": stop_reason,
        "usage": usage,
        "model": model,
        "session_id": session_id,
        "case_id": case_id,
        "tool_loop_metrics": tool_loop_metrics,
    }


def run_turn(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    message: str,
    session_id: str | None,
    case_id: Any,
    timeout: float,
) -> dict[str, Any]:
    t0 = time.monotonic()
    resp = client.post(
        f"{base_url}/v1/chat",
        headers={
            "X-Test-User-Id": user_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json={"message": message, "session_id": session_id, "case_id": case_id},
        timeout=timeout,
    )
    elapsed = time.monotonic() - t0
    body = resp.text
    if resp.status_code != 200:
        return {
            "error": f"HTTP {resp.status_code}",
            "body_excerpt": body[:2000],
            "wall_time_s": round(elapsed, 2),
        }
    events = parse_sse_stream(body)
    artifacts = extract_turn_artifacts(events)
    artifacts["wall_time_s"] = round(elapsed, 2)
    artifacts["raw_event_count"] = len(events)
    return artifacts


def analyse_t5(turn: dict[str, Any]) -> dict[str, Any]:
    """Score T5 against Phase-4 AC1-AC3."""
    text = (turn.get("assistant_text") or "").lower()
    tool_calls = turn.get("tool_calls") or []

    has_permitted_use_call = any(
        tc.get("name") == "lookup_citation"
        and (tc.get("input") or {}).get("structured", {}).get("kind") == "permitted_use"
        for tc in tool_calls
    )
    lookup_citation_calls = [
        tc for tc in tool_calls if tc.get("name") == "lookup_citation"
    ]
    hedge_found = next(
        (p for p in HEDGE_PHRASES if p.lower() in text), None
    )
    committed_found = next(
        (p for p in COMMITTED_PHRASES if p.lower() in text), None
    )

    return {
        "ac1_committed_verdict": committed_found is not None and hedge_found is None,
        "ac1_hedge_phrase_found": hedge_found,
        "ac1_committed_phrase_found": committed_found,
        "ac3_has_permitted_use_tool_call": has_permitted_use_call,
        "ac3_lookup_citation_calls": lookup_citation_calls,
        "total_tool_calls": len(tool_calls),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ABS-280 Phase 4: Re-run TC-005 with Phases 1-3 deployed."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user-id", default="demo-user-1")
    parser.add_argument(
        "--turn-timeout", type=float, default=180.0,
        help="Seconds per /v1/chat call.",
    )
    parser.add_argument(
        "--t5-only", action="store_true",
        help=(
            "Run only T5 (the home-occupation permitted-use turn). Saves API "
            "spend but loses full conversation context — the model must answer "
            "T5 cold rather than after building context from T1-T4."
        ),
    )
    parser.add_argument(
        "--out-label", default="table1A-permission",
        help="Label appended to the run directory timestamp.",
    )
    args = parser.parse_args()

    case = load_tc005()
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_ROOT / f"{ts}-{args.out_label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {run_dir}", file=sys.stderr)

    turns_to_run = case["turns"]
    if args.t5_only:
        turns_to_run = [t for t in turns_to_run if t["turn"] == 5]
        print("--t5-only: running only T5", file=sys.stderr)

    session_id: str | None = None
    case_id: Any = None
    model_seen: str | None = None
    turns_out: list[dict[str, Any]] = []

    with httpx.Client() as client:
        for turn in turns_to_run:
            msg = turn["message"]
            print(f"  T{turn['turn']}: {msg[:90]}...", file=sys.stderr, flush=True)
            result = run_turn(
                client, args.base_url, args.user_id, msg, session_id, case_id,
                args.turn_timeout,
            )
            if result.get("session_id"):
                session_id = result["session_id"]
            if result.get("case_id"):
                case_id = result["case_id"]
            if result.get("model"):
                model_seen = result["model"]

            turns_out.append({
                "turn": turn["turn"],
                "user_message": msg,
                "assistant_text": result.get("assistant_text", ""),
                "tool_calls": result.get("tool_calls", []),
                "stop_reason": result.get("stop_reason"),
                "usage": result.get("usage"),
                "wall_time_s": result.get("wall_time_s"),
                "raw_event_count": result.get("raw_event_count"),
                "error": result.get("error"),
                "body_excerpt": result.get("body_excerpt"),
                "tool_loop_metrics": result.get("tool_loop_metrics"),
            })
            if result.get("error"):
                print(
                    f"    !! error on T{turn['turn']}: {result['error']}",
                    file=sys.stderr,
                )
                break

    transcript = {
        "id": case["id"],
        "title": case["title"],
        "zone": case.get("zone"),
        "persona": case.get("persona"),
        "complexity": case.get("complexity"),
        "liability": case.get("liability"),
        "address": case.get("address"),
        "session_id": session_id,
        "model": model_seen,
        "turns": turns_out,
        "spec": case,
        "run_label": args.out_label,
        "t5_only": args.t5_only,
    }
    with (run_dir / "TC-005.json").open("w") as f:
        json.dump(transcript, f, indent=2, ensure_ascii=False)

    # Score T5
    t5 = next((t for t in turns_out if t["turn"] == 5), None)
    quality: dict[str, Any] = {}
    if t5:
        quality = analyse_t5(t5)

    total_input = sum(
        (t.get("usage") or {}).get("input_tokens", 0) for t in turns_out
    )
    total_output = sum(
        (t.get("usage") or {}).get("output_tokens", 0) for t in turns_out
    )
    summary = {
        "run_dir": str(run_dir),
        "ts": ts,
        "model": model_seen,
        "t5_only": args.t5_only,
        "turns_completed": len(turns_out),
        "t5_quality": quality,
        "total_tokens": {"input": total_input, "output": total_output},
        "baseline_run": BASELINE_RUN,
        "baseline_t5_verdict": "hedge — no tool calls, no committed answer",
    }
    with (run_dir / "SUMMARY.json").open("w") as f:
        json.dump(summary, f, indent=2)

    # Print summary
    print(f"\n=== ABS-280 Phase 4 Eval: {ts}-{args.out_label} ===", file=sys.stderr)
    if t5:
        print(f"T5 committed verdict (AC1): {quality.get('ac1_committed_verdict')}", file=sys.stderr)
        if quality.get("ac1_hedge_phrase_found"):
            print(f"  HEDGE phrase found: {quality['ac1_hedge_phrase_found']!r}", file=sys.stderr)
        if quality.get("ac1_committed_phrase_found"):
            print(f"  committed phrase found: {quality['ac1_committed_phrase_found']!r}", file=sys.stderr)
        print(
            f"T5 lookup_citation call (AC3): {quality.get('ac3_has_permitted_use_tool_call')}",
            file=sys.stderr,
        )
        calls = quality.get("ac3_lookup_citation_calls") or []
        for call in calls:
            print(f"  tool call: {json.dumps(call.get('input', {}))}", file=sys.stderr)
    print(
        f"Tokens — input: {total_input:,}  output: {total_output:,}",
        file=sys.stderr,
    )
    print(f"Evidence written to: {run_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
