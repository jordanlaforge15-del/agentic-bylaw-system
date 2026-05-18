#Coding Agents
##Setup
- Always create a worktree and feature branch off `dev` (not `main`) before starting the task. New work integrates into `dev` first; `main` is prod-only — see [docs/BRANCHING_STRATEGY.md](docs/BRANCHING_STRATEGY.md).

##Issue Management (Linear)
**Always keep the Linear issue associated with the current task updated**
- Add plan or to-do list generated as part of the task to the issue
- Record the branch name in the issue
- Update issue status and progress according to where you are in the task

##Testing
- Python unit tests: `make test` (or `.venv/bin/pytest tests/advisor/` for a scoped run).
- End-to-end browser tests (Playwright, full local stack — Next.js + FastAPI + Postgres + MockGateway): see [docs/E2E_TESTING.md](docs/E2E_TESTING.md) for the full guide. Quick start:
  - First-time setup *per worktree*: `./scripts/dev-setup.sh --skip-db && (cd web && npm install) && make e2e-install`. `.venv/` and `web/node_modules/` are not tracked in git, so every worktree provisions its own.
  - Single command: `make e2e` (boots stack, runs full suite, tears stack down) or `make e2e-smoke` (~12s critical-path coverage).
  - Iterating on one spec: `make e2e-up` once, then `cd web && npx playwright test e2e/path/to/spec.ts` repeatedly; `make e2e-down` when finished.
- **Running e2e from a worktree while another worktree's stack is up** (you have parallel agents/issues in flight): each worktree needs its own host-port triplet. The first worktree uses defaults; each subsequent worktree exports unique ports before invoking `make e2e*`. Example for a second concurrent worktree:

  ```bash
  export PG_PORT=5433 E2E_FASTAPI_PORT=8002 E2E_WEB_PORT=3002
  export E2E_API_URL=http://127.0.0.1:8002 E2E_BASE_URL=http://localhost:3002
  make e2e-up && cd web && npx playwright test e2e/smoke
  ```

  Convention: pick `PG_PORT=543X`, `E2E_FASTAPI_PORT=800X`, `E2E_WEB_PORT=300X` where `X` is the last digit of the Linear issue ID (or any free triplet — `lsof -iTCP:543X -sTCP:LISTEN` to check). The `POSTGRES_HOST_PORT` env var is derived from `PG_PORT` automatically inside `scripts/e2e-up.sh`. Full recipe and rationale in [docs/E2E_TESTING.md#parallel-worktrees](docs/E2E_TESTING.md#parallel-worktrees).
- When you add a UI-touching fix, add a Playwright spec under `web/e2e/functional/` (or `smoke/` if it belongs on the critical-path matrix). The suite is the only thing that catches Next-proxy ↔ FastAPI ↔ Postgres regressions before deploy.
