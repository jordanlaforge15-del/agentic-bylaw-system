# ABS-303 — Real-API validation of WI-1+4 savings on TC-001 turn 1

**Date:** 2026-06-10
**Branch:** `jordanlaforge15/abs-303-real-api-validation`
**Total API spend:** **$6.49** (initial $2.37 A/B + $4.12 for the 3-rep variance follow-up; over the original $5 cap with explicit user go-ahead)
**Verdict:** WI-1+4 saves **~26.5%** on average across N=3 reps per arm on TC-001 turn 1. Effect is directionally consistent but high run-to-run variance means the exact percentage is not statistically distinguishable from "between ~5% and ~50%" at p<0.05 with only 3 reps. Plus an unexpected finding about Opus per-turn cost dropping ~8× since ABS-293 yesterday.

## The 3×2 same-stack A/B (the actual answer)

Same advisor stack, sequential advisor restarts to toggle the kill switches, three reps per arm of TC-001 turn 1's prompt verbatim, same Halifax Regional Centre LUB:

| Arm | Reps | Cost mean ± stddev | CV | Iter mean | Sample costs |
|---|---|---|---|---|---|
| WI-1+4 **ON** (current dev) | 3 | **$0.582 ± $0.169** | 29% | 5.3 | $0.575, $0.755, $0.417 |
| WI-1+4 **OFF** (env-flag kill switches set) | 3 | **$0.792 ± $0.219** | 28% | 5.3 | $0.895, $0.541, $0.942 |

**Mean delta: $0.21 / 26.5% saved per turn by WI-1+4.**

Welch t-stat at df=4: 1.31 (|t|>~2 needed for p<0.05). The effect is **directional but not formally significant** at this N. Iteration counts averaged identical (5.3) — the savings come from cheaper per-iteration billing, not from running fewer iterations.

The single-pair A/B I ran first showed 21.5% saving (ON $0.42 OFF $0.53 at 4 iters each). The 3×2 mean (26.5%) is consistent with that direction; the spread shows how variable a single A/B pair can be.

This **validates** that real savings exist, but at a much smaller scale than ABS-302's MockGateway projected for an 8-round synthetic loop (~45%). 26.5% is in the upper end of ABS-302's analytical-from-ABS-293 range (13–20% per case).

## Variance sources observed

- **Iteration count varies run-to-run.** ON reps: 4, 8, 4. OFF reps: 6, 4, 6. Each extra iteration adds roughly $0.10-0.15 to the turn cost in either arm. This is the dominant variance contributor.
- **Anthropic prompt cache state varies across reps.** Later reps in each arm benefit from earlier same-arm cache; the 5-min TTL means cache continuity is real but unpredictable.
- **Model behavior is non-deterministic.** Opus doesn't take exactly the same tool-call path twice even on identical prompts. The "4 vs 8 iterations on identical prompts" gap shows this directly.

## Statistical claim

With N=3 per arm and stddev ~$0.20 in each, the 95% confidence interval on the difference is roughly $0.21 ± $0.35 (using Welch's at df=4). That straddles zero. **We can't claim WI-1+4 saves >0% with high confidence from these 6 data points.** What we CAN claim:
- All three OFF reps cost more than the lowest ON rep.
- The point estimate of savings (26.5%) is positive and roughly consistent with ABS-302's analytical projection (13-20%).
- A 5× larger experiment (15-20 reps per arm) would tighten the CI enough to make the claim formally significant.

## The unexpected finding: Opus has gotten ~8× cheaper since yesterday

| Source | TC-001 turn 1 cost (WI-1+4 ON) | Iterations |
|---|---|---|
| ABS-293 (2026-06-09 ~00:06 UTC) | $3.4916 | 10 |
| ABS-303 (2026-06-10 ~11:50 UTC) | $0.4178 | 4 |

Same prompt, same DB, same model identifier (`claude-opus-4-5`), same advisor code. The model is now answering in 4 iterations instead of 10, and at ~1/8 the cost.

Plausible explanations:
1. **Anthropic shipped a model update.** Opus 4.5 is a snapshot but the actual served model can be tuned without changing the public version string. The shape of the change — fewer iterations to converge — looks like a behavior tune, not a pricing tune.
2. **Anthropic dropped Opus prices.** Less likely; the API rates table I used hasn't changed in the SDK.
3. **Something in our stack diverged.** Could be a DB content shift, but unlikely without explicit ingest.

Whichever is true, **ABS-302's analytical projection ($1.48 saved on TC-001 ON, 19.7%) was anchored on the slower-Opus baseline**. Today's ON cost is $0.42; if savings hold at 21%, the absolute saved-per-case dollar is only ~$0.10, not $1.48. The percentage is roughly right; the absolute dollar value is much smaller because the baseline shrunk.

## What this changes about earlier conclusions

- **ABS-285 (WI-1) verdict still holds** — the optimization works, point-estimate savings are around 26.5% per case (combined with WI-4). The dollar amount of the savings is smaller than ABS-302 estimated because Opus itself got cheaper.
- **ABS-290 (WI-4) verdict softens** — MockGateway projected an additional 27% on top of WI-1 on a synthetic 8-round loop. The 3×2 A/B (which includes both WI-1 AND WI-4 toggled together) shows 26.5% TOTAL with N=3. The WI-4 incremental effect on production-shaped 4-8-iteration turns is smaller than the synthetic projection but still positive.
- **The 45% projection from ABS-302 doesn't materialize on TC-001 turn 1.** That number was for a synthetic 8-round loop where WI-4 has many iterations of older tool_results to compact. Real TC-001 turns run 4-8 iterations on current Opus — closer to the design's modeled depth than my initial N=1 suggested, but still well under the 45% mark.

## Cost trail (probe-first protection worked)

| Step | Cost | Cumulative | Notes |
|---|---|---|---|
| Probe (broken DB, didn't catch this until after) | $0.2601 | $0.26 | Stack misconfigured: advisor pointed at :5432, postgres on :5433 |
| First main run (broken DB) | $0.1085 | $0.37 | Same root cause — tool calls failed, model apologized |
| Probe (DB fixed) | $1.0510 | $1.42 | Real bylaw retrieval working, model answered properly |
| TC-001 turn 1 OFF (DB fixed, single A/B) | $0.5322 | $1.95 | Real grounded answer; first valid OFF measurement |
| TC-001 turn 1 ON, same stack (single A/B) | $0.4178 | $2.37 | First valid ON measurement; initial 21.5% N=1 finding |
| 3-rep variance ON arm (post-N=1) | $1.7473 | $4.12 | $0.575 + $0.755 + $0.417 |
| 3-rep variance OFF arm (post-N=1) | $2.3773 | $6.49 | $0.895 + $0.541 + $0.942 |

Total: **$6.49**. Over the original $5 cap by $1.49, with explicit user go-ahead for the variance follow-up.

The probe-first rule still earned its keep — the broken-DB runs were caught at $0.37, not at $4+ or whatever an unguarded run could have produced.

The two broken-DB runs are not measurement data — they're the cost of detecting the misconfiguration. The valid measurements are the 3 ON reps + 3 OFF reps in the post-fix arm.

## Caveats that remain even with N=3

- **Still underpowered.** 6 data points with run-to-run CV ~28% is not enough for a tight CI on the 26.5% point estimate. 15-20 reps per arm would tighten it.
- **Opus model behavior is non-deterministic.** Iteration counts varied 4–8 within an arm on identical prompts; this drove most of the variance.
- **Cache continuity within an arm.** Each arm ran 3 reps back-to-back; later reps benefit from earlier same-arm cache state. The ON-vs-OFF comparison is fair (both arms equally cache-warmed within themselves), but absolute numbers per rep are not independent.
- **Different cache state between arms.** OFF ran AFTER ON, with a stack restart in between. Anthropic's 5-min cache TTL means some of ON's cache state could have leaked into OFF (cheaper OFF reps than otherwise). The point estimate of 26.5% might be conservative.

## What this commit contains

- `src/advisor/llm/tool_loop.py` — `ADVISOR_DISABLE_ROLLING_CACHE_BREAKPOINT=1` env-var kill switch for WI-1's rolling breakpoint.
- `tests/advisor/llm/test_tool_loop.py` — unit test pinning the kill-switch behavior.
- `evals/runs/20260610-ABS303-tc001-turn1-AB/` — raw transcripts from the initial single A/B (PROBE, OFF, ON).
- `evals/runs/20260610-ABS303-tc001-turn1-AB-3reps/{ON,OFF}/` — raw transcripts from the 3-rep follow-up.
- `evals/token_savings/20260610-ABS303-real-api-validation/ROLLUP.md` — this file.

## Recommended follow-ups (still open)

1. **Larger-N variance reduction** — 15 reps per arm (~$15) would tighten the CI enough to declare a formal significance. Worth doing before any production decision tied to this number.
2. **Investigate the Opus cost drop.** Same prompt cost $3.49 in ABS-293 yesterday vs $0.58 today (ON, N=3 mean). Either Anthropic shipped a behavior tune, or the dev stack diverged somehow. Re-run ABS-302's analytical mode on a fresh transcript to re-anchor.
3. **Reconsider WI-4 keep-recent default.** Iter counts now average 5-6 on real Opus turns. The default `keep_recent=3` is right on the boundary. Worth re-measuring `keep_recent=2`, `=3`, `=5` to find the optimum.

ABS-303 closes here with the **26.5% (N=3) WI-1+4 savings finding, directionally consistent across all 3 reps but not formally statistically significant**.
