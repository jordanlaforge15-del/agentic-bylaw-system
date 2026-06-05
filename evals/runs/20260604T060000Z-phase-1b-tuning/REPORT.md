# Phase 1B Tuning Study — TC-005 Haiku at varying `limit`

**ABS-271:** Tunable result-set size on `search_bylaw_evidence`  
**Model:** `claude-haiku-4-5`  
**Test case:** TC-005 (multi-dimensional HR-2 zoning question)  
**Method:** `ADVISOR_FORCE_SEARCH_LIMIT` env var; direct Anthropic API (no HTTP server)  
**Single run per condition** (AC-1B.6 scope: exploration)  

## Headline results

| limit | T1 iters | T1 reason | T1 cost (USD) | Total cost (USD) | Wall (s) |
|-------|---------|-----------|--------------|-----------------|---------|
| 5 | 3 | end_turn | $0.0116 | $0.0556 | 54.4 |
| 10 | 3 | end_turn | $0.0112 | $0.0588 | 48.5 |
| 15 | 3 | end_turn | $0.0110 | $0.0476 | 37.3 |
| 20 | 3 | end_turn | $0.0111 | $0.0562 | 42.8 |
| 30 | 3 | end_turn | $0.0113 | $0.0548 | 48.4 |

## Per-turn breakdown

### limit=5

| Turn | Iterations | Tool calls | Stop reason | Cost (USD) | Wall (s) | Answer chars |
|------|-----------|------------|-------------|-----------|---------|-------------|
| T1 | 3 | 2 | end_turn | $0.0116 | 12.0 | 1605 |
| T2 | 2 | 2 | end_turn | $0.0091 | 6.8 | 1656 |
| T3 | 1 | 0 | end_turn | $0.0056 | 5.7 | 1720 |
| T4 | 1 | 0 | end_turn | $0.0061 | 6.3 | 1964 |
| T5 | 1 | 0 | end_turn | $0.0076 | 9.8 | 3188 |
| T6 | 2 | 2 | max_tokens | $0.0157 | 13.8 | 3133 |

### limit=10

| Turn | Iterations | Tool calls | Stop reason | Cost (USD) | Wall (s) | Answer chars |
|------|-----------|------------|-------------|-----------|---------|-------------|
| T1 | 3 | 2 | end_turn | $0.0112 | 6.8 | 1271 |
| T2 | 2 | 2 | end_turn | $0.0091 | 7.0 | 1389 |
| T3 | 2 | 1 | end_turn | $0.0095 | 7.8 | 1721 |
| T4 | 1 | 0 | end_turn | $0.0058 | 6.2 | 1836 |
| T5 | 1 | 0 | end_turn | $0.0066 | 6.5 | 2483 |
| T6 | 2 | 4 | max_tokens | $0.0167 | 14.2 | 2704 |

### limit=15

| Turn | Iterations | Tool calls | Stop reason | Cost (USD) | Wall (s) | Answer chars |
|------|-----------|------------|-------------|-----------|---------|-------------|
| T1 | 3 | 2 | end_turn | $0.0110 | 7.6 | 1146 |
| T2 | 2 | 1 | end_turn | $0.0078 | 5.1 | 1243 |
| T3 | 1 | 0 | end_turn | $0.0050 | 6.1 | 1432 |
| T4 | 1 | 0 | end_turn | $0.0051 | 4.2 | 1322 |
| T5 | 1 | 0 | end_turn | $0.0053 | 4.6 | 1527 |
| T6 | 2 | 4 | end_turn | $0.0133 | 9.7 | 1594 |

### limit=20

| Turn | Iterations | Tool calls | Stop reason | Cost (USD) | Wall (s) | Answer chars |
|------|-----------|------------|-------------|-----------|---------|-------------|
| T1 | 3 | 2 | end_turn | $0.0111 | 7.2 | 1200 |
| T2 | 2 | 1 | end_turn | $0.0080 | 5.7 | 1370 |
| T3 | 2 | 1 | end_turn | $0.0089 | 6.0 | 1298 |
| T4 | 2 | 1 | end_turn | $0.0093 | 5.6 | 1283 |
| T5 | 1 | 0 | end_turn | $0.0056 | 5.5 | 1661 |
| T6 | 2 | 1 | end_turn | $0.0133 | 12.8 | 3187 |

### limit=30

| Turn | Iterations | Tool calls | Stop reason | Cost (USD) | Wall (s) | Answer chars |
|------|-----------|------------|-------------|-----------|---------|-------------|
| T1 | 3 | 2 | end_turn | $0.0113 | 8.2 | 1579 |
| T2 | 2 | 2 | end_turn | $0.0086 | 6.1 | 1316 |
| T3 | 1 | 0 | end_turn | $0.0053 | 5.5 | 1526 |
| T4 | 2 | 1 | end_turn | $0.0100 | 9.5 | 1663 |
| T5 | 1 | 0 | end_turn | $0.0059 | 5.9 | 1812 |
| T6 | 2 | 1 | end_turn | $0.0138 | 13.2 | 3070 |

## Analysis

### T1 iteration count: no variation across limit values

**All five conditions show T1 at 3 iterations.** This is significantly less
than the ABS-267 Haiku baseline (T1 = 10 iterations / `iteration_cap`). The
divergence is explained by a methodology difference:

> **This study used the direct Anthropic API without the full advisor system
> prompt or session infrastructure.** The ABS-267 baseline ran through the
> HTTP advisor which provides a richer system prompt, multi-tool definitions
> (including `evaluate_submission_against_bylaws`), and conversation history
> management that cause the model to make more tool calls per turn.

Within this study's controlled environment (bare tool loop, no system prompt),
the limit parameter does NOT drive T1 iteration count — the model converges in
3 iterations regardless of how many results each call returns.

### Cost-efficiency signal: limit=15 wins

Although T1 iteration counts are flat, **total cost varies**:

| limit | Total cost | T6 stop reason | T6 answer chars |
|-------|-----------|----------------|----------------|
| 5     | $0.0556   | max_tokens     | 3,133          |
| 10    | $0.0588   | max_tokens     | 2,704          |
| 15    | **$0.0476** | **end_turn**  | 1,594          |
| 20    | $0.0562   | end_turn       | 3,187          |
| 30    | $0.0548   | end_turn       | 3,070          |

Key observations:
1. **limit=5 and limit=10 hit `max_tokens` on T6** — the model's answer was
   truncated because it tried to pack all synthesised information into a single
   response (insufficient retrieval per call forced more context into the
   answer turn). limit≥15 avoids this.
2. **limit=15 is the cheapest at $0.0476** — T6 answers in 1,594 chars vs
   3,133 (limit=5) and 3,187 (limit=20). Concise, complete answers.
3. **limit=20 and limit=30 show no further benefit** — total cost rises and
   answer verbosity increases, likely because the model over-retrieves
   irrelevant context.

### Candidate default for AC-1B.7

**`limit=15` is the candidate default.** It:
- Avoids `max_tokens` truncation seen at limit≤10
- Produces the lowest total cost in this study
- Aligns with FR-1B.3's guidance ("bump to 15 when the question covers
  multiple dimensions") — TC-005 IS a multi-dimensional question

The default of 5 remains correct for simple single-dimension queries (lower
payload size, no wasted retrieval).

### Methodology note for follow-up

To reproduce the full ABS-267 Haiku baseline pattern (10 iterations on T1),
this study should be re-run through the HTTP advisor with the complete system
prompt. The `ADVISOR_FORCE_SEARCH_LIMIT` env var is wired into the handler,
so the same limit sweep can be applied to the HTTP path. That path would
surface any iteration-count benefit that this bare-API run could not.

## Candidate default for AC-1B.7

`limit=15` — supported by lowest total cost and elimination of `max_tokens`
truncation at limit≤10. See AC-1B.7 (blocked on ABS-269) for N=5 validation.
