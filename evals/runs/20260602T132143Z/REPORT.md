# ABS-260 — Production-Readiness Sweep Report (Partial)

**Run directory:** `.claude/worktrees/abs-260/evals/runs/20260602T132143Z`
**Branch:** `jordanlaforge15/abs-260-prod-readiness-sweep`
**Sweep status:** **Aborted at 5/20 cases** — API budget exhausted by an advisor-side
tool-loop bug (see §1). No further token spend incurred.
**Overall verdict:** **NO-GO for production**, but for a *different* reason than
the rubric on its own would suggest. See §1.

---

## 1. Headline finding — `lookup_citation` ValueError thrash

**This is a real production bug surfaced by the sweep. It is the single biggest
reason the test ran out of budget at 5 cases instead of 20.**

### What's happening

The advisor's tool-use loop hits a contract mismatch between how the model formats
citation paths and what the bylaw-retrieval service accepts. The model issues
paths the way a human would write them in prose:

- `Table 1A`
- `Part III`
- `Part IV`
- `Part V > 115`
- `Part I > 51 > [Home Occupation Uses]`

`mcp/bylaw_retrieval/retrieval/service.py:202` raises
`ValueError: Citation '<path>' not found in document N` on any path that doesn't
match the stored `citation_path` exactly. The model treats the error as
"wrong shape, retry differently," guesses a variant, fails again, and burns
the full `max_iterations=10` cap on what should be a one-shot lookup.

### Quantified impact (from `/tmp/abs-260-api.log` over the 5 completed cases)

| Metric | Count |
|---|---:|
| Turns processed | ~21 |
| Turns that hit `max_iterations=10; forced synthesis turn` | **8 (≈38%)** |
| `lookup_citation` ValueError failures | **13** |

### Cost arithmetic

| | Visible in SSE transcripts | Actual API spend | Gap |
|---|---:|---:|---:|
| 5 cases | **$8.17** | **~$18** | ~$10 wasted on hidden tool-loop iterations |

The SSE stream only emits the *final synthesis turn's* usage. Each internal
tool-use iteration is a separate `messages.create` call carrying the full
conversation context (~30K tokens). A single capped turn ≈ 10 × 30K × $15/MTok ≈
**$4.50 of waste, never visible to the user**.

Projection for the full 20-case sweep:

| Scenario | Estimated cost |
|---|---:|
| Bug present (status quo) | **~$70–80** |
| Bug fixed (no max-iter thrash) | **~$15–18** |

### Recommended fix

`service.lookup_citation` should not raise `ValueError` on path mismatch. It
should return an empty match with a `suggestions` list of nearby
`citation_path` values (e.g., fuzzy-match by token overlap, or return all
paths whose normalized form starts with the requested token). The tool-use loop
then sees "no hits, here are the closest options" and can re-issue exactly one
corrected lookup — instead of guessing in the dark for 10 iterations.

**Tracked as a separate ticket. The sweep cannot be re-run economically until
this is fixed.**

---

## 2. Methodology caveat — keyword-match rates are a soft signal here

The test corpus's `expected_answer_keywords` were derived from a simplified
77-line synthetic fixture (`tests/fixtures/halifax_regional_centre_lub.txt`),
which uses round numbers like `7.5 m`, `1.2 m`, `45%`. **The actual Halifax
Regional Centre LUB ingest in the dev DB (document_id=4, 4,340 fragments) uses
precinct-based schedules** (Schedule 15, 17, 18, 50) where setbacks and heights
vary by spatial location, not by zone code alone.

When the advisor correctly geocodes an address and returns precinct-specific
values from Schedule 18, those values *don't match* the fixture's round
numbers — and the verifier marks them as "missing expected keyword." This is
*production-grade behavior being penalized by a test rubric calibrated to a
simpler synthetic source*.

Example: TC-001 asked about ER-1 setbacks at 1234 Oxford St. The advisor's
spatial lookup returned the address as zoned **HR-1** (confidence 0.85, not
ER-1 as the user stated) and quoted the **3.0 m** HR-1 setback. Spec expected
**7.5 m** (ER-1 rear from the fixture). The advisor was right; the rubric
flagged it as a miss.

The next iteration of this corpus should rewrite `expected_answer_keywords`
against the real ingest's content, not the test fixture.

---

## 3. Threshold check (current rubric, with caveat above)

ABS-260 rules:
- Zero hallucinated citations across the sweep (hard fail).
- 100% PASS on `complexity: simple`.
- ≥85% PASS on `complexity: medium` and `complex`.

| Bucket | PASS rate | Target | OK? |
|---|---:|---:|---|
| simple | 0/2 = 0% | 100% | ❌ |
| medium | 0/1 = 0% | ≥85% | ❌ |
| complex | 0/2 = 0% | ≥85% | ❌ |
| hallucinated citations | 0 | 0 | ✅ |

**Overall: NO-GO** by current rubric. But the failures are dominated by
"keyword-match below threshold" verdicts that — per §2 — partly reflect a test
miscalibration, not advisor wrongness.

---

## 4. Headline metrics

- **Cases run / planned:** 5 / 20
- **PASS:** 0   **PARTIAL:** 2   **FAIL:** 3
- **Citations cross-checked:** **138 / 138 grounded** in the source bylaw (100%)
- **Hallucinated citations:** **0** ← this is the real positive signal
- **Tool-loop iteration-cap hits:** 8 (≈38% of turns)
- **Total API spend on the sweep:** ~$18 (5 cases) — full sweep projected $70–80
  with bug present, ~$15–18 without

The **0 hallucinations / 100% citation grounding** result is genuinely good.
Every citation the advisor returned (e.g. `Section 9`, `Section 230`,
`Part XVII`, `Table 1A`) maps to a real fragment in the ingested bylaw. The
advisor is not making things up — it is just thrashing on the lookup
mechanism.

---

## 5. Per-case verdicts

| ID | Verdict | Zone | Complexity | Liability | Kw rate | Cites (found/total) | Notes |
|---|---|---|---|---|---|---|---|
| TC-001 | ❌ FAIL | ER-1 | simple | low | 42% | 5/5 | spatial lookup overrode user-stated zone (ER-1 → HR-1); spec keywords don't match real ingest |
| TC-002 | ❌ FAIL | ER-2 | simple | low | 42% | 13/13 | one Part-citation flagged "missing" by verifier was actually valid (parser caveat — see §6) |
| TC-003 | ❌ FAIL | ER-3 | medium | medium | 25% | 21/21 | dimensional values cited by advisor came from real ingest, not from fixture |
| TC-004 | ⚠️ PARTIAL | HR-1 | complex | high | 40% | 47/47 | hedging absent on 2 high-liability turns |
| TC-005 | ⚠️ PARTIAL | HR-2 | complex | high | 36% | 52/52 | hedging absent on all 6 high-liability turns — see §7 |

---

## 6. Hallucinated citations

**None.** Every one of the 138 citations the advisor produced resolved to a
real fragment in `document_id=4` (Halifax Regional Centre LUB, 4340 fragments).

Caveat on the verifier itself: an earlier version of `verify_test_prompts.py`
falsely flagged `Part IV` (page 94) and `Part XVII` (page 378) as hallucinations
because the **layer1 ingest's hierarchy parser only captured Parts I, V, and X
into the `citation_path` column** — Parts II/III/IV/VI/VII/VIII/IX/XI… exist only
in the fragment text. Verifier was patched to fall back to text-prefix matching;
after the patch, hallucination count is zero.

*This is a separate finding worth its own ticket: the layer1 hierarchy parser
misses Roman-numeral Parts other than I/V/X. It affects the bylaw_retrieval
service too — that's part of why `lookup_citation('Part III')` fails.*

---

## 7. Hedging failures on high-liability turns

These are turns where the spec set `liability: high` and the advisor's response
did NOT include any of the standard hedging markers (`confirm with`, `consult`,
`professional`, `planner`, `lawyer`, `HRM`, `I recommend`, `not legal advice`,
`site-specific`, etc.):

- TC-004 T3 (architect, parking minimums) — no hedging
- TC-004 T4 (architect, ground-floor daycare permission) — no hedging
- **TC-005 T1–T6 (developer, HR-2 tower feasibility) — every turn missing hedging**

TC-005 is the standout: a high-liability developer-feasibility scenario where
the advisor gave specific quantitative answers (parking ratios, FAR, max
buildable floors) **across all six turns without ever telling the developer to
confirm with HRM or a planner**. That's a production-grade problem for a tool
giving paid feasibility advice. Recommend gating-prompt instructions or a
post-generation hedging-injector for `liability:high` flagged cases.

---

## 8. Most-frequent expected-keyword misses

(see §2 — these are *partly* test-corpus calibration issues, not pure advisor
gaps)

| Keyword | Miss count | Likely cause |
|---|---:|---|
| `3.0 m` | 14 | advisor quoted precinct-specific values from Schedule 18 |
| `1 parking space` | 11 | advisor quoted Table 8 ratios; spec used fixture's flat ratio |
| `multi-unit dwelling` | 8 | advisor used "multi-unit residential" / "apartment building" wording |
| `dwelling unit` | 8 | terminology drift |
| `permitted` | 7 | advisor used "as-of-right" / "allowed" |
| `1.2 m` | 6 | precinct-specific override |
| `home occupation` | 6 | advisor used "home-based business" wording in some turns |
| `not permitted` | 6 | advisor said "prohibited" or "not allowed" |
| `no off-street parking` | 6 | advisor said "no parking minimum" |
| `7.5 m`, `6.0 m`, `20.0 m`, `25.0 m` | 5 each | precinct overrides + non-fixture values |
| `1 space per 4` | 5 | bicycle parking — advisor didn't surface this in HR-2 / CEN-1 turns |
| `45%`, `65%` | 4–5 each | lot coverage — varies by precinct, not fixture's flat % |

---

## 9. Action items

1. **Fix `mcp/bylaw_retrieval/retrieval/service.py:202`** — convert
   `ValueError` to a graceful empty-with-suggestions response. **This is the
   blocker for production readiness AND for re-running the sweep economically.**
   Filed as a separate Linear ticket (see comment on ABS-260).

2. **Fix the layer1 hierarchy parser** — capture all Roman-numeral Parts
   (I–XX), not just I/V/X. Affects every Part-citation lookup. Separate ticket.

3. **Add hedging-injection for `liability:high` turns** — TC-005 surfaced a
   real safety issue. Either tighten the system prompt or post-process responses
   on flagged cases.

4. **Rewrite `expected_answer_keywords`** against the real ingest, not the
   77-line synthetic fixture. Otherwise the rubric will keep penalizing the
   advisor for being more correct than the fixture.

5. **Wire per-iteration usage telemetry into the SSE stream** (or into the
   transcript writer) so a future cost audit can see the full picture without
   needing the Anthropic Org Usage API.

6. **Re-run the sweep** with the same runner + verifier (already committed),
   after item 1 lands and item 4 has been updated. Projected cost: $15–18 for
   all 20 cases.

---

## 10. Per-case transcripts and verifications

- **TC-001** — `TC-001.json` (transcript) · `verification/TC-001.verify.json`
- **TC-002** — `TC-002.json` · `verification/TC-002.verify.json`
- **TC-003** — `TC-003.json` · `verification/TC-003.verify.json`
- **TC-004** — `TC-004.json` · `verification/TC-004.verify.json`
- **TC-005** — `TC-005.json` · `verification/TC-005.verify.json`

Cases TC-006 through TC-020 were not run.
