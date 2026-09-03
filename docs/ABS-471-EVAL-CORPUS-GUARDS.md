# ABS-471 — what the eval corpus is guarded against, and what it is not

Sibling to [ABS-463-BYLAW-REFERENCE-VERIFICATION.md](ABS-463-BYLAW-REFERENCE-VERIFICATION.md),
which records how `expected_bylaw_references` was *verified*. This one records
what is now **automatically re-verified**, and — just as important — what still
is not.

## Why these exist

An audit found defects in 17 of the 20 cases in
`evals/regional_centre_test_prompts.json`: 7 wrong zones, 5 addresses that do
not exist, one zone with no polygons in the dataset at all, and a dozen
keyword/reference errors. ABS-470 corrected the data. ABS-471 built the guards,
because correcting a file once is not a fix — zoning data is re-ingested,
geocoders re-geocode, boundaries get redrawn, and the file rots again on the
next refresh unless something re-derives the claim.

**None of the 17 was caught, and none of them could have been.**
`scripts/build_bylaw_reference_index.py --check` validates that each
`expected_bylaw_references` string resolves to a real fragment. That is a real
guard and it works — but it answers only "does this citation exist?". It never
answered:

* is this section correct **for the zone this case declares**?
* does this **address** exist, and does it sit in the zone the case claims?
* is anything in `expected_answer_keywords` true?

`zone`, `address` and `expected_answer_keywords` had **no validation of any
kind**, and every one of the 17 defects lived in exactly those fields.

## The guards

### G1 — spatial zone assertion

For each case: the civic number is checked against the municipality's own data,
the address is resolved to a point, the resolution's precision is required to
clear a floor, and the point is intersected against the zoning boundaries — and
must come back with the zone the case declares.

| Step | Source of truth | Catches |
| -- | -- | -- |
| Civic number exists | `halifax_street_centerlines` per-segment `FROM_LEFT`/`TO_LEFT`/`FROM_RIGHT`/`TO_RIGHT` via `layer2.retrieval.civic_address.verify_civic_address` (ABS-469) | the 5 fabricated addresses — **with no network access at all** |
| Geocode is not an estimate | `ResolvedLocation.confidence` / Google `location_type` (ABS-466) | TC-002/003/004's old 0.60 resolutions; TC-005's point 32 km outside HRM |
| Point is inside a zoning polygon | `ST_Intersects` against `halifax_zoning_boundaries`, filtered to `bylaw_area_id = 23` | TC-009/013/016, which geocoded 5-12 m off into the road right-of-way and matched nothing; TC-017's unmapped ER-1 |
| Polygon's zone == case's zone | the same query's `zone_code` | all 7 wrong zones |

Failure is failure, not a warning, and every line names the case, the field and
expected-vs-actual. A refused civic number quotes the ranges that *do* exist
("no published address or street-segment range covers 100 Robie Street; ranges
that do exist: 820-2180, 2300-3898") — "this address does not exist" on its own
does not tell the next person whether the street was renamed, the number was a
typo, or the case belongs somewhere else.

Two deliberate non-failures:

* **`unverifiable` civic numbers pass.** A number that falls *between* two
  published ranges is usually a range the centreline layer has not caught up
  with (Nora Bernard Street after the rename from Cornwallis); a number past
  both ends is one nothing on the street has ever carried. Only the latter is
  `not_found`. Two committed cases — TC-004 and TC-006 — rest on in-gap numbers
  that are confirmed by every other check. A false "this address does not
  exist" is worse than the hedge it replaces.
* **The confidence floor is 0.85 (interpolated), not 0.95 (rooftop).**
  Rooftop-versus-interpolated is a different question and already has an owner:
  `tests/test_eval_address_zones.py` allows a below-rooftop point only where the
  case's notes declare it. Setting the bar at rooftop here would give one corpus
  two contradictory rules.

### G2 — keyword validation

Every `Section N` / `Table N` / `Schedule N` token in `expected_answer_keywords`
— the field the grader actually scores — must

1. resolve against the corpus (sections and tables; see the schedule caveat
   below), and
2. fall in the by-law chapter that governs the case's `zone`.

### G3 — the same chapter rule over `expected_bylaw_references`

So a case whose `zone` changes cannot keep references from the old zone's
chapter — the exact failure ABS-470 cleaned up by hand. Enforced inside
`build_bylaw_reference_index.py --check`, which now fails on a reference that
resolves perfectly but governs another zone.

The regressions G2/G3 are pinned against, all real, all from the audit:

| Defect | Verdict |
| -- | -- |
| `Section 196` / `Section 200` on INS, DD, COR and CDD-2 cases | Part V Chapter 7 governs HR-2 and HR-1 |
| `Section 111` on a DH case | Part V Chapter 2 governs DD |
| `Table 1A` on an RPK case | Table 1A covers DD…HR-1; RPK is in Table 1C |
| `Section 344` on an ER-3 backyard-suite case | Part VI Chapter 2 governs HCD-SV (Schmidtville) |

## The chapter map is derived, not typed

`evals/regional_centre_zone_chapter_map.json` holds the boundaries G2 and G3
apply. It is **generated from the corpus**, because a hand-typed range silently
stops describing the document it claims to:

* every chapter heading names itself and the zones it governs — *"Part V,
  Chapter 7: Built Form and Siting Requirements within the HR-2 and HR-1
  Zones"*, *"Part VI, Chapter 2: … for the Schmidtville Heritage Conservation
  District (SHCD) / HCD-SV Zone"*;
* the sections between one chapter heading and the next are that chapter's;
* the permitted-use tables name their own zones in their captions — *"Table 1B:
  Permitted uses by zone (ER-3, ER-2, ER-1, CH-2, and CH-1)"*.

A heading that names no zone code (Part V Chapter 1 "General Built Form", Part V
Chapter 19 "Accessory Structures", Part XIII "Motor Vehicle Parking") governs
every zone and constrains nothing — over-constraining would fail correct cases
and train the next person to delete the guard.

Two ingest artefacts the derivation has to survive, both real:

* Sections 111-128 — the back half of the **DD** chapter — are mis-parented onto
  `Schedule 17 > 111`. Position in the document decides the chapter, not
  `citation_path`; a path-prefix filter would cut Chapter 2 off at 110 and let
  every DD citation from 111 up look unconstrained.
* Parts I-V arrive as `PART` fragments and Parts VI+ as `HEADING` fragments.
  Both are read. Deriving only Part V would miss Part VI Chapter 2 — and with it
  the `Section 344` regression above.

## Artefacts

| File | Role |
| -- | -- |
| `evals/regional_centre_zone_chapter_map.json` | derived snapshot: chapter → sections → zones, and each permitted-use table's zones |
| `scripts/build_zone_chapter_map.py` | regenerates it; `--check` re-derives and fails on drift |
| `scripts/eval_zone_chapters.py` | the rule itself — pure, database-free, holds no data |
| `scripts/verify_eval_corpus_integrity.py` | operator CLI: all three guards, one command |
| `tests/test_eval_address_spatial.py` | G1 under `make test` |
| `tests/test_eval_keyword_chapters.py` | G2 + G3 under `make test` |
| `tests/test_zone_chapter_map.py` | the map keeps describing the corpus |
| `web/e2e/functional/abs471-eval-corpus-guards.spec.ts` | file-level Playwright coverage |

Every guard follows the offline/live split ABS-464 established: the rules that
need no database run everywhere, and the ones that need the ~4,300-fragment,
~180k-parcel Halifax ingest **skip cleanly** where it is absent (CI, every e2e
worktree) and are a hard gate where it is present. The Playwright spec
re-implements the rule against the same committed JSON rather than shelling out
to pytest — keep it that way; an earlier revision in ABS-463 invoked `pytest`
via `spawnSync`, blocked a Playwright worker for 13-50s and starved the WebKit
projects into timing out six unrelated tests.

## Running them

```bash
# everything, against a box with the Halifax ingest
.venv/bin/python scripts/verify_eval_corpus_integrity.py

# just the rules that need no database
.venv/bin/python scripts/verify_eval_corpus_integrity.py --offline

# after any re-ingest of the Regional Centre by-law
.venv/bin/python scripts/build_zone_chapter_map.py --check    # detect drift
.venv/bin/python scripts/build_zone_chapter_map.py            # accept it
.venv/bin/python scripts/build_bylaw_reference_index.py --check
```

You do not have to remember to: `make test` runs all of it on any box that has
the ingest.

---

## Closed since: G4 — the address is registered on its parcel (ABS-474)

The guards above answer "does this address resolve to the zone the case
claims?" and "was the point a rooftop match?". Five cases passed both while
naming a property that does not exist — `"251 Stairs Street"` on a parcel HRM
registers as 249/251/257 Windmill Road, `"1462 Birchdale Avenue"` on one it
registers as 1462 Thornvale Avenue.

The addresses were composed by reverse-geocoding a parcel's interior point,
which returns the *nearest* street address to a point rather than the address
assigned to that parcel. **No zone check can catch this**, because a string
composed from a parcel geocodes back onto it: the zone confirms and the
confidence reads ROOFTOP. Only the municipality's civic-address register can.

`address_resolution.registered_civics` snapshots that register's answer for the
case's parcel, written by `scripts/verify_eval_address_zones.py
--backfill-civics`, and G4 asserts the case's `address` is one of them —
offline, because the register is not ingested (ABS-475). Guarded by
`tests/test_eval_address_spatial.py` and
`web/e2e/functional/abs474-eval-address-registration.spec.ts`.

Two of the five (TC-011, TC-016) name addresses that exist *somewhere* in the
municipality but not on the parcel the case resolved to, so the recorded zone
belonged to a different property. Asking the register by `PID` rather than by
street is what separates "this address exists" from "this address is here".

Also closed by ABS-474: `verify_civic_address` now filters street segments by
community. Dartmouth's Stairs Street (1-30) and Halifax's (5600-6099) were
merged into one 1-6099 extent, so `251` read as an in-gap number and came back
`unverifiable` instead of `not_found`.

## What is NOT guarded

### 1. Numeric keywords — the open gap

`expected_answer_keywords` carries bare numbers (`6.0 m`, `2.5 m`, `80%`,
`50%`). **Nothing checks them.** Tying a bare number to the clause it came from
is a materially harder problem than placing a citation in a chapter: the number
has to be located in the right provision's text, and the provision has to be the
one the case's question actually reaches, which depends on branch conditions
(what the lot abuts, which precinct it is in) that no static rule evaluates.

This is not hypothetical. Before ABS-470, **three cases asserted lot-coverage
percentages against sections that read "No maximum required lot coverage
applies"** — TC-007 and TC-009 expected `"80%"` and TC-010 expected `"70%"`,
against CEN-2, DD and COR (Sections 168, 121, 187). Every one of those cases
would pass all three guards in this document today. Worth a follow-up ticket;
deliberately not attempted here.

### 2. Cases whose keywords cite nothing

Nine of the twenty keep `expected_answer_keywords` purely descriptive ("rear
setback", "3.0 m", "permitted", "multi-unit dwelling"), so G2 has nothing to
place. It reaches eleven cases. `tests/test_eval_keyword_chapters.py` pins that
floor so a rewrite cannot quietly reduce G2 to a no-op, but the other nine are
covered only by G1 and G3.

### 3. Schedules the corpus does not carry as fragments

The ingest carries six schedules as fragments of their own (7, 15, 17, 22, 50,
51). Schedules 18, 20, 26 and 28 exist only as clauses of Section 29's list, so
they are not citeable under the reference grammar. `Schedule 18` appears in two
cases' keywords and cannot be resolved; it is listed explicitly in
`SCHEDULES_NOT_INGESTED` rather than exempting the whole class, and a test fails
if it ever *does* resolve, so the excuse cannot outlive the problem.

### 4. Semantic correctness

"Is this the provision that actually *governs* the question this case asks?"
remains a human judgement — the chapter rule proves a citation is not from the
*wrong zone*, not that it is the *right section*. That evidence lives in
[ABS-463-BYLAW-REFERENCE-VERIFICATION.md](ABS-463-BYLAW-REFERENCE-VERIFICATION.md)
and in each case's `notes`.

### 5. The zone-appropriateness constants duplicated in the ABS-463 spec

`web/e2e/functional/abs463-bylaw-reference-validation.spec.ts` carries its own
hand-written `PART_V_CHAPTERS` and `USE_TABLE_ZONES`, written before the derived
map existed. They are a strict subset of it and agree with it today. The derived
map supersedes them; fold that spec onto `regional_centre_zone_chapter_map.json`
the next time it is touched.
