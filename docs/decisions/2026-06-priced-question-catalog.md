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

## Launch question menu (from the productization catalog)

Source: `docs/AI-Powered Land Use Bylaw Reports in Halifax_ Productization
Catalog and Licensing Analysis.md` — Stage 1 products + its proxy price bands.
Five fixed-price questions + the "Other" path. All are EXCELLENT / low-
discretion / anyone-can-author items mapping to existing engine capabilities.
Prices are grounded defaults within the doc's bands and trivially adjustable.

| # | Question | Price (CAD) | Catalog anchor | Backing call |
|---|---|---|---|---|
| 1 | Permitted-use check — "Is [use] allowed at [address]?" | $79 | Item 2 | `get_address_profile` + `get_zone_profile` |
| 2 | Development-standards / as-of-right compliance check (height, setbacks, coverage, FAR, parking) | $149 | Item 6 / Stage-1 #4 | `get_zone_profile` + `evaluate_submission` |
| 3 | Zoning due-diligence summary (preliminary) | $199 | Item 3 (vs "$1,500+") | `get_address_profile` + `search` |
| 4 | Legal non-conforming determination | $199 | Item 8 (vs $800–$2,500) | `evaluate_submission` + `search` |
| 5 | Variance justification package (3 statutory criteria) | $299 | Item 1 ("a few hundred" vs $800–$5,000) | `evaluate_submission` |
| — | "Other" — off-menu, LLM-quoted | variable | — | ABS-316 |

Cost ~$1–2 USD/answer → >98% gross margin on price; every price sits well under
the consultant fee it displaces. The Stage-2 items (pro forma / development
potential, pre-application packages) are deliberately out of the launch menu.

## Checkout shape — pure per-question charge (decided 2026-06-12)

**No balances, no packs, no token/credit currency.** The user pays for exactly
the question they asked, one Stripe charge per question. The increased
per-transaction fee is accepted deliberately — it buys simplicity and avoids
holding customer money.

Rationale (settled after evaluating wallet / packs / tokens):
- **We never hold customer funds.** No deferred-revenue liability, no float to
  manage, no segregated-account question, no stored-value/money-transmitter
  optics. Every charge is fully earned at the moment we deliver the answer.
- **Keeps the dollar anchor.** "$X for this answer vs a $1,500 consultant memo"
  stays legible and invoice-friendly for professional buyers — the thing an
  abstract token currency would have obscured.
- **Forward-compatible.** When purchases formalize into reports, "one charge per
  deliverable" already fits — packs/wallets would have been a detour.

Mechanics:
- **Catalog questions:** a Stripe Price per question (fixed amount).
- **"Other" questions:** ad-hoc amount via a Stripe Checkout Session
  (`price_data` with the quoted amount) — no pre-created Price object needed.
- **Failed-question rule → authorize-then-capture.** Place a card authorization
  at checkout, run the answer, **capture on success / void the hold on failure**
  (ungroundable, unworkable inputs). Answers are near-immediate, so the auth
  window is never a constraint. This delivers "pay only if we deliver" with no
  refund hitting the customer's statement. (Charge-then-refund is the fallback if
  auth/capture proves awkward with the checkout integration.)
- **Free grants (trial) are an entitlement counter, not a balance** — "you have N
  free questions," consumed before any charge. Granting free uses is not holding
  customer money.

## Cost safety

Per-question pricing is only safe if per-answer **cost is bounded**. The
cumulative per-turn/per-answer breaker (ABS-305) is the load-bearing primitive:
a $15 question must not cost $50 to serve. Note the two ledgers are independent:
the customer is charged the **price** (fixed); the breaker guards our **API
cost**. The "Other" quote LLM call is **free** (we never charge to produce a
price).

## Build plan

### Wave 1 — sell ASAP (MVP)
1. **ABS-305 — cumulative per-answer cost breaker.** (Existing; foundational.)
   Bounds the cost of each paid answer; underpins all per-question pricing.
2. **ABS-311 — question catalog + pricing model + Stripe.** A code catalog (like
   the old `packs.py`): question types, each with a fixed price, prompt/handler
   binding, and required-input schema. One Stripe Price per catalog question; no
   pack SKUs, no credit ledger. *(replaces ABS-307)*
3. **ABS-312 — buy-an-answer flow + failed-question rule.** Select question →
   per-question checkout (authorize) → run the bounded answer through the existing
   engine → **capture on success, void on ungroundable failure** → return raw
   output. One charge per question; no balance. *(replaces ABS-308)*
4. **ABS-313 — liability baseline.** ToS update + on-output disclaimer copy.
   Small, day one.
5. **ABS-310 — question-menu page + e2e.** Pricing page becomes the question
   menu; new purchase-flow specs. *(rescoped)*
6. **ABS-314 — retire legacy credits + trial rework.** No conversion target
   exists (pure per-question has no balance), so **grandfather** any existing
   legacy tier credits on the legacy path until their 30-day window expires, then
   remove that path. Rework the signup trial grant from "3 standard credits" to a
   free-question **entitlement counter** ("N free questions"). Likely small —
   pre-launch there may be no real paid credits to grandfather at all.
   *(replaces ABS-309)*

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
