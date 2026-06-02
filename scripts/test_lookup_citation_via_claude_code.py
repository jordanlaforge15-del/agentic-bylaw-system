"""ABS-261 regression harness — drive the bylaw_retrieval MCP via `claude -p`.

Background
----------
Before ABS-261, ``RetrievalService.lookup_citation`` raised ``ValueError`` whenever
the caller's ``citation_path`` did not match the stored canonical form (e.g. the
model said ``"Section 4.2"`` while the corpus stored ``"4.2"``). The advisor's
tool-use loop interpreted the ValueError as a soft failure and re-tried with
slight variations, often running to its ``max_iterations=10`` ceiling and
burning ~2x the necessary Anthropic tokens.

The fix changes ``lookup_citation`` to return a ``CitationLookupResponse``
envelope with ``match`` (the hit or ``None``) and ``suggestions`` (up to 8
nearest stored ``citation_path`` values via rapidfuzz WRatio, cutoff 30). When
the model probes a mis-formatted path, it now sees the canonical form in
``suggestions`` and can self-correct on the next call.

This harness proves that fix at the MCP-protocol layer — *without* burning
Anthropic API tokens, because we drive ``claude -p`` (Claude Code's headless
mode) and let it use the user's Max subscription. It is the only path the user
has for an end-to-end regression check while their API budget is exhausted.

For each test prompt we:

1. Invoke ``claude -p`` with ``--mcp-config`` pointing at this worktree's
   bylaw_retrieval MCP and ``--output-format stream-json --verbose``.
2. Parse the stream of JSON events and count ``lookup_citation`` calls,
   detect whether ``suggestions`` surfaced in any tool_result, and confirm
   the model produced a final assistant message (no terminal_reason failure).
3. PASS criterion: total lookup_citation calls <= 2 AND the run terminated
   with stop_reason ``end_turn`` (not ``max_turns`` / ``error``). When the
   prompt intentionally uses a mis-formatted path, we additionally require
   that ``suggestions`` appeared in at least one tool_result — that is the
   self-correction loop the fix is supposed to enable.

Run with::

    python scripts/test_lookup_citation_via_claude_code.py

Exit code is 0 when every prompt passes, 1 when any regresses, 2 on
environment errors (claude CLI missing, DB unreachable, etc.).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


WORKTREE_ROOT = Path(__file__).resolve().parent.parent
MCP_CONFIG_TEMPLATE = (
    WORKTREE_ROOT
    / "scripts"
    / "claude-code-mcp-configs"
    / "bylaw-retrieval.template.json"
)
# Rendered (absolute-path) config lives under the auto-generated run dir so
# the worktree itself never accumulates per-run artifacts and the rendered
# JSON is co-located with the captures it produced.
DEFAULT_DATABASE_URL = "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"

# Tools we expose to claude -p. We deliberately do NOT include
# evaluate_submission_against_bylaws or any non-MCP tools — we want the model
# to use lookup_citation / search_bylaw_evidence and nothing else.
ALLOWED_TOOLS = ",".join(
    [
        "mcp__bylaw_retrieval__list_documents",
        "mcp__bylaw_retrieval__get_document_outline",
        "mcp__bylaw_retrieval__lookup_citation",
        "mcp__bylaw_retrieval__search_bylaw_evidence",
    ]
)

# Short system prompt — we do not need to replicate the full advisor persona.
# Goal: tell the model it has the MCP and which tool to reach for first.
SYSTEM_PROMPT = (
    "You have access to the bylaw_retrieval MCP, which exposes "
    "list_documents, get_document_outline, lookup_citation, and "
    "search_bylaw_evidence over the Halifax Regional Centre Land Use "
    "By-law (document_id=4). When the user references a specific "
    "citation path (a Part / section / table / appendix identifier), "
    "call lookup_citation first with document_id=4. "
    "If lookup_citation returns match=null but suggestions is non-empty, "
    "pick the closest suggestion and call lookup_citation ONE more time "
    "with that exact canonical path — do not keep guessing variants. "
    "If lookup_citation returns match=null and suggestions is empty, "
    "fall back to search_bylaw_evidence. Answer concisely; do not call "
    "lookup_citation more than twice for a single user turn."
)


# Each prompt deliberately uses a citation form the model produced during
# ABS-260 against doc 4. The "expects_suggestions" flag means we believe the
# *first* lookup_citation call will miss and require the suggestions
# self-correct path. "exact_match" prompts use the canonical form and should
# resolve in a single call.
TEST_PROMPTS: list[dict[str, Any]] = [
    {
        "id": "table_1a",
        "prompt": (
            "What does Table 1A in the Halifax Regional Centre Land Use "
            "By-law cover? Use lookup_citation against document_id=4."
        ),
        "expects_suggestions": True,
    },
    {
        "id": "section_9",
        "prompt": (
            "What is Section 9 of the Halifax Regional Centre Land Use "
            "By-law about? Use lookup_citation with document_id=4."
        ),
        "expects_suggestions": True,
    },
    {
        "id": "part_iii",
        "prompt": (
            "Summarize Part III of the Halifax Regional Centre Land Use "
            "By-law. Use lookup_citation with document_id=4."
        ),
        "expects_suggestions": True,
    },
    {
        "id": "part_xvii",
        "prompt": (
            "What does Part XVII of the Halifax Regional Centre Land Use "
            "By-law cover? Use lookup_citation with document_id=4."
        ),
        "expects_suggestions": True,
    },
    {
        "id": "exact_part_x_398",
        "prompt": (
            "Look up the canonical citation 'Part X > 398' in the Halifax "
            "Regional Centre Land Use By-law (document_id=4) and tell me "
            "what it says."
        ),
        "expects_suggestions": False,
    },
]


@dataclass
class PromptResult:
    id: str
    prompt: str
    expects_suggestions: bool
    lookup_calls: int = 0
    lookup_paths_tried: list[str] = field(default_factory=list)
    suggestions_seen: bool = False
    suggestions_samples: list[list[str]] = field(default_factory=list)
    other_tool_calls: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    terminal_reason: str | None = None
    is_error: bool = False
    final_text: str = ""
    duration_ms: int | None = None
    num_turns: int | None = None
    passed: bool = False
    notes: list[str] = field(default_factory=list)


def _fatal(msg: str, code: int = 2) -> None:
    print(f"[fatal] {msg}", file=sys.stderr)
    sys.exit(code)


def render_mcp_config(run_dir: Path, database_url: str) -> Path:
    """Render the MCP config template with worktree-absolute paths.

    The committed template uses ``${WORKTREE_ROOT}`` / ``${DATABASE_URL}``
    placeholders so it is portable across worktrees; we resolve those here
    and write the rendered JSON next to the run's captures.
    """
    if not MCP_CONFIG_TEMPLATE.exists():
        _fatal(f"MCP config template missing: {MCP_CONFIG_TEMPLATE}")
    raw = MCP_CONFIG_TEMPLATE.read_text()
    rendered = (
        raw.replace("${WORKTREE_ROOT}", str(WORKTREE_ROOT))
        .replace("${DATABASE_URL}", database_url)
    )
    target = run_dir / "mcp-config-bylaw.rendered.json"
    target.write_text(rendered)
    return target


def smoke_check(mcp_config: Path) -> None:
    """Confirm prerequisites before running prompts."""
    if shutil.which("claude") is None:
        _fatal("`claude` CLI not found on PATH. Install Claude Code first.")
    if not mcp_config.exists():
        _fatal(f"Rendered MCP config missing: {mcp_config}")

    cfg = json.loads(mcp_config.read_text())
    server = cfg.get("mcpServers", {}).get("bylaw_retrieval", {})
    cmd_path = server.get("command")
    if not cmd_path or not Path(cmd_path).exists():
        _fatal(f"layer1-mcp binary missing at {cmd_path!r}")

    # Probe Postgres on 5432 — the MCP needs it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        try:
            s.connect(("127.0.0.1", 5432))
        except OSError as exc:
            _fatal(f"Cannot reach Postgres at localhost:5432 ({exc})")

    # Briefly launch the MCP via stdin EOF to make sure the import graph is
    # healthy and the DB connect succeeds.
    env = os.environ.copy()
    server_env = server.get("env") or {}
    db_url = server_env.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    env.setdefault("DATABASE_URL", db_url)
    # Launch with stdin redirected from /dev/null so the server hits EOF
    # immediately and we can observe whether the import graph is healthy.
    proc = subprocess.Popen(
        [cmd_path] + server.get("args", []),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        try:
            stdout, stderr = proc.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            # Server is alive and waiting on stdio — that's success.
            proc.terminate()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
            return
        tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()[-10:]
        joined = "\n".join(tail)
        if "ModuleNotFoundError" in joined or "Traceback" in joined:
            _fatal("MCP server failed to import:\n" + joined)
    finally:
        if proc.poll() is None:
            proc.kill()


def _events_from_stream_json(blob: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # Some lines may be free-form CLI banner text; ignore.
            continue
    return events


def _tool_calls_in_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool_use blocks from an assistant message."""
    content = msg.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    return [
        block for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _tool_results_in_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool_result blocks from a user message."""
    content = msg.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    return [
        block for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]


def _parse_tool_result_payload(block: dict[str, Any]) -> Any:
    """Tool results in stream-json mode arrive as a list of text blocks
    whose ``text`` field carries the JSON-serialized MCP tool response.
    """
    content = block.get("content")
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    if isinstance(content, list):
        for inner in content:
            if isinstance(inner, dict) and inner.get("type") == "text":
                text = inner.get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    continue
    return None


def analyze_stream(blob: str, result: PromptResult) -> None:
    events = _events_from_stream_json(blob)
    # Map tool_use_id -> tool_name (so we can credit suggestions to the
    # right tool when we see the matching tool_result).
    tool_use_index: dict[str, str] = {}

    for ev in events:
        et = ev.get("type")
        if et == "assistant":
            for tu in _tool_calls_in_message(ev):
                name = tu.get("name", "")
                tool_use_index[tu.get("id", "")] = name
                if name == "mcp__bylaw_retrieval__lookup_citation":
                    result.lookup_calls += 1
                    args = tu.get("input", {}) or {}
                    path = args.get("citation_path")
                    if isinstance(path, str):
                        result.lookup_paths_tried.append(path)
                elif name.startswith("mcp__bylaw_retrieval__"):
                    result.other_tool_calls[name] = (
                        result.other_tool_calls.get(name, 0) + 1
                    )
                elif name:
                    result.other_tool_calls[name] = (
                        result.other_tool_calls.get(name, 0) + 1
                    )
            # Capture the latest assistant text as the running "final".
            content = ev.get("message", {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    result.final_text = block.get("text", "") or result.final_text
        elif et == "user":
            for tr in _tool_results_in_message(ev):
                tool_id = tr.get("tool_use_id", "")
                tool_name = tool_use_index.get(tool_id, "")
                if tool_name != "mcp__bylaw_retrieval__lookup_citation":
                    continue
                payload = _parse_tool_result_payload(tr)
                if isinstance(payload, dict):
                    sugg = payload.get("suggestions")
                    if isinstance(sugg, list) and sugg:
                        result.suggestions_seen = True
                        result.suggestions_samples.append([str(x) for x in sugg])
        elif et == "result":
            result.stop_reason = ev.get("stop_reason")
            result.terminal_reason = ev.get("terminal_reason")
            result.is_error = bool(ev.get("is_error"))
            result.duration_ms = ev.get("duration_ms")
            result.num_turns = ev.get("num_turns")
            # If the model's final text is empty (e.g. it only emitted tool
            # calls), use the "result" field as a stand-in.
            if not result.final_text and isinstance(ev.get("result"), str):
                result.final_text = ev["result"]


def evaluate(result: PromptResult) -> None:
    """Set result.passed and append notes."""
    if result.is_error:
        result.notes.append(f"claude run reported is_error=true ({result.terminal_reason})")
    if result.stop_reason not in (None, "end_turn"):
        result.notes.append(f"stop_reason was {result.stop_reason!r} (expected 'end_turn')")
    if result.lookup_calls == 0:
        result.notes.append("model never called lookup_citation")
    elif result.lookup_calls > 2:
        result.notes.append(
            f"lookup_citation called {result.lookup_calls} times "
            "(post-fix should be 1-2; pre-fix would thrash to 10)"
        )
    if result.expects_suggestions and not result.suggestions_seen:
        result.notes.append(
            "prompt was expected to miss on first call and surface "
            "suggestions, but no tool_result carried a non-empty "
            "suggestions list"
        )

    converged = 1 <= result.lookup_calls <= 2
    finished = (not result.is_error) and result.stop_reason in (None, "end_turn")
    suggestions_ok = (not result.expects_suggestions) or result.suggestions_seen
    result.passed = converged and finished and suggestions_ok


def run_one(
    prompt_case: dict[str, Any],
    run_dir: Path,
    mcp_config: Path,
    timeout_sec: int,
) -> PromptResult:
    result = PromptResult(
        id=prompt_case["id"],
        prompt=prompt_case["prompt"],
        expects_suggestions=prompt_case["expects_suggestions"],
    )
    args = [
        "claude",
        "-p",
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--append-system-prompt",
        SYSTEM_PROMPT,
        "--allowedTools",
        ALLOWED_TOOLS,
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "stream-json",
        "--verbose",
        prompt_case["prompt"],
    ]

    raw_path = run_dir / f"{prompt_case['id']}.stream.jsonl"
    err_path = run_dir / f"{prompt_case['id']}.stderr.txt"

    print(f"[run] {prompt_case['id']}  -> {prompt_case['prompt'][:70]}")
    try:
        proc = subprocess.run(
            args,
            cwd=str(WORKTREE_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raw_path.write_text(exc.stdout or "")
        err_path.write_text((exc.stderr or "") + f"\n[TIMEOUT after {timeout_sec}s]")
        result.is_error = True
        result.terminal_reason = "timeout"
        result.notes.append(f"claude -p timed out after {timeout_sec}s")
        evaluate(result)
        return result

    raw_path.write_text(proc.stdout or "")
    if proc.stderr:
        err_path.write_text(proc.stderr)

    analyze_stream(proc.stdout or "", result)
    evaluate(result)
    return result


def _report_line(r: PromptResult) -> str:
    status = "PASS" if r.passed else "FAIL"
    sugg = "Y" if r.suggestions_seen else "n"
    return (
        f"  [{status}] {r.id:<22} lookup_calls={r.lookup_calls} "
        f"suggestions={sugg} stop={r.stop_reason} "
        f"tried={r.lookup_paths_tried}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Per-prompt timeout in seconds (default 180).",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help=(
            "Output directory for stream-json captures + summary. "
            "Defaults to evals/runs/abs-261-claude-code-regression/<UTC>/."
        ),
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="Run only the named prompt IDs (default: all).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help=(
            "Postgres URL used by the MCP server. Defaults to "
            "$DATABASE_URL if set, else the standard dev URL."
        ),
    )
    args = parser.parse_args()

    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = (
            WORKTREE_ROOT
            / "evals"
            / "runs"
            / "abs-261-claude-code-regression"
            / stamp
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    mcp_config = render_mcp_config(run_dir, args.database_url)
    smoke_check(mcp_config)

    cases = TEST_PROMPTS
    if args.only:
        wanted = set(args.only)
        cases = [c for c in TEST_PROMPTS if c["id"] in wanted]
        if not cases:
            _fatal(f"--only matched no prompt IDs (got {sorted(wanted)})")

    results: list[PromptResult] = []
    for case in cases:
        results.append(run_one(case, run_dir, mcp_config, args.timeout))

    summary = {
        "worktree": str(WORKTREE_ROOT),
        "mcp_config": str(mcp_config),
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "results": [asdict(r) for r in results],
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    lines = [
        "ABS-261 lookup_citation regression via claude -p",
        f"  run_dir: {run_dir}",
        f"  prompts: {len(results)}",
        f"  passed:  {summary['passed']}",
        f"  failed:  {summary['failed']}",
        "",
    ]
    for r in results:
        lines.append(_report_line(r))
        for note in r.notes:
            lines.append(f"        note: {note}")
    report = "\n".join(lines)
    (run_dir / "summary.txt").write_text(report + "\n")
    print()
    print(report)

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
