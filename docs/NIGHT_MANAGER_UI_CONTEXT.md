# Night Manager UI — Design Context

This document briefs a design agent on the Night Manager (NM) system so they
can create a monitoring/control UI for it. Read this alongside
[NIGHT_MANAGER.md](NIGHT_MANAGER.md) for the full operational reference.

## What the Night Manager is

The NM is a Python orchestrator that runs overnight. It pulls "Triaged" issues
from our Linear backlog, plans parallel execution, and spawns multiple Claude
Code AI agents — each working on a different issue in an isolated git worktree.
When agents finish, the NM reviews their code, runs end-to-end tests, and merges
passing work into the `dev` branch.

Today it runs headless in a tmux session. The operator launches it before bed
and reads a markdown report in the morning. There is no visual interface.

## Why a UI

The operator needs to:

1. **See what's happening** — which agents are running, which are done, which
   failed, and why. Today this requires tailing log files and reading JSON.
2. **Act on problems** — retry a failed agent, skip a blocked issue, abort the
   whole run. Today this requires killing tmux sessions and editing state files.
3. **Review the morning-after report** without reading raw markdown.
4. **Configure and launch** a run without memorizing CLI flags.

## Data model

All state lives in a single JSON file at `.night-manager/state.json`. The UI
should read this file (poll or watch) — it is the single source of truth. The
NM writes to it after every state transition.

### Run-level fields

```
run_id        string    "nm-20260523-2300"
started_at    ISO 8601  when the run began
config        object    {max_agents, label, model, deploy}
plan          array     ordered list of execution groups
```

### Execution plan

The plan is an ordered array of groups. Each group contains issues that run in
parallel. Groups execute sequentially — group 2 doesn't start until group 1 is
done.

```json
{
  "plan": [
    { "parallel": ["ABS-90", "ABS-91", "ABS-92"], "deploy": false },
    { "parallel": ["ABS-93"],                      "deploy": false },
    { "parallel": [],                              "deploy": true  }
  ]
}
```

A group with `"deploy": true` triggers the promotion + deployment phase.

### Issue states

Each issue progresses through a lifecycle:

```
queued → in_progress → reviewing → merged
                   ↘ failed
                   ↘ blocked
```

Per-issue fields:

| Field            | Type        | Description                                      |
|------------------|-------------|--------------------------------------------------|
| identifier       | string      | Linear issue ID, e.g. "ABS-90"                   |
| title            | string      | Issue title from Linear                           |
| status           | enum        | queued, in_progress, reviewing, merged, failed, blocked |
| branch           | string      | Git branch name, e.g. "agent/ABS-90-fix-login"   |
| worktree         | string      | Filesystem path to the git worktree               |
| ports.pg         | int         | Postgres port for this agent's test stack          |
| ports.api        | int         | FastAPI port                                       |
| ports.web        | int         | Next.js dev server port                            |
| session_id       | string      | Claude Code session UUID (for resume)              |
| pid              | int         | OS process ID of the agent                         |
| log_file         | string      | Path to the agent's JSONL log                      |
| attempts         | int         | How many times the agent has been (re)started      |
| review_attempts  | int         | How many code review cycles have run               |
| started_at       | ISO 8601    | When the agent started (null if queued)             |
| completed_at     | ISO 8601    | When the agent finished coding (null until done)   |
| merged_at        | ISO 8601    | When the branch was merged to dev (null until done)|
| error            | string|null | Error description if failed/blocked                |
| linear_id        | string      | Linear internal UUID (for API calls)               |

### Agent logs

Each agent writes a JSONL file at `.night-manager/logs/{identifier}.jsonl`.
Lines are Claude Code stream-json events — tool calls, assistant messages,
errors. The UI could stream these for a live activity feed per agent.

### NM log

The orchestrator itself logs to `.night-manager/night_manager.log` and
`.night-manager/nm-{timestamp}.log`. Plain text, one line per event.

### Reports

On completion, a markdown report is written to
`.night-manager/report-{timestamp}.md` summarizing merged/failed/blocked issues.

## Key screens to design

### 1. Dashboard (run overview)

The primary screen. Shows:

- **Run status bar** — run ID, started time, elapsed time, overall progress
  (e.g. "7/11 issues processed").
- **Execution plan timeline** — the groups visualized as sequential lanes. Each
  group is a row; issues within a group are side-by-side (they run in parallel).
  Color-code by status.
- **Issue cards** — one per issue, showing: identifier, title, status badge,
  elapsed time, attempt count. Click to drill into the issue detail view.
- **Aggregate stats** — agents running, queued, merged, failed, blocked.

Status colors:
- queued: gray
- in_progress: blue (with a pulse/spinner)
- reviewing: yellow/amber
- merged: green
- failed: red
- blocked: orange

### 2. Issue detail

Drill-down from dashboard. Shows:

- Issue metadata (identifier, title, branch, worktree path, port triplet).
- Status timeline (queued → started → completed → review → merged, with
  timestamps).
- **Live log stream** — tail the agent's JSONL log. Show tool calls as
  collapsible rows (tool name, args summary, duration). Show assistant text
  messages inline.
- Error panel (if failed/blocked) — full error text, prominently displayed.
- Actions: Retry (re-spawn the agent), Skip (mark as blocked and move on),
  Kill (send SIGTERM to the agent process).

### 3. Launch / configure

Form to start a new run:

- Max parallel agents (number input, default 3)
- Label filter (text, default "Triaged")
- Model (dropdown: opus, sonnet, haiku)
- Deploy after merge (toggle, default off)
- Single issue override (optional text input for an issue ID)
- Dry run toggle

Preview of matching issues (fetched from Linear when the label is entered)
before the user hits "Start."

### 4. Report viewer

Renders the `.night-manager/report-{timestamp}.md` as formatted HTML. List of
past reports by date, click to view. The current run's report appears here once
the run finishes.

## Interaction patterns

- **Polling**: The UI should poll `state.json` every 2-5 seconds during an
  active run. The file is small (<50KB even with many issues).
- **Agent logs**: Tail the JSONL file. New lines appear as the agent works.
  Expect bursts of activity followed by quiet periods (agent is thinking).
- **Actions**: The UI doesn't directly mutate `state.json`. Instead it invokes
  shell commands:
  - Start run: `./scripts/start-night-manager.sh [flags]`
  - Kill run: `tmux kill-session -t night-manager`
  - Retry issue: not yet supported (future: write a retry marker to state.json
    that the NM picks up)
  - Skip issue: not yet supported (future: same pattern)

## Constraints

- The NM runs on the operator's local Mac, not a server. The UI can be:
  - A local web app (Next.js dev server on a spare port)
  - A terminal TUI (if the design agent prefers that)
  - A native macOS app (Electron, Swift — heavier)
- The UI is single-user. No auth needed.
- The system manages 3-15 issues per run, rarely more.
- The UI should degrade gracefully when no run is active (show last report,
  offer to start a new run).

## Existing design system

The main product uses Next.js + Tailwind + shadcn/ui. If the UI is web-based,
reuse those conventions. The product lives at `web/` — the NM UI could be a
separate lightweight app or a route group within the existing app.

## Files to read

| What                    | Where                                           |
|-------------------------|-------------------------------------------------|
| NM operational docs     | `docs/NIGHT_MANAGER.md`                         |
| State data model        | `scripts/night_manager/state.py`                |
| Config / CLI flags      | `scripts/night_manager/config.py`               |
| Orchestrator flow       | `scripts/night_manager/main.py`                 |
| Agent lifecycle         | `scripts/night_manager/agent.py`                |
| Review/merge logic      | `scripts/night_manager/reviewer.py`             |
| Example state.json      | `.night-manager/state.json` (after any run)     |
| Example report          | `.night-manager/report-*.md` (after any run)    |
| Example agent log       | `.night-manager/logs/ABS-*.jsonl`               |
| Launch script           | `scripts/start-night-manager.sh`                |
