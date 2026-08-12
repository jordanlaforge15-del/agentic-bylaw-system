# Data gap: 20 of HRM's 22 by-law areas have no ingested by-law

**Raised by:** ABS-472 (zoning layer is HRM-wide but linked wholesale to the
Regional Centre LUB).
**Status:** the mis-attribution is fixed in code; the *coverage* is an
ingestion decision, recorded here.

## What ABS-472 changed, and what it deliberately did not

`halifax_zoning_boundaries` is municipality-wide — 11,069 features spanning 22
by-law areas — and was linked wholesale to one document, the Regional Centre
LUB. Every feature was therefore served with a Regional Centre citation,
including ground that by-law does not govern.

The code fix resolves the governing document **per feature** from `BYLAW_ID`
and, where that by-law is not in the corpus, refuses instead of citing:
`AddressProfile.governing_bylaw_status = "not_held"`, no zone citation, and a
caveat the answer path turns into an explicit disclosure. What it does **not**
do is make the missing by-laws answerable. That needs the documents.

## Current coverage

Measured 2026-08-12 from the issue's own audit of the live layer:

| By-law area | Features | Governing by-law ingested? |
| -- | --: | -- |
| `BYLAW_ID 23` — Regional Centre LUB | 1,910 | yes (document 4) |
| `BYLAW_ID 9` — Halifax Mainland LUB | 1,209 | yes (document 5) |
| everything else (20 areas) | **7,950** | **no** |

Run `.venv/bin/python scripts/corpus_coherence_audit.py` against a corpus to
get the current numbers; the `governing_bylaw_coverage` section lists each
unheld by-law with its feature count, largest first. The same section is on
`GET /v1/monitoring/corpus-coherence` (informational — it never turns that
endpoint red, because a municipality publishes far more by-law areas than any
corpus ingests).

## Should we ingest the Downtown Halifax LUB?

**Recommendation: yes, and it should be the next by-law ingested — but it is
its own ticket, not part of ABS-472.**

The case for it:

* **The ground is small, central and expensive.** The residual DHLUB parcels
  in the zoning layer are 28 features: 23 × DH-1 (12.24 ha) + 5 × ICO
  (3.74 ha). Sixteen hectares of downtown Halifax is exactly the land that
  generates paid questions.
* **It is live law, not stale data.** Confirmed with HRM: DHLUB is
  *partially*, not fully, repealed — *"a portion of the lands will continue to
  be governed by the Downtown Halifax Secondary Municipal Planning Strategy
  and Land Use By-law until these lands are fully incorporated in the Regional
  Centre Plan."* RCLUB s.198(1)(e) carries a live operative cross-reference to
  those lands. Purging the features would be wrong; refusing them forever
  would be a permanent hole in the middle of the highest-value market.
* **We now know exactly when we are hitting it.** Before ABS-472 a DHLUB
  parcel was indistinguishable from a Regional Centre one. It is now a typed
  state, so the demand for the ingest is measurable rather than inferred.

The case against doing it *now*: the other 19 areas are a much larger surface
(7,922 features) and a spot check on a Bedford address returned
`unresolvable`, so the geocoder may not reach most of that ground today. That
makes the rest **structural** exposure — real, but not yet demonstrated.
Ingesting by-law areas in demand order, starting with DHLUB, is the sensible
sequence; ingesting all 20 up front is not.

## Operational follow-up after deploying ABS-472

1. **Refresh the ingested layer's declaration.** Retrieval reads
   `links_to.governing_bylaw_from` from the dataset's persisted
   `metadata_json`, written once at ingest. An already-ingested layer keeps
   the old declaration until refreshed:

   ```sh
   DATABASE_URL=... .venv/bin/python scripts/backfill_zoning_bylaw_names.py
   ```

   Idempotent; it refreshes `links_to` and backfills any feature still missing
   `bylaw_area_name` / `bylaw_area_code`. A full re-ingest of the layer does
   the same thing.

2. **Confirm Halifax Mainland reads as covered.** The governing by-law is
   matched to a document by normalized name (case/hyphen-insensitive, with a
   prefix allowance for title qualifiers like "(Consolidated to 2024)"). If
   document 5's title diverges from `"Halifax Mainland Land Use By-law"` by
   more than that, its 1,209 features will report `not_held` — a *conservative*
   failure (refuse rather than mis-cite), but a false one. The coverage audit
   above names it immediately if so; the fix is to align the document title,
   not to loosen the matching.
