# ABS-303 — Real-API validation of WI-1+4 savings on TC-001 turn 1

**Date:** 2026-06-10
**Branch:** `jordanlaforge15/abs-303-real-api-validation`
**Total API spend:** **$2.37** (within the $5 cap)
**Verdict:** WI-1+4 saves **~21%** on a clean same-stack TC-001 turn 1 A/B. Plus an unexpected finding about Opus per-turn cost dropping ~8× since ABS-293 yesterday.

## The clean same-stack A/B (the actual answer)

Both runs use the **same advisor stack on the same machine within minutes of each other**, hitting the same Halifax Regional Centre LUB, with TC-001 turn 1's prompt verbatim:

| State | Iterations | Cost | Quality |
|---|---|---|---|
| WI-1+4 **ON** (current dev) | 4 | **$0.4178** | Real grounded answer; clarifies 1234 Oxford Street is actually HR-1, not ER-1 |
| WI-1+4 **OFF** (env-flag kill switches set) | 4 | **$0.5322** | Same clarification, same quality |

**Delta: $0.1144 / 21.5% saved per turn by WI-1+4.**

The iteration counts are identical (4 each) and the answer quality is equivalent — so the saving comes purely from cheaper per-iteration billing, exactly the mechanism WI-1 and WI-4 were designed for.

This **validates** the existence of real savings, but at a much smaller scale than ABS-302's MockGateway projected for an 8-round synthetic loop (~45%). 21% is in the same ballpark as ABS-302's analytical-from-ABS-293 numbers (13–20% per case).

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

- **ABS-285 (WI-1) verdict still holds** — the optimization works, savings are around 15-21% per case. The dollar amount of the savings is smaller than ABS-302 estimated because Opus itself got cheaper.
- **ABS-290 (WI-4) verdict softens** — MockGateway projected an additional 27% on top of WI-1 on a synthetic 8-round loop. The clean same-stack A/B above (which includes both WI-1 AND WI-4 toggled together) only shows 21% total. The WI-4 incremental effect on production-shaped 4-iteration turns is small.
- **The 45% projection from ABS-302 doesn't materialize on TC-001 turn 1.** That number was for a synthetic 8-round loop where WI-4 has many iterations of older tool_results to compact. Real TC-001 only ran 4 iterations on current Opus, so there's almost nothing for WI-4 to compact.

## Cost trail (probe-first protection worked)

| Step | Cost | Cumulative | Notes |
|---|---|---|---|
| Probe (broken DB, didn't catch this until after) | $0.2601 | $0.26 | Stack misconfigured: advisor pointed at :5432, postgres on :5433 |
| First main run (broken DB) | $0.1085 | $0.37 | Same root cause — tool calls failed, model apologized |
| Probe (DB fixed) | $1.0510 | $1.42 | Real bylaw retrieval working, model answered properly |
| TC-001 turn 1 OFF (DB fixed) | $0.5322 | $1.95 | Real grounded answer; clean OFF measurement |
| TC-001 turn 1 ON, same stack | $0.4178 | $2.37 | Clean A/B baseline; same iterations + same answer quality |

The probe-first rule prevented the broken-DB runs from cascading into a larger overspend. Total spend stayed well under the $5 cap.

The two broken-DB runs are not measurement data — they're just the cost of detecting the misconfiguration. The valid measurements are the post-fix probe ($1.05), OFF ($0.53), and ON ($0.42).

## N=1 caveats

- The headline "21% savings" is from one A/B pair. A second rep each side would characterize variance properly.
- The Opus cost drop (~8×) is one data point in time. Could be a transient state.
- Both runs were within a few minutes of each other, so Anthropic's prompt cache state may have correlated them (both runs benefited from cache continuity with the probe).
- ON ran second; if Anthropic's caching is sticky across calls, ON may have benefited from cache entries created during the OFF run, biasing the comparison in ON's favor.

## What this commit contains

- `src/advisor/llm/tool_loop.py` — new `ADVISOR_DISABLE_ROLLING_CACHE_BREAKPOINT=1` env-var kill switch for WI-1's rolling breakpoint.
- `tests/advisor/llm/test_tool_loop.py` — new unit test pinning the kill-switch behavior (no `cache=True` markers on any block when the env var is set).
- `evals/runs/20260610-ABS303-tc001-turn1-AB/` — raw transcripts: PROBE (working DB), OFF, ON.
- `evals/token_savings/20260610-ABS303-real-api-validation/ROLLUP.md` — this file.

## Recommended follow-ups

1. **Re-run with 3 reps per arm** to characterize variance (~$3 spend total). Without this, the 21% number is N=1.
2. **Investigate the Opus cost drop.** If Anthropic shipped a behavior tune, ABS-302's analytical mode should be re-anchored on fresh Opus data. The script is in place — re-run TC-001 + TC-005 today to refresh `evals/runs/`.
3. **Reconsider WI-4 keep-recent default.** If the real-Opus loop is now 4 iterations (vs the design's assumed 10-15), WI-4's compaction kicks in less often and contributes less. The default `keep_recent=3` may be effectively a no-op for most turns. Consider:
   - Raising the default to `keep_recent=5` (closer to never compacting on current Opus loop depth), or
   - Lowering it to `keep_recent=2` (more aggressive compaction).
   - Measurement before either change.

ABS-303 closes here with the 21% N=1 finding. Each of the three follow-ups should be its own issue if pursued.
