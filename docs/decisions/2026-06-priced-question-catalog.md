# Phase 0 (revised) — Priced-Question Catalog ("Buy an Answer")

**Date:** 2026-06-12 · **Status:** ACCEPTED — supersedes the turn-pack model as
the launch product.
**Supersedes:** `docs/decisions/2026-06-pricing-v2-turn-packs.md` (turn packs —
no longer the launch SKU; see reconciliation below).
**Fits within:** `docs/decisions/2026-06-product-strategy-turns-plus-artifacts.md`
(this IS the bridge between free-form chat and full artifacts — sellable now,
no rendering/QA platform required).

## Decision

We cannot sell free-form chat — we sell **answers to questions**. The product
presents a **menu of priced questions**; the user picks one, pays, and gets the
answer the engine produces today (no report formatting — that evolves later).

- **Each catalog question has a fixed price.** Harder questions cost more.
  Buying a question ≈ buying a (lightweight) report, without calling it one.
- **Output is whatever the engine produces today.** Formatting is explicitly
  out of scope for launch.
- **An "Other" category** covers anything off-menu: the user types a question,
  an LLM analyses it and **quotes a price** (the quote step is free), they buy,
  we answer.
- **Consultant-style intake:** if a question needs inputs, the LLM detects what
  is missing, asks for it conversationally, records it, then answers.

## Why this over turn packs

- **Legible + value-anchored.** "Answer this for $X" anchors to the value of the
  answer, not app pricing — and kills the per-question taximeter anxiety that
  sank the $5–8/turn model.
- **Same engine, minimal build.** It is the existing chat tool-loop gated behind
  a question-selection + payment step. No new reasoning capability needed.
- **It is the artifacts bridge.** Productized questions are "artifacts-lite" —
  we capture the artifact business model (sell a deliverable) without the
  composition/rendering/QA platform.

## Liability — neutralized, not avoided

Not calling the output a "report" does **not** remove liability — payment +
reliance create it regardless of the label, and charging per answer arguably
*raises* the expectation vs. free chat. We neutralize it cheaply, from day one:

- ToS limiting liability + an on-output disclaimer ("bylaw-analysis tool, not an
  official municipal determination; verify before relying").
- Never represent output as a Licensed Professional Planner's work; never use
  "LPP"/"MCIP" (NS Professional Planners Act s.31 — the one bright line).
- **Failed-question rule:** if inputs are unworkable or the question can't be
  grounded, no charge / credit back. This is also the liability shield.

## Cost safety

Per-question pricing is only safe if per-answer **cost is bounded**. The
cumulative per-turn/per-answer breaker (ABS-305) is the load-bearing primitive:
a $15-quoted question must not cost $50 to serve. The "Other" quote LLM call is
**free** (we never charge to produce a price).

## Build plan

### Wave 1 — sell ASAP (MVP)
1. **ABS-305 — cumulative per-answer cost breaker.** (Existing; foundational.)
   Bounds the cost of each paid answer; underpins all per-question pricing.
2. **ABS-311 — question catalog + pricing model + Stripe.** A code catalog (like
   the old `packs.py`): question types, each with price, prompt/handler binding,
   and required-input schema. Stripe Prices per question (or a credit users spend
   on questions — pick the simpler checkout). *(replaces ABS-307)*
3. **ABS-312 — buy-an-answer flow + failed-question rule.** Select question →
   checkout → run the bounded answer through the existing engine → return raw
   output. No charge / refund when ungroundable. *(replaces ABS-308)*
4. **ABS-313 — liability baseline.** ToS update + on-output disclaimer copy.
   Small, day one.
5. **ABS-310 — question-menu page + e2e.** Pricing page becomes the question
   menu; new purchase-flow specs. *(rescoped)*
6. **ABS-314 — existing-credit migration.** Convert legacy tier credits → account
   credit / question credits; rework the signup trial grant. Needed before launch
   only if real paying credits exist. *(replaces ABS-309)*

### Wave 2 — evolve (fast-follow)
7. **ABS-315 — LLM intake detection.** Detect required inputs per question, ask
   conversationally, record, then answer.
8. **ABS-316 — "Other" free-form question → LLM price quote → buy → answer.**
   Free quote.
9. **ABS-306 — Sonnet validation.** Parallel anytime; margin upside.
10. **Output formatting / report polish.** Later; only once demand is proven (no
    issue yet).

## Critical path to launch

ABS-305 → ABS-311 → ABS-312 → ABS-313 → ABS-310, with ABS-314 in before any real
paying user exists. ABS-306 runs in parallel. Waves 2 items (ABS-315/316) ship
after launch. The MVP is "menu of fixed-price questions, pick → pay → raw
answer, disclaimer attached, ungroundable questions refunded."

## Linear reconciliation

| Issue | Action | Why |
|---|---|---|
| ABS-305 (cumulative cost breaker) | **KEEP** (note question-pricing context) | Foundational for per-answer cost bounding. |
| ABS-306 (Sonnet validation) | **KEEP** | Margin; packaging-independent. |
| ABS-307 (turn-pack catalog/Stripe) | **CANCEL** | Per-turn pricing; replaced by question catalog. |
| ABS-308 (turn-wallet spend path) | **CANCEL** | Per-turn spend; replaced by buy-an-answer flow. |
| ABS-309 (turn migration + turn trial) | **CANCEL** | Per-turn; replaced by question-credit migration. |
| ABS-310 (pricing page + e2e) | **RESCOPE** | Becomes the question-menu page. |
| ABS-311 | **CREATED** | Question catalog + pricing model + Stripe. |
| ABS-312 | **CREATED** | Buy-an-answer flow + failed-question rule. |
| ABS-313 | **CREATED** | Liability baseline (ToS + output disclaimer). |
| ABS-314 | **CREATED** | Existing-credit migration to question credits. |
| ABS-315 | **CREATED** | LLM intake detection (consultant-style). |
| ABS-316 | **CREATED** | "Other" free-form question → LLM price quote → buy. |

Executed in Linear 2026-06-12: ABS-307/308/309 cancelled (with pivot comments),
ABS-305/310 updated, ABS-311–316 created.
