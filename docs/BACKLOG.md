# Backlog

Tracks deferred work that's been agreed on but not yet scheduled. Each
item is a logical unit of work that can be picked up independently —
pull one off the top, branch, ship, check it off. Add new items by
category; keep entries terse and link out for longer designs.

## Cost Analytics

Captured 2026-05-12 after deploying the five cost-optimization chips
that took daily token spend from ~$37 (2026-05-11) to a fraction of
that on the first comparable smoke test (single address query: 270k →
11k input tokens, ~25× reduction). The items below either make those
savings *visible* or *verifiable*.

- [ ] **Build `/v1/admin/usage` endpoint** — per-prompt and per-session
  cost rollups. Reads `advisor_usage_event`, groups by `session_id`,
  rolls up by user and time window. This is the dashboard surface that
  replaces SSHing into Postgres to eyeball cost.

- [ ] **Add `src/advisor/pricing.py`** keyed by model (Opus 4.5, Opus
  4.6, plus cache-write and cache-read rates). Compute
  `cost_estimate_cents` on every `UsageEvent` row at write time. The
  column exists in `src/advisor/db/models.py` but is hardcoded to 0
  today.

- [ ] **Expand `UsageEvent.metadata_json`** to capture per-turn detail:
  `cache_creation_input_tokens`, `cache_read_input_tokens`,
  `iterations` (tool-loop count), `iteration_input_tokens` (array of
  per-iteration input sizes), `retrieval_blob_chars` per tool call,
  `user_prompt_text`, `user_prompt_chars`. Stored as JSON so no schema
  migration is needed; analytics queries unnest.

- [ ] **Wire usage recording onto the in-memory dev path** so local
  demo traffic is visible too. Currently gated on
  `isinstance(store, DbSessionStore)` in `src/advisor/api/app.py`
  (~line 298). Prod path unaffected — this is for local debugging
  parity.

- [ ] **Verify chip #1 prompt caching is actually firing in production.**
  The 2026-05-12 smoke test showed `cache_read_input_tokens: 0` on the
  final iteration of a multi-iteration turn — either caching isn't
  wired correctly or the turn happened to be single-iteration. Once
  per-iteration cache stats above are captured, this becomes a data
  question instead of a code-reading question.

- [ ] **Design model-tier routing** — Haiku for trivial lookups, Sonnet
  for mid-complexity, Opus for long-context turns. Deferred from the
  2026-05-11 optimization plan because it changes answer behavior, not
  just cost. Needs product-quality discussion + an eval set before
  implementation.

## Case-based pricing

Captured 2026-05-14 after shipping the case-credit billing model end-
to-end (plan: `/Users/christopherrafuse/.claude/plans/add-a-new-cost-keen-frost.md`).
The three items below are pre-launch loose ends that the velocity
pass deferred.

- [ ] **Provision Stripe Price IDs.** Twelve `STRIPE_PRICE_<TIER>_<PACK>`
  env vars need real Price IDs in both test and live modes; each must
  match `PackOffer.amount_due_cents` from
  `src/advisor/billing/packs.py`. Default: mint them in the Stripe
  dashboard. Alternative: a one-shot
  `scripts/create_stripe_prices.py` that creates them idempotently and
  prints the env-var lines. Reusable for test → live promotion. Until
  this lands the catalog renders with every offer's `available: false`
  flag set, so the pricing page shows but checkout is disabled.

- [ ] **Code-vs-Stripe price-drift assertion at boot.** `packs.py` is
  the source of truth for catalog prices; Stripe-side Prices are
  configured independently. On FastAPI startup (when
  `ADVISOR_BILLING_ENABLED=true`), iterate `all_offers()`, fetch each
  configured Price via `stripe.Price.retrieve(...)`, and assert
  `unit_amount == offer.amount_due_cents AND currency.lower() == "cad"`.
  Fail loud at boot rather than ship a half-correct catalog. One
  network round-trip per configured Price at startup.

- [ ] **Auto-send first message on case-open redirect.** The
  `/cases/new` flow already passes `first_message=...` in the
  redirect URL, but the chat page only reads `case_id`. Touch points:
  `web/app/(product)/app/page.tsx` (read `first_message` from
  `useSearchParams`, prefill composer state, clear after first send
  to avoid replay on refresh) and `web/components/product/composer.tsx`
  (accept a `defaultValue` prop). Small UX win; skipped during the
  velocity pass to keep the chat page's surface area bounded.

## Auth surface (resolved 2026-05-14 in web:0.6.1)

Two related top-nav / auth-affordance bugs observed after `web:0.6.0`
deploy:
1. No Sign-in / Sign-up CTA on `/` or marketing pages while
   unauthenticated.
2. `/admin/invites` redirected to `/access?gate=admin` (legacy
   cookie-gate fallback) instead of Clerk's `/sign-in`.

Both shared one root cause: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` is
inlined by Next.js at build time, and `web:0.6.0` was built without
`--build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=…` — so every
`process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` read in the bundle
resolved to `undefined` in production.

Fixed by two changes shipped together in `web:0.6.1`:
- **`isClerkConfigured()` in `web/proxy.ts` and `web/lib/advisor-auth.ts`
  now reads `CLERK_SECRET_KEY`** (no `NEXT_PUBLIC_` prefix → resolved
  at runtime from container env, immune to the inlining trap). The
  client-side `CLERK_ENABLED` checks in `top-nav.tsx` and
  `sidebar.tsx` still read the publishable key — they have to, since
  they run in the browser.
- **`web/Dockerfile` build-arg is now documented as required** in
  `docs/DEPLOYMENT.md` so future builds don't hit the same footgun.

The `/access` page and `abs_demo` / `abs_admin` cookie gate remain
in the codebase as the fallback for unconfigured-Clerk deployments
(local dev, future Clerk outages). Production no longer reaches them
when Clerk is configured.
