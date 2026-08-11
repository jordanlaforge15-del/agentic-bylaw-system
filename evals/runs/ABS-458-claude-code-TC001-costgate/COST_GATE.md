# ABS-458 — Cost gate: TC-001 alone on the `claude_code` backend

**Date:** 2026-08-10 · **Branch:** `agent/ABS-458-run-the-regional-centre-20case-eval-suit`
**Backend:** `ADVISOR_LLM_PROVIDER=claude_code`, `ANTHROPIC_API_KEY` unset in the
process environment (the registry's `_assert_no_api_key_billing` guard would have
refused to boot otherwise). Model `claude-opus-4-5`, CLI `claude` 2.1.x.
**Command:** unmodified `scripts/run_test_prompts.py --ids TC-001
--turn-timeout 1800 --model claude-opus-4-5`.

## Measurement

| Metric | TC-001 (2 turns) |
|---|---|
| Wall clock, whole case | **168.4 s** (T1 102.5 s, T2 65.8 s) |
| Tool-loop iterations | 5 (3 + 2) → 5 `claude -p` subprocess invocations |
| Advisor tool calls executed | 7 (`search_bylaw_evidence` ×6, `get_adjacent_zoning` ×1) |
| `terminated_reason` | `end_turn` on both turns — no breaker fired |
| Turns completed | 2/2 |
| **Metered USD spend** | **$0.00** — billed to the Claude Code subscription, not an API key |

Reported usage, summed over both turns (`tool_loop_metrics.total_usage`):

```
input_tokens                    61      <-- see caveat
output_tokens                7,882
cache_creation_input_tokens 177,080
cache_read_input_tokens     147,813
```

### Why there is no real `total_cost_usd` on this path

Nothing on this backend emits a dollar figure, and that is correct: the meter is
a subscription, not a per-token invoice. The closest available number is a
**notional API-equivalent** — what these same token counts *would* have cost at
Opus 4.5 list rates, computed with `compare_ab_runs.py`'s own `tok_cost_usd`:

* TC-001 notional API-equivalent: **$4.13 USD**

That number is **not** a spend figure and should not be treated as one. Per
ABS-457, `input_tokens` on this backend is a near-constant (~10/call) regardless
of prompt size, and the prompt instead lands in `cache_creation_input_tokens`,
which prices at a different rate. Claude Code's own 20–80k-token per-call
scaffolding rides in those same cache fields. The notional figure is recorded
only to show the order of magnitude and to make clear *why* DoD #5 excludes the
cost axis from the baseline comparison.

## Extrapolation to the full 20-case suite

The suite is 20 cases / **86 turns**. TC-001 is one of only two `simple` cases
and carries 2 turns; naive ×20 therefore understates it. Both projections:

| Basis | Wall clock | Notional API-equivalent |
|---|---|---|
| Naive ×20 cases | 0.94 h | $82.68 |
| **Turn-weighted (86 turns × 84.2 s/turn)** | **≈ 2.0 h** | **≈ $178** |

Subprocess invocations, turn-weighted: 86 × 2.5 iterations/turn ≈ **215**
`claude -p` calls (the design estimate said ~480; the measured iteration rate is
lower).

**Read the 2.0 h as a floor, not a point estimate.** Cost and latency per turn
grow with conversation length — TC-001's turn 2 carried 95k cache-creation
tokens against turn 1's 82k — and 11 of the 20 cases are `complex` with 5–6
turns each. 2–4 h is the realistic band, consistent with the design estimate.

## Gate decision

**Proceed with the remaining 19 cases.** The constraint the gate exists to
protect against — running out of purchased API credits mid-suite, which is
exactly how the ABS-306 attempt died — does not apply here: metered spend is
**$0**. The only real budget being consumed is wall clock (~2–4 h) and
subscription usage. No breaker fired on TC-001 and both turns completed cleanly,
so there is no evidence the run would degrade at scale.
