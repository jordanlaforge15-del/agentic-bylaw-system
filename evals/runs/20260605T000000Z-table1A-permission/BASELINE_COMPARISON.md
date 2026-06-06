# ABS-280 Phase 4 — Baseline Comparison

**Run timestamp:** 2026-06-05T00:00:00Z  
**Purpose:** Measurement gate proving Phases 1-3 moved the needle on TC-005 T5 answer quality.

---

## TC-005 T5 — "Is home occupation permitted in HR-2?"

**Test notes:** "Home occupation is NOT permitted in HR-2 (Table 1A). High liability: 60-80 unit building."

---

## Pre-Phase-3 Baseline (haiku-baseline, 2026-06-03T10:00:00Z)

```
I **did not retrieve the home occupation provisions** before exhausting my budget,
so I cannot confirm whether home occupation is permitted in HR-2 or under what conditions.

What I can tell you about the bylaw structure:
- Home occupation is defined and regulated in Part I of the Regional Centre Land Use By-law...
- Zone-specific permitted uses are listed in Part IV (which includes the HR-2 chapter, which
  I did not fully retrieve)...

**The key question:** Is home occupation listed as a permitted use in HR-2, or is it only
permitted in certain residential zones (e.g., ER-1, ER-2, ER-3)?
...
**Recommendation:** Before designing a ground-floor office space, confirm with HRM Planning
& Development whether: Home occupation is permitted in HR-2 at all.
```

**Analysis:** Pure hedge — no tool calls, no committed verdict, deferred to planner.
**Tool calls:** 0  
**AC1 committed verdict:** FAIL — hedged  
**AC3 Table 1A citation:** FAIL — no tool call  

---

## Pre-Phase-3 Post-Fix Run (ABS-261, 2026-06-03T09:28:04Z)

The 2026-06-03T092804Z run (ABS-261 fix) gave a **committed but wrong** answer:

> ## Home Occupation in HR-2 — Yes, It's Permitted
> **Short answer: Yes, a property management office as a home occupation is permitted
> in HR-2**, subject to the standard home occupation requirements.
> Per **Section 51(2)**, the bylaw explicitly lists... "office use"... [Section 51(2)(f)]

**Analysis:** Committed but WRONG — said "permitted" when Table 1A shows it's NOT permitted.
The answer was inferred from the home occupation *definition* (Section 51), not from the
zone-specific *permission matrix* (Table 1A). This is the exact error Phases 1-3 fix.
**Tool calls:** 0  
**AC1 committed verdict:** PARTIAL (committed but incorrect)  
**AC3 Table 1A citation:** FAIL — no Table 1A lookup  

---

## Expected Post-Phase-3 Behavior (AC1-AC3 targets)

With Phases 1-3 deployed, the advisor MUST:

1. **AC1:** Make a `lookup_citation` call with `{"kind": "permitted_use", "use": "home occupation", "zone": "HR-2"}` and return a **committed "not permitted"** verdict, not a hedge.

2. **AC2:** No footnote expected (blank cell = not_permitted = no condition text).

3. **AC3:** Answer is grounded in a Table 1A citation — the tool call result must reference `source_table_cell.metadata_json.permission_marker = "not_permitted"`.

Expected answer pattern:
```
Home occupation is **not permitted** in HR-2 per Table 1A of the Regional Centre LUB.
[Table 1A, page XX, [bylaw name]]
```

---

## Mechanical Evidence (AC4 — automated regression)

The unit test `tests/advisor/test_tc005_home_occupation_regression.py` confirms:
- `RetrievalService.lookup_permitted_use(use="home occupation use", zone="HR-2")` → `permission="not_permitted"`
- `RetrievalService.lookup_permitted_use(use="multi-unit dwelling use", zone="HR-2")` → `permission="permitted"`
- Zone-specificity: same use returns `permitted` in DD but `not_permitted` in HR-2

The Playwright spec `web/e2e/functional/tc005-home-occupation-regression.spec.ts`
confirms the same via the live HTTP endpoint with seeded data.

---

## Live Eval Instructions

To produce the TC-005.json / SUMMARY.json live evidence, run:

```bash
# Prerequisites:
# 1. Advisor running with ANTHROPIC_API_KEY and Phases 1-3 code
# 2. Production layer1 database enriched (backfill_permission_markers.py applied)
# 3. Table 1A caption: "Table 1%Permitted uses by zone%" present in source_table

python scripts/run_phase4_tc005_eval.py --base-url http://127.0.0.1:8000
# or for T5-only to minimize spend:
python scripts/run_phase4_tc005_eval.py --t5-only --base-url http://127.0.0.1:8000
```

Evidence writes to `evals/runs/<ts>-table1A-permission/`.

### Database Preconditions
The local dev `layer1` database shows **0 source tables with captions** as of 2026-06-05.
Table 1A permission matrix detection requires `SourceTable.caption ILIKE 'Table 1%Permitted uses by zone%'`.
This suggests the ingest pipeline hasn't populated captions for the RCLUB tables.

Check with:
```sql
SELECT COUNT(*) FROM source_table WHERE caption ILIKE 'Table 1%Permitted uses by zone%';
-- Must be >= 1 before running the eval
```

If 0: the bylaw needs to be re-ingested with caption extraction enabled, or the
`backfill_table_profiles.py` script needs to be run to backfill captions.
