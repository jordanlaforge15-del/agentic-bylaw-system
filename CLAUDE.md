#Coding Agents
##Setup
- Always create a worktree and feature branch off `dev` (not `main`) before starting the task. New work integrates into `dev` first; `main` is prod-only — see [docs/BRANCHING_STRATEGY.md](docs/BRANCHING_STRATEGY.md).

##Issue Management (Linear)
**Always keep the Linear issue associated with the current task updated**
- Add plan or to-do list generated as part of the task to the issue
- Record the branch name in the issue
- Update issue status and progress according to where you are in the task

##SDLC
- **In Progress → testing requirement:** Every issue must be covered by Playwright e2e tests. If existing tests already cover the changed behavior, reuse them; otherwise add new specs under `web/e2e/functional/` (or `smoke/` for critical-path) as part of the same issue. Code changes without e2e coverage of the new/changed behavior are not done.
- **Commit cadence during In Progress:** Commit whenever a logical unit lands (a passing test, a coherent refactor, a working slice). Don't batch unrelated changes into one giant end-of-task commit — small, reviewable commits make the eventual merge into `dev` easier to reason about.
- **Gate for In Review:** Before moving the Linear issue to In Review, run the full e2e suite **inside the worktree** (`make e2e` from the worktree root) and confirm it passes. A green e2e run in the worktree is the precondition for transition; do not flip status on the basis of unit tests or partial runs.
- **In Review (no PR required):** This project does not require a GitHub PR for the In Review step. After e2e passes, surface a summary of the change + test evidence to the user and **explicitly ask for approval to merge into `dev`**. Wait for that approval before merging — do not auto-merge, force-push, or open a PR unless the user asks for one. Post-merge integration testing on `dev` is handled outside the issue's worktree/agent — once the merge lands, the issue is done from the agent's perspective.
- **Post-merge cleanup:** Once the feature branch is merged into `dev`, delete the worktree and the feature branch. From the main checkout: `git worktree remove <worktree-path>` then `git branch -d <branch-name>` (use `-D` only if the branch was squash-merged and `-d` refuses). Confirm with `git worktree list` and `git branch --list <branch-name>` that both are gone. Leaving stale worktrees around causes parallel-build cross-contamination ([[feedback_parallel_worktree_builds]]) and clutters `git worktree list`.

##Testing
- Python unit tests: `make test` (or `.venv/bin/pytest tests/advisor/` for a scoped run).
- End-to-end browser tests (Playwright, full local stack — Next.js + FastAPI + Postgres + MockGateway): see [docs/E2E_TESTING.md](docs/E2E_TESTING.md) for the full guide. Quick start:
  - First-time setup *per worktree*: `./scripts/dev-setup.sh --skip-db && (cd web && npm install) && make e2e-install`. `.venv/` and `web/node_modules/` are not tracked in git, so every worktree provisions its own. `dev-setup.sh` installs `[dev,advisor]` extras by default, so `uvicorn`/`fastapi` are available immediately.
  - Single command: `make e2e` (boots stack, runs full suite, tears stack down) or `make e2e-smoke` (~12s critical-path coverage).
  - Iterating on one spec: `make e2e-up` once, then `cd web && npx playwright test e2e/path/to/spec.ts` repeatedly; `make e2e-down` when finished.
  - **Always run `./scripts/e2e-down.sh` before `make e2e` (or `make e2e-up`).** This is mandatory, not optional. `start_fastapi` / `start_web` in `scripts/e2e-up.sh` reuse any existing listener on the FastAPI/Next.js ports — by design, so parallel worktrees on separate port triplets don't fight each other, but the side effect is that a process left over from a prior shell (older code, sibling worktree, crashed run) gets silently adopted. The suite then runs against stale code: endpoints added in your commits return 404, fixed bugs reappear, and the failure looks like a flake instead of an environment issue. `e2e-down.sh` is idempotent and safe to run when nothing is up. If you see the "REUSING EXISTING … LISTENER" banner during `e2e-up`, the run is already poisoned — abort, run `e2e-down.sh`, and start over.
  - **Export your worktree's port triplet BEFORE `e2e-down.sh`, not just before `make e2e`.** `e2e-down.sh` defaults to the standard 8001 / 3001 ports when its `E2E_FASTAPI_PORT` / `E2E_WEB_PORT` env vars are unset, and falls back to `lsof` on those ports if the local pidfile is missing. A fresh shell with no env exports will therefore kill whatever listener is squatting on `:8001` / `:3001` — which on a multi-worktree machine almost certainly belongs to *another* worktree's stack, not yours. Same `export PG_PORT=… E2E_FASTAPI_PORT=… E2E_WEB_PORT=…` line you'd put before `make e2e` goes before `e2e-down.sh` too.
- **Running e2e from a worktree while another worktree's stack is up** (you have parallel agents/issues in flight): each worktree needs its own host-port triplet. The first worktree uses defaults; each subsequent worktree exports unique ports before invoking `make e2e*`. Example for a second concurrent worktree:

  ```bash
  export PG_PORT=5433 E2E_FASTAPI_PORT=8002 E2E_WEB_PORT=3002
  export E2E_API_URL=http://127.0.0.1:8002 E2E_BASE_URL=http://localhost:3002
  make e2e-up && cd web && npx playwright test e2e/smoke
  ```

  Convention: pick `PG_PORT=543X`, `E2E_FASTAPI_PORT=800X`, `E2E_WEB_PORT=300X` where `X` is the last digit of the Linear issue ID (or any free triplet — `lsof -iTCP:543X -sTCP:LISTEN` to check). `scripts/e2e-up.sh` derives and exports both `POSTGRES_HOST_PORT` and `DATABASE_URL` from `PG_PORT`, so Playwright's globalSetup and seed scripts always connect to the right database without any extra export. Full recipe and rationale in [docs/E2E_TESTING.md#parallel-worktrees](docs/E2E_TESTING.md#parallel-worktrees).
- When you add a UI-touching fix, add a Playwright spec under `web/e2e/functional/` (or `smoke/` if it belongs on the critical-path matrix). The suite is the only thing that catches Next-proxy ↔ FastAPI ↔ Postgres regressions before deploy.
