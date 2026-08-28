---
name: test-and-deploy-bylaw
description: >
  Verify dev with the full Playwright e2e suite, fix any failures within a
  bounded loop (max 5 iterations, test files only), promote dev → main per
  BRANCHING_STRATEGY, then hand off to the deploy-bylaw skill. Use this
  whenever the user says "test and deploy", "verify and ship", "release dev to
  prod", "promote dev and deploy", "run the full release", "test dev then
  deploy", or any phrase that implies the full dev → prod pipeline rather than
  just a deploy of an already-green main. Prefer this over deploy-bylaw when
  work is sitting on dev and has not been promoted yet. If main is already
  current and the user only wants to build and ship, use deploy-bylaw directly
  instead.
---

# test-and-deploy-bylaw — verify dev, promote to main, deploy to prod

## Context

- **Repo root**: `~/dev/agentic-bylaw-system` (or the active worktree)
- **Integration branch**: `dev` — where feature work lands first
- **Prod branch**: `main` — every commit is a candidate to be built and tagged
- **Max fix/test iterations**: **5**. Hard ceiling — halt and consult the user past this.
- **Safe-fix scope**: edits are confined to **`web/e2e/**`, `tests/**`, and Playwright snapshot files (`*-snapshots/*`)**. Anything outside this scope — `web/app/**`, `web/lib/**`, `advisor/**`, migrations, configs, env files — requires user consultation before editing.
- **Downstream skill**: this skill ends by handing off to `deploy-bylaw`. That skill owns the build → push → ssh-sed → up -d → verify → rollback recipe; do not restate it here.

Anchor docs (link, don't restate):

- [docs/BRANCHING_STRATEGY.md](../../../docs/BRANCHING_STRATEGY.md) — `dev → main` promotion gate.
- [docs/E2E_TESTING.md](../../../docs/E2E_TESTING.md) — e2e commands, worktree port convention.
- [CLAUDE.md](../../../CLAUDE.md) — `In Review` gate already requires green e2e; this skill enforces it at the dev→main boundary.
- [evals/golden/README.md](../../../evals/golden/README.md) — the golden-case deploy gate Step 7.0 enforces, and the attestation procedure that is the only way to open it.

---

## Step 1 — Snapshot dev and confirm clean working tree

Move to `dev` and pull. From the repo root or worktree root:

```bash
git checkout dev
git pull --ff-only origin dev
git status --porcelain
```

`git status --porcelain` must come back empty. If it returns any lines:

- **Halt.** Do not stash, do not discard. The lines may be the user's WIP.
- Surface the file list and ask the user how to proceed.

If `git pull --ff-only` fails (non-fast-forward), halt and ask the user — local `dev` has diverged from `origin/dev` and the right resolution is theirs.

---

## Step 2 — Boot the e2e stack

```bash
make e2e-up
```

This is idempotent: it boots Postgres, creates `layer1_test`, migrates, seeds the demo user, starts uvicorn on `:8001`, and starts Next dev on `:3001`. See [docs/E2E_TESTING.md](../../../docs/E2E_TESTING.md) for the topology.

**If running from a worktree while another stack is already up**, export a unique host-port triplet before `make e2e-up` — pattern documented in [CLAUDE.md](../../../CLAUDE.md):

```bash
export PG_PORT=5433 E2E_FASTAPI_PORT=8002 E2E_WEB_PORT=3002
export E2E_API_URL=http://127.0.0.1:8002 E2E_BASE_URL=http://localhost:3002
```

If `make e2e-up` fails to boot, halt — port collisions across worktrees are the most common cause and need operator judgement, not a retry.

---

## Step 3 — Run the full suite

```bash
cd web && npx playwright test
```

Run the **full** suite (smoke + functional + a11y + visual), not `e2e-smoke`. The whole point of this gate is shipping confidence; ~20–25s is not a budget worth optimizing.

Capture the failing-spec list, the failing assertion(s), and any trace/screenshot paths Playwright surfaces.

- **All green** → jump to [Step 7](#step-7--promotion-gate-dev--main).
- **Any failures** → continue to Step 4.

---

## Step 4 — Triage each failure

For each failing spec, in order:

1. Read the assertion message and any attached trace/screenshot.
2. Classify the **cause**:
   - Stale selector after an intentional DOM rename
   - Stale visual / screenshot snapshot after an intentional render change
   - Assertion text drift (UI copy changed in app code on dev, spec still expects old string)
   - Fixture / seed mismatch
   - Real app regression (code on dev is broken)
   - Flake (timing-sensitive, retried by Playwright config, or non-deterministic ordering)
3. Classify **fix safety**:

   | Safety | Criteria |
   |---|---|
   | **Safe to auto-fix** | Fix is confined to `web/e2e/**`, `tests/**`, or `*-snapshots/*`. Cause is selector/snapshot/assertion/fixture drift caused by *already-landed* app changes on dev. Single conceptual change. |
   | **Consult user** | Anything else — app-code regression, ambiguous intent (test could be right and app wrong, or vice versa), fix spans more than one test file in a non-obvious way, touches `advisor/migrations/**`, `web/app/**`, `web/lib/**`, `advisor/**`, env, or configs. |

Do **not** try to fix flakes in this loop — if a spec is genuinely flaky, that is a separate problem worth surfacing to the user; do not paper over it with sleeps or retries inside the test.

---

## Step 5 — Apply or escalate

### Safe-fix path

- Edit only files inside the safe scope.
- Re-run the specific failing spec(s) first to confirm the local fix:

  ```bash
  cd web && npx playwright test e2e/path/to/spec.ts
  ```

- If those pass, re-run the full suite (Step 3) to confirm no collateral damage.
- Commit on `dev` with the project's `[test]` prefix:

  ```bash
  git add web/e2e/... tests/... web/e2e/**-snapshots/...
  git commit -m "[test] fix <spec name>: <one-line reason>"
  ```

  Never use `git add -A` or `git add .` — keep the diff inside the safe scope.

- Increment the iteration counter and return to Step 3.

### Consult path

- Halt edits.
- Summarize to the user:
  - Which spec(s) failed
  - The failing assertion (verbatim)
  - Suspected cause (per Step 4 classification)
  - The fix you would apply, or the app-code investigation you would propose, *and* why it is outside the safe-fix scope
- Wait for user direction. Do **not** silently edit app code, migrations, or configs.

### Iteration cap

If the iteration counter would exceed **5**, halt regardless of fix safety. Surface:

- Iteration count and the diff applied at each step
- The remaining red specs and their failures
- A best-guess root cause for what is not yielding to the loop

5 is the hard ceiling.

---

## Step 6 — Loop

After a safe-fix commit, return to [Step 3](#step-3--run-the-full-suite) and re-run the **full** suite (not just the previously-failing specs — fixes can cascade). Continue until one of:

- All green → [Step 7](#step-7--promotion-gate-dev--main)
- A failure requires the consult path → halt, ask user
- Iteration count > 5 → halt, ask user

---

## Step 7 — Promotion gate (dev → main)

This step encodes [docs/BRANCHING_STRATEGY.md §The promotion gate](../../../docs/BRANCHING_STRATEGY.md). Tests are green at this point; the goal is to land `dev` on `main` cleanly and tag the resulting commit.

### 7.0 — Golden-case deploy gate (hard, runs first)

A green Playwright suite says the product works. It says nothing about whether the advisor's *answers* are correct — every expectation in the generated eval was authored by a model, and the system under test is a model. The golden subset is the only tier where a qualified human recorded the right answer, and `evals/golden/golden_cases.json` has always declared that a production deploy requires it. Until ABS-485 nothing ran that check, which is how a known-wrong answer reached prod ([docs/data-gaps/abs461-production-impact.md](../../../docs/data-gaps/abs461-production-impact.md)).

Run it before anything else in Step 7 — before inspecting the diff, before choosing a version, and certainly before merging:

```bash
python scripts/check_deploy_gate.py
```

No database, no run, no API spend: the condition that holds the gate today is a file check. Branch on the exit code:

| Exit | Meaning | Action |
|---|---|---|
| **0** | Gate OPEN — every entry attested, and a run graded against *this* golden file passed them all. | Proceed to 7.1. |
| **1** | Gate HELD — promotion must not proceed. | **Halt.** Surface the script's output verbatim; it names the reason. |
| **2** | The gate could not be evaluated (missing or malformed golden file, run dir that does not exist). | **Halt.** "I could not check" is not "I checked and it passed." Fix the input and re-run. |

The output distinguishes the ways a hold happens, because the operator's next move is completely different:

- **`unattested`** — no qualified human has recorded the correct answer for one or more cases. Nothing has been demonstrated about correctness, in either direction. This is a hold, not a failure. The unblock is a human filling in the attestations per [evals/golden/README.md](../../../evals/golden/README.md) § "Filling in an attestation".
- **`graded_failing`** — a human recorded the correct answer and the advisor gave a different one. A real defect. Fix the advisor; do not edit the attestation to match the output.
- **`no_graded_run`** — everything is attested but no eval run has been graded against this exact golden file. Every grade records the golden file's SHA-256, so an attestation edited after a green run does not inherit that run's verdict. Produce and grade a run (`python scripts/verify_run.py evals/runs/<ts>`), then re-check.

> **The gate is HELD today (0/6 attested) and that is correct.** Do not author, draft, or backfill an attestation to clear it — not as a placeholder, not "to unblock the release", not even from a careful reading of the by-law. A model-authored attestation is not an attestation: it converts the project's only non-model ground truth into a record of what the model already says, and it defeats the entire artifact. The attestation work is a human task tracked outside this skill. If a release is genuinely urgent while the gate is held, that is the user's call to make explicitly — surface the hold and ask.

CI enforces the same check independently: the `golden-gate` job in [.github/workflows/ci.yml](../../../.github/workflows/ci.yml) is a `needs` of both image builds, so a held gate blocks the prod images even if this step were skipped.

### 7.1 — Inspect what is being promoted

```bash
git fetch origin main
git log main..dev --oneline
git log main..dev --name-only | sort -u | head -50
```

Surface the commit list and the touched-file summary to the user. Two things to call out before proceeding:

- **Migrations**: if any path under `advisor/migrations/` (or wherever Alembic revisions live in this repo) appears in the file list, name it explicitly. The user needs to decide whether the migration follows the expand/contract pattern documented in `deploy-bylaw`'s "Alembic migrations" section, and whether it must run *before* or *after* the new image deploys.
- **Web vs advisor split**: note whether the changes are `web/`-only, advisor-only, or both. This is what `deploy-bylaw` Step 1 will need.

### 7.2 — Choose the version bump

Default to **patch** (`1.4.2 → 1.4.3`). Bump **minor** (`1.4.2 → 1.5.0`) if the diff contains new user-facing features or if the user explicitly signalled "significant." Never bump major without an explicit user instruction.

Get the current tag from the server to know what's deployed (this is the same source `deploy-bylaw` uses):

```bash
ssh bylaw-prod "grep -E 'bylaw-(web|advisor):' /srv/bylaw/docker-compose.yml"
```

Compute the new `vX.Y.Z`. Both services use the same tag — the git tag and both image tags stay in lockstep.

### 7.3 — Merge dev into main, no-ff

```bash
git checkout main
git pull --ff-only origin main
git merge --no-ff dev -m "Promote dev → main $(date +%Y-%m-%d) (<summary>)"
git push origin main
```

Replace `<summary>` with a one-line description of what is being promoted (e.g., Linear issue IDs or feature names from the commit log in 7.1).

If `git pull --ff-only origin main` fails, halt — something landed on `main` outside the promotion path. Per BRANCHING_STRATEGY, investigate; do not paper over with a merge commit.

### 7.4 — Tag the promotion commit

```bash
git tag -a vX.Y.Z -m "prod" HEAD
git push origin vX.Y.Z
```

The git tag must match the GHCR image tag `deploy-bylaw` is about to push — that correspondence is what makes rollback ("flip the compose tag back to `vX.Y.Z-1`") trivially correspond to a git revision.

### 7.5 — Resync dev from main

```bash
git checkout dev
git merge --ff-only origin/main
git push origin dev
```

If the `--ff-only` fails, halt. The integration branch should fast-forward; anything else means the model has been violated and needs operator attention.

---

## Step 8 — Hand off to `deploy-bylaw`

Tests are green, `main` is current, the promotion commit is tagged `vX.Y.Z`, and `dev` is resynced. The state matches `deploy-bylaw`'s frontmatter precondition ("the feature branch is already merged to main before this skill runs").

Invoke the `deploy-bylaw` skill now. Carry forward into it:

- The new version `vX.Y.Z` chosen in 7.2 (its Step 2 would otherwise re-derive this).
- The web/advisor scope from 7.1 (its Step 1 needs this).
- Any migration note from 7.1 — `deploy-bylaw`'s "Alembic migrations" section applies *before* the advisor restart.

Do **not** restate `deploy-bylaw`'s build / push / ssh-sed / up -d / verify / rollback steps here — defer to that skill as the single source of truth.

> **Pre-deploy gate (advisor scope only):** `deploy-bylaw` Step 5a runs an import
> smoke test (`import advisor.api.main`) against the new image under prod-mirroring
> runtime constraints before swapping the container. This is a hard gate — if it
> exits non-zero, `deploy-bylaw` halts and the old container keeps running. You do
> not need to wire this yourself; it is part of `deploy-bylaw`'s Step 5a.

After `deploy-bylaw` reports verification green, this skill is done.

---

## Common abort branches

| Symptom | Branch |
|---|---|
| `check_deploy_gate.py` exits 1 at Step 7.0 | **Halt — do not promote.** Surface the script's output; it names whether the hold is `unattested` (a qualified human has work to do), `graded_failing` (the advisor is wrong), or `no_graded_run`. Never clear it by writing an attestation. |
| `check_deploy_gate.py` exits 2 at Step 7.0 | Halt. The gate could not be evaluated — a malformed or missing golden file. This is not a passing gate. |
| `git status --porcelain` not empty at Step 1 | Halt. Surface the dirty files. Do not stash or discard — assume it's the user's WIP. |
| `make e2e-up` fails | Halt. Per [E2E_TESTING.md](../../../docs/E2E_TESTING.md), port collisions across worktrees are the most common cause. Do not retry blindly. |
| Iteration counter > 5 | Halt. Summarize all 5 attempts, the cumulative diffs, and remaining red specs. |
| Failure needs an app-code fix | Halt at Step 5 consult path. Never silently edit outside the safe scope. |
| `git pull --ff-only` on `dev` or `main` fails | Halt. Investigate before merging — paper-over merges break the promotion model. |
| `git merge --ff-only origin/main` on `dev` fails (Step 7.5) | Halt. Per BRANCHING_STRATEGY, this means something landed on `main` outside the promotion path. Surface to user. |
| Migration touched on dev | Do not silently proceed. Surface in Step 7.1 and let the user decide expand/contract ordering before invoking `deploy-bylaw`. |
| Test failure is a flake (timing / nondeterminism) | Halt at Step 5 consult path. Do not paper over with sleeps or retries inside the spec. |

---

## What this skill explicitly does NOT do

- Edit or fork `deploy-bylaw`. That skill owns the deploy procedure; this one chains into it.
- Handle the `hotfix/* → main` path. Hotfixes stay manual per BRANCHING_STRATEGY.
- Roll back. Defer to `deploy-bylaw`'s rollback recipe (Step 7a there).
- Author, draft, or backfill a golden-case attestation, under any circumstance. Step 7.0 reads them; nothing in this repo writes them. See [evals/golden/README.md](../../../evals/golden/README.md).
- Grade an eval run. Step 7.0 only checks whether a matching grade exists; producing one is `scripts/verify_run.py` and costs metered API spend.
- Replace the In Review gate in [CLAUDE.md](../../../CLAUDE.md). Per-issue e2e still happens in each worktree; this skill is the final, full-suite gate at the dev→main boundary.
