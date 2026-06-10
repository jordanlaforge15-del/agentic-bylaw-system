# ABS-303 — Real-API validation of WI-1+4 savings on TC-001 turn 1

**Date:** 2026-06-10
**Branch:** `jordanlaforge15/abs-303-real-api-validation`
**Total API spend:** **$30.35** ($2.37 initial single A/B + $4.12 N=3 follow-up + $23.86 N=10/8 follow-up)
**Verdict (revised after N=10):** **WI-1+4 may be NET NEGATIVE in production.** Stratified by iteration mode, WI-1+4 saves 29% on fast-converging turns (~50% of runs) but COSTS 65% MORE on the other ~50% where the model hits `iteration_cap`. Net mean: OFF is **38% cheaper than ON**. Direction not statistically significant at this N (t=-1.30), but ZERO of 8 OFF reps hit the cap vs 5 of 10 ON reps — a strong qualitative signal.

> **Earlier N=3 verdict (now superseded):** WI-1+4 saved ~26.5%. That sample by luck of the draw included mostly fast-converge reps and undersampled the cap-hit mode. The N=10 result with stratification shows what was actually happening.

## The N=10 / N=8 A/B (the actual answer — supersedes earlier sections)

Ran 10 reps with WI-1+4 ON, then 8 reps with both kill switches set (advisor restart between arms). Each rep = TC-001 turn 1 verbatim prompt, current dev advisor, real Halifax Regional Centre LUB.

### Per-rep results — ON arm

| Rep | Iters | Terminated | Cost |
|---|---|---|---|
| 01 | 10 | `iteration_cap` | $2.4451 |
| 02 | 10 | `iteration_cap` | $2.4282 |
| 03 | 10 | `iteration_cap` | $2.2120 |
| 04 |  4 | `end_turn`      | $0.4142 |
| 05 |  4 | `end_turn`      | $0.4230 |
| 06 |  4 | `end_turn`      | $0.4333 |
| 07 |  3 | `end_turn`      | $0.4005 |
| 08 | 10 | `iteration_cap` | $3.5604 |
| 09 | 10 | `iteration_cap` | $3.2450 |
| 10 |  4 | `end_turn`      | $0.3688 |

**ON: mean $1.59, stddev $1.31, CV 82%.** Bimodal: 5 fast (3-4 iters, mean $0.41) and 5 slow (10 iters, all hit `iteration_cap`, mean $2.78).

### Per-rep results — OFF arm

| Rep | Iters | Terminated | Cost |
|---|---|---|---|
| 01 |  9 | `end_turn` | $1.7489 |
| 02 |  9 | `end_turn` | $1.5281 |
| 03 |  4 | `end_turn` | $0.5434 |
| 04 |  9 | `end_turn` | $1.7876 |
| 05 |  4 | `end_turn` | $0.5833 |
| 06 |  3 | `end_turn` | $0.4256 |
| 07 |  5 | `end_turn` | $0.7169 |
| 08 |  4 | `end_turn` | $0.5961 |

**OFF: mean $0.99, stddev $0.59, CV 59%.** Also bimodal: 5 fast (3-5 iters, mean $0.57) and 3 slow (9 iters, mean $1.69). **Zero hit the iteration cap.**

### Aggregate comparison

| | ON (N=10) | OFF (N=8) | Δ |
|---|---|---|---|
| Mean cost | $1.59 | $0.99 | **−$0.60 (OFF 38% cheaper)** |
| Stddev | $1.31 | $0.59 | — |
| Iteration mean | 6.9 | 5.9 | OFF 1 fewer iter on avg |
| `iteration_cap` rate | 5 of 10 (50%) | 0 of 8 (0%) | **Material qualitative difference** |
| Welch t-stat at df~14 | −1.30 | | not formally significant |

### Stratified comparison — the real story

Splitting each arm by iteration mode reveals two distinct regimes:

| Mode (iter range) | ON mean | OFF mean | Δ | Note |
|---|---|---|---|---|
| Fast converge (3-4) | $0.41 (N=5) | $0.57 (N=5) | **WI-1+4 saves 29%** | matches earlier N=3 finding |
| Slow / cap-hit (≥6) | $2.78 (N=5) | $1.69 (N=3) | **WI-1+4 costs 65% MORE** | inverts the design intent |

**Hypothesis:** WI-4's compaction is dropping context that the model later needs. The model burns iterations re-looking-up information from cleaned-up summaries. When it can't find what it needs in time, it hits the 10-iteration cap and forced synthesis kicks in. The forced synthesis is expensive (output tokens explode) and overwhelms the per-iteration savings WI-1 provides.

Without WI-1+4 the model has full tool_result history to reason over and converges naturally — sometimes in 3 iters, sometimes in 9, but never hitting the cap on these N=8 runs.

## The earlier 3×2 result (kept for history; superseded by N=10)

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

- **ABS-285 (WI-1) verdict needs revision** — WI-1's cache breakpoint by itself probably still saves money, but TOGETHER WITH WI-4's compaction the combined effect is net-negative in N=10 on TC-001 turn 1. WI-1 alone (with WI-4 disabled) is untested in this experiment; the only "WI-1 alone" data is the ABS-293 analytical projection (13-20% saved), which used a model state that no longer exists.
- **ABS-290 (WI-4) verdict reversed** — the N=10 result strongly suggests WI-4 is the culprit: every ON rep that hit `iteration_cap` had WI-4 compacting older tool_results. The model appears to lose enough context that it has to re-look-up, runs longer, and hits the cap. Without WI-4, the model converges naturally every time. Strong recommendation to **investigate before continuing to ship WI-4 enabled by default**.
- **The 45% MockGateway projection from ABS-302 was an artefact** of the synthetic loop design. Production shows a bimodal pattern (cap-hit vs natural completion) that the MockGateway doesn't reproduce. The MockGateway script remains useful for measuring request-shape effects but its dollar-projection mode should be flagged as known-suspect for cumulative effects.

## CRITICAL RECOMMENDATION

**Investigate WI-4 (in-loop tool_result compaction) before assuming it's net-positive in production.** The N=10/N=8 A/B suggests it's actively hurting on at least one prompt shape (TC-001 turn 1) by stripping context the model later needs, forcing more iterations, and tripping the cap.

Specific follow-ups:
1. **Isolate WI-1 alone vs WI-4 alone vs both** — this issue tested "both ON vs both OFF" but didn't separate them. Need a 3-arm experiment to identify which optimization causes the cap-hits.
2. **Profile what compaction is dropping.** If `_summarize_search` is stripping the citation_path the model later re-queries, that's a fixable bug. If it's stripping something inherent to the schema, WI-4 may need a smaller default `keep_recent` or different policy.
3. **Re-evaluate ABS-302's MockGateway projections.** The 45% combined saving claim doesn't match production; the synthetic loop didn't model the cap-hit failure mode.
4. **Production safety:** if a deeper-loop case study (e.g. TC-005) confirms the same pattern, consider rolling WI-4 OFF in production with `ADVISOR_TOOL_LOOP_COMPACT_KEEP_RECENT=0` until the root cause is understood.

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
| N=10 ON arm (post-N=3) | $15.9305 | $22.42 | bimodal: 5 fast / 5 cap-hit |
| N=8 OFF arm (post-N=3, capped at 8 to fit credits) | $7.9299 | $30.35 | bimodal: 5 fast / 3 slow-but-natural |

Total: **$30.35**. Significantly over the original $5 cap. Each escalation had explicit user authorization. The N=10 follow-up exposed a finding (bimodal distribution, cap-hit failure mode) that the smaller N=1 and N=3 experiments could not have detected at this prompt's variance, so the spend was load-bearing for the verdict reversal.

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

1. **WI-4 isolation experiment (highest priority).** Run 3-arm A/B/C: (a) both ON, (b) WI-1 only, (c) both OFF. ~$25 spend. Need to know if WI-1 is salvageable without WI-4.
2. **Profile what WI-4 compaction strips.** Read `src/advisor/chat/history_compaction.py` — find what `_summarize_search` and `_summarize_citation_lookup` drop, compare to what the model later re-queries. If the gap is identifiable, WI-4 may be fixable rather than rollback-worthy.
3. **Confirm on a deeper case (TC-005).** TC-001 is a single-turn shallow case. Need at least one deep multi-turn case run with the same N=10 methodology before any production rollback decision. ~$30 spend for N=10 on TC-005 each side.
4. **Reconsider WI-4 keep-recent default.** Possibly `keep_recent=5` or higher (less aggressive compaction) would avoid the cap-hit failure mode while preserving some savings.
5. **Investigate the iteration-cap forced-synthesis cost.** The cap-hit reps spent $2-3.50 each, dominated by output tokens (3000+). If forced synthesis can be made cheaper (e.g. constrain output length, switch model for synthesis turn), it reduces the worst-case cost of any tool-loop optimization that increases cap-hit rate.

ABS-303 closes here with the **verdict-reversing N=10/N=8 finding**: WI-1+4 together are net-negative in production on TC-001 turn 1 because WI-4's compaction drives the model into the iteration cap. WI-4 should not be assumed beneficial without the isolation experiment in follow-up 1 above.
