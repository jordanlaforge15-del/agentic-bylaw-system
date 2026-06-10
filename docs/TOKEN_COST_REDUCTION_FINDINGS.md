# Token Cost Reduction — Findings & Work Items

**Date:** 2026-06-07
**Context:** Free-trial cost is ~$10 USD/trial. Goal: cut the advisor tool-loop token
cost without impacting answer quality. Constraints already settled: no business-model
change, thick API rejected (normalization gaps), cheap-model retrieval agent rejected
(iterated more, cost more).
**Source:** Architecture review of the live retrieval MCP + `src/advisor/llm/*`,
`src/advisor/chat/*`, `docs/COST_REGRESSION.md`, `docs/agent/persona.md`, pricing brief.
Each finding below is intended to become one Linear issue.

---

## Core insight

**Every existing cost optimization operates at the cross-user-turn granularity. The
cost lives intra-loop — a single question firing 10–20 retrieval iterations — which
none of them touch.**

- `compact_history_for_submission` (`src/advisor/chat/history_compaction.py`)
  summarizes tool_results only for turns older than `keep_recent=2` **user-prompt**
  boundaries. One question's tool loop creates no new user-prompt boundaries, so all
  of its tool_results stay full-size.
- `_mark_conversation_cache_milestones` (`src/advisor/chat/session.py:391`) spends its
  2 conversation cache breakpoints on the **first assistant turns of the session** and
  explicitly skips tool-result turns.
- `run_tool_loop` (`src/advisor/llm/tool_loop.py:248–292`) appends each new
  `ToolResultBlock` with no cache flag and re-sends the full conversation every
  iteration.

Net effect: during a deep loop, iterations 3…N of tool results are re-sent
**full-size and uncached** every round — the triangular N(N+1)/2 input term at the
full Opus rate. Rough sizing at N=15 with ~1.5k-token results: ~169k cumulative
uncached input tokens × $15/MTok (Opus input) ≈ **$2.5/case**, vs ~$0.34 of output.
This is the dominant cost line. The existing `cache_system`/`cache_tools` breakpoints
sit *before* the growing region and do not help.

Reference: [Anthropic — tool use with prompt caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching),
[Don't Break the Cache (arXiv 2601.06007)](https://arxiv.org/pdf/2601.06007).

---

## Work items

### WI-1 — Rolling cache breakpoint inside the tool loop

**Priority: Urgent. Highest impact, lowest risk. Do this first.**

In `run_tool_loop` (`src/advisor/llm/tool_loop.py`), set `cache=True` on the **last**
`ToolResultBlock` of each iteration's result message. Anthropic caches up to and
including the marked block; the prior iteration's prefix is byte-identical, so
iteration N reads iterations 1…N-1 from cache at ~10% of the input rate (incremental
caching pattern).

Anthropic allows 4 breakpoints and all 4 are currently spent (2 on system/tools in
`session.py:217–218`, 2 on session-start milestones in
`_mark_conversation_cache_milestones`). **Reallocate one session-milestone breakpoint
to the rolling intra-loop breakpoint** — intra-loop growth (10–20 rounds/question)
dwarfs cross-turn growth (a handful of session milestones).

- **Expected impact:** dominant input term drops ~10× (≈$2.5 → ≈$0.25 per deep case).
- **Risk:** low. A misplaced breakpoint is just a cache miss (today's cost); it cannot
  affect answer content.
- **Verification:** `tool_loop_metrics.per_iteration[].usage` (ABS-266) —
  `cache_read_input_tokens` should dominate `input_tokens` from iteration 2 onward.
  Run Workflow A (cheap-model TC-005 regression, `docs/COST_REGRESSION.md`) before/after,
  then the Opus full-suite gate before merge.

### WI-2 — A/B Opus vs Sonnet on the 20-case suite

**Priority: High. The single largest dollar lever (~5×).**

Production runs `claude-opus-4-5` (`src/advisor/llm/registry.py:52`) at $15/$75 per
MTok. The pricing brief's cost model assumed **Sonnet** at $3/$15 — 5× cheaper across
the board. This gap is the main reason the brief's ~$0.50/case estimate meets a
~$10/trial reality. No documented full-suite comparison justified the Opus choice.

Run the existing 20-case suite (`scripts/run_test_prompts.py`, Workflow B) on Sonnet
4.x and compare answer quality + `tool_loop_metrics` against the Opus baseline.

- **Expected impact:** ~5× total cost reduction *if* quality holds. Dwarfs every other
  item.
- **Risk:** quality regression — this is measure-don't-switch. Answer quality is the
  hard constraint; if Sonnet fails the suite, close the issue with the evidence and
  keep Opus.
- **Verification:** side-by-side suite run; document verdict either way.

### WI-3 — Persona instruction: fan out independent retrievals in one turn

**Priority: High. Low effort, already plumbed.**

`run_tool_loop` already executes multiple `tool_use` blocks from a single assistant
turn (`tool_loop.py:273`) and returns them as one tool_result turn. The persona
(`docs/agent/persona.md`) never instructs this, so the model chases leads serially —
the root of the high iteration counts on the thin API.

Add a persona section instructing the model to issue independent
`search_bylaw_evidence` / `lookup_citation` calls **in the same turn** (e.g. the
schedule lookups for height, FAR, setbacks, streetwall on a property question;
following multiple cross-references at once). Match payloads already carry
`cross_references` and `ancestor_chain` — the leads are in hand by round 2.

- **Expected impact:** collapses ~15 serial rounds into ~5 fan-out rounds → cuts the
  N(N+1)/2 input term super-linearly and removes expensive Opus output rounds.
  Compounds with WI-1 and WI-2.
- **Risk:** low. Possible mild over-fetching of leads that serial pruning would have
  skipped — a wasted parallel search is one cheap input delta vs a full extra round.
- **Verification:** `tool_loop_metrics.per_iteration[].tool_call_count` rises above 1;
  `iterations` falls. TC-005 regression before/after.
- Reference: [parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use),
  [Scaling Parallel Tool Calling for Deep Research (arXiv 2602.07359)](https://arxiv.org/pdf/2602.07359).

### WI-4 — Extend tool-result compaction into the in-flight loop

**Priority: Medium. Do after WI-1; design around the cache.**

The summarizers in `history_compaction.py` (`_summarize_search`,
`_summarize_citation_lookup`, …) currently fire only across user-prompt boundaries.
Extend the policy into `run_tool_loop`: keep the last K iterations' tool_results
full, replace older ones in the *current* loop with the existing one-line summaries.
Summaries preserve citation paths, so the model can re-issue `lookup_citation` if it
needs a full payload back.

**Cache interaction (important):** rewriting bytes pops the prompt cache for the
rewritten region. Compact only the stable old region and keep the rolling breakpoint
(WI-1) at the recent boundary; compacted bytes must be deterministic (the module
already guarantees this).

- **Expected impact:** shrinks the cached-but-still-billed (10%) tail and the cache
  writes (1.25×) on long loops.
- **Risk:** medium — the model may occasionally need a summarized payload; mitigated
  by re-lookup. Hold this item if WI-1 + WI-3 already bring cost into range.
- **Verification:** TC-005 cost before/after with no `terminated_reason` or accuracy
  regression on the Opus suite.

### WI-5 — Make the cost-circuit estimator cache-aware

**Priority: High once WI-1 lands (coupled).**

`estimate_request_input_tokens` (`src/advisor/llm/budget.py`) is a char-based
heuristic that weights all input at full price. After WI-1, most of each request is
cache reads at ~10%, but the breaker (`tool_loop.py:212`) will still trip as if they
were full-price — **prematurely forcing synthesis and degrading answers**. Discount
expected cache-read tokens in the estimate (or re-base the budget on estimated
*billed-equivalent* tokens).

- **Risk of skipping:** WI-1's savings get partially converted into worse answers via
  early `cost_circuit_trip` terminations.
- **Verification:** `terminated_reason` distribution on the Opus suite unchanged (or
  improved) after WI-1 + WI-5 together.

### WI-6 — Reconcile `max_iterations` with advertised tier rounds

**Priority: Medium. Possible correctness bug, affects cost accounting.**

`session.py:220` calls `run_tool_loop` without `max_iterations`, so it defaults to
**10** (`tool_loop.py:170`). The persona and pricing brief advertise 12–18 rounds
(Standard) and 35–50 (Complex). Either Complex cases silently hit the iteration cap
and get forced-synthesis answers, or the token budget is the de-facto limiter and the
tier copy is wrong. Audit `terminated_reason` frequencies in production transcripts,
then either thread a per-tier `max_iterations` through `ChatSession` or fix the tier
documentation.

### WI-7 — Trim the retrieval API response envelope

**Priority: Low. Real waste, bounded payoff.**

Observed on the live MCP: every `search_bylaw_evidence` response **echoes the full
request object including all null defaults**, and every match carries always-present
fields that are often empty (`ancestor_chain`, `cross_references`, `related_tables`,
`linked_datasets`, `metadata_json`, `retrieval_channels`, `parse_status`). Drop the
request echo, omit null/empty fields, and consider flipping `include_*` defaults to
opt-in.

- **Expected impact:** modest — after WI-1 this region bills at the 10% cache rate;
  savings are on cache writes and the recent uncached tail. Worth doing because it's
  free and compounds, but it is not the hero.
- **Risk:** low; ensure the persona/tool docs don't reference removed fields.

---

## Explicitly rejected / not levers (do not re-open)

| Option | Verdict | Evidence |
|---|---|---|
| Thick API | Rejected | Tried; normalized representation has gaps, returned ~zero, LLM fell back to thin API. Revisit only after full-graph normalization research. |
| Cheaper retrieval sub-agent | Rejected | Tried; iterated more, total cost higher. Matches literature: weaker models iterate more and iteration count dominates. |
| Constrain extended thinking | Moot | Thinking is not enabled (`anthropic_backend.py` sends no `thinking` param). |
| Anthropic context editing (`clear_tool_uses`) | Deferred | Clearing invalidates the cache at the clear point and pays off mainly at 50–100+ turns. If WI-1/WI-3 land, loops won't grow long enough. Reconsider only for Complex-tier if costs remain high. |

---

## Sequencing

1. **WI-1** (rolling breakpoint) + **WI-5** (cache-aware breaker) — ship together.
2. **WI-2** (Opus vs Sonnet suite run) — independent; can run in parallel.
3. **WI-3** (fan-out persona) — after WI-1 baseline is measured, so effects are
   attributable.
4. **WI-6** (iteration-cap audit) — anytime; informs tier copy.
5. **WI-4**, **WI-7** — only if cost is still out of range after the above.

Every item must go through the existing cost-regression workflow
(`docs/COST_REGRESSION.md`): cheap-model TC-005 loop for iteration, Opus 20-case
full-suite as the merge gate. Per-item savings claims should cite
`tool_loop_metrics` deltas, not estimates.

---

## Post-implementation: WI-7 ↔ WI-3 surface-asymmetry resolution

**Date:** 2026-06-09 · **Issues:** ABS-288 (WI-7), ABS-289 (WI-3) · **Status:** resolved

A review flagged a default-flag divergence between ABS-288 and ABS-289. Determination
after reading the shipped code: **intentional-compatible, not functional drift —
fan-out is not broken — but the divergence was accidental-by-omission and the headline
metric was measured on the wrong surface.**

### What is true

There are two independent retrieval tool surfaces with **opposite `include_*`
defaults**, and that is correct:

| Surface | `include_*` default | Rationale |
|---|---|---|
| External MCP / OpenAI tool schema + `RetrievalRequest` | `False` (opt-in) | Lean default for external/MCP callers (ABS-288). |
| **Advisor production path** (`src/advisor/chat/tools.py`) | **`True`** | Feeds ABS-289 fan-out: the model must see `cross_references` / `ancestor_chain` / `linked_datasets` to follow leads. |

The advisor keeps `include_*=True` in two places — the model-facing JSON schema
(`chat/tools.py:277-279`, `322-325`) and the handler
(`search_bylaw_evidence_handler`, `:774-777`, `payload.get(..., True)`). ABS-288 never
modified `chat/tools.py`, so fan-out leads survive on the live path. The empty-field
serializer added by ABS-288 only omits fields when empty, so populated cross-refs are
unaffected.

### What was wrong

1. **Undocumented + accidental.** The asymmetry was not designed; ABS-288 simply didn't
   touch the advisor surface, and the advisor's pre-existing explicit `True` shielded
   it. Recorded here as an intended asymmetry going forward.
2. **The "47% reduction" does not describe production.** It was measured on the
   test-only endpoint `POST /v1/_test/search-evidence-raw` with all `include_*=False`
   (the lean external surface). The advisor path runs `include_*=True`, so it captures
   only **echo removal + empty-field omission** — a much smaller delta. The 47% is the
   external-surface number; the advisor-path saving must be re-measured.

### Follow-up actions (do NOT align defaults — that would break fan-out)

- **ABS-288 (amend/comment):** label 47% as the external-surface figure; add the
  advisor-path re-measurement (echo + empty-omission only, `include_*=True`).
- **New guard test (drift hazard):** the advisor relies on the *explicit*
  `payload.get(..., True)`. If a future refactor drops it and falls through to the now-
  `False` `RetrievalRequest` default, fan-out dies silently with no error. Neither
  existing spec catches this — `abs288-…-raw` hits the test endpoint;
  `abs289-persona-fan-out` only asserts the loop handles two `tool_use` blocks, not
  that cross-refs are present by default. Add an advisor-path assertion that a default
  `search_bylaw_evidence` call returns populated `cross_references` / `linked_datasets`.
- **Persona:** no correctness change — the advisor schema already advertises
  `default: True`, so it is self-consistent. A one-line developer note on the surface
  split is sufficient; the model needs no awareness of the external surface.

---

## Post-mortem: WI-1 and WI-4 reverted (ABS-304, 2026-06-10)

The two largest projected wins in this doc — WI-1 (rolling cache breakpoint) and
WI-4 (in-flight tool_result compaction) — were **reverted in production** after
ABS-303's real-API measurement showed they are net-negative.

### What was measured

ABS-303 ran a 3-arm experiment on real Opus 4.5, TC-001 turn 1 prompt verbatim,
same-stack restarts to swap env-var configurations:

| Arm | N | Cap-hit rate | Mean cost |
|---|---|---|---|
| Both ON (WI-1 + WI-4, the design target) | 10 | **50%** | $1.59 |
| WI-1 only | 6 | 33% | $0.96 |
| Both OFF | 8 | **0%** | $0.99 |

- Both OFF → Both ON: cap-hit rate goes 0% → 50%, mean cost +61%
  (Fisher exact on the cap-hit difference p ≈ 0.038, formally significant).
- WI-1 alone introduces the cap-hit failure mode (0% → 33%) — contrary to the
  design assumption that `cache_control` markers are a billing-only effect with
  no model-behaviour impact.
- WI-4 compounds on top of WI-1: cap-hit 33% → 50%, mean cost $0.96 → $1.59.

Full rollup: `evals/token_savings/20260610-ABS303-real-api-validation/ROLLUP.md`.
Raw data: `evals/runs/20260610-ABS303-tc001-turn1-AB-N10/`.

### Why the design's projections didn't hold

The "Core insight" section above modeled WI-1 saving the dominant input term by
~10× (≈$2.5 → ≈$0.25 per deep case). Two assumptions broke:

1. **Cache_write premium swamps cache_read savings on real loops.** Each new
   tool_result enters the cache once at the 1.25× write rate; the break-even
   reads-per-write ratio is ~0.278. Production transcripts run at 0.56–0.75,
   well above break-even — but the savings are modest, not 10×. ABS-302's
   analytical mode on existing ABS-293 transcripts showed only 13–20% saved.

2. **`cache_control` hints change model behaviour, not just billing.** The 0%
   → 33% cap-hit jump from adding WI-1 alone (vs Both OFF) suggests the model
   treats cache-marked prefixes differently — possibly as "already validated"
   — and over-iterates when its early reasoning was wrong. WI-4's compaction
   then strips context the model later needs, forcing re-lookups and pushing
   the run into the iteration cap. The cap-hit synthesis turns cost $2–3 each
   (dominated by output tokens), which dwarfs whatever per-iteration savings
   the optimizations deliver.

### Expected-value math against keeping the code

For WI-4 to be net-positive even if it helps on deep multi-turn cases:

- TC-001-like penalty (measured): ~$0.60 per shallow case.
- TC-005-like savings (unverified; ABS-302 MockGateway projected ~27%):
  ~$5.40 per deep case at the original $20 baseline.
- Break-even shallow:deep traffic ratio at 27% deep savings: ~9:1.
- Realistic production case mix is probably more shallow than that.

Verifying the deep-case upside would cost ~$300 (N=10 per arm × 3 arms × ~$10/
case on TC-005). Not worth keeping code with that expected value.

### What was reverted in ABS-304

- ABS-285's `_mark_rolling_cache_breakpoint` and its call site in `run_tool_loop`.
- ABS-290's `compact_tool_loop_history`, `resolve_tool_loop_keep_recent`, and
  the compaction call site in `run_tool_loop`.
- The `ADVISOR_DISABLE_ROLLING_CACHE_BREAKPOINT` env-var kill switch from
  ABS-303 (redundant once the underlying code was gone).
- `MOCK_DEEP_SEARCH` from `mock_dispatcher.py` and the WI-4 e2e spec that used it.
- ABS-285 e2e spec.
- Unit tests for all of the above.
- MockGateway mode in `scripts/measure_wi_token_savings.py` (and its tests),
  which depended on the WI-1/WI-4 functions being toggleable.

### What was kept

- `_MAX_CONVERSATION_CACHE_MILESTONES = 1` (the milestone reallocation from
  ABS-285). Conservative on cache markers given the observed behavioural side
  effect; cost of reverting to 2 is real cache writes for unclear gain.
- **WI-5** (ABS-291 cache-aware estimator). No behavioural side effect; pure win.
- **WI-7** (ABS-288 envelope trim on retrieval responses). Smaller payloads, no
  model behaviour change.
- **WI-6** (ABS-287 per-tier `max_iterations`). Unrelated to the cache machinery.
- The analytical mode of `scripts/measure_wi_token_savings.py` for reading
  historical transcripts.

### Lessons recorded

- The MockGateway-based projection in ABS-302 modeled bytes accurately but
  could not model the model's behavioural reaction to cache markers. Synthetic
  loop benchmarks should be flagged as known-suspect for any optimization
  whose intervention is visible to the model.
- Single-turn shallow prompts are the worst test case for WI-4; deep multi-turn
  prompts would have shown different results. But the data we have is what we
  acted on, and acting on it was the right call given the expected-value math.
- The probe-first cost protection (from the earlier ABS-293 overshoot) prevented
  worse damage during the ABS-303 measurement runs.
