# ABS-462 — re-grade of this run

This run is the regression that motivated ABS-462. TC-001 turn 2 told a
homeowner their side setback was **0.0 m**, citing clause 198(1)(d).

The corpus says (`Part V > 198 > [Side Setback Requirements] > (d)`, p. 172):

> (d) where a lot line abuts a lot, any portion of which, is zoned DD, DH,
> CEN-2, CEN-1, or COR zone, 0.0 metre, except as provided in Clause 198(1)(a);

Turn 1 of the same conversation established that every resolvable neighbour is
**HR-1**. None of DD / DH / CEN-2 / CEN-1 / COR abut the lot, so the governing
clause is `(f) 2.5 metres elsewhere`. The answer also glossed (d) as applying to
"all other uses", which is not what the clause says.

## Before / after

| | old grader | ABS-462 grader |
|---|---|---|
| verdict | `PARTIAL` | `FAIL_APPLICABILITY` |
| keyword rate | 67% (8/12 — 6 keywords, scored twice) | 100% (6/6, case level) |
| `expected_bylaw_references` | not read | 100% (3/3), all resolve |
| `expected_topics` | not read | 100% (3/3) |
| citations resolved | 7/7 | 7/7 |
| hallucinated | 0 | 0 |
| inapplicable | — | **1** — `198(1)(d)` |

The keyword jump is not a loosened bar: the conversation always said all six
keywords, but each turn was scored against the whole case list, so turn 2 was
penalised for not repeating turn 1. The verdict flipped from PARTIAL to FAIL for
the opposite reason — the answer is substantively wrong, and nothing the old
scorer measured could see it.

## Reproduce

Against the dev DB (the Regional Centre ingest, document_id=4):

```bash
.venv/bin/python scripts/verify_test_prompts.py evals/runs/20260811T113204Z
```

Or offline, against the committed corpus slice — same verdict, same 7/7
citations, same finding:

```bash
.venv/bin/python scripts/verify_test_prompts.py evals/runs/20260811T113204Z \
  --corpus-json evals/fixtures/abs462_corpus_snapshot.json
```

`verification/TC-001.verify.json` in this directory was regenerated from the
dev DB. Expectations come from the live `evals/regional_centre_test_prompts.json`
rather than the copy frozen into `TC-001.json`, which predates ABS-463 and still
names `Table 3` / `Section 9(a)` / `Section 9(d)`.

## What is still not graded

The applicability check catches zone-conditional misapplication. It does not
catch use-conditional clauses ("for a townhouse dwelling use"), paraphrase drift
(the "all other uses" gloss above), arithmetic, or a provision omitted
altogether. See the module docstring in `scripts/verify_test_prompts.py` —
closing those needs an LLM-judge stage, which is deliberately not built here.
