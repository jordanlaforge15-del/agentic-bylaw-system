# Night Manager

The Night Manager (NM) is an overnight orchestrator that takes a set of triaged
Linear issues and develops them in parallel using multiple Claude Code instances.
It plans execution, spawns agents in isolated worktrees, monitors progress,
reviews output, gates merges with code review + e2e tests, and optionally
promotes to prod — all unattended.

## Quick start

```bash
# Dry run — plan but don't execute
./scripts/start-night-manager.sh --dry-run

# Run with defaults (3 parallel agents, "Triaged" label)
./scripts/start-night-manager.sh

# Single issue
./scripts/start-night-manager.sh --issue ABS-90

# Full pipeline including deployment
./scripts/start-night-manager.sh --max-agents 2 --deploy
```

The launch script wraps the NM in `caffeinate` (prevents macOS sleep) and `tmux`
(survives terminal disconnect).

## Prerequisites

- `LINEAR_API_KEY` in `.env` (Linear Settings → API → Personal API keys)
- Python venv at `.venv/` with project deps (`./scripts/dev-setup.sh`)
- `tmux` installed (`brew install tmux`)
- Docker running (for Postgres containers per worktree)

## How it works

### 1. Issue selection

The NM queries Linear for issues with the configured label (default: `Triaged`)
in `backlog` or `unstarted` state, ordered by backlog priority. Use `--issue
ABS-XX` to override and run a single issue.

### 2. Planning

For multiple issues, the NM calls Claude (Sonnet) to analyze issue descriptions
for file/feature conflicts. Issues that touch independent areas of the codebase
run in parallel; issues likely to edit the same files are placed in sequential
groups. Group size is capped at `--max-agents` (default: 3).

The plan is written to `.night-manager/state.json` and posted as a comment on
each Linear issue.

### 3. Execution

For each parallel group:

1. **Worktree setup** — `git worktree add` off `dev`, then `dev-setup.sh
   --skip-db`, `pip install -e ".[advisor]"`, and `npm install` in each
   worktree.
2. **Agent launch** — Each issue gets a `claude -p` subprocess with:
   - `--output-format stream-json` for monitoring
   - `--permission-mode acceptEdits` + scoped `--allowedTools` for safety
   - `--append-system-prompt` constraining the agent to its worktree
   - Port triplet env vars for isolated e2e stacks
3. **Monitoring** — The NM reads each agent's JSON stream via asyncio, tracking
   tool calls and detecting stuck agents (no activity for 10 minutes → kill and
   resume).
4. **Linear updates** — Progress comments posted at start, completion, review
   feedback, merge, and failure.

### 4. Review & merge

When an agent exits successfully:

1. **Code review** — A `claude -p` subprocess reviews `git diff dev...HEAD`,
   checking for correctness bugs, security issues, and breaking changes. If
   findings are high-severity, feedback is sent to the dev agent (resumed via
   `--resume <session-id>`). Max 3 review cycles.
2. **E2E gate** — `make e2e` runs in the worktree with the issue's port triplet.
   If tests fail, the dev agent gets the failure output and retries. Max 3 fix
   cycles.
3. **Migration rechain** — Before rebasing, `scripts/rechain_migration.py` runs
   automatically if the branch touches `alembic/versions/`. It detects dual-head
   collisions (two branches both parented to the same `down_revision`) and fixes
   the feature branch's root migration to point to dev's current head. See
   [Alembic collision resistance](#alembic-collision-resistance) below.
4. **Rebase** — The agent branch is rebased onto the current `origin/dev` tip to
   absorb sibling merges.
5. **Merge** — Sequential `git merge --no-ff` into `dev`, one issue at a time.
6. **Regression check** — `make e2e` on `dev` after each merge. If regression
   detected, the merge is reverted and the issue marked as blocked.
7. **Cleanup** — Worktree and branch removed after successful merge.

### 5. Deployment (optional)

With `--deploy`, after all issues are merged the NM invokes the
`test-and-deploy-bylaw` skill to promote `dev → main`, build images, and deploy
to prod. Without the flag, it stops at "all issues merged to dev."

### 6. Reporting

On completion, the NM writes `.night-manager/report-{timestamp}.md` with:
- Issues merged, failed, and blocked
- Any items requiring human attention

## CLI reference

```
python -m scripts.night_manager [OPTIONS]

Options:
  --max-agents N    Max parallel dev agents (default: 3)
  --label LABEL     Linear label to filter issues (default: Triaged)
  --model MODEL     Claude model for dev agents (default: opus)
  --dry-run         Plan but don't execute — no agents spawned
  --deploy          Enable full pipeline: promote dev→main, build, deploy
  --issue ID        Run a single issue by identifier (e.g. ABS-90)
```

## Monitoring a run

```bash
# Attach to the NM session
tmux attach -t night-manager

# Tail the NM log
tail -f .night-manager/nm-*.log

# Tail a specific agent's output
tail -f .night-manager/logs/ABS-90.jsonl | jq .

# Check state
cat .night-manager/state.json | jq .

# List active Claude Code sessions
claude agents --json
```

## Agent permissions

Dev agents run with a scoped tool allowlist — no `rm -rf`, `curl`, `ssh`, or
arbitrary system commands. The full allowlist:

- File tools: `Read`, `Write`, `Edit`, `Glob`, `Grep`
- Bash patterns: `git *`, `make *`, `npm *`, `npx *`, `python *`, `.venv/bin/*`,
  `cd *`, `ls *`, `find *`, `grep *`, `mkdir *`, `cp *`, `mv *`, `docker
  compose *`, `./scripts/*`, `pip *`, and common read-only utilities
- MCP: all Linear tools (`mcp__claude_ai_Linear__*`)

Agents cannot push to remote, merge to dev, or deploy. Those actions are
performed by the NM after review gates pass.

## Port allocation

Each agent gets a unique port triplet to avoid collisions with parallel
worktrees:

| Slot | PG_PORT | E2E_FASTAPI_PORT | E2E_WEB_PORT |
|------|---------|------------------|--------------|
| 0    | 5433    | 8002             | 3002         |
| 1    | 5434    | 8003             | 3003         |
| 2    | 5435    | 8004             | 3004         |
| …    | …       | …                | …            |

Ports are allocated sequentially by slot index. Check availability with
`lsof -iTCP:<port> -sTCP:LISTEN` before starting if many worktrees are active.

## State file

The NM persists state to `.night-manager/state.json` after every mutation. This
enables crash recovery — if the NM process dies, restarting it will read the
state file and understand what was in progress. The state includes:

- Run ID and timestamp
- Execution plan (parallel groups)
- Per-issue status, branch, worktree, ports, session ID, and error info

## File layout

```
scripts/night_manager/
├── __init__.py
├── __main__.py        # Entry point
├── main.py            # Orchestrator loop
├── config.py          # CLI args, constants
├── linear_client.py   # Linear GraphQL client
├── state.py           # Persistent state management
├── planner.py         # AI conflict analysis + grouping
├── agent.py           # Dev agent lifecycle
└── reviewer.py        # Code review + e2e gate + merge

scripts/start-night-manager.sh   # caffeinate + tmux launcher

.night-manager/                  # Runtime state (gitignored)
├── state.json
├── nm-{timestamp}.log
├── logs/ABS-{id}.jsonl
└── report-{timestamp}.md
```

## Alembic collision resistance

*Background (2026-06-15 incident — ABS-312/ABS-314/ABS-316):* Two concurrent
agents each created a migration with `down_revision` pointing to the same dev
head. When the first branch merged, the second still carried the old
`down_revision` — producing two Alembic heads. `alembic upgrade head` then
failed with "Multiple head revisions are present," breaking `e2e-up` on the
merged branch and triggering a revert/re-pickup loop.

### How the NM prevents this

Two interlocking guards:

**1. Pre-merge rechain (`scripts/rechain_migration.py`)**

`merge_to_dev` calls this automatically when the feature branch adds files
under `alembic/versions/`. The script:
- Identifies migration files new on the branch vs dev (via `git diff
  --diff-filter=A --name-only dev...HEAD -- alembic/versions/`).
- Finds the "root" of the new chain — the migration whose `down_revision`
  points to an existing dev revision (not another new migration).
- Compares that `down_revision` to dev's current single head. If they differ,
  rewrites the `down_revision` and commits a `[rechain]` fix on the feature
  branch.
- The rechain commit becomes part of the feature branch; the subsequent rebase
  onto `origin/dev` carries the corrected chain forward.

Run manually from any feature branch:

```bash
python scripts/rechain_migration.py          # auto-detects worktree
python scripts/rechain_migration.py --dry-run  # show what would change
```

**2. Single-head guard (`scripts/e2e-up.sh`)**

Before `alembic upgrade head`, `e2e-up.sh` runs `alembic heads` and counts
revisions labelled `(head)`. If the count is > 1 it prints an actionable error
and exits 1 before migrations run, so the failure surfaces at `make e2e` time
with a clear message rather than an opaque crash deep in the migration chain.

This guard catches collisions that slip past the rechain step (e.g. manually
merged branches, or a rechain that couldn't determine dev's head).

### Rule for implementing agents

When you add a migration, the only rule is: **parent it to the current
single head when you create it.** The NM's rechain step handles the case
where a sibling branch merges before yours does.

If you see "Multiple Alembic heads" from `make e2e-up`, run
`python scripts/rechain_migration.py` from your worktree root and commit the
fix before re-running.

## Failure modes

| Symptom | NM behavior |
|---------|-------------|
| Agent stuck (no tool calls for 10 min) | Kill, resume with nudge prompt |
| Agent exits non-zero | Mark failed, post error to Linear |
| Code review fails 3× | Mark blocked, move to next issue |
| E2E tests fail 3× | Mark blocked, move to next issue |
| Merge conflict | Mark failed, move to next issue |
| Post-merge regression | Revert merge, mark blocked |
| NM process dies | Restart reads state.json, resumes |
| Machine tries to sleep | `caffeinate -s` prevents it |
| Terminal disconnect | `tmux` keeps session alive |
| Dual Alembic heads after merge | Rechain runs pre-merge; guard fires in e2e-up |

## Relationship to other docs

- [BRANCHING_STRATEGY.md](BRANCHING_STRATEGY.md) — The NM follows the
  `dev → main` promotion model. Dev agents branch off `dev`; merges use
  `--no-ff`.
- [E2E_TESTING.md](E2E_TESTING.md) — Port triplet conventions and parallel
  worktree patterns used by the NM.
- [DEPLOYMENT.md](DEPLOYMENT.md) — The `--deploy` flag invokes the same
  build → push → ssh-sed → up -d recipe.
- [CLAUDE.md](../CLAUDE.md) — Dev agents follow the SDLC defined there. The NM
  acts as the reviewer/approver that CLAUDE.md expects a human to be.
