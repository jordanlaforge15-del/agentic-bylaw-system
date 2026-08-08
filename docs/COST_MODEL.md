# Cost Model — Unit Economics per Tier (grounded in real-API measurement)

**Date:** 2026-06-11
**Supersedes:** the "Margin against API cost is 98%+ at all tiers" claim in
`src/advisor/billing/packs.py` (and the original pricing brief's ~$0.50/case
estimate, which was priced at Sonnet rates while production runs Opus).
**Primary data source:** ABS-303 real-API validation
(`evals/token_savings/20260610-ABS303-real-api-validation/ROLLUP.md`,
raw transcripts in `evals/runs/20260610-ABS303-tc001-turn1-AB-N10/OFF/`).
The OFF arm is the current production posture (WI-1/WI-4 reverted in ABS-304).

---

## 1. Measured cost per user turn

One **user turn** = one question from the user = one `run_tool_loop` invocation
= 3–9 tool-loop **iterations** ("reasoning steps") on the shallow reference
case. These are different units; the pricing page advertises *reasoning
steps*, not turns.

TC-001 turn 1, Opus 4.5 (`claude-opus-4-5`), N=8, current prod config:

| Statistic | Value (USD) |
|---|---|
| Mean cost per turn | **$0.99** |
| Std dev | $0.59 |
| Fast-converge turns (3–5 iters, 5 of 8) | $0.57 mean |
| Slow turns (9 iters, 3 of 8) | $1.69 mean |
| Observed range | $0.43 – $1.79 |

Mean token decomposition per turn (from the raw N=8 transcripts):

| Component | Tokens | Rate ($/MTok) | Cost |
|---|---|---|---|
| Uncached input | 32,279 | 15.00 | $0.484 |
| Output | 2,059 | 75.00 | $0.154 |
| Cache write | 12,318 | 18.75 | $0.231 |
| Cache read | 81,081 | 1.50 | $0.122 |
| **Total** | 127,737 billed | | **$0.991** |

Two structural facts fall out of this table:

1. **The case-budget ledger counts 27% of billed tokens.**
   `ChatSession.send_user_message_blocking` decrements the per-case budget by
   `input_tokens + output_tokens` only (`src/advisor/chat/session.py:313`) —
   34.3k of the 127.7k billed tokens. Cache writes and reads (35% of the
   dollar cost) are invisible to the ledger.
2. **Effective cost per budget-counted token: ~$28.9/MTok USD**
   ($0.99 / 34.3k), i.e. ~$39.5/MTok CAD at the 1.37 display FX. This is the
   number to use when converting tier token budgets to expected API cost —
   not the headline $15/$75 rates.

## 2. What a credit actually buys (and costs)

Tier budgets are cumulative `input + output` tokens per case
(`case_budget_for`). The budget is checked **between turns** — a turn always
runs to completion, then the case is blocked when remaining ≤ 0. At the
measured 34.3k budget-counted tokens per shallow turn:

| Tier | Budget | Turns delivered (typ.) | Advertised | Expected API cost | PAYG price (CAD) | Expected margin |
|---|---|---|---|---|---|---|
| Quick | 12,000 | **1** (budget < 1 turn; ~3× overshoot) | 4–6 steps | $0.99 USD ≈ $1.36 CAD | $12.50 | **~89%** |
| Standard | 45,000 | **1–2** | 12–18 steps | ~$2.00 USD ≈ $2.71 CAD | $32.50 | **~92%** |
| Complex | 130,000 | **~4 shallow** (fewer if deep) | 35–50 steps | ~$4.00 USD ≈ $5.43 CAD | $75.00 | **~93%** |

At the Enterprise pack discount (−25% on revenue) expected margins are
~85–91%. The free trial is 3 Standard credits
(`STARTER_GRANT_TIER/QUANTITY`, `src/advisor/db/cases.py:673`); measured trial
cost ≈ **$10 USD** (TOKEN_COST_REDUCTION_FINDINGS), i.e. acquisition cost, not
margin.

**The "98%+ at all tiers" claim was wrong** — it was anchored on Sonnet rates
($3/$15) from the pricing brief while production runs Opus ($15/$75). Real
expected-case margins are **85–93%**: healthy, but a different number, and the
tails below are what actually threaten it.

### Why "a Standard credit = 12–18 turns × $0.99 ≈ $18 cost" is NOT the model

Two independent corrections:

- "12–18" on the pricing page is **reasoning steps (tool-loop iterations)**,
  not user turns. A typical turn consumes ~6 iterations, so the advertised
  capacity is ~2–3 turns.
- The binding constraint is the 45k token budget, which the measured
  consumption rate (34.3k/turn) exhausts after **1–2 turns** regardless.

Per-credit expected cost is therefore ~$2–3 CAD, not ~$18 CAD.

## 3. Tail risks (the real margin threats)

1. **No cumulative per-turn cost cap.** ✅ **CLOSED (ABS-305).** The
   per-request cost-circuit breaker (`src/advisor/llm/budget.py`) caps each
   *request* at ~150k billed-equivalent input tokens (~$2.25 of input per
   iteration), not the turn's cumulative spend. Documented runaway turns on
   2026-05-11: **849k tokens / $12.93** and 611k / $9.30 — at the
   then-default `max_iterations=10`. Complex tier now allows
   `max_iterations=55` (ABS-287); a worst-case deep turn could plausibly
   have billed **$30–50**, consuming most of a Complex credit's $54.7 USD
   revenue, and the case budget only reacts *after* the turn completes.
   ABS-305 adds a **second, cumulative breaker** in `run_tool_loop`: it
   sums every iteration's billed-equivalent estimate and forces synthesis
   (`terminated_reason="cumulative_cost_trip"`) once the running total would
   cross `ADVISOR_TURN_CUMULATIVE_TOKEN_BUDGET` (default 165k ≈ **$2.50**).
   The worst case is now a chosen ceiling rather than `max_iterations ×
   per-request cap`. This is also the load-bearing cost primitive for the
   priced-question catalog — it bounds the cost of each PAID answer.

   **ABS-404 adds a third, MEASURED breaker** for the chat-wallet rail. Both
   breakers above are pre-flight *estimates* of *input* tokens, and §1 is
   exactly why that is not enough: the wallet bills `input + output`, output
   is never estimated, and the 4-chars/token heuristic under-counts JSON
   tool_results. A prod turn burned 247,566 wallet tokens under the 165k
   cumulative cap. The new breaker sums the provider's *reported*
   `input + output` between iterations and forces synthesis
   (`terminated_reason="wallet_cap_trip"`) at
   `ADVISOR_TURN_MAX_WALLET_TOKENS` (default `2 × ADVISOR_TOKENS_PER_TURN`
   = 350k ≈ **$10** at the $28.9/MTok wallet-counted rate below). It is
   disabled on the paid-report rail, which is bounded by its own per-slug
   budget instead. Note the two ceilings are in different units and should
   not be compared directly: 165k billed-equivalent *input* tokens versus
   350k measured *wallet-counted* tokens.
2. **Cap-hit forced synthesis costs $2–3.50 per occurrence** (output-heavy,
   3000+ tokens). Currently 0% cap-hit rate in the measured prod posture
   (the ABS-304 revert eliminated the WI-1/WI-4-induced failure mode), but
   any future tool-loop change that raises cap-hit rates re-imports this
   cost. Treat cap-hit rate as a guarded metric in cost regressions.
3. **Served-model drift.** The same prompt on the same model ID cost
   $3.49 on 2026-06-09 and $0.42 on 2026-06-10 (~8×, ABS-303). Per-turn cost
   is not a constant; re-measure (cheap N=3 probe per `docs/COST_REGRESSION.md`)
   before any pricing decision, and after any suspected provider-side change.
4. **Depth mix is unmeasured.** All N=8 baseline turns are TC-001-shallow.
   Real users on Standard/Complex ask deeper questions; slow-mode turns
   already run $1.69 mean. Until a TC-005-style deep-case baseline exists,
   model per-turn cost as a **$1–2 USD band**, not a point.

## 4. Known inconsistencies to resolve (product copy vs enforcement)

- **Quick tier budget is below single-turn consumption.** 12k budget vs
  34.3k mean per turn: every Quick case overshoots ~3× (delivered anyway —
  the turn completes, then the case blocks). Economically fine at $12.50;
  but the budget number is fiction and "4–6 reasoning steps" is what the
  *iteration cap* (8) enforces, not the token budget.
- **Standard's advertised 12–18 steps vs ~1–2 delivered turns.** If a
  customer reads "12–18 reasoning steps" as session depth, the 45k budget
  under-delivers vs copy on multi-turn cases. Either raise the Standard
  budget or reword the copy in steps-per-question terms.

## 5. Levers, in order of measured impact

| Lever | Measured effect | Status |
|---|---|---|
| Switch Opus → Sonnet 4.x | **7.2×** cheaper on the 2-case suite ($25.75 → $3.56), hallucinations tied at 0 (ABS-293, N=2 — needs the 20-case suite before a switch) | Open — largest lever by far; takes expected turn cost to ~$0.14–0.20 |
| Per-turn *cumulative* cost cap | Bounds tail risk #1; converts worst case from ~$30–50 to a chosen ceiling (default 165k ≈ $2.50) | **Built (ABS-305)** |
| Cheaper forced synthesis (constrained output / cheaper model for the synthesis turn) | Cuts the $2–3.50 cap-hit penalty | Not built |
| WI-1/WI-4 cache machinery | **Net-negative** — reverted (ABS-304); do not re-open without TC-005 deep-case evidence | Closed |

## 6. Pricing verdict (2026-06-11)

On current evidence **prices do not need to rise**: expected-case margins are
85–93% at list across all tiers. The exposure is in the tails (one deep
Complex turn ≈ a credit's revenue), the trial ($10 each), and copy/budget
inconsistency — not in the average case. Re-run this model if: the depth mix
shifts, a Sonnet switch lands (margins → 98%+ for real), or served-model
drift moves the per-turn baseline by more than ~2×.
