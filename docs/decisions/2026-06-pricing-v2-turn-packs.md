# Pricing v2 — One Product: Turn Packs

**Date:** 2026-06-11 · **Status:** PROPOSED (design for review; no code yet)
**Grounding:** `docs/COST_MODEL.md` (measured $0.99 USD mean/turn, ABS-303 N=8)

## Problem

The quick/standard/complex tiers sell *quantity* dressed up as *grades*: all
three deliver identical model quality, and the token budgets actually deliver
~1 / 1–2 / ~4 turns respectively (`COST_MODEL.md` §2). Customers can't reason
about token budgets, the "what do I buy when I run out" answer is a tier-
upgrade flow nobody understands, and Quick's budget is below single-turn
consumption. We are already selling turns — illegibly.

## Proposal

**One sellable unit: the turn** (one user question → one bounded tool-loop
run → one grounded answer). One catalog row replaces three tiers; the four
existing pack SKUs and their discount machinery carry over unchanged.

| SKU | Turns | Discount | Price (CAD) |
|---|---|---|---|
| PAYG | 1 | — | **$10** |
| Starter | 5 | 5% | $47.50 |
| Pro | 20 | 15% | $170 |
| Enterprise | 100 | 25% | $750 |

Entry price *drops* ($10 vs $12.50 Quick); effective per-turn price stays
inside today's realized $12.50–$32 band. Trial: **3 free turns** (~$3 USD
cost vs ~$10 today). Quality is identical for every customer at every volume
— deliberate: graded-quality compliance advice is a liability posture we
reject.

### The turn must be a bounded unit (precondition)

Expected turn cost is $0.99 USD but today's worst case is unbounded-ish
(documented $12.93 runaway; per-request-only breaker). Before launch:

1. **Uniform iteration cap = 15** (measured natural completions max out at 9).
2. **Cumulative per-turn breaker**: sum billed-equivalent tokens across the
   loop's requests; force synthesis at ~165k (~**$2.50 USD ceiling**/turn).
3. **"Go deeper" affordance**: a turn that hits either cap delivers its
   synthesis *plus* an offer to continue; continuing spends another turn and
   resumes with full context. This replaces the tier-upgrade flow — deep
   files (old "Complex") naturally consume 4–8 turns instead of a $75 SKU.

Margins at $10 CAD (≈$7.30 USD): **~86% expected, ~66% at the all-worst-case
ceiling**; a future Sonnet switch (ABS-293: 7.2× cheaper, pending 20-case
suite) lifts expected margin to ~98%.

### Mechanics (reuse, don't rebuild)

- **A turn is a `case_credit` row** with `tier="turn"` — per-credit storage,
  grant/refund paths, and analytics survive intact. Spend = consume one row
  per user message instead of decrementing a per-case token ledger.
- **Cases become free containers** (address anchor, history, 30-day window
  for organization only). Case-open no longer consumes anything; the per-case
  token budget (`case_budget_for`) and Layer-3 upgrade machinery are deleted.
- **Stripe**: 4 new Prices replace 12; same `STRIPE_PRICE_<TIER>_<PACK>`
  env-var convention (`STRIPE_PRICE_TURN_PAYG`, …).

### Migration of existing credits

Convert unspent credits at purchase-price ÷ $10, rounded **up** (generous):
quick → 2 turns, standard → 4, complex → 8. One Alembic data migration;
in-flight open cases keep legacy token-budget enforcement until they expire
(30 days), then the legacy path is removed.

## Open decisions (need a call before implementation)

1. **Turn price point** — $10 modeled here; anything ≥ $5 holds ≥ 80%
   expected margin, so this is a market/anchoring choice, not a cost one.
2. **Turn expiry** — none, or 12 months? (Today's credits live until used;
   cases expire at 30 days.)
3. **Iteration cap 15 vs 20** — cost ceiling vs depth-per-turn; affects how
   often "go deeper" fires on heavy files.
4. **Trial size** — 3 turns modeled; 5 turns ≈ $5 USD CAC.

## Rough implementation phases (each its own issue/branch)

1. Cumulative per-turn breaker + uniform cap (ships standalone; also closes
   `COST_MODEL.md` tail-risk #1 under the *current* pricing).
2. Catalog/packs rewrite + Stripe prices + checkout/webhook.
3. Turn-wallet spend path; delete tier-upgrade flow; "go deeper" UX.
4. Credit migration + pricing page + persona/marketing copy + e2e specs
   (`pricing-page-amounts.spec.ts` rewrite, new wallet-spend specs).
