# ABS-306 — Opus Baseline Run (PARTIAL — Credit Exhausted)

**Date:** 2026-06-14  
**Model:** `claude-opus-4-5`  
**Status:** PARTIAL — API credits exhausted after TC-003 fully; TC-004 partial.

## What happened

This run was intended to be the 20-case Opus baseline for the ABS-306 Sonnet vs Opus A/B
comparison. The advisor was started in permissive mode (no CLERK_JWKS_URL) using an API key
recovered from `tmp/.env.swp`. That key ran out of credits during TC-004.

## Valid data in this run

| TC  | Complexity | Valid? | Cost (USD) | Turns | Iters | Notes |
|-----|-----------|--------|------------|-------|-------|-------|
| TC-001 | simple | ✅ VALID | $1.45 | 2/2 | 12 | Full run |
| TC-002 | simple | ✅ VALID | $3.98 | 3/3 | 23 | iter_cap on T1, cost_trip on T2 |
| TC-003 | medium | ✅ VALID | $3.36 | 4/4 | 21 | All end_turn |
| TC-004 | complex | ⚠️ PARTIAL | ~$2.97 | 2/5 valid | 9 | Turns 3–5 empty (no credits) |
| TC-005–TC-020 | various | ❌ INVALID | $0 | empty | 0 | Empty responses (no credits) |

Total spend on this key: ~$11.76 USD (TC-001 through TC-004 partial)

## Cost breaker effect (ABS-305)

Note: ABS-305 introduced a cumulative per-turn cost breaker **after** ABS-293's 2-case probe.
This run shows the breaker firing:
- TC-002 T2: `cumulative_cost_trip` (9 iters, then stopped)
- TC-004 T1: `cumulative_cost_trip` (5 iters, then stopped)

The ABS-293 probe costs ($6.00 for TC-001, $19.75 for TC-005) were WITHOUT the breaker.
With the breaker, TC-001 costs $1.45 (76% reduction). The breaker materially changes
expected Opus costs for the full suite.

## What's needed to complete ABS-306

1. **Valid ANTHROPIC_API_KEY with ~$80-100 of credits** (Opus 20-case: ~$40-50, Sonnet: ~$8-12)
2. Start the advisor in **permissive mode** (no CLERK_JWKS_URL):
   ```bash
   kill $(lsof -ti :8000) 2>/dev/null || true
   nohup env \
     ANTHROPIC_API_KEY="<key>" \
     PYTHONPATH=src \
     DATABASE_URL="postgresql+psycopg://layer1:layer1@localhost:5432/layer1" \
     ADVISOR_LLM_MAIN_MODEL="claude-opus-4-5" \
     PYTHONUNBUFFERED=1 \
     .venv/bin/uvicorn advisor.api.dev:app --host 127.0.0.1 --port 8000 \
     > /tmp/advisor-opus.log 2>&1 &
   sleep 5 && curl -s http://127.0.0.1:8000/healthz | jq '.llm.main_model'
   # expected: "claude-opus-4-5"
   ```
3. Run Opus baseline (all 20 cases):
   ```bash
   TS_OPUS=$(date -u +%Y%m%dT%H%M%SZ)
   .venv/bin/python scripts/run_test_prompts.py \
     --model claude-opus-4-5 \
     --out-dir "evals/runs/${TS_OPUS}-ABS306-opus-baseline"
   ```
4. Switch to Sonnet:
   ```bash
   kill $(lsof -ti :8000) && sleep 2
   nohup env \
     ANTHROPIC_API_KEY="<key>" \
     PYTHONPATH=src \
     DATABASE_URL="postgresql+psycopg://layer1:layer1@localhost:5432/layer1" \
     ADVISOR_LLM_MAIN_MODEL="claude-sonnet-4-6" \
     PYTHONUNBUFFERED=1 \
     .venv/bin/uvicorn advisor.api.dev:app --host 127.0.0.1 --port 8000 \
     > /tmp/advisor-sonnet.log 2>&1 &
   sleep 5 && curl -s http://127.0.0.1:8000/healthz | jq '.llm.main_model'
   # expected: "claude-sonnet-4-6"
   ```
5. Run Sonnet suite:
   ```bash
   TS_SONNET=$(date -u +%Y%m%dT%H%M%SZ)
   .venv/bin/python scripts/run_test_prompts.py \
     --model claude-sonnet-4-6 \
     --out-dir "evals/runs/${TS_SONNET}-ABS306-sonnet-candidate"
   ```
6. Compare:
   ```bash
   .venv/bin/python scripts/compare_ab_runs.py \
     --baseline  "evals/runs/${TS_OPUS}-ABS306-opus-baseline" \
     --candidate "evals/runs/${TS_SONNET}-ABS306-sonnet-candidate" \
     --output-md "evals/runs/${TS_SONNET}-ABS306-sonnet-candidate/AB_COMPARISON.md"
   ```

## Prior art: ABS-293 2-case probe

The ABS-293 probe (in `evals/runs/20260610T000639Z-ABS293-opus-2case` and
`20260610T001529Z-ABS293-sonnet-2case`) established:
- **7.2× cheaper**: Sonnet $3.56 vs Opus $25.75 (2 cases: TC-001 + TC-005)
- **0 hallucinations** on both models (PARTIAL verdicts, hallucinations tied at 0)
- Run WITHOUT the ABS-305 cost breaker — absolute costs will be lower in new runs

The ABS-306 full suite is designed to confirm this 7.2× ratio holds across all 20 cases
and across the complexity spectrum (simple/medium/complex).

## Decision rule (once full suite runs)

From `docs/COST_REGRESSION.md § Workflow C`:
> Switch to Sonnet **only** if hallucination count ≤ Opus count AND PASS rate ≥ Opus PASS rate.

`compare_ab_runs.py` will print `SWITCH TO SONNET` or `KEEP OPUS` as the final line.
