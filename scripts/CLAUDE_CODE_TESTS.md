# Claude Code-driven MCP regression tests

This directory holds scripts that exercise the project's MCP servers via
`claude -p` (Claude Code's headless mode). They use the developer's Max
subscription instead of API tokens, so they're safe to run when the
Anthropic API budget is exhausted but a real-model regression check is
still needed.

## When to use

Reach for these tests when:

- A change to an MCP tool's *response shape* could push Claude into a
  thrashing tool-use loop (multiple variant calls, max_iterations).
- A unit test or Playwright e2e can prove the wire contract but cannot
  prove that *the model itself* converges on the new contract.
- Anthropic API budget is unavailable but `claude` CLI + a Max
  subscription are.

These tests are **not** a replacement for `make e2e` — they don't cover
the FastAPI advisor's tool loop, billing, persistence, or any UI. They
cover only the MCP-protocol layer.

## Available tests

### `test_lookup_citation_via_claude_code.py` — ABS-261

Confirms that the post-fix `lookup_citation` (which returns
`{match, suggestions}` instead of raising on path miss) lets the model
self-correct in 1-2 calls rather than thrashing to the advisor's
`max_iterations=10` ceiling. See the docstring at the top of the script
for the full background.

#### Prerequisites

- `claude` CLI on PATH and logged into a Max subscription
  (`claude /login` if needed).
- Dev Postgres running on `localhost:5432` with the Halifax Regional
  Centre LUB ingest at `document_id=4`. The standard local dev stack
  (`docker compose up postgres`) is sufficient — the FastAPI advisor is
  **not** required.
- The worktree's venv has the `mcp` extra installed:
  `./.venv/bin/pip install -e ".[mcp]"`.

#### Run

```bash
./.venv/bin/python scripts/test_lookup_citation_via_claude_code.py
```

Useful flags:

- `--only table_1a section_9` — restrict to specific prompt IDs.
- `--timeout 240` — per-prompt timeout in seconds (default 180).
- `--run-dir path/to/dir` — override the auto-timestamped output dir.

Exit code: `0` when every prompt converged in ≤2 `lookup_citation`
calls with a clean `end_turn` stop_reason; `1` on regression; `2` on
environment problems (claude missing, DB unreachable, MCP import
broken).

#### Output

Each run writes to
`evals/runs/abs-261-claude-code-regression/<UTC-timestamp>/`:

- `<prompt_id>.stream.jsonl` — raw stream-json events from `claude -p`,
  one event per line. Useful for post-mortem.
- `<prompt_id>.stderr.txt` — stderr capture, only present if non-empty.
- `summary.json` — machine-readable per-prompt + aggregate verdict.
- `summary.txt` — short human-readable report.

#### What "pass" means

A prompt passes when:

1. The model called `lookup_citation` at least once but no more than
   twice. (Pre-fix the advisor's loop would re-try the same tool with
   minor variations up to ~10 times.)
2. The run terminated with `stop_reason == "end_turn"` and
   `is_error == false`.
3. For mis-formatted-path prompts (`expects_suggestions: True`), at
   least one `lookup_citation` tool_result carried a non-empty
   `suggestions` array — proving the new self-correct path actually
   fired.

The exact-path prompt (`exact_part_x_398`) is the inverse control: it
should resolve in a single call with no suggestions.

## Things you should not assume

- These scripts intentionally do **not** count tokens or estimate cost.
  `claude -p` reports per-run usage in its `result` event, but Max
  subscriptions are flat-rate, so the only thing the harness checks is
  convergence.
- The committed MCP config at
  `scripts/claude-code-mcp-configs/bylaw-retrieval.template.json` uses
  `${WORKTREE_ROOT}` / `${DATABASE_URL}` placeholders. The script
  renders an absolute-path copy into the run directory on every
  invocation, so the template stays portable across worktrees.
- `--latest-only` is deliberately **off** so the model can target
  `document_id=4` (the real Regional Centre LUB ingest) rather than
  whichever bylaw was ingested last (often the E2E seed bylaw).
