# Branching strategy

This doc is the canonical rule for how branches flow into production. Pair it with
[`DEPLOYMENT.md`](DEPLOYMENT.md) (which describes the build + ship recipe) and the
project root [`CLAUDE.md`](../CLAUDE.md) (which tells coding agents how to start a session).

The short version:

```
feature/* ──► dev ──► main ──► (build image tag) ──► prod
                       ▲
                   hotfix/* ─┘   (also back-merged into dev)
```

`main` is "what is running in prod right now, or the next thing we will deploy."
`dev` is the integration buffer where parallel work collides safely. Promotion
from `dev` to `main` is a deliberate, human-gated event tied to a versioned
image tag.

## Why we are changing things

Today every feature branch (including each agent worktree) merges directly to
`main`. That makes `main` a moving target: a branch that passed local tests
yesterday can collide with another branch merged this morning, and the operator
discovers it on the next deploy. The blast radius is prod because `main` *is*
what gets built and shipped.

Adding a single integration branch (`dev`) gives us a place for those collisions
to surface and get resolved *before* they reach the artifact that prod runs.

## Branching models considered

Brief honest survey, then the choice and why.

| Model | Fit for this repo |
|---|---|
| **GitHub Flow** (current — `main` + short-lived feature branches, deploy from `main`) | Optimal when you have CI gates on every PR and trust them. We have neither, and the resulting instability is the bug we are fixing. |
| **Git Flow** (`main`, `develop`, `feature/*`, `release/*`, `hotfix/*`, version-tagged releases) | Designed for versioned software with cut release branches. Overkill for a single web service that we redeploy continuously. The `release/*` ceremony adds friction without buying anything. |
| **GitLab / release flow** (`main` + one or more environment branches, promotion is a fast-forward or merge from an upstream branch) | Maps cleanly onto our "build → push → flip tag" deploy. Add a `dev` integration branch upstream of `main` and we are there. |
| **Trunk-based development** (single trunk, all changes via short-lived branches, feature flags hide unfinished work) | The gold standard when you have automated test coverage and a feature-flag system. We have neither yet. Adopting trunk-based today would just be GitHub Flow with extra steps. |

**Recommendation: a trimmed release-flow / GitLab-flow with exactly two long-lived
branches — `dev` (integration) and `main` (prod-tracking).** It is the smallest
move that buys us isolation, and it is a strict superset of where we are; we can
graduate to trunk-based later by collapsing `dev` into `main` once CI and feature
flags exist.

## Branch roles and naming

| Branch | Lives for | Off of | Merges into | Notes |
|---|---|---|---|---|
| `main` | Forever | — | — | Prod source of truth. Every commit on `main` is a candidate to be built and tagged. Protected: only `dev → main` PRs and `hotfix/* → main` PRs. |
| `dev` | Forever | `main` | `main` (via promotion PR) | Integration trunk. All feature work converges here first. Re-syncs from `main` after every promotion or hotfix. |
| `feature/<short-name>` | Hours–days | `dev` | `dev` | Operator-written feature work. |
| `agent/<linear-id>-<short-name>` *(or the existing `worktree-<linear-id>-*` form, see CLAUDE.md)* | Hours–days | `dev` | `dev` | Coding-agent work. One worktree per branch as the project rule already requires. |
| `hotfix/<short-name>` | Hours | `main` | `main` **and** `dev` (back-merge) | Emergency prod fix that cannot wait for `dev` to be promotable. |
| `fix/*`, `chore/*`, `docs/*` (existing) | Hours–days | `dev` | `dev` | Same rule as `feature/*`; the prefix only signals intent. |

Notes:

- The existing `claude/*` and `worktree-*` naming used by the harness is fine to
  keep — the rule that matters is **branch off `dev`, PR into `dev`**, not the
  prefix. CLAUDE.md should be updated to make that the default base (see
  [Migration plan](#migration-plan) below).
- Stale long-lived branches (we currently have ~30 unmerged ones from past
  sessions) should be deleted or archived as part of the rollout; they are not
  part of the new model.

## The promotion gate (dev → main)

Promotion is the only thing standing between a merge collision and prod, so it
has to be more than "git merge." Until we have CI, the gate is a manually-run
checklist on the `dev` tip:

1. **Local rebase clean.** `git checkout dev && git pull && git fetch origin main`
   then `git log main..dev --oneline` to inspect what is being promoted.
2. **Tests green on `dev`:**
   - `pytest tests/advisor/` (must be 100% pass — the suite has 167+ tests).
   - `cd web && npm run typecheck`.
   - `cd web && npm run lint` (if it exists; do not skip on a noisy run, fix it).
   - The e2e suite (Playwright, landed in `1ae7666 [test] Add instrumented UI test
     framework`) for the touched surfaces. Full suite when in doubt.
3. **Image builds cleanly.** A no-push smoke build of both images on the operator
   laptop — this is the only way today to catch Dockerfile / dependency
   regressions before they hit GHCR:

   ```bash
   docker buildx build --platform linux/amd64 -f web/Dockerfile \
     --build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$(grep ^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY /srv/bylaw/.env | cut -d= -f2) \
     web/
   docker buildx build --platform linux/amd64 -f Dockerfile.advisor .
   ```
4. **Migration review.** If any alembic revision was added on `dev`, render the
   SQL (`alembic upgrade head --sql`) and confirm it follows the expand/contract
   rule documented in `DEPLOYMENT.md`. A migration that is not safe to run
   *before* the new image deploys is a promotion blocker, not a deploy-time problem.
5. **Open a `dev → main` PR.** Title: `Promote dev → main (YYYY-MM-DD)`. Body
   pastes the `git log main..dev --oneline` output and notes any migration / env
   var changes. Merge with `--no-ff` so the promotion shows up as one commit on
   `main`'s first-parent history.
6. **Tag the promotion commit on `main`** with the image version that is about
   to be built: `git tag -a vX.Y.Z -m "prod" <sha> && git push origin vX.Y.Z`.
   The tag and the GHCR image tag must match — that is what makes rollback ("flip
   the tag in compose, recreate") trivially correspond to a git revision.
7. **Run the deploy recipe** from `DEPLOYMENT.md` (build → push → ssh sed → up -d).
8. **Resync `dev` from `main`** so the integration branch starts the next cycle
   in lockstep with prod: `git checkout dev && git merge --ff-only origin/main &&
   git push origin dev`. (If the merge isn't fast-forward, something landed
   directly on `main` outside the promotion path — investigate, do not paper
   over with a merge commit.)

Promotion cadence is operator's call. Every working day is fine for low-risk
work; weekly is fine for heavy weeks. The thing we are buying is "promotion is a
decision," not "promotion is automatic."

## Hotfix path

Hotfix exists because `dev` will sometimes contain unshippable WIP when prod
breaks. Steps:

1. `git checkout main && git pull`
2. `git checkout -b hotfix/<short-name>`
3. Fix, commit, run the relevant subset of tests locally.
4. PR `hotfix/<short-name>` → `main`. Merge `--no-ff`.
5. Tag `main` with the new image version and run the deploy recipe.
6. **Back-merge into `dev` immediately:** `git checkout dev && git merge --no-ff
   origin/main && git push origin dev`. Skipping this step recreates the fix as
   a phantom conflict on the next promotion.

For *most* prod regressions the right move is still the existing image-tag
rollback (`ssh bylaw-prod "sed -i 's|bylaw-X:NEW|bylaw-X:OLD|' …"`) — that is
faster than a hotfix and gives time to write the fix correctly on `dev`.
Hotfixes are for cases where rollback is not an option (data already migrated
forward, schema commitment, etc).

## What `main` is allowed to contain

Concrete invariants — these are how we tell whether `main` is healthy:

- Every commit on `main` is either a promotion merge from `dev` or a hotfix merge.
  Direct `git push` to `main` should never happen.
- Every prod-deployed commit on `main` has a matching `vX.Y.Z` git tag and a
  matching `ghcr.io/.../bylaw-{web,advisor}:X.Y.Z` image tag in GHCR.
- The advisor test suite and the web typecheck pass at every commit on `main`.

When we get GitHub Actions, those invariants become enforced status checks. For
now they are operator discipline.

## Migration plan (rolling this in without disrupting in-flight work)

1. **Create `dev`** off the current `main` tip and push: `git checkout -b dev main
   && git push -u origin dev`.
2. **Set `dev` as the default base** in GitHub repo settings → Branches → Default
   branch (or leave `main` default and instead protect it via branch-protection;
   the default-branch change is cosmetic but makes new PRs target the right
   thing). Protect `main`: require PR, dismiss stale reviews, no force push.
3. **Rebase the live worktree branches.** For each branch that is not merged and
   is still active, `git rebase --onto dev main <branch>` so its base becomes
   `dev`. Inactive / abandoned worktree branches get deleted as part of the
   rollout (we have ~30 stale ones today — see `git worktree list`; this is a
   good time to prune the ones that are not coming back).
4. **Update CLAUDE.md** so the project-wide rule for agents is "worktree off
   `dev`, PR into `dev`" rather than "off `main`." Proposed minimal diff:

   ```diff
   - 1. Create a worktree on a unique branch name
   + 1. Create a worktree on a unique branch name, **based on `dev`** (not `main`)
   ```

   And in the after-merging section:

   ```diff
   - **After merging the worktree branch into main, add the following to the coding session.md file**
   + **After merging the worktree branch into `dev`, add the following to the coding session.md file**
   -   - Merged Into Main: Yes
   +   - Merged Into Dev: Yes
       - Date Merged: (Date of the merge into main)
   ```

   This is intentionally not part of this PR — it is a one-line follow-up that
   should land *after* `dev` exists and is the default base, so agents do not
   start cutting branches against a not-yet-existent branch.
5. **First promotion.** Once one or two PRs have landed on `dev`, run the
   promotion gate end-to-end as a dry run. The first promotion proves the
   process; subsequent ones are routine.

Estimated rollout effort: one operator-afternoon for steps 1–3, plus the
CLAUDE.md edit and a Linear sub-issue to track "delete stale worktree branches."

## Forward-looking: when we add CI/CD

This model is the runway to the GitHub Actions follow-up already tracked in
`DEPLOYMENT.md` open follow-up #5. The natural next steps, in order:

1. **CI on `dev`:** GH Actions runs pytest + typecheck on every PR into `dev`.
   This eats most of step 2 of the promotion gate.
2. **CI on `main`:** same checks gate the `dev → main` promotion PR, plus an
   automatic `docker buildx build --push` of both images keyed off the new
   `vX.Y.Z` tag. This eats step 3 and the build half of step 7.
3. **CD on `main` (optional):** an Action that SSHes to `bylaw-prod` and runs the
   `sed` + `compose up -d` flip. At this point the operator's only manual step
   is "approve the promotion PR" — which is the right place for the human gate
   to live anyway.

Once that is in place, the case for collapsing `dev` back into `main` and going
trunk-based opens up — but only after a feature-flag system exists so that
unfinished work can be merged dark. That is a separate decision.
