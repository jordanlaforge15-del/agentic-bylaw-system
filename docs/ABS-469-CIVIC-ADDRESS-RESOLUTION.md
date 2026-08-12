# ABS-469 — Resolving civic addresses authoritatively

ABS-466 made a weak address resolution *visible*: `get_address_profile` now
reports whether the point was a rooftop match or an estimate, and the answer
hedges accordingly. It did not make resolution *correct*. `RANGE_INTERPOLATED`
means Google never found the civic number and estimated a position from the
surrounding numbering; near a zone boundary that estimate lands on the
neighbouring parcel, and every setback derived from it is somebody else's.

This is the correctness half. Everything below was measured against the live
dev corpus (`halifax_zoning_boundaries`, `halifax_property_parcels`,
`halifax_street_centerlines`) and HRM's published ArcGIS services on
2026-08-11.

## Tier 1 — check the civic number against HRM's own street data

`external_dataset_feature` dataset 10 (`halifax_street_centerlines`, 18,468
segments) carries `FROM_LEFT` / `TO_LEFT` / `FROM_RIGHT` / `TO_RIGHT` and
`STR_NAME` / `STR_TYPE` per segment. A civic number no segment's range covers
is an address that almost certainly does not exist.

Implemented in `src/layer2/retrieval/civic_address.py` and consulted by
`RetrievalService.get_address_profile` **before** the address is geocoded — a
fabricated address never reaches the geocoder, so there is no interpolated
point for anything downstream to use.

### Three rules that make it safe

**Per segment, never aggregated.** Robie Street's segments span 0–3899 in
aggregate, which covers every number anyone could type.

**Street type matters.** Jubilee Road, Jubilee Court and Jubilee Lane share
`STR_NAME = JUBILEE`. Ignoring the type confirms "89 Jubilee Road" out of
Jubilee Court's 2–98 range. The filter is applied only when at least one
segment of that type exists — HRM writes `CRT` for Court and `LANE` for Lane,
so an exact abbreviation match cannot be required.

**An unknown street is `unverifiable`, never `not_found`.** Renamed streets,
typos and out-of-municipality addresses land there.

### What the ranges actually get right — measured

4,000 real HRM civic-address points inside the Regional Centre bounding box,
checked against the ingested centerline ranges:

| Rule | False refusals of real addresses |
| -- | -- |
| Any uncovered civic number is `not_found` | 15 / 4,000 (0.38%) |
| Only numbers outside the street's whole addressed extent | **6 / 4,000 (0.15%)** |

The difference is the shape of the failure. A number sitting *between* two
published ranges is usually a range that has not caught up with the street —
Nora Bernard Street publishes 5401–5439 and 5551–5589, and the 5440–5549
stretch in between is real and inhabited (the street was renamed from
Cornwallis). A number past *both ends* of everything the street publishes is a
different population: nothing on that street has ever carried it.

So the shipped rule refuses only the second class, and an in-gap number comes
back `unverifiable` with the reason recorded — the ABS-466 hedging still
applies to it, which is the right treatment for "we cannot tell".

The remaining 6 false refusals are all one street: Chadwick Street's segments
stop at 81 while its real addresses run to 355.

Against the issue's own table, with the shipped rule:

| Address | Before (ABS-466) | After |
| -- | -- | -- |
| 100 Robie Street | resolved, no zone (point in the road) | **does not exist** — valid: 820–2180, 2300–3898 |
| 567 Windsor Street | resolved, no zone | **does not exist** — valid: 2001–3799 |
| 2563 Maitland Street | resolved, no zone | **does not exist** — valid: 2081–2385 |
| 200 Bayers Road | resolved, no zone | **does not exist** — valid: 6260–7150 |
| 89 Jubilee Road | resolved, no zone | **does not exist** — valid: 6001–6769 |
| 1234 Oxford Street | HR-1 | HR-1, confirmed |
| 2500 Robie Street | COR | COR, confirmed (24.6 m from ER-2) |
| 6615 Jubilee Road | ER-3 | ER-3, confirmed |
| 6960 Mumford Road | CDD-2 | CDD-2, confirmed |

Reproduce with `scripts/measure_address_resolution.py --address "…"`.

## Tier 2 — local interpolation: not done, deliberately

The issue calls it "marginal on its own; worth doing only if it falls out of
Tier 1". It does not, and Tier 3 (below) is reachable, which makes a locally
interpolated point strictly worse than the alternative — same accuracy class
as Google's, but ours to maintain.

What *did* fall out of Tier 1 is the auditable half: a confirmed address
records which segment, which range and which side of the street covered the
number (`CivicAddressVerdict.matched_segment` / `matched_range` /
`matched_side`). That is what an opaque geocoder coordinate cannot give, and
it costs nothing.

## Tier 3 — authoritative civic address points: **reachable**

**HRM publishes one, on the service the repo already pulls from.**

```
https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/CivicAddresses/FeatureServer/0
```

* 158,523 point features, `esriGeometryPoint`, `Query,Extract` enabled,
  `maxRecordCount` 2000 (the same pagination the centerline ingest already
  handles).
* Service description: *"Geographic points representing civic addresses that
  include civic number, street name, community, PID etc. … the source of civic
  address information for internal and external service providers such as
  NSPI, E-911, Property Online and NSCAF."*
* Fields include `CIV_NUM`, `STR_NAME`, `STR_TYPE`, `PID`, `UNIT_NUM`,
  `CIV_POSTAL`, `GSA_NAME`, `CIV_ID`.

Spot-checked against the addresses this issue turns on:

| Query | Result |
| -- | -- |
| `CIV_NUM=1222 AND STR_NAME='ROBIE'` | 1 point, PID `00055764` |
| `CIV_NUM=100 AND STR_NAME='ROBIE'` | 0 |
| `CIV_NUM=89 AND STR_NAME='JUBILEE'` | 0 |
| `CIV_NUM=2147 AND STR_NAME='MAYNARD'` | 0 |

That last row is the one worth noting: 2147 Maynard Street *is* covered by a
centerline range (2081–2199) and Tier 1 therefore confirms it, but the address
register says it does not exist. Tier 1 is an inference; only the register is
a fact. The verifier already prefers a `role: civic_address` dataset over the
centerline ranges when one is in scope, and
`src/layer1/datasets/halifax_civic_addresses.yaml` is the config to ingest it.

**Two things must be fixed before that ingest is switched on**, both in
`src/layer2/retrieval/geocode.py`, and neither is in this issue's scope:

1. `_find_by_civic_address` / `_find_by_parcel_id` load **every** feature of
   every `civic_address` dataset into Python and compare in a loop. At 158,523
   points that is unusable on the per-question path; they need the same
   SQL-side street filter `civic_address._features_on_street` uses.
2. Both compare `normalize_street(canonical["street_name"])` against the
   user's street *including* its suffix ("robie st"). HRM splits the name and
   the type across `STR_NAME` and `STR_TYPE`, so either the canonical mapping
   has to concatenate them or the resolver has to compare the split form (the
   verifier in this issue already does the latter).

Ingesting the layer also changes resolution globally — the in-database
resolver runs ahead of Google, so civic addresses would stop being geocoded
externally at all. That is the outcome the issue wants ("address resolution
stops being interpolation entirely"), but it is a corpus-wide behaviour change
that deserves its own issue and its own before/after, not a side effect of
this one.

**The parcel → AAN → Nova Scotia assessment route was not pursued.** Dataset 9
(`halifax_property_parcels`) does carry `AAN`, so the join exists in
principle, but it would resolve an address only via a second external system,
with a slower and less reliable path, to reach data the ArcGIS layer above
already publishes directly with the PID attached. If the ArcGIS layer is ever
withdrawn, this is where to look next.

## Tier 4 — zone-boundary proximity and split lots

Orthogonal to everything above: an exact rooftop point is still unsafe when
the parcel abuts or straddles a zone line, which is precisely the mechanism
that produces a confidently wrong setback.

`get_address_profile` now reports:

* `zone_boundary_distance_m` + `nearest_other_zone` — metres from the resolved
  point to the nearest polygon carrying a *different* zone code, when within
  25 m.
* `parcel_zones` — every zone the containing parcel intersects, when it is
  split across more than one.

**Why 25 m.** Over the 45 addresses the dev corpus has resolved into a zone,
the distance from the geocoded point to the nearest different-zone polygon
runs 7.6 m to 188 m. 25 m is roughly an arterial right-of-way plus a lot's
frontage: below it the boundary is on this lot, its neighbour, or directly
across the street. Twelve of the 45 (27%) fall inside it, so the flag stays
worth reading. 6321 Quinpool Road is the canonical case — a ROOFTOP match,
squarely inside CEN-2, 7.6 m from CEN-1.

**Why split lots need a sliver guard.** Zone polygons share their edges, so
almost every parcel picks up a sliver of its neighbour's zone from coordinate
precision alone. Measured on the HRM fabric those slivers run 0.2–5 m²; a real
split gives each zone tens of square metres *and* a real share of the lot.
Requiring both ≥10 m² and ≥5% of the parcel keeps 2563 Maitland's genuine
PCF/HR-1 split (107 m² and 66 m² of a ~180 m² lot) and drops 2500 Robie's
0.6 m² of ER-2 against 705 m² of COR.

## The open sub-question: why `89 Jubilee Road` returns no zone

The issue offered two hypotheses — outside the Regional Centre plan area, or a
gap in `halifax_zoning_boundaries`. **Measured, it is neither.**

Every address that resolved to a point but no zone lands 6.8–11.9 m from the
nearest zoning polygon:

| Address | Google | Distance to nearest zoning polygon |
| -- | -- | -- |
| 89 Jubilee Road | 0.60 (GEOMETRIC_CENTER) | 8.6 m (ER-3) |
| 2563 Maitland Street | 0.85 (RANGE_INTERPOLATED) | 6.8 m (HR-1) |
| 567 Windsor Street | 0.60 | 7.4 m (COR) |
| 5455 Spring Garden Road | 0.85 | 11.9 m (DH) |

Zoning polygons follow the parcel fabric and stop at the right-of-way edge.
An interpolated or block-centroid point sits on the *street centreline*, which
is inside the right-of-way and therefore inside no zone polygon. The plan area
covers these locations perfectly well; the point was simply never on a parcel.

So `outside_mapped_area` was reporting something true but useless — and for
these addresses the real answer is one level up: `89 Jubilee Road` does not
exist (HRM's register has no such point; Jubilee Road runs 6000–6770). The
first hypothesis' useful outcome — "this by-law does not govern that
property" — remains unimplemented because no address in the corpus has yet
produced it: every zoneless case measured here was a road-right-of-way point,
not an out-of-plan-area one.

`2147 Maynard Street` is the residual case: it *is* covered by a centerline
range, so Tier 1 confirms it, and it still resolves to no zone for the same
right-of-way reason. Only Tier 3 settles it (the register has no such
address).

## Before / after against ABS-468's golden subset

`scripts/measure_address_resolution.py` runs both paths — the pre-ABS-469
builder and the shipped one — over every address the eval is anchored on, and
labels each row `golden` or `generated` from `evals/golden/golden_cases.json`.

Against the dev corpus:

```
addresses measured        : 20
civic number not found    : 0
zone answer changed       : 0
within 25 m of a zone line: 8
parcel split across zones : 0
```

* **No golden case's grounding changed.** All six (TC-001, TC-002, TC-008,
  TC-009, TC-012, TC-014) resolve to the same zone as before, all `confirmed`.
* **No generated case's zone changed either**, so the eval's expectations are
  untouched.
* Eight of the twenty now carry a zone-boundary distance they did not have
  before — new information, no changed answers.

**Answer quality on the golden subset cannot be graded yet, and that is by
design.** Every entry in `evals/golden/golden_cases.json` is `unattested`;
`scripts/verify_golden_cases.py` grades them `UNATTESTED`, which is not a pass
and holds the deploy gate closed (see
[ABS-468](ABS-468-EVAL-GROUND-TRUTH.md)). Until a qualified human fills in the
attestations there is no ground truth to measure an answer against, and
running a model over the cases would produce exactly the model-graded evidence
ABS-468 exists to separate out. What this issue can honestly claim is the
grounding measurement above: the inputs to those answers are unchanged for
every golden case, and five fabricated addresses that previously produced a
confident-looking profile now produce a correction.

## ABS-467's zone-vs-address guard, re-run afterwards

```
$ python scripts/verify_eval_address_zones.py --check
20 PASS, 0 FAIL
```

Green. Worth recording *why* it is green: an earlier, stricter version of the
Tier 1 rule refused TC-004 (`251 Stairs Street`) and TC-006 (`5531 Nora
Bernard Street`). HRM's register confirms 251 Stairs Street does not exist —
Google resolves it ROOFTOP at 0.95 anyway — while 5531 Nora Bernard Street is
real and was a false refusal. The shipped in-gap rule spares both. Ingesting
the Tier 3 register would refuse TC-004 correctly and keep TC-006; that is a
reason to do it, and a thing to expect when it lands.
