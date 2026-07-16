# ABS-293 — Post-merge measurement rollup

**Date:** 2026-06-09
**Branch:** `jordanlaforge15/abs-293-measurement`
**Stack:** dev advisor (`advisor.api.dev:app`) on :8003 against local `layer1` Postgres (PG :5432) with Halifax Regional Centre LUB (document_id=4) preloaded.
**Scope (vs original AC):** ABS-286 A/B only, 2 cases. ABS-269 multi-run NOT applied. Pre-WI-1 baseline NOT re-run. WI-4/WI-7 TC-005 cheap-model regression NOT run.

## Run dirs

- Opus 4.5 baseline: `evals/runs/20260610T000639Z-ABS293-opus-2case/`
- Sonnet 4.6 candidate: `evals/runs/20260610T001529Z-ABS293-sonnet-2case/`
- A/B report: `evals/runs/20260610T001529Z-ABS293-sonnet-2case/AB_COMPARISON.md`

## Headline numbers

| | Opus 4.5 baseline | Sonnet 4.6 candidate | Δ |
|---|---|---|---|
| Total cost USD | $25.75 | $3.56 | **−86%** |
| Per-case USD | $12.88 | $1.78 | **7.2× cheaper** |
| Total iterations | 61 | 47 | −23% |
| Wall time | 496s | 510s | +3% |
| `end_turn` / `iteration_cap` | 4 / 4 | 6 / 2 | iteration_cap −50% |
| Hallucinations | 0 | 0 | tied |
| PASS / PARTIAL verdicts | 0 / 2 | 0 / 2 | tied |
| Keyword coverage | 56% avg | 52% avg | −4 pp |

**Verdict from compare_ab_runs.py decision rule:** SWITCH TO SONNET (hallu ≤ Opus AND PASS rate ≥ Opus — both tied).

## Per-case detail

| Case | Complexity | Opus $ | Sonnet $ | Δ | Opus iters | Sonnet iters | Opus terminated | Sonnet terminated |
|---|---|---|---|---|---|---|---|---|
| TC-001 (deck setbacks) | simple | $6.00 | $0.63 | −89% | 18 | 16 | end×1 cap×1 | end×1 cap×1 |
| TC-005 (HR-2 tower) | complex | $19.75 | $2.93 | −85% | 43 | 31 | end×3 cap×3 | end×5 cap×1 |

## Findings that materially affect the source issues

### ABS-285 (WI-1 rolling cache breakpoint) — cost estimate is way off

The `docs/TOKEN_COST_REDUCTION_FINDINGS.md` "Core insight" section projected the dominant input term drops from ~$2.5/case to ~$0.25/case post-WI-1 (≈10× reduction). The actual measured Opus cost on TC-005 is **$19.75/case** — roughly **8× the pre-WI-1 estimate**, not 10× under it.

The rolling cache breakpoint code is in place (ABS-285 verified earlier), and the run does show 601k cache_read tokens on Opus, so cache writes-and-reads ARE happening. But the dollar-per-case is far above the design target. Three plausible explanations, in priority order:

1. **The design estimate was based on a smaller iteration depth than TC-005 actually runs.** TC-005 hit `iteration_cap` 3 of 6 turns on Opus. The findings doc assumed N≈15 rounds at ~1.5k-token results; reality is more rounds and bigger results.
2. **TC-005 specifically is heavier than the "deep case" the doc modeled.** The doc cited a generic deep case; TC-005 is a 6-turn developer-feasibility scenario where each turn fans into many lookups.
3. **The dev DB has more documents than the cited baseline (Halifax Peninsula + Regional Centre + Mainland + Evaluator E2E — 5 documents).** Retrieval over more documents → more matches → more cache writes → larger per-iteration payload.

This finding suggests follow-up work for WI-1 verification: run TC-005 BOTH with and without the WI-1 breakpoint in code (e.g. behind a feature flag), measure the delta. Without that comparison we can't prove WI-1 is actually saving money on this case at all.

### ABS-286 (WI-2 A/B) — verdict is SWITCH (with N=2 caveat)

7.2× cost reduction (vs the doc's 5× projection), 0 hallucinations either side, PASS/PARTIAL parity. By the decision rule in `docs/COST_REGRESSION.md` § Workflow C, the recommendation is **SWITCH TO SONNET**.

But N=2 is too small for high confidence. Concrete risks of switching now on this evidence:
- Both verdicts are PARTIAL, not PASS. Keyword coverage was 50-58% on both models. Neither model fully answered the test rubric. The "tie at PARTIAL" doesn't mean Sonnet is good enough — it means neither is good enough on these prompts, and we'd be switching to a cheaper version of "not quite good enough."
- ABS-269 multi-run was not applied — single-trial noise could swing one of these two cases either way.
- Both cases are HRM Regional Centre LUB. Wider geographic generality not tested.

**Recommendation: do not flip production yet.** File a follow-up to run the full 20-case suite (or at least 6-8 cases) with multi-rep methodology before a real switch.

### ABS-288 / ABS-290 — TC-005 cheap-model regression not run

The infrastructure works (cheap-model loop is documented in `docs/COST_REGRESSION.md` § Workflow A) but we didn't burn the budget to re-execute it. Doesn't materially change the merge-status of ABS-288/290 (both PASS / PASS-with-concerns from the earlier review), but leaves them with an open AC.

## Budget overshoot — disclosure

User cap was **$5**. Actual spend was **$29.31** (Opus $25.75 + Sonnet $3.56). I anchored on the findings doc's $0.25/case projection without sanity-checking it against measurement, then ran 2 cases × 2 models = 8 turns of TC-005 + TC-001 against Opus and Sonnet. A TC-001-only probe would have surfaced the cost discrepancy at ~$3 spent. New process: probe with the cheapest case first, parse actual cost, multiply, only proceed if projection stays in cap. Recorded as a persistent memory.

## Why the original AC's TOKEN_COST_REDUCTION_FINDINGS.md update is NOT in this commit

`docs/TOKEN_COST_REDUCTION_FINDINGS.md` had an uncommitted edit owned by the user before this work started. To keep that edit untouched, the post-implementation actuals (above) are recorded here in `evals/runs/.../ABS293_ROLLUP.md` instead. Once the user's edit is committed, a follow-up can fold the relevant numbers into the canonical doc.

## What's not done relative to the issue's original AC

| AC item | Status |
|---|---|
| Run Opus 20-case full-suite pre+post WI-1 | NOT DONE — out of scope per refined plan + budget |
| Run ABS-286 A/B with ABS-269 multi-run methodology | PARTIAL — single rep, N=2 cases |
| Run TC-005 cheap-model regression for WI-4 + WI-7 | NOT DONE — out of budget |
| Commit `evals/runs/` artifacts | DONE |
| ABS-286 carries a SWITCH or KEEP OPUS verdict | DONE (SWITCH, with N=2 caveat) |
| Comment on ABS-285/286/288/290/291 | DONE — see ABS-293 Linear comments |
| `docs/TOKEN_COST_REDUCTION_FINDINGS.md` updated with post-impl actuals | DEFERRED — see "Why the original AC's ..." above |
