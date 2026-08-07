# Night Manager

**The Night Manager lives in its own repository:
[github.com/jordanlaforge15-del/night-manager](https://github.com/jordanlaforge15-del/night-manager).
Its code, docs, and UI are there. Nothing about it is maintained here.**

## Why this file exists

The NM — an overnight orchestrator that picks up Triaged Linear issues, runs
Claude Code agents in parallel worktrees, reviews their work, and merges what
passes — was originally built inside this repo (ABS-102) and later extracted.
The original copy lingered until ABS-446 removed it: the Mission Console UI
(`web/app/nm/`), its API routes (`web/app/api/nm/`), the orchestrator engine
(`scripts/night_manager/`), the launcher, the tests and Playwright specs that
covered them, and the `design/night-manager/` handoff (byte-identical to the
copy already in the NM repo). This page is the tombstone, so links and searches
land somewhere useful instead of on a 404.

## The relationship, stated plainly

This repo is one of the Night Manager's **targets**, never its host. At runtime
the NM executes from a standalone clone of its own repo and reads a profile
under `~/.night-manager/profiles/` that names the repo it should work on — for
this project, `target_repo = agentic-bylaw-system`. Practically that means:

- The NM creates worktrees, branches, and commits **in** this repo. It is not
  run **from** this repo.
- `.night-manager/` here is runtime output the NM writes into its target — logs,
  state, run reports. It stays in `.gitignore` for exactly that reason.
- Anything you want to change about NM behavior — planning, agent supervision,
  the reviewer, the Mission Console — is a change in the `night-manager` repo.

## What stayed behind, and why

Two scripts the NM leaned on are this repo's own infrastructure rather than NM
code, so they stayed:

| Path | What it is | Covered by |
|---|---|---|
| `scripts/linear_client.py` | Generic Linear GraphQL client — no NM concepts in it. Lifted out of the deleted package; used by `scripts/investigate_coverage.py --promote-to-linear`. | `tests/test_investigate_coverage.py` |
| `scripts/rechain_migration.py` | Alembic dual-head guard. Always lived at this path; run it by hand per [E2E_TESTING.md — troubleshooting](E2E_TESTING.md#troubleshooting), and the NM also calls it before merging. | `tests/test_migration_rechain.py` |

`tests/test_e2e_port_recreate.py` moved out of the NM test directory for the
same reason: it exercises this repo's own `scripts/e2e-up.sh` and
`scripts/e2e-down.sh`, not anything the NM owns.
