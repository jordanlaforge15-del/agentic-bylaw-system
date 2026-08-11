# Cost regression workflow (ABS-267)

## Why this exists

The advisor's tool-use loop makes multiple `messages.create` calls per
user turn — each one carrying the full conversation history. The
billed cost grows in a staircase with iteration count, but until
ABS-266 the runner could only see the final synthesis turn's usage.
That under-reported true cost by ~1.89× on multi-iteration turns,
and made it impossible to verify whether a cost-targeted fix actually
saved tokens.

This doc explains the cheap-model regression workflow that came out
of that gap. The short version:

- **Cheap-model TC-005 loop** ← development iteration on cost fixes
- **Opus full-suite (20 cases)** ← merge gate before a fix lands on
  `dev`

The cheap-model loop catches regressions on a single case for under
$0.50 USD per run. The Opus gate is the one that protects against
the case where a fix helps TC-005 but breaks the other 19 cases.

## Cost guardrails — read this before any live run

The user pays the bills in CAD. **USD → CAD is currently 1.34× plus
HST**, so:

| USD spend | CAD equivalent (≈) |
|-----------|--------------------|
| $1        | $1.55              |
| $5        | $7.70              |
| $25       | $38.50             |
| $50       | $77.00             |

Treat every USD listed in this doc as **~1.5× CAD**. A "$5 quick
test" is a $7.70 CAD line item. Budget like the person paying.

## Workflow A — cheap-model regression on TC-005

**When to use:** any change that touches the tool-use loop, retrieval
contracts, system prompt, hedging, or anything plausibly cost-affecting.

**Cost per run:** $0.30 – $0.50 USD on `claude-haiku-4-5`.

### Setup (one-time per worktree)

```bash
./scripts/dev-setup.sh --skip-db
(cd web && npm install)
```

### Boot the dev stack on Haiku

```bash
export ANTHROPIC_API_KEY="..."     # real key
export ADVISOR_LLM_MAIN_MODEL="claude-haiku-4-5"
unset CLERK_JWKS_URL CLERK_ISSUER CLERK_SECRET_KEY  # permissive auth fallback
./scripts/dev-up.sh &
```

`ADVISOR_LLM_MAIN_MODEL` is read by `AdvisorLLMSettings.main_model`
and threaded down to `ChatSession.model` at session-create time. The
legacy `ADVISOR_LLM_MODEL` env var is also honoured for backwards
compatibility (see `src/advisor/llm/registry.py`).

### Verify the model before spending money

```bash
curl -s http://127.0.0.1:8000/healthz | jq '.llm.main_model'
# expected: "claude-haiku-4-5"
```

### Run the case with the precondition check on

```bash
.venv/bin/python scripts/run_test_prompts.py \
  --ids TC-005 \
  --model claude-haiku-4-5
```

`--model` pings `/healthz` before any chat traffic and aborts if the
live advisor reports a different `main_model`. This is the
spend-protection trip — without it, a stale Opus stack will quietly
serve a $4+ run when you meant a $0.50 one.

### Inspect the results

Each turn's transcript now carries a `tool_loop_metrics` field
(ABS-266) with iteration count, per-iteration usage, terminated
reason, and per-tool latency. Use that — not the SSE-derived
`usage` — for cost analysis:

```bash
.venv/bin/python -c "
import json
d = json.load(open('evals/runs/<ts>/TC-005.json'))
for t in d['turns']:
    m = t['tool_loop_metrics'] or {}
    print(f\"T{t['turn']}: iters={m.get('iterations')} reason={m.get('terminated_reason')}\")
"
```

## Workflow B — Opus full-suite (merge gate)

**When to use:** before merging a cost-touching change to `dev`.
Confirms the cheap-model improvement carries to Opus AND that no
other case regressed.

**Cost per run:** $15 – $25 USD on `claude-opus-4-5` (depends on cap
hits and case complexity).

```bash
export ANTHROPIC_API_KEY="..."
unset ADVISOR_LLM_MAIN_MODEL  # Opus is the default
./scripts/dev-up.sh &
.venv/bin/python scripts/run_test_prompts.py --model claude-opus-4-5
```

Verify `iterations` and `total_usage` on `tool_loop_metrics` look
sane across all 20 cases before approving the merge. The Opus run
is the only one that catches a fix-helps-TC-005 / breaks-the-others
regression.

## Interpreting cheap-model vs Opus iteration counts

**They are NOT comparable on absolute numbers.** Haiku and Opus make
different decisions about when to stop calling tools. Haiku may need
more iterations to reach the same answer (worse one-shot reasoning)
or fewer (less elaborate tool sequences). What IS comparable across
models is the **direction of change** for a given case before vs
after a fix:

- "Haiku TC-005 iterations dropped from 22 → 14 after the fix" →
  structural evidence the fix removes thrash, not just an
  Opus-specific accident
- "Haiku TC-005 cost dropped from $0.48 → $0.34 after the fix" →
  the change is saving billed tokens, not just iteration count

Translate Haiku savings to Opus expectations cautiously. A 30% Haiku
cost reduction usually predicts a similar % reduction on Opus, but
the absolute dollar savings on Opus are ~15–25× larger.

## Live Haiku smoke test

`tests/integration/test_haiku_smoke.py` exercises Haiku 4.5 with one
real tool round-trip. It's skipped unless both
`ABS_RUN_LIVE_HAIKU_SMOKE=1` and `ANTHROPIC_API_KEY` are set:

```bash
ABS_RUN_LIVE_HAIKU_SMOKE=1 \
  ANTHROPIC_API_KEY="..." \
  .venv/bin/pytest tests/integration/test_haiku_smoke.py -v
```

Cost: under $0.01 USD per run. Use it after any Anthropic-SDK
upgrade or before a long cheap-model regression to catch the
"Haiku tool-use silently broken" failure mode early.

## Workflow C — Opus vs Sonnet A/B (model-swap evaluation)

**When to use:** one-time evaluation of whether Sonnet can replace
Opus in production. This is *measure-don't-switch*: run both models
on the full 20-case suite, compare cost + quality, document the
verdict. See ABS-286.

**Cost per run:** ~$15–25 USD (Opus) + ~$3–5 USD (Sonnet) = ~$18–30
total for the comparison pair. Run both from the **same** advisor
stack build so WI-1 cache changes are held constant.

### 1. Run the Opus baseline

```bash
export ANTHROPIC_API_KEY="..."
unset ADVISOR_LLM_MAIN_MODEL  # Opus 4.5 is the default
./scripts/dev-up.sh &
sleep 5
curl -s http://127.0.0.1:8000/healthz | jq '.llm.main_model'
# expected: "claude-opus-4-5"

TS_OPUS=$(date -u +%Y%m%dT%H%M%SZ)
.venv/bin/python scripts/run_test_prompts.py \
  --model claude-opus-4-5 \
  --out-dir "evals/runs/${TS_OPUS}-opus-baseline"
```

### 2. Run the Sonnet candidate

Stop the advisor process and restart with Sonnet:

```bash
# Kill the advisor uvicorn (leave Postgres running)
kill $(lsof -ti :8000) 2>/dev/null || true
sleep 2

export ADVISOR_LLM_MAIN_MODEL="claude-sonnet-4-6"
./scripts/dev-up.sh &
sleep 5
curl -s http://127.0.0.1:8000/healthz | jq '.llm.main_model'
# expected: "claude-sonnet-4-6"

TS_SONNET=$(date -u +%Y%m%dT%H%M%SZ)
.venv/bin/python scripts/run_test_prompts.py \
  --model claude-sonnet-4-6 \
  --out-dir "evals/runs/${TS_SONNET}-sonnet-candidate"
```

### 3. Run the comparison

```bash
.venv/bin/python scripts/compare_ab_runs.py \
  --baseline  "evals/runs/${TS_OPUS}-opus-baseline" \
  --candidate "evals/runs/${TS_SONNET}-sonnet-candidate" \
  --output-md "evals/runs/${TS_SONNET}-sonnet-candidate/AB_COMPARISON.md"
```

### 4. (Optional) Run quality verification on both

Requires the dev DB to be up — or pass `--corpus-json <snapshot>` to grade
against a committed corpus slice instead (ABS-462), which is how the graded
provisions are checked in CI and in worktrees without the Halifax ingest:

```bash
.venv/bin/python scripts/verify_test_prompts.py \
  "evals/runs/${TS_OPUS}-opus-baseline"
.venv/bin/python scripts/verify_test_prompts.py \
  "evals/runs/${TS_SONNET}-sonnet-candidate"
# Re-run compare to incorporate quality scores:
.venv/bin/python scripts/compare_ab_runs.py \
  --baseline  "evals/runs/${TS_OPUS}-opus-baseline" \
  --candidate "evals/runs/${TS_SONNET}-sonnet-candidate" \
  --output-md "evals/runs/${TS_SONNET}-sonnet-candidate/AB_COMPARISON.md"
```

### Interpreting the report

`scripts/compare_ab_runs.py` prints a Markdown report with:

- **Aggregate cost table** — total USD, cost per case, cost ratio
- **Tool-loop metrics** — `terminated_reason` distribution, total
  iterations (iteration-cap hits signal quality loss)
- **Per-case breakdown** — cost + iterations per TC-NNN
- **Quality comparison** — PASS/PARTIAL/FAIL verdicts + hallucination
  count per case (if verification data present)
- **Verdict recommendation** — SWITCH TO SONNET / KEEP OPUS / REVIEW

Verdicts from the verifier are `PASS`, `PARTIAL`, `FAIL`,
`FAIL_HALLUCINATION` (a citation with no matching fragment in the
corpus) and `FAIL_APPLICABILITY` (a *real* provision applied where its
stated condition is not met — ABS-462). Treat `FAIL_APPLICABILITY` as
at least as serious as a hallucination: the answer is wrong and every
existence check passes it.

Decision rule: switch to Sonnet **only** if hallucination count ≤
Opus count AND PASS rate ≥ Opus PASS rate. A regression in either
metric keeps Opus regardless of cost saving.

## What this workflow deliberately does NOT do

- **Establish Haiku baselines for TC-001…TC-004 or TC-006…TC-020.**
  Per ABS-267 scoping, only TC-005 has a cheap-model baseline. The
  Opus full-suite (Workflow B) is the cross-case safety net.
- **Automate cost regression in CI.** Manual runs only, until we
  have enough signal to justify the spend pattern of CI-on-PR.
- **Replace Opus in production before ABS-286 closes.** Haiku is the
  regression-testing tool; production answer quality stays on Opus
  until the A/B (Workflow C) documents the verdict.

## Related

- ABS-260 — Production-readiness sweep (the run that surfaced the
  cost-observability gap)
- ABS-261 — `lookup_citation` ValueError fix (the cost-fix this
  workflow would have validated rigorously)
- ABS-266 — Tool-loop metrics SSE event (the instrumentation this
  workflow depends on; blocks ABS-267)
- `evals/runs/20260603T092804Z/ABS-261-FIX-EVIDENCE.md` — post-mortem
  that motivated this doc
