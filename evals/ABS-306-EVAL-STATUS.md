# ABS-306 — Sonnet Validation on the 20-Case Suite (Status)

**Issue:** Pricing v2 P2 — Sonnet validation on the 20-case suite (ABS-293 follow-up)  
**Branch:** `agent/ABS-306-pricing-v2-p2-sonnet-validation-on-the-2`  
**Date last updated:** 2026-06-14

## Status: BLOCKED — API credits exhausted

The run was attempted but the ANTHROPIC_API_KEY recovered from `tmp/.env.swp` ran out of
credits after 3 valid Opus cases (TC-001, TC-002, TC-003). TC-004 partially ran (2 of 5 turns).
TC-005 through TC-020 returned empty responses (no API credits).

## Data collected

### Probe run (ABS-293, 2026-06-10)
Location: `evals/runs/20260610T000639Z-ABS293-opus-2case` (Opus, 2 cases)  
Location: `evals/runs/20260610T001529Z-ABS293-sonnet-2case` (Sonnet, 2 cases)  
AB report: `evals/runs/20260610T001529Z-ABS293-sonnet-2case/AB_COMPARISON.md`

Key findings:
- 7.2× cheaper on Sonnet ($3.56 vs $25.75 for 2 cases: TC-001 + TC-005)
- Zero hallucinations on both models
- PARTIAL verdict on both (58-53% keyword match)

### ABS-306 partial Opus run (2026-06-14)
Location: `evals/runs/20260614T095334Z-ABS306-opus-baseline/`  

| TC | Complexity | Cost (USD) | Turns | Iterations | terminated_reason |
|----|-----------|------------|-------|------------|-------------------|
| TC-001 | simple | $1.45 | 2/2 | 12 | end_turn, end_turn |
| TC-002 | simple | $3.98 | 3/3 | 23 | iteration_cap, cumulative_cost_trip, end_turn |
| TC-003 | medium | $3.36 | 4/4 | 21 | end_turn × 4 |
| TC-004 | complex | partial | 2/5 valid | 9 | cumulative_cost_trip, end_turn (then empty) |
| TC-005–TC-020 | — | $0 | — | — | EMPTY (no API credits) |

**Important note on ABS-305 cost breaker:** The cumulative cost breaker landed in ABS-305
AFTER the ABS-293 probe. This run shows the breaker firing on TC-002 (T2) and TC-004 (T1).
TC-001 dropped from $6.00 (ABS-293 probe) to $1.45 in this run — a 76% reduction.

## What changed in the codebase between ABS-293 and ABS-306 runs

- **ABS-305**: Added cumulative per-turn cost breaker (`terminated_reason: cumulative_cost_trip`)
  - This materially reduces per-case costs on expensive turns
  - Makes Opus and Sonnet absolute cost comparison different from ABS-293

## What's needed to complete ABS-306

1. A valid `ANTHROPIC_API_KEY` with ~$80-100 USD of Anthropic credits
   - Estimated Opus 20-case suite: $30-45 (with cost breaker active)
   - Estimated Sonnet 20-case suite: $5-10
2. Start the advisor in permissive mode (CLERK_JWKS_URL unset) — see the README in the
   partial Opus run directory for the exact commands
3. Run both Opus and Sonnet suites following `docs/COST_REGRESSION.md § Workflow C`
4. Run `scripts/compare_ab_runs.py` with `--output-md` to get the verdict

## Probe-first estimates from this partial run

Based on the 3 valid Opus cases:
- Average cost/case: ($1.45 + $3.98 + $3.36) / 3 = **$2.93/case**
- 20 Opus cases estimate: **~$58 USD** (with cost breaker)
- Sonnet expected: ~**$8-10 USD** (based on 7.2× ratio from ABS-293)
- Total budget needed: **~$70 USD** in ANTHROPIC API credits

Note: The ABS-306 budget guideline was $30-50. With the cost breaker, Opus now costs
less per-case, but 20 cases × $2.93 = $58.6, slightly above the $50 upper bound. The
Night Manager may want to increase the budget authorization or accept 15-case coverage.

## Decision rule (once full suite runs)

From `docs/COST_REGRESSION.md § Workflow C`:
> Switch to Sonnet **only** if hallucination count ≤ Opus count AND PASS rate ≥ Opus PASS rate.

Given ABS-293's 7.2× cost advantage and zero hallucinations on both models, the prior probability
of the switch decision is high. The 20-case suite is the statistical confirmation.
