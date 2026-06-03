# TC-005 — Cheap-Model Regression Baseline (Haiku 4.5)

**Run:** `evals/runs/20260603T100000Z-haiku-baseline/`
**Branch:** `jordanlaforge15/abs-266-267-tool-loop-metrics`
**Date:** 2026-06-03
**Model:** `claude-haiku-4-5`
**Sealed by:** ABS-267 acceptance criterion #4

This is the sealed cheap-model baseline that future cost-touching
changes regression-test against. Per ABS-267 scoping, only TC-005
has a cheap-model baseline — see `docs/COST_REGRESSION.md` for the
full workflow.

## Headline numbers

| Metric | Value |
|---|---|
| Cases | TC-005 only |
| Turns completed | 6 / 6 |
| Wall time | 80.6 s |
| Estimated cost (Haiku 4.5) | **$0.33 USD / $0.45 CAD** |
| Verifier verdict (when run) | — (not graded; baseline is for cost+iteration, not quality) |
| `terminated_reason` distribution | `iteration_cap` × 1, `end_turn` × 5 |

For comparison, the prior Opus 4.5 TC-005 run on `dev` cost **$4.92 USD**
(`evals/runs/20260603T092804Z/`). The cost ratio Opus:Haiku is **15×**.

## Per-turn iteration evidence (the data ABS-266 makes visible)

| Turn | Iterations | Reason | Tool calls | Input | Cache read | Cache write | Output |
|---|---|---|---|---|---|---|---|
| T1 | **10** | **iteration_cap** | 17 | 166,783 | 297,434 | 56,505 | 2,428 |
| T2 | 1 | end_turn | 0 | 4,737 | 57,152 | 1,848 | 356 |
| T3 | 1 | end_turn | 0 | 4,784 | 7,253 | 932 | 664 |
| T4 | 1 | end_turn | 0 | 702 | 8,185 | 4,781 | 428 |
| T5 | 1 | end_turn | 0 | 1,169 | 8,185 | 4,781 | 502 |
| T6 | 1 | end_turn | 0 | 1,030 | 12,966 | 699 | 1,261 |

**Why T2–T6 show `tool_calls=0` despite the run completing 6 turns
of substantive Q&A:** the chat session carries forward the
conversation, so by T2 the model already has the evidence T1
retrieved in its context. The cache_read column shows this — T2's
57k cache-read is the T1 evidence riding the prompt cache. The
model answers from already-retrieved context without re-calling
tools. That's by design and is the same pattern Opus showed.

## T1 deep dive — the iteration_cap turn

T1 is the canonical "wide-net info-gather" turn that ABS-261's
post-mortem flagged. Now we have the full staircase:

| Iter | Input | Cache R | Cache W | Output | Latency (ms) | Tool calls |
|------|-------|---------|---------|--------|--------------|------------|
| 1 | 372 | 0 | 6,542 | 348 | 2,682 | 3 |
| 2 | 6,579 | 6,542 | 369 | 211 | 3,449 | 2 |
| 3 | 42,613 | 6,542 | 369 | 209 | 3,740 | 2 |
| 4 | 36,513 | 7,055 | 6,428 | 191 | 2,184 | 2 |
| 5 | 2,736 | 13,616 | 35,901 | 201 | 2,444 | 2 |
| 6 | 4,603 | 49,642 | 347 | 198 | 2,381 | 2 |
| 7 | 4,511 | 50,096 | 2,150 | 131 | 1,732 | 1 |
| 8 | 2,425 | 52,363 | 2,222 | 150 | 1,927 | 1 |
| 9 | 2,255 | 54,699 | 2,051 | 117 | 1,912 | 1 |
| 10 | 4,203 | 56,879 | 126 | 132 | 1,982 | 1 |
| **11 (forced synthesis)** | 59,973 | 0 | 0 | 540 | 8,934 | 0 |

Tool-call breakdown across the 17 invocations in T1:

- `search_bylaw_evidence`: 10
- `lookup_citation`: 6
- `get_document_outline`: 1
- **Errors:** 0 / 17

This is the key finding the Opus run could not produce: **T1's cap
hit is NOT thrash.** The model made 17 *successful* tool calls
across 10 iterations. Every tool returned cleanly. The cap fired
because TC-005 T1 ("height + lot coverage + setbacks for HR-2")
genuinely needs more than 10 lookups to gather all the dimensions
the user asked for. `max_iterations=10` is the bottleneck, not
`lookup_citation` returning errors.

This validates the hypothesis in `evals/runs/20260603T092804Z/ABS-261-FIX-EVIDENCE.md`:

> T1 and T3 are wide-net info gathers that need 3+ distinct Table-1A
> row reads PLUS use-permission lookups. That is a different
> failure mode than the lookup_citation thrash ABS-261 addressed —
> it looks like the model is making 8–9 legitimate tool rounds
> plus 1–2 wasted, not 10 thrash rounds.

What that prior write-up couldn't prove without ABS-266, this run
now confirms with zero ambiguity.

## Implication for follow-up work

The cap-hit is real and persistent, but the root cause is **iteration
budget**, not **tool reliability**. Possible fixes (none of which
this run scoped to address):

1. Raise `max_iterations` for the chat tool loop (cheap but raises
   the upper bound on a runaway turn's cost — needs a corresponding
   cost-circuit-breaker tightening).
2. Have the model batch lookups via `search_bylaw_evidence`'s
   structured query rather than serial `lookup_citation` calls.
3. Add a "gather phase / synthesis phase" prompt structure that
   asks the model to enumerate needed lookups up front and execute
   them in parallel.

None of these are filed as tickets yet, per
[[feedback_followup_tracking]] — flagged here for visibility.

## How to regression against this baseline

After a cost-touching change, boot the dev stack on Haiku, run:

```bash
.venv/bin/python scripts/run_test_prompts.py \
  --ids TC-005 \
  --model claude-haiku-4-5 \
  --out-dir evals/runs/<new-ts>-haiku-regression
```

Then compare iteration counts and total cost via the
`tool_loop_metrics` event on each turn (see
`docs/COST_REGRESSION.md` for the script template).

Expected signal for a successful cost fix:

- T1 iterations drop from 10 (cap) toward ≤ 6
- Total cost drops from $0.33 toward lower
- T2–T6 stay at 1 iteration each (don't regress these)

## Reproduction

```bash
export ANTHROPIC_API_KEY="$(cat anthropic_api_key)"
export ADVISOR_LLM_MAIN_MODEL="claude-haiku-4-5"
unset CLERK_JWKS_URL CLERK_ISSUER CLERK_SECRET_KEY
.venv/bin/uvicorn advisor.api.dev:app --host 127.0.0.1 --port 8000 &
# Verify model before spending:
curl -s http://127.0.0.1:8000/healthz | jq '.llm.main_model'
# Run:
.venv/bin/python scripts/run_test_prompts.py \
  --ids TC-005 --model claude-haiku-4-5 \
  --out-dir evals/runs/20260603T100000Z-haiku-baseline
```

## Files in this directory

- `TC-005.json` — full transcript with per-turn `tool_loop_metrics`
- `SUMMARY.json` — runner summary
- `server-tool-loop.log` — server-side cap-hit warnings
- `BASELINE.md` — this file
