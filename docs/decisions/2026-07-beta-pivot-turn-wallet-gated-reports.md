# Beta Pivot — Turn-Based Chat SKU Exposed, Report SKUs Gated

**Date:** 2026-07-14 · **Status:** ACCEPTED (design spec frozen; implementation
tracked in the Linear sub-issues at the bottom)
**Issue:** ABS-379 (umbrella) — sub-issues ABS-380 … ABS-391
**Supersedes:** [`2026-06-pricing-v2-turn-packs.md`](2026-06-pricing-v2-turn-packs.md)
(turn *packs* → prepaid *token wallet, pure burn-down, presented as turns*)
**Related:** [`2026-06-product-strategy-turns-plus-artifacts.md`](2026-06-product-strategy-turns-plus-artifacts.md),
[`2026-06-priced-question-catalog.md`](2026-06-priced-question-catalog.md),
[`2026-07-followup-model-conversation-continuation.md`](2026-07-followup-model-conversation-continuation.md)
**UX source of truth:** `design/abs-ux-mockup-convo-sku-gated-reports/`
(pricing + /cases/new fully designed; chat workspace / billing / small-surface
sweep are design-pending per that package's README — those issues build to the
global rules and fold in mockups when delivered).

## Problem

The five report SKUs ($79–$299 priced questions, ABS-324) can't reach quality
in the beta window. The conversation/chat product worked well *before* report
work began. Today's codebase is the inverse posture we need for beta:

- reports are **live**,
- chat is **hidden** behind `ADVISOR_CONVERSATION_ENTRY_ENABLED=false`,
- chat billing is the **retired** tier / case-credit model.

The beta must flip this: expose the chat product on a legible prepaid balance,
and hide the reports behind individually releasable gates so a single slug can
graduate to "quality" and be turned on without a redeploy.

## Decision

### Locked product decisions

1. **Account-level token balance (pure burn-down).** Signup grants free trial
   tokens; Stripe tops up; each chat turn burns *actual* usage
   (`tokens_input + tokens_output`); opening a case is **free**. Pre-flight
   refuses a turn at balance ≤ floor (the last allowed turn may overdraw
   negative). No tiers / credits / upgrade flow in the UI.
2. **Both billing postures supported:** free-trial-only
   (`ADVISOR_PAYMENTS_ENABLED=false`) and paid (Stripe top-ups live).
3. **Per-report gates.** Each report slug is individually enable/disableable
   via config **without redeploy**. Disabled = hidden from menus + purchase
   paths rejected server-side; **already-purchased reports stay
   viewable/refinable forever**.
4. **UX speaks in turns, never tokens.** A backend-owned conversion factor
   `ADVISOR_TOKENS_PER_TURN` drives every balance-bearing response
   (`approx_turns*` + `tokens_per_turn` on the wire). Displays are "~N turns" +
   "Counts are approximate — complex questions use more." Calibrate the factor
   by replaying the ABS-305 estimator over persisted transcripts before launch.

### Business parameters (calibrated ABS-416, resized ABS-404, 2026-08-08)

| Parameter | Default | ABS-416 | Was (placeholder) |
| -- | -- | -- | -- |
| `ADVISOR_TOKENS_PER_TURN` | 175,000 | 175,000 | 2,500 |
| `ADVISOR_SIGNUP_TOKEN_GRANT` | **525,000 = 3 turns** | 1,750,000 = 10 | 25,000 |
| Top-ups (CAD) | small $15 → 1.4M = 8 turns · medium $50 → 5.25M = 30 · large $120 → 14M = 80 | same | 20k / 75k / 200k |
| `ADVISOR_CHAT_MIN_BALANCE_TOKENS` (floor) | 0 | 0 | 0 |
| `ADVISOR_LOW_BALANCE_WARN_TOKENS` (warn) | **175,000 = 1 turn** | 350,000 = 2 | 5,000 |
| `ADVISOR_TURN_MAX_WALLET_TOKENS` (ABS-404) | **350,000 = 2 turns** | — | — |
| `ADVISOR_CHAT_MAX_ITERATIONS` | 20 | 20 | 20 |

The 2,500 placeholder was ~70x low: measured against prod, a real grounded
question burns 103k–248k tokens, so every surface promised dozens-to-hundreds
of questions against a wallet worth a handful. 175,000 is the midpoint of the
two full-research prod questions and sits above the recent all-turn mean, so
the advertised count errs toward under-promising. The token-denominated knobs
were all sized in *turns* against the old rate, so each is rescaled by the same
factor and the turn counts the product advertises are unchanged. Full sample
and derivation: `src/advisor/billing/turns.py`.

**ABS-404 corrected the cost anchor those turn counts were priced against.**
ABS-416 assumed ~$0.55 USD per 100k wallet tokens. The wallet counts
`input + output` only, and cache writes plus reads are 35% of the dollar cost,
so the honest denominator is cost per *wallet-counted* token — measured at
**~$28.9/MTok USD** in `docs/COST_MODEL.md` (ABS-303, real API, N=8). A 175k
turn is ~$5.05, not ~$0.96. Consequences: the grant drops to 3 turns (~$15 per
free no-card signup, down from ~$50), the warn threshold drops to 1 turn
(2 turns of a 3-turn grant would have warned on every user's first question),
and the price ladder is now a known-blocking open question (below).

## Design specification (D1–D8)

### D1 — Wallet
`User.token_balance` (BigInteger, **signed**) + append-only
`advisor_token_transaction` (`entry_type` ∈ grant|topup|burn|adjust, signed
`amount_tokens`, `balance_after`, UNIQUE `stripe_checkout_session_id`, optional
session / case / usage-event FKs). Service `src/advisor/db/wallet.py`:
`grant_tokens`, `credit_topup`, `burn_tokens` (**no floor check**),
`adjust_tokens`, `get_balance`; all mutations `SELECT … FOR UPDATE` on the user
row. Migration `alembic/versions/0023_token_wallet.py`; **no backfill** — lazy
self-heal grant.

### D2 — Signup grant
`grant_signup_tokens_if_needed`, idempotent on
`metadata_json["token_grant_issued"]`, called from `resolve_or_create_user`
(**both branches**). `free_questions_remaining` untouched.

### D3 — Free case open + chat pre-flight
`POST /v1/cases` uses `open_case_free`; `tier` accepted-but-ignored, `credit_id`
null; upgrade endpoint → **410 `tier_model_retired`**. Chat pre-flight keeps
429 / 412 / `case_id_required`; **deletes** CaseCredit reserve/consume +
`case_no_active_tier`; **adds 402 `insufficient_tokens`** at balance ≤ floor
(skipped for `unlimited_credits`). Settlement `_settle_token_burn`: burn actual
usage (no refund heuristic), keep `add_case_tokens`, emit SSE `token_balance`
`{balance_tokens, burned_tokens, approx_turns_remaining, low_balance,
warn_threshold_tokens}` **every turn**; remove
`case_budget_warning` / `case_upgrade_offer` + unregister the
`request_tier_upgrade` tool. `token_budget_remaining` stays None; cost circuit
breakers stay; `max_iterations` uses the env default when tier is None.

**Amended by ABS-404.** D1's "the last allowed turn may overdraw negative" was
unbounded in practice: the floor is 0, so any positive balance starts a turn,
and the only ceilings on that turn were pre-flight *estimates* of *input*
tokens. The char heuristic under-counts JSON tool_results and never models
output at all, so a prod turn burned 247,566 wallet tokens under a 165k
cumulative cap. `run_tool_loop` now carries a third breaker fed by **measured**
`input + output` — the wallet's own unit — tripping `wallet_cap_trip` at
`ADVISOR_TURN_MAX_WALLET_TOKENS` (default 2 turns). Overdraw stays by design;
it is now bounded at roughly one extra turn rather than arbitrarily deep. The
breaker is off on the paid-report rail (`answers.run_turn` passes 0), which is
priced per slug in Stripe and never touches the wallet.

Also amended: `grant_signup_tokens_if_needed` (D2) takes the user row's write
lock around its check-and-set. The metadata flag alone was a TOCTOU gate, and
prod issued two grants to one user on 2026-07-17 through it.

### D4 — Top-up checkout + webhook
Fixed SKUs; env `STRIPE_PRICE_TOPUP_{SMALL,MEDIUM,LARGE}`; catalog
`src/advisor/billing/topups.py` is server-side truth.
`POST /v1/billing/checkout/topup {sku}`; payments off → **503
`payments_disabled`**; immediate capture. Webhook routes on
`metadata.topup_sku` **before** the pack branch; tokens resolved from the server
catalog with a price-id reverse-lookup fallback. Idempotency = `stripe_event_id`
dedupe + UNIQUE ledger session id (IntegrityError → `duplicate_topup`, **never
5xx**).

### D5 — Per-report gates
Env `ADVISOR_ENABLED_QUESTIONS` (csv slugs; `*` = all; unset/empty = none,
**deny-by-default**), read at request time. Disabled slug: filtered out of
`GET /v1/billing/questions` (live + dormant); **503 `question_disabled`** on
checkout / intake / free-start / answer-start (authorized). **No gate** on
purchase reads / refine / reports-list / answer delivery. Runbook: drain
`authorized` holds before disabling a slug. Free-start on enabled slugs keeps
working payments-off.

### D6 — API contract
Turns conversion is backend-owned:
`approx_turns = floor(tokens / ADVISOR_TOKENS_PER_TURN)`, floored at 0 for
display. Public `GET /v1/billing/topups`; authed `GET /v1/billing/wallet`
(+ `/transactions`, cursor-paged); `/me` gains `token_balance`. Menu omits
disabled slugs.

| Surface | Code / event |
| -- | -- |
| chat, balance ≤ floor | **402 `insufficient_tokens`** (detail incl. `approx_turns_remaining`) |
| reports, disabled slug | **503 `question_disabled`** |
| top-up, payments off | **503 `payments_disabled`** |
| tier upgrade | **410 `tier_model_retired`** |
| every balance-bearing turn | SSE event **`token_balance`** |

New Next proxies: `/api/billing/topups` (skipAuth),
`/api/billing/checkout/topup`, `/api/billing/wallet` (+ `/transactions`),
`POST /api/cases`. Removed proxies: pack / catalog / purchases.

### D7 — UX rules (global; source of truth is the design package)
Turns-only vocabulary; brick + alarm-glyph attention states (lime accent =
positive only, **never color alone**); `~` prefix + standard disclosure;
aria-labels expand `~` to "approximately"; all figures live from API; buttons
never wrap labels; single document-level export affordance; no dead
per-message action rows. Pricing + /cases/new are fully designed (exact copy in
the package's `design_files/pages.jsx` + `open-case.jsx`); chat workspace /
billing / small-surface sweep are design-pending — build to the global rules
and fold in mockups when delivered.

### D8 — Env posture (beta)
`ADVISOR_BILLING_ENABLED=true`, `ADVISOR_CONVERSATION_ENTRY_ENABLED=true`,
`ADVISOR_PAYMENTS_ENABLED` false (trial) / true (paid),
`ADVISOR_ENABLED_QUESTIONS` per-slug list, new wallet/turns vars per the
parameters table; legacy `STRIPE_PRICE_<TIER>_<PACK>` retired.
**Kept-dormant** (not deleted): `packs.py`, quota/credit helpers, credit tables,
webhook pack branch. **Removed from live paths:** chat credit calls, upgrade
(410), pack checkout (410), tier-upgrade chat tool.

## Dependency graph

```
I1 (wallet) ─┬─ I2 (top-up) ─┬─ I7 (chat UX) ─ I10 ─ I12
             │              ├─ I8 (pricing)
             │              └─ I9 (billing pages)
             └─ I4 (pre-flight) ─ I7
I3 (free open) ─┬─ I4
                └─ I6 (/cases/new) ─ I10
I5 (report gates) ─┬─ I8
                   └─ I11 ─ I12
```

Parallel lanes for 3 agents — A: I1→I2→I9 · B: I3→(I4 after I1)→I7 ·
C: I5→I8/I6 · then I10/I11 → I12.

## Sub-issue tracking

| # | Linear | Title | Spec | Depends on |
| -- | -- | -- | -- | -- |
| I1 | ABS-380 | Token wallet foundation: model, ledger, signup grant, read APIs | D1, D2, D6 | — |
| I2 | ABS-381 | Stripe top-up checkout + webhook crediting | D4, D6 | I1 |
| I3 | ABS-382 | Free case open + tier upgrade retirement | D3 | — |
| I4 | ABS-383 | Chat pre-flight swap: balance floor, burn settlement, `token_balance` SSE | D3, D6 | I1, I3 |
| I5 | ABS-384 | Per-report gates (backend): `ADVISOR_ENABLED_QUESTIONS` | D5, D6 | — |
| I6 | ABS-385 | Conversation-first /cases/new (designed surface) | D3, D7 | I3 |
| I7 | ABS-386 | Chat workspace balance UX: BalanceStrip + TopUpPrompt | D6, D7 | I2, I4 |
| I8 | ABS-387 | Pricing page redesign: "Pay by the turn" | D7 | I2, I5 |
| I9 | ABS-388 | Unified billing pages: balance, ledger, reports, cases | D6, D7 | I2 |
| I10 | ABS-389 | Marketing/nav copy sweep + dead-code removal | D7, D8 | I6, I7 |
| I11 | ABS-390 | Config posture, docs, legacy-spec reconciliation | D8 | I5 |
| I12 | ABS-391 | Integration pass: full e2e green + visual regen | all | I10, I11 |

## Process (every sub-issue)

Worktree + feature branch off `dev`; TDD (spec-first); Playwright e2e for the
changed behavior (proxy-contract specs count); green `make e2e` in the worktree
before In Review; record branch name + plan on the issue. **Deploy order:
backend before web** — the 0023 migration is additive / zero-downtime.

## Open decisions (confirm before launch copy freezes)

1. ~~**`ADVISOR_TOKENS_PER_TURN` value**~~ — **RESOLVED (ABS-416, 2026-08-08):**
   175,000, calibrated against measured prod burn rather than transcript
   replay. Prod's `advisor_usage_event` already records one row per assistant
   turn whose totals match the wallet burn exactly, so the ground truth was
   directly available and no replay was needed.
2. **Top-up price ladder** — **OPEN, and now BLOCKING `ADVISOR_PAYMENTS_ENABLED
   =true`.** ABS-416 rescaled the token grants so each SKU still credits the
   turns it advertises (8 / 30 / 80), and judged the margin "real but thin"
   against the ~$0.55/100k anchor. That anchor was wrong by ~5x (see the
   parameters section). At the measured ~$28.9/MTok wallet-counted cost:

   | SKU | Price (CAD) | Turns | Revenue/turn (USD ≈) | Cost/turn (USD) |
   | -- | -- | -- | -- | -- |
   | small | $15 | 8 | ~$1.37 | ~$5.05 |
   | medium | $50 | 30 | ~$1.22 | ~$5.05 |
   | large | $120 | 80 | ~$1.10 | ~$5.05 |

   Every SKU sells turns for roughly a quarter of what they cost, before
   Stripe fees. ABS-404 deliberately did **not** change these: list prices are
   a revenue decision, and the fix could equally be raising prices, shrinking
   the turn counts per SKU, or cutting per-turn cost (caching, retrieval
   trimming, a cheaper model for shallow turns). It is harmless only while
   payments are off — nothing can be sold below cost if nothing can be sold.
   **Settle before flipping payments on.**
3. ~~**Signup grant size**~~ — **RESOLVED (ABS-404):** 3 turns (525,000),
   down from 10. At the corrected anchor, 10 turns was ~$50 of API spend per
   free, no-card signup, not the ~$9.60 previously believed. Three turns is
   enough to evaluate the product — the harm ABS-404 was filed for was a new
   account locked out after *one* question — at ~$15 exposure.
   `ADVISOR_SIGNUP_TOKEN_GRANT` remains a no-restart env knob, so
   trial-to-paid conversion data can move it either way as an ops flip.
4. **Per-turn cost reduction** — newly implied by the corrected anchor. A 175k
   turn at ~$5 is the input to every pricing question above, and it is the one
   variable that improves both margin and grant exposure at once. Not scoped
   anywhere yet.

## Not doing (in this pivot)

- No migration of legacy CaseCredit / pack balances — the credit tables and
  `packs.py` stay dormant, not deleted, and no data migration runs. Any
  in-flight credits are out of scope for the beta and handled (if at all)
  outside this umbrella.
- No turn expiry.
- No report-quality work — gating is the mechanism to ship a slug when its
  quality is independently signed off, not part of this umbrella.
