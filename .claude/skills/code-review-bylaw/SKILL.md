---
name: code-review-bylaw
description: >
  Reviews a worktree's changes against the Linear issue's acceptance criteria,
  verifies unit + Playwright coverage for every behavior change, checks
  regression risk (migrations, cross-service contracts, mock-dispatcher
  keywords, auth/billing), and audits FastAPI / Next.js / Clerk best practices
  and SOLID / OOP / API design. Classifies findings as **must-fix** (blocks
  merge) vs **nits** and posts a Linear comment on the associated ABS-XXX
  issue. Use this skill whenever the user (or another agent) says "code
  review", "review the worktree", "review the diff", "review this issue",
  "audit the changes", or any phrase implying a pre-merge audit. Required
  gate between a green `make e2e` and the In Review → merge-approval ask
  defined in [CLAUDE.md](../../../CLAUDE.md). Returns `BLOCKED` or
  `APPROVED FOR USER REVIEW` on the last line; the implementing agent must
  not request merge approval until this skill returns `APPROVED`.
---

# code-review-bylaw — pre-merge code review gate

## Context

- **Repo root**: `~/dev/agentic-bylaw-system` (or the active worktree)
- **Integration branch**: `dev` — the diff under review is `dev...HEAD`
- **Prod branch**: `main` — never reviewed here; this skill is upstream of [test-and-deploy-bylaw](../test-and-deploy-bylaw/SKILL.md)
- **Linear MCP prefix**: `mcp__claude_ai_Linear__*` (already allowed in `.claude/settings.local.json`)
- **Safe-scope rule**: this skill is **read-only on application code**. It does not edit `src/**`, `web/app/**`, `web/lib/**`, `web/components/**`, `migrations/**`, configs, env, or compose files. The only paths it may write are `tests/**`, `web/e2e/**`, and `.claude/plans/**` — and even those only when the user explicitly asks the skill to add a missing test stub (default behavior is to *report* the gap, not fill it). Fixing must-fix items is the implementing agent's job, not this one's.

Anchor docs (link, don't restate):

- [CLAUDE.md](../../../CLAUDE.md) — SDLC, parallel-worktree port convention, In Review gate.
- [docs/E2E_TESTING.md](../../../docs/E2E_TESTING.md) — Playwright stack topology, smoke/functional/a11y/visual matrix.
- [docs/BRANCHING_STRATEGY.md](../../../docs/BRANCHING_STRATEGY.md) — promotion gate, expand/contract migration rules.
- [.claude/skills/test-and-deploy-bylaw/SKILL.md](../test-and-deploy-bylaw/SKILL.md) — the next skill in the lifecycle; runs *after* the issue has been merged to `dev` and the user wants to promote.

---

## Step 1 — Precondition check

Before doing any work, confirm the worktree is in a reviewable state. From the worktree root:

```bash
git rev-parse --abbrev-ref HEAD
git status --porcelain
git log -1 --format='%H %s'
git fetch origin dev
git log --oneline dev..HEAD | head -1
```

Abort with a clear error message to the user if any of:

| Condition | Action |
|---|---|
| Current branch is `dev` or `main` | Halt. "code-review-bylaw runs on a feature branch off `dev`, not on `dev`/`main` itself." |
| `git status --porcelain` is non-empty | Halt. Surface the dirty files. Reviewing uncommitted work hides the diff from `git diff dev...HEAD`; ask the user to commit or stash. |
| `git log dev..HEAD` is empty | Halt. "No commits ahead of `dev` — nothing to review." |
| No `ABS-\d+` in branch name OR in any commit message on `dev..HEAD` | Halt. Ask the user for the Linear issue ID — every reviewable change must trace to an issue per [CLAUDE.md](../../../CLAUDE.md) §Issue Management. |
| `make e2e` evidence missing | Soft halt: check whether `web/playwright-report/index.html` exists and its `mtime` is newer than the last commit in `dev..HEAD`. If not, ask the implementing agent / user to confirm e2e ran green within this worktree; do not proceed on assumption. |

If all conditions pass, extract `ABS_ID` (the first `ABS-\d+` match — branch name takes precedence over commit messages) and continue.

---

## Step 2 — Fetch Linear context

Pull the issue and its conversation. Use these MCP tools (already permitted):

```
mcp__claude_ai_Linear__get_issue          → title, description, acceptance criteria, labels, status, assignee
mcp__claude_ai_Linear__list_comments      → later-clarified requirements, scope changes, prior reviewer notes
```

Capture explicitly:

- **Title** and **description** verbatim (you will quote them in the report).
- **Acceptance criteria** — usually a checklist in the description or a comment. Parse into a list of distinct AC bullets. If no AC is found, halt and ask the user — a code review without acceptance criteria has nothing to verify against.
- **Status** — must be `In Progress` or already `In Review`. If `Todo`/`Backlog`/`Done`, halt and ask.
- **Prior automated-review comment** (if any) — search the comment list for a header matching `## 🔍 Automated code review (code-review-bylaw)`. If found, capture its comment ID for the idempotency step.

---

## Step 3 — Snapshot the change surface

```bash
git diff --stat dev...HEAD
git diff dev...HEAD
git log dev..HEAD --oneline
git log dev..HEAD --name-only | sort -u
```

Build three in-memory lists:

- `changed_py` — files matching `src/**/*.py` or `mcp/**/*.py`.
- `changed_ui` — files under `web/app/**`, `web/components/**`, `web/lib/**`, `web/middleware.ts`.
- `changed_migrations` — files under `migrations/versions/*.py` (or wherever Alembic revisions live in this repo — discover with `find . -path '*/migrations/versions/*.py' -newer dev 2>/dev/null` if the conventional path is empty).
- `changed_tests` — files matching `tests/**/*.py` or `web/e2e/**/*.ts`.

These four lists drive Steps 4 and 5.

---

## Step 4 — Map changes → tests (the coverage check)

For every changed file, find the test(s) that exercise it. A gap is a **must-fix** finding.

### 4.1 Python coverage

For each file in `changed_py`:

- Mirror the path under `tests/` and look for a matching test file. Convention: `src/advisor/billing/credits.py` → `tests/advisor/billing/test_credits.py` (or `test_credits_*.py`). Discover with:

  ```bash
  find tests -name "test_$(basename <file> .py)*.py"
  ```

- If at least one matching test file exists **and** appears in `changed_tests`, coverage is satisfied for this file.
- If a matching test file exists but is **not** in `changed_tests`, flag as `coverage-stale`: the implementer likely changed behavior without updating the test (could be fine if the change is internal refactor — judge from the diff).
- If no matching test file exists at all, flag as `coverage-missing` (must-fix).

### 4.2 UI coverage

For each file in `changed_ui`:

- Extract identifying tokens: the route (`web/app/cases/[id]/page.tsx` → `/cases/`), the component default-export name, any `data-testid="..."` attributes added in the diff.
- Grep `web/e2e/**` for any of those tokens:

  ```bash
  grep -RIn -e "page.goto(.*<route>" -e "<ComponentName>" -e "data-testid=.<testid>." web/e2e/
  ```

- If at least one spec matches **and** appears in `changed_tests`, coverage is satisfied.
- If a spec matches but is not in `changed_tests`, flag `coverage-stale`.
- If no spec matches anywhere, flag `coverage-missing` (must-fix per [CLAUDE.md](../../../CLAUDE.md) §SDLC — "Code changes without e2e coverage of the new/changed behavior are not done").

### 4.3 Migrations

For each file in `changed_migrations`:

- Confirm a test under `tests/**` exercises the new schema (either an integration test importing the new model, or an upgrade/downgrade test).
- If neither exists, flag `migration-untested` (must-fix unless the user has previously approved a migration-only change for this issue — check Linear comments).

---

## Step 5 — Run targeted tests

Re-run only the tests relevant to the diff. The plan is **targeted**, not full-suite — the full suite already ran as the In Review precondition.

### 5.1 Targeted unit tests

If `changed_py` is non-empty, build the test path list from Step 4.1 and run:

```bash
.venv/bin/pytest <mapped_unit_test_paths> -v --tb=short
```

If `changed_py` is empty, skip.

### 5.2 Targeted e2e

Boot the stack with worktree-isolated ports if another stack is up. Detect with:

```bash
lsof -iTCP:5432 -sTCP:LISTEN >/dev/null 2>&1 && echo "default stack occupied"
```

If occupied, derive ports from the last digit of `ABS_ID` per [CLAUDE.md](../../../CLAUDE.md):

```bash
LAST_DIGIT=$(echo "$ABS_ID" | grep -oE '[0-9]+$' | tail -c 2)
export PG_PORT=543${LAST_DIGIT} E2E_FASTAPI_PORT=800${LAST_DIGIT} E2E_WEB_PORT=300${LAST_DIGIT}
export E2E_API_URL=http://127.0.0.1:${E2E_FASTAPI_PORT} E2E_BASE_URL=http://localhost:${E2E_WEB_PORT}
```

Then run **smoke + mapped functional specs only**:

```bash
make e2e-up
cd web && npx playwright test e2e/smoke <mapped_functional_specs>
cd .. && make e2e-down
```

If `changed_ui` is empty (pure backend change), run smoke only as a sanity check — UI smoke is the cheapest way to catch a backend regression that breaks a route.

Capture: pass/fail counts, names of any failing specs, and paths to traces / screenshots Playwright surfaces.

Any test failure during Step 5 is **must-fix** — the In Review precondition asserted green, so a fresh red here means either flake or a true regression the full-suite run missed. Either way, surface it.

---

## Step 6 — Static review checklist

Read the diff (`git diff dev...HEAD`) and judge each section below. Cite line numbers (`path/to/file.py:42`) for every finding.

### 6.1 Acceptance criteria coverage — must-fix on miss

For each AC bullet parsed in Step 2:

- Point at the code change and/or the test that satisfies it. Format: `AC: "<verbatim AC text>" → <file>:<line> (impl) + <test_file>:<line> (verifies)`.
- If an AC is **not** satisfied by anything in the diff: `must-fix — AC unimplemented: "<verbatim AC text>"`.
- If an AC is satisfied by code but **not verified by a test**: `must-fix — AC unverified: "<verbatim AC text>" implemented at <file>:<line> but no test asserts the behavior`.

### 6.2 Regression risk — must-fix triggers

Walk the diff against this list. Any hit is must-fix unless an explicit countermeasure is also in the diff.

| Trigger | Why must-fix | Countermeasure that clears it |
|---|---|---|
| New/modified Alembic migration | Schema changes can break prod if not expand/contract per [BRANCHING_STRATEGY.md](../../../docs/BRANCHING_STRATEGY.md) | Migration is additive-only (new nullable cols, new tables, new indexes `CONCURRENTLY`); no rename/drop of in-use columns; explicit ordering note in the Linear issue if deploy ordering matters. |
| Edit to `src/advisor/llm/mock_dispatcher.py` keyword contract | E2E mocks rely on specific keyword triggers; changing them silently red-pills every spec that uses them | Every affected Playwright spec under `web/e2e/**` is also updated in the diff. |
| Auth surface change — `web/middleware.ts`, `src/advisor/auth/**`, Clerk wiring | Auth regressions are the highest-blast-radius bug class | A spec under `web/e2e/auth/**` covers the new behavior. |
| Billing / credit-reservation change — `src/advisor/billing/**` | Credit accounting bugs are user-visible and hard to roll back | A spec under `web/e2e/functional/**` exercises the new path. |
| Next.js proxy route ↔ FastAPI endpoint pair — one side edited without the other | Cross-service contract drift breaks Next-proxy → FastAPI → Postgres | Both sides are in the diff, or there's an explicit comment in the diff/commit explaining why one side is unchanged. |
| New dependency added to `pyproject.toml` or `web/package.json` | Supply-chain + image-size impact; can silently change behavior of unrelated code | Pinned version, justification in the commit message, no transitive replacements of existing pins. |
| Edit to `.dockerignore`, `Dockerfile*`, or `docker-compose.yml` | Build-output changes don't surface until deploy; ABS-67 is the cautionary tale | Note in Linear issue confirming the change was tested in an image build, not just locally. |

### 6.3 API best practices — nits unless functional

These are nits unless they cause a Step 6.2 hit:

- **FastAPI** ([fastapi.tiangolo.com](https://fastapi.tiangolo.com)):
  - Endpoints declare an explicit `response_model=<Pydantic>` — not bare `dict`.
  - Dependencies via `Depends(...)`, not module-level globals.
  - Async DB access uses async sessions, never blocks the event loop with sync SQLAlchemy.
  - HTTP status codes are explicit (`status_code=201` on create, `204` on delete) rather than relying on FastAPI's default 200.
- **Next.js 16** ([nextjs.org/docs](https://nextjs.org/docs)):
  - Server components by default; `'use client'` only where interactivity demands it.
  - No secrets in client bundle — anything from `process.env.*` used in a client component must be `NEXT_PUBLIC_*`.
  - `fetch()` to the FastAPI side goes through the project's existing proxy helper, not a hard-coded URL.
- **Clerk** ([clerk.com/docs](https://clerk.com/docs)):
  - Trust decisions use `auth()` / `getAuth()` on the server, never the client-side `useUser()` claim.
  - Webhooks verify the Svix signature.

### 6.4 SE principles — nits unless egregious

- **SRP**: a function doing 5 unrelated things → nit; a function doing 5 things across two domain boundaries (e.g., a billing function that also writes auth state) → must-fix.
- **Layer boundaries**: respect `src/layer1` / `src/layer2` / `src/advisor` separation. A new import that crosses the boundary in the wrong direction → must-fix.
- **API design**: new public functions / classes / endpoints have a one-line docstring describing intent + contract.
- **Error handling**: no bare `except:` clauses; no swallowed exceptions without a log line; no `try/except` wrapping a single statement to suppress a known failure mode (the failure mode should be fixed at the source).
- **Naming**: no single-letter names outside loop indices and math; new public symbols match the surrounding module's casing convention.

### 6.5 Hygiene — must-fix

- No committed secrets (`grep -nE 'sk-[A-Za-z0-9]{20,}|api[_-]?key|password\s*=\s*[\"'\'']' <diff>`).
- No debug noise (`console.log`, `print(` added in non-test, non-CLI code).
- No commented-out code blocks.
- No `TODO` / `FIXME` without an `ABS-\d+` link.

---

## Step 7 — Classify and report

### 7.1 Build the report

Assemble a single markdown document with these sections, in this order:

```markdown
## 🔍 Automated code review (code-review-bylaw)

**Issue**: ABS-XXX — <title>
**Branch**: <branch> (commits ahead of dev: <N>)
**Reviewed at**: <ISO timestamp>
**Reviewer**: code-review-bylaw skill

## Summary
<2–3 sentences: what the change does, what's good, what's blocking.>

## Acceptance Criteria
<Table or list, one row per AC bullet, with impl and test citations.>

## Test Coverage
<Per-file coverage summary from Step 4 + targeted-run results from Step 5.>

## Regression Risk
<Hits from Step 6.2; "None detected" if clean.>

## Best Practices
<Notes from Step 6.3 and 6.4; group by file.>

## Hygiene
<Hits from Step 6.5; "Clean" if none.>

## Must-fix (N)
1. <file:line> — <one-line finding> — <one-line remediation>
2. ...

## Nits (M)
1. <file:line> — <one-line finding>
2. ...
```

Print this entire document to the conversation so the implementing agent sees it inline.

### 7.2 Post to Linear (idempotent)

- If Step 2 captured a prior `code-review-bylaw` comment ID on this issue, **update it** via `mcp__claude_ai_Linear__save_comment` (passing the existing comment ID). This keeps the issue's comment thread clean across re-runs.
- If no prior comment exists, **create** a new one via `mcp__claude_ai_Linear__save_comment`.
- Comment body = the markdown report from 7.1, unchanged.

If the Linear MCP call fails (network, permission), do **not** halt — surface the error to the user, print the full report to the conversation, and proceed to Step 8. The gate decision is still authoritative.

---

## Step 8 — Gate decision

Print **one** of the following as the final line of skill output:

- If the `Must-fix` list is non-empty:

  ```
  BLOCKED: <N> must-fix items. Address and re-run /code-review-bylaw before requesting merge approval.
  ```

- If the `Must-fix` list is empty:

  ```
  APPROVED FOR USER REVIEW: 0 must-fix items, <M> nits. Implementing agent may now summarize the change and request explicit merge approval per CLAUDE.md In Review gate.
  ```

This line is the machine-readable contract every caller (human or agent) keys off. Do not soften, qualify, or reword it.

---

## Common abort branches

| Symptom | Branch |
|---|---|
| Branch is `dev` or `main`, or working tree is dirty | Halt at Step 1 with the specific reason. Do not stash or auto-commit. |
| No `ABS-\d+` extractable from branch or commits | Halt at Step 1. Ask the user for the issue ID; never guess. |
| Linear issue has no acceptance criteria | Halt at Step 2. A review without AC has nothing to verify against — ask the user to add them to Linear first. |
| Targeted test re-run (Step 5) red | Do not stop the skill — finish the static review, surface the failures as must-fix, return `BLOCKED`. |
| `make e2e-up` port collision | Halt at Step 5 — do not retry blindly. Surface the conflicting port and the worktree convention. |
| Linear MCP save_comment fails | Continue. Print the report to the conversation and note the post-failure in the summary so the user can paste it manually. |
| Diff is purely doc/comment changes | Skip Steps 4–5 (no behavior to test). Run Step 6.5 (hygiene) only. Default to `APPROVED FOR USER REVIEW` if hygiene passes. |
| Iteration counter ≥ 3 BLOCKED returns on the same branch | Halt and surface the pattern — the implementing agent is not converging; the user should intervene. |

---

## What this skill explicitly does NOT do

- **Fix application code.** Findings flow back to the implementing agent. The safe-scope rule above is absolute.
- **Run the full e2e suite.** That's the In Review precondition and is owned by the implementing agent. This skill re-runs targeted specs only.
- **Merge `dev`.** The user explicitly approves the merge per [CLAUDE.md](../../../CLAUDE.md) §SDLC. This skill only clears the path to that ask.
- **Promote to `main`, build images, or deploy.** Those are owned by [test-and-deploy-bylaw](../test-and-deploy-bylaw/SKILL.md) and `deploy-bylaw`, which run *after* `dev` integration.
- **Replace human judgment on subjective design calls.** Flag them as nits; let the user decide.
