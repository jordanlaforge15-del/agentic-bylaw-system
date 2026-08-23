#!/usr/bin/env python3
"""
Drive the local dev advisor with the Regional Centre test prompt corpus.

Reads ``evals/regional_centre_test_prompts.json``, streams each multi-turn
conversation through ``POST /v1/chat`` (SSE), and persists structured
transcripts under ``evals/runs/<timestamp>/TC-NNN.json``.

Usage:
  python scripts/run_test_prompts.py
  python scripts/run_test_prompts.py --ids TC-001 TC-005
  python scripts/run_test_prompts.py --base-url http://127.0.0.1:8000 \
    --user-id demo-user-1 --turn-timeout 120

Outputs (per case):
  evals/runs/<ts>/TC-NNN.json
    {
      "id": "TC-001",
      "title": "...",
      "zone": "ER-1",
      "complexity": "simple",
      "session_id": "...",
      "model": "claude-...",
      "turns": [
        {
          "turn": 1,
          "user_message": "...",
          "assistant_text": "...",
          "tool_calls": [
            {
              "name": "search_bylaw_evidence",
              "input": {"query": "side yard setback", "limit": 10},
              "result_excerpt": "{\\"total_matches\\": 12, \\"matches\\": [...",
              "result_chars": 41233,
              "result_truncated": true,
              "result_citations": ["s. 198", "Table 1B", ...],
              "is_error": false,
              "latency_ms": 349
            }
          ],
          "stop_reason": "end_turn",
          "usage": {"input_tokens": ..., "output_tokens": ...},
          "wall_time_s": 12.3
        }
      ],
      "spec": {<full test case for downstream verification>}
    }

The transcript is the input to scripts/verify_test_prompts.py (separate step).

What each ``parser_version`` guarantees about ``tool_calls``, and how to
use the payload fields to tell a retrieval gap from a synthesis gap on a
failing case: docs/EVAL_TRANSCRIPTS.md.
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

# ABS-459. Bump when a transcript field changes meaning. See the field note
# in ``run_case`` for what each version guarantees about ``tool_calls``.
TRANSCRIPT_PARSER_VERSION = 3


def load_prompts() -> list[dict[str, Any]]:
    with PROMPTS_FILE.open() as f:
        return json.load(f)


def parse_sse_stream(text: str) -> list[dict[str, Any]]:
    """Split a raw SSE response body into ordered ``{event, data}`` dicts.

    Ignores ``: ping`` heartbeats and other comment lines. Decodes each
    ``data:`` payload as JSON; if decoding fails we surface the raw
    string so the caller can still see it in the transcript.
    """
    events: list[dict[str, Any]] = []
    cur_event: str | None = None
    data_buf: list[str] = []

    def flush() -> None:
        nonlocal cur_event, data_buf
        if cur_event is None and not data_buf:
            return
        raw = "\n".join(data_buf)
        parsed: Any
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
            # SSE comment / heartbeat
            continue
        if line.startswith("event:"):
            cur_event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_buf.append(line.removeprefix("data:").strip())
    flush()
    return events


def extract_turn_artifacts(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull assistant text, tool calls, stop reason, usage from one turn's events.

    The dev advisor emits the *full* text in a ``content_block_start``
    payload (block.text) plus incremental ``content_block_delta``s.
    We prefer the full text from content_block_start and only fall back
    to concatenating deltas if that's missing.

    ``tool_calls`` comes from ``tool_loop_metrics`` in practice, not from
    the content stream — see the ABS-459 note below the event loop. Since
    ABS-517 those entries carry the call's input and a bounded excerpt of
    its result, which is what makes a failing case diagnosable.
    """
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
    # ABS-266: per-turn tool-loop rollup emitted after the content
    # stream (one event per user message). Carries iterations,
    # per-iteration usage, terminated_reason, and per-tool-call
    # latency / error state. Captured verbatim so downstream scripts
    # can reason about loop behaviour without re-deriving from logs.
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
                # Finalize the tool_use block.
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
                    # A tool_use block carries the request, never the
                    # response — tool_result blocks are what the client
                    # sends back, and the advisor never streams those.
                    # Result fields are declared so every entry has one
                    # shape regardless of which source produced it.
                    "result_excerpt": None,
                    "result_chars": None,
                    "result_truncated": False,
                    "result_citations": [],
                    "source": "content_stream",
                })
        elif et == "message_delta":
            stop_reason = data.get("stop_reason") or stop_reason
            if data.get("usage"):
                usage = data["usage"]

    # Assemble final assistant text: prefer content_block_start full
    # text when present (the dev backend emits this); otherwise stitch
    # together the deltas in index order.
    parts: list[str] = []
    for idx in sorted(set(text_full.keys()) | set(text_chunks.keys())):
        if text_full.get(idx):
            parts.append(text_full[idx])
        else:
            parts.append(text_chunks.get(idx, ""))
    assistant_text = "\n".join(p for p in parts if p)

    # ABS-459: the harvest above is empty on EVERY backend, always.
    #
    # ``advisor.chat.session`` synthesises the SSE content stream from the
    # tool loop's *final* response (session.py:415). By construction that
    # response holds no ``tool_use`` blocks — the loop has already run to
    # ``end_turn`` before the first SSE byte is emitted. So watching
    # ``content_block_start`` for ``tool_use`` can never see the calls the
    # loop actually dispatched.
    #
    # ABS-266 added ``tool_loop_metrics`` for precisely this blind spot; it
    # is the only record of the loop's internals. Fall back to it.
    #
    # The content-stream harvest still wins whenever it has entries: those
    # carry each call's real ``tool_use`` id alongside its input. A backend
    # that someday streams real ``tool_use`` blocks therefore keeps the
    # richer identity without changing this code.
    #
    # ABS-517: ``ToolCallMetric`` now also carries the call's arguments and
    # a bounded excerpt of its result, so the fallback path is no longer
    # name-and-latency-only. ``.get`` throughout, with the pre-ABS-517
    # values as defaults, keeps the runner working against an advisor that
    # predates the change — it just records nulls again, exactly as before.
    if not tool_calls and tool_loop_metrics:
        for metric in tool_loop_metrics.get("tool_calls") or []:
            if not isinstance(metric, dict):
                continue
            tool_calls.append({
                "name": metric.get("name", ""),
                "id": None,
                # Null rather than {} so consumers can distinguish "no
                # input recorded" (old advisor) from "called with {}".
                "input": metric.get("input"),
                "is_error": bool(metric.get("is_error", False)),
                "latency_ms": metric.get("latency_ms"),
                # Head of the tool's output, its full pre-truncation
                # length, and every citation the result named. Together
                # these answer the question the transcript could not
                # answer before ABS-517: did this provision come back
                # from the tool, or did the answer drop it?
                "result_excerpt": metric.get("result_excerpt"),
                "result_chars": metric.get("result_chars"),
                "result_truncated": bool(metric.get("result_truncated", False)),
                "result_citations": list(metric.get("result_citations") or []),
                "source": "tool_loop_metrics",
            })

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
    """Send one user message and return the parsed turn artifacts."""
    t0 = time.monotonic()
    resp = client.post(
        f"{base_url}/v1/chat",
        headers={
            "X-Test-User-Id": user_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json={
            "message": message,
            "session_id": session_id,
            "case_id": case_id,
        },
        timeout=timeout,
    )
    elapsed = time.monotonic() - t0
    body = resp.text
    if resp.status_code != 200:
        return {
            "error": f"HTTP {resp.status_code}",
            "body_excerpt": body[:2000],
            "wall_time_s": elapsed,
        }
    events = parse_sse_stream(body)
    artifacts = extract_turn_artifacts(events)
    artifacts["wall_time_s"] = round(elapsed, 2)
    artifacts["raw_event_count"] = len(events)
    return artifacts


def summarise_case_result(
    case: dict[str, Any],
    result: dict[str, Any],
    wall: float,
) -> dict[str, Any]:
    """Build one SUMMARY.json row from a finished case transcript.

    Split out of ``main`` so the counting is testable without a running
    advisor (ABS-459). ``tool_calls`` is the total across the case's turns
    and must equal the number of entries the transcript itself carries —
    that equality is the contract the ABS-459 Playwright invariant checks
    against committed artifacts.

    ``error`` is reported only when the case produced no turns at all; a
    case that failed partway still has usable transcript data, and the
    per-turn ``error`` field carries the detail.

    ABS-517 adds ``tool_calls_with_input``. A run against an advisor that
    predates the payload fields still succeeds and still counts its calls,
    but produces transcripts nobody can do RCA on — the failure ABS-517
    exists to end. Counting the calls that actually carry an input lets
    ``main`` say so at the end of the run instead of letting it be
    discovered days later by whoever opens the transcript.
    """
    turns = result.get("turns") or []
    n_turns = len(turns)
    calls = [c for t in turns for c in (t.get("tool_calls") or [])]
    n_tool = len(calls)
    n_with_input = sum(
        1 for c in calls if isinstance(c, dict) and c.get("input") is not None
    )
    return {
        "id": case["id"],
        "title": case["title"],
        "complexity": case.get("complexity"),
        "turns_completed": n_turns,
        "turns_expected": len(case["turns"]),
        "tool_calls": n_tool,
        "tool_calls_with_input": n_with_input,
        "wall_s": wall,
        "error": result.get("error") if not n_turns else None,
    }


def run_case(
    client: httpx.Client,
    base_url: str,
    user_id: str,
    case: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    session_id: str | None = None
    case_id: Any = None
    model_seen: str | None = None
    turns_out: list[dict[str, Any]] = []
    for turn in case["turns"]:
        msg = turn["message"]
        print(f"  T{turn['turn']}: {msg[:90]}...", file=sys.stderr, flush=True)
        result = run_turn(client, base_url, user_id, msg, session_id, case_id, timeout)
        # Thread session forward.
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
            # ABS-266: per-turn tool-loop observability (iterations,
            # terminated_reason, per-iteration usage, per-tool-call
            # error/latency). ``None`` for servers that haven't been
            # upgraded to emit the event — runner stays backward
            # compatible with older deployments.
            "tool_loop_metrics": result.get("tool_loop_metrics"),
        })
        if result.get("error"):
            print(f"    !! error on turn {turn['turn']}: {result['error']}", file=sys.stderr)
            # Stop the case on first error — later turns depend on prior context.
            break
    return {
        "id": case["id"],
        # ABS-459: transcript schema version.
        #
        #   (absent) — written before ABS-459. ``tool_calls`` on every turn
        #              is unreliable: it was harvested from the synthetic SSE
        #              content stream, which structurally cannot carry
        #              tool_use blocks, so it reads [] no matter what the
        #              loop dispatched. Read ``tool_loop_metrics`` instead.
        #   2        — ``tool_calls`` falls back to ``tool_loop_metrics`` and
        #              can be trusted. Each entry names the tool and reports
        #              its error state and latency, and NOTHING ELSE:
        #              ``input`` is always null and there is no result. A
        #              v2 transcript can say a tool ran but not what it was
        #              asked or what came back, so it cannot distinguish a
        #              retrieval gap from a synthesis gap (ABS-517).
        #   3        — ABS-517. Each entry additionally guarantees:
        #                ``input``            the arguments the model passed,
        #                                     long string values truncated.
        #                                     Null ONLY if the advisor
        #                                     predates ABS-517.
        #                ``result_excerpt``   head of the tool's output (or
        #                                     its error text when
        #                                     ``is_error``); null when the
        #                                     advisor has result capture
        #                                     switched off.
        #                ``result_chars``     full output length before
        #                                     truncation.
        #                ``result_truncated`` whether the excerpt is a prefix.
        #                ``result_citations`` every citation the result named,
        #                                     in result (rank) order — the
        #                                     field that settles "was this
        #                                     provision retrieved?" even when
        #                                     the excerpt stops short of it.
        #
        # Version 3 is a pure superset of 2: every v2 field keeps its
        # meaning, so a v2-era consumer reads a v3 transcript unchanged and
        # a v3-era consumer reads a v2 transcript as "payloads not recorded".
        #
        # Consumers that assert on tool_calls must gate on this rather than
        # allowlisting run directories, which would go stale on every run.
        "parser_version": TRANSCRIPT_PARSER_VERSION,
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
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Regional Centre test prompts against the local dev advisor.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user-id", default="demo-user-1")
    parser.add_argument("--turn-timeout", type=float, default=180.0, help="Seconds per /v1/chat call (Opus + tool use can be slow).")
    parser.add_argument("--ids", nargs="*", help="Optional subset of TC IDs to run.")
    parser.add_argument("--out-dir", help="Override output directory. Defaults to evals/runs/<UTC timestamp>/.")
    parser.add_argument(
        "--model",
        help=(
            "Expected main chat model (e.g. claude-haiku-4-5). When set, "
            "the runner pings /healthz before any spend and aborts if "
            "the live advisor reports a different model. Set "
            "ADVISOR_LLM_MAIN_MODEL on the advisor process to actually "
            "switch the model — this flag only verifies."
        ),
    )
    args = parser.parse_args()

    cases = load_prompts()
    if args.ids:
        wanted = set(args.ids)
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            parser.error(f"No cases matched IDs: {args.ids}")

    # ABS-267: model-precondition check. We assert against /healthz
    # BEFORE spending money on a run that would otherwise hit the
    # wrong model (e.g. a Haiku-baseline command accidentally exercising
    # the still-configured Opus stack). Healthz is unauthenticated, so
    # this works even when CLERK is on.
    if args.model:
        try:
            r = httpx.get(f"{args.base_url}/healthz", timeout=10.0)
            r.raise_for_status()
            live_model = (r.json().get("llm") or {}).get("main_model")
        except httpx.HTTPError as exc:
            parser.error(
                f"--model precondition: could not read /healthz from "
                f"{args.base_url}: {exc}"
            )
        if live_model != args.model:
            parser.error(
                f"--model precondition: advisor reports main_model="
                f"{live_model!r}, but --model={args.model!r} was requested. "
                "Set ADVISOR_LLM_MAIN_MODEL on the advisor process and "
                "restart before re-running."
            )
        print(
            f"model precondition OK: {args.base_url} is serving {live_model}",
            file=sys.stderr,
        )

    if args.out_dir:
        out_dir = Path(args.out_dir).resolve()
    else:
        ts = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        out_dir = RUNS_ROOT / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing transcripts to {out_dir}", file=sys.stderr)

    summary: list[dict[str, Any]] = []
    with httpx.Client() as client:
        for case in cases:
            print(f"==> {case['id']}: {case['title']}", file=sys.stderr, flush=True)
            t0 = time.monotonic()
            try:
                result = run_case(client, args.base_url, args.user_id, case, args.turn_timeout)
            except httpx.HTTPError as exc:
                print(f"    !! transport error: {exc}", file=sys.stderr)
                result = {
                    "id": case["id"],
                    "title": case["title"],
                    "error": f"transport: {type(exc).__name__}: {exc}",
                    "spec": case,
                }
            wall = round(time.monotonic() - t0, 2)
            result["case_wall_time_s"] = wall
            out_path = out_dir / f"{case['id']}.json"
            with out_path.open("w") as f:
                json.dump(result, f, indent=2)
            row = summarise_case_result(case, result, wall)
            summary.append(row)
            print(
                f"    {row['turns_completed']}/{row['turns_expected']} turns, "
                f"{row['tool_calls']} tool calls, {wall}s",
                file=sys.stderr,
            )

    summary_path = out_dir / "SUMMARY.json"
    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nRun complete: {len(summary)} cases, summary at {summary_path}", file=sys.stderr)

    # ABS-517: a run whose transcripts carry no tool inputs is not
    # diagnosable, and the run itself looks completely healthy — it
    # reports its turns and its call counts as usual. Say so here rather
    # than letting it surface when someone tries to RCA a failure days
    # later and finds nulls where the payloads should be.
    dispatched = sum(row.get("tool_calls", 0) for row in summary)
    with_input = sum(row.get("tool_calls_with_input", 0) for row in summary)
    if dispatched and not with_input:
        print(
            "\nWARNING: none of the "
            f"{dispatched} recorded tool calls carry an input payload.\n"
            "  The advisor at "
            f"{args.base_url} predates ABS-517 (or has result capture off).\n"
            "  These transcripts cannot distinguish a retrieval gap from a\n"
            "  synthesis gap. Upgrade the advisor and re-run before doing RCA.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
