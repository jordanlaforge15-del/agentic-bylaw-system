# ABS-286 — Opus 4.5 vs Sonnet 4.x A/B Comparison (PENDING)

**Status:** Infrastructure complete. Live API runs needed to produce results.

## What's been built

- `scripts/compare_ab_runs.py` — analysis script that ingests two run directories
  and produces a side-by-side cost + quality report
- `tests/scripts/test_compare_ab_runs.py` — 18 unit tests covering cost calculation,
  cache hit rate, terminated-reason rollup, and report generation
- `docs/COST_REGRESSION.md` — new **Workflow C** section with step-by-step
  instructions for running the Opus vs Sonnet comparison

## How to run the actual comparison

See `docs/COST_REGRESSION.md § Workflow C` for the full recipe. Short form:

```bash
# 1. Opus baseline
export ANTHROPIC_API_KEY="..."
unset ADVISOR_LLM_MAIN_MODEL
./scripts/dev-up.sh &
sleep 5
TS_OPUS=$(date -u +%Y%m%dT%H%M%SZ)
.venv/bin/python scripts/run_test_prompts.py \
  --model claude-opus-4-5 \
  --out-dir "evals/runs/${TS_OPUS}-opus-baseline"

# 2. Sonnet candidate (same stack, swap model)
kill $(lsof -ti :8000) && export ADVISOR_LLM_MAIN_MODEL="claude-sonnet-4-6"
./scripts/dev-up.sh &
sleep 5
TS_SONNET=$(date -u +%Y%m%dT%H%M%SZ)
.venv/bin/python scripts/run_test_prompts.py \
  --model claude-sonnet-4-6 \
  --out-dir "evals/runs/${TS_SONNET}-sonnet-candidate"

# 3. Compare
.venv/bin/python scripts/compare_ab_runs.py \
  --baseline  "evals/runs/${TS_OPUS}-opus-baseline" \
  --candidate "evals/runs/${TS_SONNET}-sonnet-candidate" \
  --output-md "evals/runs/${TS_SONNET}-sonnet-candidate/AB_COMPARISON.md"

# 4. (Optional) quality verification
.venv/bin/python scripts/verify_test_prompts.py "evals/runs/${TS_OPUS}-opus-baseline"
.venv/bin/python scripts/verify_test_prompts.py "evals/runs/${TS_SONNET}-sonnet-candidate"
# Re-run compare to fold in quality scores
.venv/bin/python scripts/compare_ab_runs.py \
  --baseline  "evals/runs/${TS_OPUS}-opus-baseline" \
  --candidate "evals/runs/${TS_SONNET}-sonnet-candidate" \
  --output-md "evals/runs/${TS_SONNET}-sonnet-candidate/AB_COMPARISON.md"
```

## Decision rule

Switch to Sonnet **only if**:

1. Hallucinated citations on candidate ≤ Opus baseline
2. PASS rate on candidate ≥ Opus baseline (by complexity tier)
3. `iteration_cap` terminations on candidate ≤ Opus baseline (if higher,
   Sonnet is thrashing and the cost saving is partly illusory)

If all three hold, `compare_ab_runs.py` will print:

```
**Recommendation: SWITCH TO SONNET** — quality holds, cost materially lower.
```

## Cost estimate for the runs

| Run | Est. cost (USD) | Notes |
|-----|-----------------|-------|
| Opus 20-case suite | $15–25 | WI-1 cache in place; expect lower than pre-fix baseline |
| Sonnet 20-case suite | $3–5 | 5× pricing advantage |
| **Total** | **$18–30** | One-time evaluation |

USD → CAD ≈ 1.34× + HST = ~1.54×. Total ~CAD $28–46.
