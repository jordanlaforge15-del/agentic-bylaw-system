# TC-005 Haiku cap=20 — cap-removal hypothesis test (ABS-268)

**Status:** NEGATIVE RESULT — hypothesis NOT supported by single-run evidence.

**Date:** 2026-06-03
**Model:** `claude-haiku-4-5`
**Change tested:** `ChatSession.send_user_message_blocking` calls `run_tool_loop` with `max_iterations=20` instead of the default 10. Local-only edit, reverted after the run; no code shipped.
**Baseline compared against:** `evals/runs/20260603T100000Z-haiku-baseline/` (cap=10)

## Headline result

| Metric | Baseline (cap=10) | Test (cap=20) | Delta |
|---|---|---|---|
| Total cost | $0.333 USD | **$1.006 USD** | **+$0.673 (+202%)** |
| Total cost CAD | $0.447 | $1.349 | +$0.902 |
| Wall time | 80.6 s | 234.5 s | +191% |
| T1 stop reason | iteration_cap | iteration_cap (STILL) | — |
| T1 cost | $0.279 | $0.245 | −$0.034 (−12%) |
| T2–T6 cost (sum) | $0.054 | $0.761 | **+$0.707 (+14×)** |
| T2–T6 tool calls (sum) | 0 | 46 | — |

## What happened

### T1 hit the cap in BOTH runs

Raising max_iterations from 10 to 20 did not let T1 finish naturally. With 20 rounds it still produced tool_use blocks on iteration 20, triggering the forced-synthesis fallback. The model genuinely needs more than 20 lookups for this question shape under the current retrieval API.

T1 cost dropped slightly ($0.279 → $0.245, −12%) because the extra cache-warm iterations are cheap relative to the cold forced-synthesis call. So the cap-removal mechanism DOES save money on the directly-affected turn — just not as much as hoped, and only when the cap actually changes the loop outcome (which it didn't here for T1).

### T2–T6 cost EXPLODED

| Turn | Baseline | Cap=20 | Tool call ratio |
|---|---|---|---|
| T2 | 0 calls / $0.014 | 6 calls / $0.071 | 0 → 6 |
| T3 | 0 calls / $0.010 | 6 calls / $0.111 | 0 → 6 |
| T4 | 0 calls / $0.010 | 11 calls / $0.168 | 0 → 11 |
| T5 | 0 calls / $0.011 | 8 calls / $0.180 | 0 → 8 |
| T6 | 0 calls / $0.010 | 15 calls / $0.231 | 0 → 15 |

In the baseline, T2–T6 each used 0 tool calls — the model answered from T1's already-retrieved context (riding the prompt cache). In the cap=20 run, T2–T6 each made 6–15 tool calls. Answers were 5–135% longer.

For T2 specifically: both runs produced answers reaching the same conclusion ("Schedule 17 governs FAR but I couldn't pull the specific HR-2 number"). The cap=20 run made 6 redundant tool calls to reach an equivalent answer.

## Two competing explanations

1. **Real downstream effect.** Raising T1's cap allowed the model to retrieve slightly different evidence (21 tool calls vs 17 in baseline). That different evidence shape changes the model's confidence in subsequent turns, causing it to look things up rather than reuse context.

2. **Stochastic variance.** Anthropic models sample at non-zero temperature. A 3× cost swing between two single runs of the same case may simply be sampling noise — the baseline (0 tool calls on T2–T6) could be the LUCKY outcome and cap=20 the NORMAL one, or vice versa.

**Single-run comparisons cannot distinguish these.** This is the methodology gap that ABS-269 addresses.

## What this tells us about ABS-268

The change should NOT ship on this evidence. Before any cap-removal lands:

1. Need ≥3 baseline runs AND ≥3 candidate runs to establish whether the effect is real or noise (ABS-269 prerequisite).
2. If the effect is real, the win on T1 ($0.034 saved) is dwarfed by the cost on downstream turns ($0.707 added).
3. If the effect is noise, we still don't have evidence the change is net positive.

## What this tells us beyond ABS-268

The fact that the model used 0 tool calls in T2–T6 baseline and 6–15 in cap=20 indicates that **the model's decision to use tools is unstable across runs**. This has bigger implications than the cap question: it means **any cost regression test on tool-use behavior needs to be multi-run** to be meaningful. ABS-269 is the methodology fix.

It also raises the deeper question: **why does the retrieval API force the model into 17+ tool calls in the first place?** A redesigned API surface that returned more complete information per call might reduce iteration count from "many" to "few" regardless of the cap. That's worth a separate design exploration.

## Files

- `TC-005.json` — full transcript with per-turn tool_loop_metrics (ABS-266 instrumentation)
- `SUMMARY.json` — runner summary
- `EXPERIMENT.md` — this file
