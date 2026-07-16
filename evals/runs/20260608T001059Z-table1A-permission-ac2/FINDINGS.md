# ABS-280 Phase 4 — TC-005 T5 permitted-use validation

**Model:** claude-opus-4-5 (production chat model) · **DB:** layer1 doc 4 (Regional Centre LUB)
**Question (T5):** "Can we add a home occupation component — a property management office —
for the building manager in a ground-floor unit? Is home occupation permitted in HR-2?"

**Ground truth** (verified against doc 4 table 1056, cell row=Home occupation use × col=HR-2):
**conditional**, footnote ⑮ — "Use is permitted, except within the Halifax Grain Elevator
(HGE) Special Area, as shown on Schedule 3F."

---

## Result — all four ACs met (final run: `20260608T001059Z-table1A-permission-ac2`)

| AC | Requirement | Result |
|----|-------------|--------|
| AC1 | Committed verdict, not a hedge | ✅ "Yes, Conditionally Permitted" — no verdict hedge |
| AC2 | Conditional answer cites footnote condition text | ✅ Quotes ⑮ Halifax Grain Elevator / Schedule 3F verbatim, applies it to the site |
| AC3 | Grounded in Table 1A structured lookup | ✅ `lookup_citation` (permitted_use) called, **0 errors** |
| AC4 | Automated regression pins resolver output | ✅ `tests/advisor/test_tc005_home_occupation_regression.py` |

Baseline (2026-06-03 haiku): pure hedge — *"I did not retrieve the home occupation
provisions… I cannot confirm whether home occupation is permitted in HR-2."*

---

## What the live validation uncovered (and this issue fixed)

The validation did its job: re-running TC-005 against the real model exposed that the
permitted-use feature did **not** work end-to-end, despite correct data and a correct
resolver. Two bugs, both fixed here:

1. **Tool-handler crash on the structured arg (the real blocker).**
   Opus serialized the nested `structured` argument as a JSON *string*
   (`'{"kind":"permitted_use",...}'`) instead of a nested object. `lookup_citation_handler`
   did `CitationLookupRequest.model_validate(payload)`, which raised a pydantic
   `ValidationError` on every structured permitted-use call. The model thrashed re-issuing
   the call to the 10-iteration cap, then fell back to ungrounded prose — answering
   "permitted" with the wrong (non-conditional) verdict and mis-citing the table.
   *Fix:* `_coerce_stringified_object_arg` parses a stringified nested object before
   validation (`src/advisor/chat/tools.py`).

2. **Resolver never surfaced the footnote condition text (AC2 blocker).**
   `_footnote_condition_text` matched only `FOOTNOTE`-typed fragments, but the Regional
   Centre ⑮ legend was ingested as `PROSE`, so `condition_text` was always null.
   *Fix:* match a footnote legend by its leading circled glyph regardless of fragment_type
   (`mcp/bylaw_retrieval/retrieval/service.py`). Deeper ingest-typing fix → ABS-284.

3. **Writer dropped the footnote in favour of operating standards.**
   Once the data reached the model, it committed to "conditional" but paraphrased the
   Section 51 operating requirements as the "conditions" rather than quoting the ⑮ carve-out.
   *Fix:* a conditional `permitted_use` result now carries an inline `instruction` telling the
   writer to quote `condition_text` verbatim (`src/advisor/chat/compact.py`), the same
   writer-steering pattern ABS-261 uses for citation-lookup misses.

## Evidence trail (run directories)

- `20260607T235752Z-table1A-permission-full` — **before** the handler fix: full 6-turn run,
  `lookup_citation` calls error (ValidationError), answer ungrounded/over-permissive.
- `20260608T000758Z-table1A-permission-fixed` — after handler fix: 0 tool errors, committed
  conditional, but footnote text not yet surfaced (AC2 fail).
- `20260608T001059Z-table1A-permission-ac2` — after writer nudge: **all ACs pass.**

## Known follow-ups (not blocking these ACs)

- The tool loop still hits the 10-iteration cap on this turn (ABS-268 territory) — the answer
  is correct but the loop is chattier than ideal.
- Ingest should type Table 1A footnote legends as `FOOTNOTE`, not `PROSE` (ABS-284), so the
  resolver's type-agnostic fallback becomes belt-and-suspenders rather than load-bearing.
