# Instrumented UI tests

End-to-end browser tests for the advisor web app. Drives the full local stack (Next.js → FastAPI → Postgres) with a deterministic `MockGateway` standing in for Anthropic, so the suite is fast and offline.

Pair with `architecture.md` for the data flow this exercises, and `DEPLOYMENT.md` for what's running in prod that the suite mimics.

## What it covers

| Tier | Specs | Runs on | Wall clock |
|---|---|---|---|
| Smoke | 8 | All 4 viewport projects (32 runs) | ~12s |
| Functional | 7 | `desktop-chrome` only | ~4s |
| Accessibility | 3 | `desktop-chrome` + `mobile-iphone` | ~2s |
| Visual | 2 | `desktop-chrome` only | ~1s |

Smoke covers the critical paths: marketing landing, dev-fallback sign-in, case open, chat SSE, no-case gate, cases list, billing dormant state, admin gate. Functional covers deeper flows: classifier output, existing-case match, agent-driven upgrade offers, error states, multi-turn chat, composer keyboard handling. Accessibility runs `axe-core` and fails only on `critical` violations (logs `serious` as warnings). Visual takes pixel-tolerant snapshots of key screens.

## Stack topology

```
Playwright workers (browser contexts)
   │
   ▼
Next.js dev server  :3001        (NEXT_PUBLIC_API_BASE → :8001)
   │
   ▼
FastAPI test instance :8001       advisor.api.e2e_server
   ├─ MockGateway (callable_ dispatcher) — no Anthropic API key needed
   ├─ retrieval_service_factory → real Postgres pgvector
   └─ verifier=None              → X-Test-User-Id header path
   │
   ▼
Postgres test database            layer1_test
   └─ Alembic-migrated, seeded with one demo user + 200 credits per tier
```

The test stack runs on `:3001/:8001` so it never clashes with `make dev` (which uses `:3000/:8000`). The test database is a separate database name (`layer1_test`) on the same Postgres container — no second container.

The web dev server runs with `CLERK_SECRET_KEY=""`, which trips the `isClerkConfigured() === false` branch in `web/proxy.ts` — `/app` and `/admin` are then gated by a shared-password cookie (`abs_demo`). The Playwright fixture mints that cookie before each test by POSTing to `/api/access` with `DEMO_PASSWORD=e2e-demo-pw`.

The FastAPI test server runs with no Clerk verifier, so its routes accept an `X-Test-User-Id` header. The Next.js proxy (`web/lib/advisor-auth.ts`) forwards that header automatically when Clerk isn't configured.

## First-time setup

```bash
# 1. Python venv (only if not already done)
./scripts/dev-setup.sh

# 2. JS deps + Playwright browsers
make e2e-install
```

`make e2e-install` runs `npm install` in `web/` and `npx playwright install --with-deps`. The browser download is ~150 MB and only happens once.

## Common commands

```bash
make e2e              # boot stack, run full suite, tear stack down
make e2e-smoke        # boot stack, run smoke tier only (all 4 viewports), tear down
make e2e-up           # idempotent: boot Postgres + create layer1_test + migrate
                      # + seed demo user + start uvicorn:8001 + start next dev:3001
make e2e-down         # graceful teardown of uvicorn + next dev (DB is left alone)
make e2e-down --drop-db   # also drop layer1_test
```

When iterating on a single spec, leave the stack up and re-run Playwright directly:

```bash
make e2e-up
cd web

npx playwright test e2e/smoke/04-chat-sse.spec.ts          # one spec
npx playwright test --project=desktop-chrome               # all specs, one viewport
npx playwright test --ui                                   # interactive runner
npx playwright show-report                                 # last run's HTML report
npx playwright test --update-snapshots e2e/visual          # regenerate visual baselines
```

The stack stays up across reruns; the `globalSetup` re-seeds credits and verifies `/healthz` before each Playwright invocation.

## Viewport projects

Defined in `web/playwright.config.ts`:

| Project | Browser | Viewport | Hits the breakpoint |
|---|---|---|---|
| `desktop-chrome` | Chromium | 1440×900 | `lg`/`xl` |
| `tablet-ipad` | WebKit | iPad Pro landscape (1366×1024) | `md`/`lg` |
| `mobile-iphone` | WebKit | iPhone 15 (393×852) | base |
| `mobile-android` | Chromium | Pixel 7 (412×915) | base |

Only smoke runs on all four. Functional, a11y, and visual run on a narrower set — multiplying every test by every viewport bloats wall time without much added coverage.

## File layout

```
web/e2e/
├── playwright.config.ts       # projects, retries, timeouts, reporters
├── global-setup.ts            # re-seed credits + verify FastAPI healthz
├── fixtures/
│   └── test-env.ts            # base `test`, `openCaseViaApi` helper,
│                              #   abs_demo cookie auto-mint fixture
├── smoke/                     # critical-path coverage, all viewports
├── functional/                # deeper flows, desktop-chrome only
├── a11y/                      # axe-core sweeps
└── visual/                    # screenshot snapshots
```

Backend test seam:

```
src/advisor/
├── api/e2e_server.py          # FastAPI entrypoint with MockGateway wired in
└── llm/mock_dispatcher.py     # pattern-based callable for MockGateway
```

Orchestration:

```
scripts/
├── e2e-up.sh                  # boot the stack (idempotent)
├── e2e-down.sh                # tear down (uvicorn + next dev only)
└── seed_e2e_user.py           # idempotent demo-user + credits seed
```

## The mock dispatcher

`src/advisor/llm/mock_dispatcher.py` is a deterministic stand-in for Anthropic. It inspects each `CompletionRequest` and returns a shaped response. Three branches:

1. **`request.tools == []`** → treat as the pre-flight classifier. Returns a JSON text block matching `ClassifierResult`. Default `standard` / 0.85; keywords in the anchor or message override:
   - `simple` or `MOCK_QUICK` → `quick` / 0.92
   - `rezoning` or `MOCK_COMPLEX` → `complex` / 0.90
2. **`tools` non-empty AND no prior `tool_use` in history** → emit a `search_bylaw_evidence` tool_use block. The chat session loop executes the tool, appends a tool_result, and calls back.
3. **`tools` non-empty AND prior `tool_use`** → emit a final text answer mentioning the citation in `_DEFAULT_CITATION`.

Scenario keywords any spec can put in the user message to drive specific paths:

| Keyword | Effect |
|---|---|
| `MOCK_REQUEST_UPGRADE` | First call returns `request_tier_upgrade` tool_use instead of search — drives the `case_upgrade_offer` SSE event. |
| `MOCK_EMPTY_TURN` | Returns empty text, no tool_use — exercises the "non-qualifying turn" refund path. |
| `no_citation` in user text | Final answer omits the source line — lets tests assert citation rendering. |

Add new scenarios by editing `_dispatch` in `mock_dispatcher.py`. Keep responses byte-stable — visual snapshots and a11y sweeps depend on it.

## Writing a new test

Most specs follow this shape:

```ts
import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("describes the user-visible behaviour", async ({ page }) => {
  const { caseId } = await openCaseViaApi();   // seeds a case via API
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await textarea.fill("Question that pattern-matches the dispatcher");
  await textarea.press("Enter");                // Enter > clicking Send; stabler on WebKit mobile

  await expect(page.getByTestId("chat-thread"))
    .toContainText(/expected mock response/i, { timeout: 15_000 });
});
```

Conventions:

- Use `openCaseViaApi()` to bypass the case-open form unless you're testing that form. The seed user has 200 credits per tier topped up before every run.
- Prefer role and placeholder queries over CSS selectors. The only `data-testid` in the app is `chat-thread` — add new ones sparingly.
- Use unique anchor labels (`` `case-${Date.now()}` ``) when opening cases so parallel workers don't collide on the 30-day match window.
- Smoke tests must work on every viewport — assume `lg+`-only elements may be `display: none`. Match against text that's visible on every breakpoint, or use `.first()`/`.last()` deliberately.
- File numbering (`01-...`, `02-...`) in `smoke/` just sorts the report; nothing depends on the order.

## Auth and credits

There is no real Clerk in the e2e stack. Every test runs as `clerk_user_id="demo-user-1"`:

1. The web proxy sees `CLERK_SECRET_KEY=""` and falls into the password-gate branch.
2. Playwright's `authedContext` fixture POSTs `{gate:"demo", password:"e2e-demo-pw"}` to `/api/access`, which sets the `abs_demo=1` cookie.
3. Subsequent requests from the browser reach `/app` and `/admin` without redirect.
4. When the browser hits `/api/chat` etc., the Next.js proxy forwards `X-Test-User-Id: demo-user-1`.
5. The FastAPI test server's user dependency (`verifier=None` path) accepts that header and resolves the seeded `advisor_user` row.

The seed (`scripts/seed_e2e_user.py`) provisions:

- `advisor_user` with `clerk_user_id="demo-user-1"`, `requests_per_minute_limit=600` (raised from the prod default of 6 to survive 6-worker parallel load).
- 200 available credits per tier (`quick`, `standard`, `complex`). `globalSetup` tops this up before every Playwright run, so a long suite doesn't drain into 402s.

### Sign-up / approval / logout lifecycle (auth specs)

`web/e2e/auth/` covers the Clerk lifecycle (sign-up → admin approval → login → logout/login) without hosting Clerk in tests. Three pieces let the suite simulate the journey:

1. **JIT user resolution in the test backend.** `advisor.api.e2e_server` mounts a header-auth user-dependency that — when a `db_session_factory` is wired (which the e2e entrypoint always does) — creates an `advisor_user` row on first sight of an unknown `X-Test-User-Id`, matches any approved `InviteRequest` by `X-Test-User-Email`, and gifts the row's `granted_starter_credits`. This mirrors `advisor.api.auth.resolve_or_create_user` so the post-Clerk invite-redemption code path is actually exercised.
2. **Test-only invite endpoint.** `POST /v1/_test/invite-approve` writes a row directly into `invite_request` in the `approved` state, bypassing the Clerk allowlist API that the production `/api/admin/invites/{id}/approve` route calls. Companion endpoint `POST /v1/_test/reset-user` deletes a test user (FK cascades clean up cases, credits, chat sessions).
3. **Per-context identity cookies.** `web/lib/advisor-auth.ts` honours three cookies in fallback mode:

   | Cookie | Forwarded as | Used by |
   |---|---|---|
   | `abs_test_sub_user_id` | `X-Test-User-Id` | clerk_user_id lookup + JIT-create |
   | `abs_test_sub_email` | `X-Test-User-Email` | invite redemption match |
   | `abs_test_sub_full_name` | `X-Test-User-Full-Name` | `advisor_user.full_name` |

   The `signInAs` fixture mints these alongside `abs_demo`; `signOut` clears all four. After sign-out a navigation to `/app` redirects through `/access`, matching the user-visible behaviour of Clerk's sign-out under the password-gate fallback.

Spec layout:

- `auth/01-signup-approve-login-case-chat.spec.ts` — flow 1 from the issue: invite via the `/signup` form, approve via the test endpoint, sign in as a fresh identity, open a case from `/cases/new`, get a streamed SSE reply. Exercises the JIT-create + redemption path end-to-end.
- `auth/02-logout-resume-same-case.spec.ts` — flow 2: same identity, first turn, sign out, navigate to `/app` (asserts the `/access` redirect), sign back in, open the existing case URL, send a second turn. Guards the `user_id`-mismatch class of bugs called out in `functional/multi-turn.spec.ts`.
- `auth/03-logout-resume-new-case.spec.ts` — flow 3: after logout/login, opening a *second* case must surface both rows on `/cases` for the same user.

Run them in isolation:

```bash
cd web
npx playwright test e2e/auth
```

The specs use unique synthetic identities (`auth-<slug>@e2e.test`) per run, so they don't share state with each other or with `demo-user-1`. Identity uniqueness keeps parallel workers from colliding on the `invite_request.email` UNIQUE constraint; the test endpoint also drops prior rows for the same email defensively.

## Test seams — what's mocked, what's real

| Layer | In production | In the e2e suite | Why |
|---|---|---|---|
| Anthropic LLM | `AnthropicGateway` | `MockGateway(callable_=build_dispatcher())` | Determinism, speed, no API key. |
| Clerk auth | JWT verifier + allowlist API | `verifier=None` + `X-Test-User-Id/-Email/-Full-Name` headers + `/v1/_test/invite-approve` | Avoids Clerk dev-tenant flakiness; the JIT-create code path still exercises the `resolve_or_create_user` + invite-redemption logic. |
| Postgres | Real | Real, separate DB (`layer1_test`) | Migrations, FKs, credit-reservation logic are part of what we test. |
| Retrieval (pgvector) | Real | Real, seeded synthetic bylaw on demand | Reuses `scripts/seed_synthetic_fragment.py`. |
| Google Geocoder | Optional | Disabled by `tests/conftest.py` default | Off in tests; opt-in only. |
| Stripe | Live or dormant | Dormant (503) | Tests the dormant path the frontend actually probes. |
| Browser-side fetch | Real | Real | We want the full proxy round-trip. |

Tests that need a particular failure mode at the proxy boundary use Playwright's `page.route()` to synthesize the response (see `e2e/functional/error-state.spec.ts`).

## Catching regressions before you do

Two recommended trigger points:

1. **Pre-push git hook**. The smoke suite runs in ~12s and covers every critical path on every viewport. A `.git/hooks/pre-push` that runs `make e2e-smoke` keeps obvious regressions out of `main`.
2. **`/loop 30m make e2e-smoke`** while you're working on UI-heavy features. Surfaces breakage within half an hour without you having to remember.

CI integration is left out of scope for now (the suite is local-first). `playwright.config.ts` already honours `process.env.CI` for retries and reporter switches — adding a `.github/workflows/e2e.yml` later is a small follow-up.

## Parallel worktrees

Each worktree has its own compose project (different project name → its own Postgres container and `layer1_test` DB), but the host-side ports collide by default. To run `make e2e` from two worktrees at the same time, override the port triplet in the second one before invoking the script:

```bash
PG_PORT=5433 \
E2E_FASTAPI_PORT=8002 \
E2E_WEB_PORT=3002 \
E2E_API_URL=http://127.0.0.1:8002 \
E2E_BASE_URL=http://localhost:3002 \
  make e2e
```

`scripts/e2e-up.sh` derives `POSTGRES_HOST_PORT` from `PG_PORT` and exports it so `docker-compose.yml`'s `"${POSTGRES_HOST_PORT:-5432}:5432"` interpolation picks it up. `playwright.config.ts` already reads `E2E_BASE_URL` for `baseURL`, and `global-setup.ts` / `fixtures/test-env.ts` read `E2E_API_URL` for upstream calls.

The first worktree (using all defaults) and the second (using the overrides above) can each run the full suite end-to-end without seeing each other.

Note: the test database `layer1_test` lives inside each worktree's own Postgres container, so concurrent runs don't share state. The seeded demo user, credits, and synthetic bylaw are all per-container.

## Troubleshooting

**`make e2e-up` says ports already in use.** A previous run didn't tear down cleanly. `pkill -9 -f advisor.api.e2e_server` and `pkill -9 -f "next dev -p 3001"`, then re-run. If another worktree is intentionally running e2e, use the override recipe in [Parallel worktrees](#parallel-worktrees) instead.

**`alembic upgrade head` fails with `value too long for type character varying(32)`.** Revision id `0008_advisor_billing_subscription` is 33 chars and overflows the default `alembic_version.version_num`. `e2e-up.sh` pre-creates the table with `VARCHAR(255)` to work around this for fresh databases — confirm the pre-create ran by checking `\d alembic_version`.

**FastAPI logs show `database "layer1_test" does not exist` even though it was created.** Symptom of two worktrees both trying to bind `host:5432` — one container ends up unpublished and the host-side alembic / uvicorn hit the wrong Postgres. Use the parallel-worktrees recipe to give each worktree its own host port, or tear down the stack of the worktree you're not actively using.

**Tests hit 402 Payment Required.** Credits drained — `globalSetup` should be topping them up but didn't fire. Run `scripts/seed_e2e_user.py --credits-per-tier 200` manually with the right `DATABASE_URL`.

**Tests hit 429 Too Many Requests.** The user's `requests_per_minute_limit` is too low. Re-run the seed; it raises any user with `< 600` to `600`.

**WebKit chat-sse flakes ~1 in 5 under parallel load.** Documented in [ABS-6](https://linear.app/agenticbylawsystems/issue/ABS-6/investigate-webkit-only-sse-flake-under-parallel-chat-load-harness-vs). Re-run the failing spec in isolation to confirm it's the parallel-load flake (passes 4/4 when alone).

## Determinism contract

The suite is meant to be deterministic — every flake is a bug. Repeat-run policy:

- Run the full suite five times back-to-back. Expect 5/5 on Chromium projects, and ≥4/5 on WebKit projects until ABS-6 is resolved.
- Any flake on a Chromium project, or a brand-new flake on WebKit, should fix the seam, not add `retries`. Retries mask intermittent backend races and we built the e2e_server explicitly to expose them.

## See also

- Plan that produced this: `~/.claude/plans/come-up-with-a-fizzy-axolotl.md`.
- Mock gateway baseline: `src/advisor/llm/mock.py`.
- Backend chat handler the SSE specs exercise: `src/advisor/api/app.py:358`.
