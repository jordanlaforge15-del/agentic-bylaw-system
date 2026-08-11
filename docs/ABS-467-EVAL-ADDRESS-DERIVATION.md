# ABS-467 — eval addresses are derived from the zone, not asserted alongside it

## The defect

Every case in `evals/regional_centre_test_prompts.json` carries a `zone` and an
`address`. They were two independent inputs to
`scripts/generate_regional_centre_test_prompts.py`: the operator passed
`--zone CEN-1 --address "1505 Barrington Street"` and `claude -p` wrote a
conversation around both. The address was never derived from the zone and never
checked against it.

Resolving all 20 through the production `get_address_profile` path against the
HRM zoning dataset:

| Outcome | Count | Cases |
| -- | -- | -- |
| Claimed zone confirmed | 3 | TC-001, TC-008, TC-015 |
| Resolved to a **different** zone | 7 | TC-006 CEN-1→DH, TC-007 CEN-2→DH, TC-010 COR→DH, TC-011 INS→H, TC-014 CEN-2→DH-1, TC-018 COR→CDD-2, TC-020 HR-1→DH |
| Resolved to **no** zone at all | 10 | TC-002, 003, 004, 005, 009, 012, 013, 016, 017, 019 |

This is not cosmetic. A case claiming CEN-1 whose address is in DH grades the
advisor's *correct* DH answer against CEN-1 expectations: it marks right
answers wrong, and a real regression can hide behind the resulting "known
failure".

## The fix: invert the generation order

`scripts/zone_address_picker.py` derives an address from a zone in three steps,
ordered by how much each costs to check:

1. **Ask the zoning data for an unambiguous parcel.** The parcel must lie
   wholly inside a polygon of the target zone (`ST_Contains`) and touch no
   polygon of any other zone. The dataset carries several by-law areas whose
   polygons overlap — a point inside a Regional Centre CEN-2 polygon can also
   sit under a legacy Downtown Halifax DH-1 polygon, which is exactly how
   TC-014 came to resolve as DH-1 — and `_resolve_zone_at_point` returns the
   first match. A parcel with one and only one zone over it cannot be decided
   by that ordering.
2. **Reverse-geocode the parcel's interior point.** This is the step that makes
   the address *real* rather than plausible.
3. **Round-trip it through production.** The composed address goes through
   `RetrievalService.get_address_profile` — the same call the advisor makes —
   and is kept only if it comes back with the zone we asked for. Everything
   else is discarded, including addresses Google forward-geocodes to another
   city (the production geocoder queries civic-number + street with only a
   country filter, so that is a live risk).

ROOFTOP matches are searched for first. All 20 cases now resolve at ROOFTOP,
and each records the resolution it was verified against:

```json
"address": "1222 Robie Street, Halifax, NS",
"address_resolution": {
  "resolved_zone": "HR-1",
  "resolution_quality": "rooftop",
  "location_type": "ROOFTOP",
  "location_confidence": 0.95,
  "location_resolver": "google_maps",
  "parcel_pid": "00055764"
}
```

`resolution_quality` is ABS-466's vocabulary. Recording it is what stops the
eval silently depending on an *estimated* point: an interpolated address sits
where the geocoder guessed the civic number falls along the street, and near a
boundary that lands on the neighbouring parcel.

## Two cases needed more than a new address

**TC-017 (was ER-1 → now ER-2).** The by-law defines ER-1 (Part I s.30), but the
zoning schedule maps **no ER-1 polygon anywhere** — 0 of 11,069 features carry
`ZONE=ER-1`. No address could ever confirm that case's zone. ER-2 carries the
same use permissions the non-conforming-duplex premise depends on (single-unit
dwelling P, secondary suite P, multi-unit N), so the question is unchanged; it
also reconciles the expected keywords, which already carried ER-2's 40% lot
coverage rather than ER-1's 35%.

**TC-012 (RPK).** The case asserted that the North Park Street side of Halifax
Common is RPK. It is not — the Common resolves to PCF, INS and H depending on
where you stand, and the Regional Centre's entire RPK extent is Georges Island.
The premise now names a parcel that actually carries the zone.

TC-001 keeps its deliberate ER-1 misstatement (ABS-463): only the address and
the note's evidence moved. The premise is sharper now — the zone the user names
does not exist on the ground anywhere in the Regional Centre.

## Zone coverage: why 11, not 25

The Regional Centre schedule maps 25 zone codes. The eval exercises 11 of them,
which is **58.5% of the mapped land area** (2,679 ha total):

| zone | polygons | hectares | share | in eval |
| -- | --: | --: | --: | -- |
| ER-3 | 719 | 760.6 | 28.4% | yes |
| PCF | 180 | 331.8 | 12.4% | |
| HR-1 | 190 | 212.1 | 7.9% | yes |
| HRI | 13 | 197.7 | 7.4% | |
| CDD-2 | 24 | 157.7 | 5.9% | |
| ER-2 | 237 | 118.5 | 4.4% | yes |
| RPK | 4 | 108.9 | 4.1% | yes |
| COR | 196 | 102.5 | 3.8% | yes |
| DND | 11 | 101.3 | 3.8% | |
| LI | 15 | 94.9 | 3.5% | |
| CEN-2 | 48 | 73.4 | 2.7% | yes |
| UC-1 | 18 | 63.9 | 2.4% | |
| HR-2 | 23 | 62.7 | 2.3% | yes |
| DH | 58 | 52.3 | 2.0% | yes |
| CDD-1 | 2 | 43.1 | 1.6% | |
| CLI | 18 | 42.6 | 1.6% | |
| INS | 64 | 36.9 | 1.4% | yes |
| H | 7 | 33.3 | 1.2% | |
| DD | 31 | 31.4 | 1.2% | yes |
| WA | 17 | 28.0 | 1.0% | |
| CH-2 | 3 | 9.1 | 0.3% | |
| CEN-1 | 21 | 6.9 | 0.3% | yes |
| UC-2 | 2 | 4.1 | 0.2% | |
| HCD-SV | 6 | 4.0 | 0.1% | |
| CH-1 | 3 | 1.7 | 0.1% | |

The 11 are the zones that carry the by-law's substantive dimensional and use
standards for the development the advisor is actually asked about — residential
(ER-2/3, HR-1/2), the centres and downtowns (CEN-1/2, COR, DD, DH), plus INS
and RPK as deliberate edge cases. Each has its own Part V chapter of setback,
height, lot-coverage and parking provisions, which is what an eval case
exercises.

The 14 uncovered zones fall into three groups, none of which is a coverage gap
of the same kind:

* **Site-specific or agreement-governed** — CDD-1, CDD-2, DND, HCD-SV. Their
  standards come from a development agreement or a heritage district plan
  rather than from a zone chapter the advisor can look up, so a case would
  test the wrong thing.
* **Industrial and special-purpose** — HRI, LI, CLI, WA, H, UC-1, UC-2, PCF.
  Real zones with real chapters, and the honest next increment of coverage.
  PCF (12.4% of the area) and HRI (7.4%) are the two worth adding first.
* **Marginal** — CH-1, CH-2 at 0.1% and 0.3% of the area, three polygons each.

Widening coverage is **not** blocked by tooling any more:
`--zone` now offers all 25 mapped codes and derives a verified address for each
(the smoke test for this work generated a PCF case at 5805 Africville Road).
What each new case still needs is authored conversation turns and a validated
`expected_bylaw_references` set — ABS-463's index is per-reference, so new
references have to be resolved against the corpus and the snapshot rebuilt.
That is the actual cost of going from 11 to 25, and it is a separate piece of
work rather than something this issue silently half-does.

## Running it

```bash
# Check: does every case's address still resolve to its zone?
python scripts/verify_eval_address_zones.py --check

# Repair: re-derive an address for every case that does not
python scripts/verify_eval_address_zones.py --repair --upgrade-interpolated

# One zone, ad hoc
python scripts/zone_address_picker.py --zone CEN-1 --json
```

Both need `DATABASE_URL` pointing at a database with the HRM zoning and parcel
datasets, and `GOOGLE_MAPS_API_KEY` for the reverse geocode. `--repair` rewrites
`address` and `address_resolution` only; it reports which turns still name the
old street so a human re-checks that question, zone and expected keywords agree.

## The guards

* `tests/test_eval_address_zones.py` — resolves every address through
  `get_address_profile` and fails on any disagreement. Skips cleanly when
  Postgres is unreachable or the zoning dataset is absent; its offline tier
  (the file's internal agreement) runs everywhere.
* `web/e2e/functional/abs467-eval-address-zone.spec.ts` — the offline tier for
  Playwright. The ~180k-parcel HRM ingest is not present in CI or an e2e
  worktree database, so a spec cannot resolve addresses, but it can catch a
  hand-edited case.
* `scripts/generate_regional_centre_test_prompts.py` no longer takes
  `--address`, and rejects a spec file that carries one.
