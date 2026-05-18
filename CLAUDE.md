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
- **In Review (no PR required):** This project does not require a GitHub PR for the In Review step. After e2e passes, surface a summary of the change + test evidence to the user and **explicitly ask for approval to merge into `dev`**. Wait for that approval before merging — do not auto-merge, force-push, or open a PR unless the user asks for one.
- **Post-merge verification on `dev`:** After merging into `dev`, check out `dev`, pull, and re-run the full e2e suite (`make e2e`) against the integrated tree. The worktree run proves the branch is green in isolation; the `dev` run proves it still passes once integrated with whatever else has landed. If the post-merge run fails, treat it as a regression on `dev` and fix forward before moving on.

##Testing
- Python unit tests: `make test` (or `.venv/bin/pytest tests/advisor/` for a scoped run).
- End-to-end browser tests (Playwright, full local stack — Next.js + FastAPI + Postgres + MockGateway): see [docs/E2E_TESTING.md](docs/E2E_TESTING.md) for the full guide. Quick start:
  - First-time setup: `./scripts/dev-setup.sh && make e2e-install`.
  - Single command: `make e2e` (boots stack, runs full suite, tears stack down) or `make e2e-smoke` (~12s critical-path coverage).
  - Iterating on one spec: `make e2e-up` once, then `cd web && npx playwright test e2e/path/to/spec.ts` repeatedly; `make e2e-down` when finished.
- When you add a UI-touching fix, add a Playwright spec under `web/e2e/functional/` (or `smoke/` if it belongs on the critical-path matrix). The suite is the only thing that catches Next-proxy ↔ FastAPI ↔ Postgres regressions before deploy.
