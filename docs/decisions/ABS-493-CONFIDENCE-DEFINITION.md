# ABS-493 — What "confidence" means on a zone profile

**Status:** accepted · **Date:** 2026-08-13 · **Phase:** Data Model 3.0 / P3

## The decision

`ZoneProfile.confidence` is an **ordinal evidence class**, not a probability.
It answers *"what kind of evidence ties this value to the thing that was asked
about?"* — never *"how likely is this value correct?"*

The class is decided by **where** the query's terms land in the supporting
fragment, and never by **how many** of them land.

The vocabulary lives in `EvidenceClass` (`mcp/bylaw_retrieval/retrieval/schemas.py`),
the rung each class maps to in `EVIDENCE_CLASS_CONFIDENCE`, and the gate in
`MIN_GATED_EVIDENCE_CONFIDENCE`.

| Rung | Class | The evidence |
|-----:|-------|--------------|
| 1.0 | `exact_path` | The query names the fragment's `citation_path` verbatim. An identity lookup, not a search. |
| 0.9 | `bound_table_cell` | Read out of a table cell on an enrichment-bound axis (the ABS-409 permission-matrix path). Structured, not keyword-matched. |
| 0.8 | `path_anchored` | A query term matches inside the fragment's `citation_path`. The corpus itself files this fragment under what was asked about. |
| 0.6 | `labelled_row` | A query term matches the fragment's `citation_label` — a labelled row or table entry bearing the term. |
| **0.4** | **`body_phrase`** | **The query appears verbatim in the body text. ← the gate** |
| 0.2 | `body_terms` | Only scattered query terms appear in the body. A brush, not a hit. |
| 0.0 | `no_match` | Nothing matched anywhere. |

Two ordering principles: **structural addressing (path, label) outranks textual
mention (body)**, and **within textual mention, a verbatim phrase outranks
scattered terms**.

Below `body_phrase` a field is not confidently extracted: its value is dropped
to `None` and **no citation is emitted for it**. That behaviour (AC-2.9) is
unchanged — the instinct was always right; only the number backing it was
meaningless.

## Why not a calibrated probability

The ticket offered two options: a probability calibrated against DM-09/DM-10
labels, or an explicit ordinal evidence class. We took the ordinal class.

A calibrated probability needs labelled outcomes to calibrate against. This
repo's retrieval labels (`evals/retrieval/queries.json`) are
`review_status: unreviewed`, agent-drafted, and — as their own README insists —
grade **ranking, never correctness**. Fitting a "probability the value is right"
to a hit-rate label set would produce a number that looks calibrated and is not.
That is the same failure mode as `score / 40.0`, one layer up: a plausible float
whose provenance nobody can state.

An ordinal class asserts only what we can actually observe — *how the corpus's
own structure connects this fragment to the query* — and it is auditable by
reading the fragment. When a reviewed label set exists, a probability can be
layered **beside** this field rather than smuggled into it; the classes are
exactly the strata you would calibrate within.

The floats are ordinal labels. `EvidenceClass` says so, the `ZoneProfile.confidence`
field description says so, and callers are told to compare rungs to each other
rather than read 0.8 as "80% likely correct".

## What was wrong before

```python
_ZONE_FIELD_FULL_SCORE = 40.0
_ZONE_FIELD_MIN_CONFIDENCE = 0.5

def _field_confidence(self, match):
    return min(1.0, match.score / self._ZONE_FIELD_FULL_SCORE)
```

`_score_fragment` awards a **fixed bonus per matching query token** (+12 in the
citation path, +8 in the label, +4 in the body). It is a sum over tokens, so it
grows with query length. Dividing that sum by a constant and comparing to 0.5
therefore made the verdict a function of **query word count**.

`get_zone_profile` builds its five internal queries by templating the zone code
into fixed phrases, so the token count varies by zone code and by section — and
so, in turn, did the gate. Reproduced on the Regional Centre fixture
(`tests/test_get_zone_profile.py::_seed_regional_centre`), which gives every
zone the *same* `Table 3 > <zone>` setback row:

| Zone | Query | Tokens | Old confidence | Old outcome |
|------|-------|-------:|---------------:|-------------|
| CEN-2 | `CEN-2 setback` | 4 (`cen-2`,`cen`,`2`,`setback`) | 1.000 | setbacks served |
| HR-2 | `HR-2 setback` | 4 | 1.000 | setbacks served |
| **COR** | `COR setback` | **2** (`cor`,`setback`) | **0.425** | **all three setbacks dropped** |

COR lost its front, side and rear setbacks — not because the bylaw is silent,
not because retrieval missed the row, but because "COR" has no hyphen to split
and so contributed one token instead of three. The same effect ran the other
way on the zone's `uses`, which scraped through at 0.525 on a longer query
template.

Under the evidence class all four zones classify as `path_anchored` (0.8) on
that row, and all four keep their setbacks. This is a **recall improvement**,
not a loosening: the fields that were being dropped were dropped for a reason
that had nothing to do with the evidence.

## Why this is verbosity-independent

`_classify_evidence` walks the ladder strongest-rung-first and returns the first
rung the (query, fragment) pair reaches. Every rung is an *existence* test —
"does any query term match here?" — never a count or a sum. Adding words to a
query cannot promote a fragment by weight of numbers.

`body_phrase` is the one rung sensitive to the query string as a whole, and only
in the safe direction: lengthening a query can turn a verbatim phrase into
scattered terms (a demotion to `body_terms`), never the reverse. The
pathological "more words ⇒ clears the gate" behaviour is unreachable.

Guarded by `tests/test_get_zone_profile.py` (the ABS-493 block) and
`web/e2e/functional/abs493-confidence-evidence-class.spec.ts`.

## Scope

- **Ranking is untouched.** `_score_fragment`, `_merge_channel_scores` and the
  spatial channel are unmodified, so `search_bylaw_evidence` returns the same
  ordering it did before. The retrieval eval (`evals/retrieval/BASELINE.json`,
  Recall@10 = 0.1029) measures that surface and is unaffected by this change —
  the evidence class is read *after* a match is selected and only decides
  whether the zone profile stands behind the value it extracted.
- **`RetrievalMatch.confidence` is a different field.** That is the ingest
  parser's per-fragment parse confidence and carries no relation to this ladder.
- **Submission-attribute confidence badges** in the web app
  (`web/app/(product)/submissions/…`) are also unrelated — a different pipeline,
  a different number.

## Related

- ABS-272 — `get_zone_profile`, where AC-2.9 (drop the value, drop the citation)
  was first specified.
- ABS-409 / ABS-484 — the permission-matrix path, now named `bound_table_cell`,
  and the prose fallback that takes the `min` of two rungs when a `uses` block
  mixes readings. Taking a `min` is well-defined precisely because the values
  are rungs on one ladder.
- ABS-478 — word-boundary token matching, which the classifier reuses via
  `_token_matches`; the mechanical noise it removed is why the classes describe
  evidence rather than tokenizer artifacts.
