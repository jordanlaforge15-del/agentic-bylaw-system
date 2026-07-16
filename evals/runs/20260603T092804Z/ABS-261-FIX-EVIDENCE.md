# ABS-261 Fix Verification — TC-005 Single-Case Re-Run

**Run:** `evals/runs/20260603T092804Z/`
**Baseline:** `evals/runs/20260602T132143Z/` (TC-005 transcript only)
**Date:** 2026-06-03
**Scope:** TC-005 only (six-turn HR-2 developer feasibility, complex / high-liability)
**Backend:** local dev stack on `:8000`, real `https://api.anthropic.com` (no proxy, no mock)
**Model:** Claude Opus 4.5 (advisor default)
**Dev branch:** `cbf6c3f` Merge ABS-260 + downstream ABS-263 / ABS-265 already merged

---

## 1. What ABS-261 fixed

`mcp/bylaw_retrieval/retrieval/service.py::lookup_citation` was raising `ValueError`
on any path miss. The advisor's tool-use loop translated that into a `tool_error`
and the model would re-attempt the same lookup (or wander) until
`max_iterations=10` forced synthesis — burning ~10 × full-context API calls per
capped turn.

The fix changed the contract to a `CitationLookupResponse(match, suggestions)`
envelope: on a miss, the model gets up to 8 rapidfuzz-ranked suggestions plus an
inline instruction telling it to either retry with a suggested path or switch
tools. No exception is raised.

Unit + e2e coverage (`tests/test_retrieval*.py`, `web/e2e/functional/abs261-citation-suggestions.spec.ts`)
is already on `dev`. This document is the SUT-level evidence.

## 2. Headline result

| Metric | Baseline (pre-fix) | Post-fix | Delta |
|---|---|---|---|
| Stop reason (per turn) | `end_turn` × 6 | `end_turn` × 6 | unchanged ✓ |
| Verifier verdict | PASS | PASS | unchanged ✓ |
| Citations grounded | 52 / 52 | **62 / 62** | +10 citations (broader sourcing) |
| Hallucinated citations | 0 | 0 | unchanged ✓ |
| Keyword hit rate | 36% | **63%** | +27 pp (also reflects ABS-265 keyword recalibration) |
| Total tokens (input + cache + output) — SSE-visible only | 365,836 | **279,165** | −24% |
| Estimated cost from SSE usage (Opus 4.5) | $2.87 | $2.60 | −$0.27 (−10%) |
| **Actual Anthropic-bill cost** | (~$5.40 inferred) | **$4.92 measured** | ~−$0.48 (~−9%) |
| Wall time (6 turns) | 385.0 s | 322.3 s | −16% |
| Server-side `max_iterations` cap hits | (not measured) | **2 / 6 turns** | — |

### Important — SSE usage is NOT the full bill

The SSE stream emits `usage` only for the final synthesis turn per user message.
Every intermediate tool-use round (each one is its own `messages.create` call
carrying the full conversation context) is billed but invisible to the runner.
For this TC-005 run, the actual Anthropic invoice was **$4.92**, vs the **$2.60**
computed from SSE-captured tokens — a **1.89× multiplier** from hidden tool-loop
rounds. The same multiplier almost certainly applied to the pre-fix baseline,
which is why the *delta* (−10%) holds even though both absolute numbers are
nearly 2× what the transcripts show.

Implication for future cost projections: take the SSE-derived cost and multiply
by ~1.9× for complex multi-turn cases with active tool use to get a realistic
billed-cost estimate.

## 3. Per-turn breakdown (post-fix)

Pulled from `TC-005.json[turns][i].usage` and from `/tmp/dev-api.log`
correlated by wall-clock against per-turn start times.

| Turn | Wall | Input | Cache read | Cache write | Output | Stop | Cap hit? |
|---|---|---|---|---|---|---|---|
| T1 | 54.5 s | 38,710 | 0 | 0 | 954 | end_turn | **YES** (06:28:38) |
| T2 | 14.5 s | 10,770 | 27,148 | 4,970 | 664 | end_turn | no |
| T3 | 51.5 s | 5,119 | 18,583 | 245 | 826 | end_turn | **YES** (06:30:46) |
| T4 | 66.5 s | 44,082 | 0 | 4,135 | 931 | end_turn | no |
| T5 | 57.3 s | 5,082 | 55,482 | 2,072 | 856 | end_turn | no |
| T6 | 78.1 s | 2,619 | 52,039 | 2,252 | 2,626 | end_turn | no |

Note: every turn's SSE-emitted `stop_reason` is `end_turn`. The cap-hit warnings
appear only in the server-side `advisor.llm.tool_loop` logger because the
synthesis-turn forced after a cap also exits cleanly. The SSE stream does NOT
expose intermediate tool rounds in the dev profile (this is by-design for the
chat UI), so the only way to see iteration-cap hits is the server log:

```
[2026-06-03 06:28:38] WARNING advisor.llm.tool_loop [1b191d2c7def47bc8edf6e5a40c8d9bd] tool-use loop hit max_iterations=10; forced synthesis turn
[2026-06-03 06:30:46] WARNING advisor.llm.tool_loop [81ec44f3a637440b88e88feb5d95634a] tool-use loop hit max_iterations=10; forced synthesis turn
```

## 4. Interpretation — was the fix successful?

**Partial.** The fix is doing what it was designed to do (zero exceptions raised,
suggestion envelope returned cleanly, advisor answer quality preserved), but the
cap-hit blast radius on TC-005 specifically is smaller than projected:

- The cost savings (~$0.48 actual, ~10% on this case — measured against the
  real $4.92 Anthropic invoice, not the $2.60 SSE-derived estimate) are real
  but well below the $1–2/case I projected in the post-mortem. The pre-fix
  $18 / 5-case estimate was dominated by cap-hits on multiple cases combined;
  the per-case savings are not uniformly distributed.
- TC-005 still has **2 cap-hit turns out of 6 (33%)**, vs the baseline aggregate
  of 38% across all 5 cases. The pattern persists.
- T1 ("base zoning standards: height, lot coverage, setbacks") and T3 ("how
  many floors at 65% lot coverage and 25 m height? is multi-unit confirmed?")
  are the cap-hitting turns. Both are wide-net info gathers that need 3+
  distinct Table-1A row reads PLUS use-permission lookups. That is a different
  failure mode than the lookup_citation thrash ABS-261 addressed — it looks
  like the model is making 8–9 legitimate tool rounds plus 1–2 wasted, not
  10 thrash rounds.
- The verifier outcome is unchanged (PASS), citation grounding actually
  improved (52 → 62 found), and hedging now passes (ABS-263 effect). So the
  fix did not regress quality on TC-005.

## 5. Follow-up implications

The fix lands, but the residual cap hits suggest at least one more issue
worth a ticket:

- **Persistent cap hits on wide-net info-gather turns** (TC-005 T1, T3). The
  iteration budget of 10 may simply be too tight for queries that legitimately
  need ≥5 lookups per attribute. Options: raise the cap, parallelize tool calls,
  or have the model batch lookups inside a single `search_bylaw_evidence`
  rather than serial `lookup_citation` calls. This is NOT what ABS-261 fixed
  and is not blocked by ABS-261's merge.

(Not filing as a new ticket without explicit user approval per
[[feedback_followup_tracking]] — flagging here for visibility.)

## 6. Evidence inventory

- `TC-005.json` — full transcript, all 6 turns, usage per turn
- `SUMMARY.json` — runner-level summary
- `REPORT.md` — verifier-driven readiness report (PASS verdict)
- `verification/TC-005.verify.json` — independent SQL grounding of every cited
  fragment against `layer1.fragments` for document_id=4 (Halifax Regional
  Centre LUB, real ingest, no test fixture)
- `verification/SUMMARY.json` — verifier-level summary
- `/tmp/dev-api.log` — server-side tool-loop warnings (cap-hit timestamps)
- `evals/runs/20260602T132143Z/TC-005.json` — sealed pre-fix baseline for the
  same case (transcript-level diff source)

## 7. Reproduction

```bash
# Boot advisor only, real Anthropic API, X-Test-User-Id auth, Postgres on :5432
export ANTHROPIC_API_KEY="$(cat anthropic_api_key)"
export DATABASE_URL="postgresql+psycopg://layer1:layer1@localhost:5432/layer1"
unset CLERK_JWKS_URL CLERK_ISSUER CLERK_SECRET_KEY  # force permissive fallback
.venv/bin/uvicorn advisor.api.dev:app --host 127.0.0.1 --port 8000 &

# Single-case sweep
.venv/bin/python scripts/run_test_prompts.py --ids TC-005 --turn-timeout 300

# Independent SQL verification
DATABASE_URL_PLAIN="postgresql://layer1:layer1@localhost:5432/layer1" \
  .venv/bin/python scripts/verify_test_prompts.py evals/runs/<ts>/

# Roll up
.venv/bin/python scripts/build_readiness_report.py evals/runs/<ts>/
```
