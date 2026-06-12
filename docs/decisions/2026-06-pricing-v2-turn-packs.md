# Pricing v2 — One Product: Turn Packs

**Date:** 2026-06-11 · **Updated:** 2026-06-12 · **Status:** ACCEPTED
(structure decided 2026-06-12; implementation tracked in the Linear issues
listed at the bottom)
**Grounding:** `docs/COST_MODEL.md` (measured $0.99 USD mean/turn, ABS-303 N=8)

## Problem

The quick/standard/complex tiers sell *quantity* dressed up as *grades*: all
three deliver identical model quality, and the token budgets actually deliver
~1 / 1–2 / ~4 turns respectively (`COST_MODEL.md` §2). Customers can't reason
about token budgets, the "what do I buy when I run out" answer is a tier-
upgrade flow nobody understands, and Quick's budget is below single-turn
consumption. We are already selling turns — illegibly.

## Decision

**One sellable unit: the turn** (one user question → one bounded tool-loop
run → one grounded answer). **Sold only in packs** — there is no single-turn
SKU. Per-turn price runs **$8 down to $5** with volume:

| Pack | Turns | $/turn | Pack price (CAD) | discount_bps off $8 list |
|---|---|---|---|---|
| Starter | 3 | $8.00 | **$24** | 0 |
| Standard | 10 | $7.00 | $70 | 1250 |
| Pro | 30 | $6.00 | $180 | 2500 |
| Firm | 100 | $5.00 | $500 | 3750 |

Quality is identical for every customer at every volume — deliberate:
graded-quality compliance advice is a liability posture we reject.

**Why packs-only:**

- Kills the taximeter effect: the purchase moment happens once ("$24 covers
  my project research, follow-ups included"), not per question.
- Kills the worst transaction economics: a hypothetical $8 single pays
  Stripe ~6.6% of revenue (2.9% + $0.30); the $24 pack pays ~4.2%, the
  $500 pack ~3%.
- One list price ($8) + the existing `Pack.discount_bps` machinery expresses
  the whole ladder — no new pricing concepts in code.

**Margins (incl. Stripe fees, Opus 4.5 at measured $1.36 CAD/turn expected):**
~70–79% expected across the ladder; worst-case (every turn at the $3.40 CAD
bounded ceiling) stays positive everywhere, bottoming at ~28% on Firm. The
band clears the Opus floor, so **this pricing does not depend on the Sonnet
switch** — if ABS-293's 7.2× validates on the full suite, margins rise to
~93–97% with no repricing.

**Trial: 3 free turns** (~$4 CAD cost vs ~$10 today). The trial is the
homeowner entry product — the door price is $24, so the first paid moment
must come mid-engagement, after the trial answered the first question.

**Failed-turn rule:** a turn that produces no grounded answer (tool failure,
unresolvable address, forced synthesis with zero evidence) is not consumed.
Cheap to honor; it is what makes a $24 commitment feel safe.

**Known trade-off, accepted:** the cheapest possible purchase rises from
$12.50 to $24. If post-launch data shows leakage at the trial-exhaustion
moment ("one more question" → $24), the escape valve is a one-time in-product
top-up micro-pack — deliberately NOT in scope at launch; let the data ask.

### The turn must be a bounded unit (precondition)

Expected turn cost is $0.99 USD but today's worst case is effectively
unbounded (documented $12.93 runaway; the existing breaker is per-request
only). Before turn packs launch:

1. **Cumulative per-turn breaker**: sum billed-equivalent tokens across all
   of a turn's requests; force synthesis at ~165k (~**$2.50 USD ceiling**).
2. **Uniform iteration cap = 15** (measured natural completions max at 9),
   replacing the per-tier 8/20/55 caps when tiers are removed.
3. **"Go deeper" affordance**: a turn that hits either cap delivers its
   synthesis *plus* an offer to continue; continuing spends another turn and
   resumes with full context. Replaces the tier-upgrade flow — deep files
   naturally consume 4–8 turns instead of a $75 SKU.

### Mechanics (reuse, don't rebuild)

- **A turn is a `case_credit` row** with `tier="turn"` — per-credit storage,
  grant/refund paths, and analytics survive intact. Spend = consume one row
  per user message instead of decrementing a per-case token ledger.
- **Cases become free containers** (address anchor, history, 30-day window
  for organization only). Case-open consumes nothing; the per-case token
  budget (`case_budget_for`) and Layer-3 upgrade machinery are deleted.
- **Stripe**: 4 Prices replace 12; same `STRIPE_PRICE_<TIER>_<PACK>` env-var
  convention (`STRIPE_PRICE_TURN_STARTER`, …).

### Migration of existing credits

Convert unspent credits at purchase-price ÷ $8, rounded **up** (generous):
quick → **2** turns, standard → **5**, complex → **10**. One Alembic data
migration; in-flight open cases keep legacy token-budget enforcement until
they expire (30 days), then the legacy path is removed.

## Remaining open decisions

1. **Turn expiry** — none, or 12 months? (Today credits live until used.)
2. **Iteration cap 15 vs 20** — cost ceiling vs depth-per-turn; affects how
   often "go deeper" fires on heavy files.

Resolved 2026-06-12: price structure ($8→$5 packs-only ladder), no single-
turn SKU, trial stays at 3 turns, top-up micro-pack deferred to post-launch
data.

## Implementation plan (one Linear issue each, in dependency order)

1. **ABS-305 / P1 — Cumulative per-turn cost breaker.** Standalone; also
   closes `COST_MODEL.md` tail-risk #1 under *current* pricing. Per-tier
   iteration caps untouched here.
2. **ABS-306 / P2 — Sonnet validation on the 20-case suite** (ABS-293
   follow-up, ~$30–50 spend). Independent of pricing; pure margin upside.
3. **ABS-307 / P3 — Catalog rewrite**: `turn` product + 4 pack SKUs, Stripe
   Prices, checkout/webhook, `/v1/billing/catalog`.
4. **ABS-308 / P4 — Turn-wallet spend path**: consume one credit per user
   message, cases become free containers, delete per-case budgets + upgrade
   flow, uniform cap 15, go-deeper affordance, failed-turn rule. Depends on
   ABS-305 + ABS-307.
5. **ABS-309 / P5 — Credit migration + trial grant** (3 standard credits →
   3 turns). Depends on ABS-307/308.
6. **ABS-310 / P6 — Pricing page, persona/marketing copy, e2e rewrite**
   (`pricing-page-amounts.spec.ts` + new wallet-spend specs). Depends on
   ABS-307; final issue before launch.
