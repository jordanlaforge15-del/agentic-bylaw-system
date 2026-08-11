# ABS-463 — how every `expected_bylaw_references` entry was verified

`evals/regional_centre_test_prompts.json` now carries a validated reference set
for all 20 cases. This document is the research record: it says *how* each
reference was checked, so the next person can re-check it without redoing the
work.

Everything below was derived from the Halifax **Regional Centre Land Use
By-law** ingest, `document_id = 4`, 4,341 fragments, in the dev database
(`postgresql+psycopg://layer1:layer1@localhost:5432/layer1`).

## The three artefacts

| File | Role |
| -- | -- |
| `evals/regional_centre_test_prompts.json` | the eval data (references live in `expected_bylaw_references`) |
| `evals/regional_centre_bylaw_reference_index.json` | machine-readable proof: for each of the 101 distinct references, the SQL that resolved it, the matched `fragment_id`, its `citation_path`, page, and a text excerpt |
| `scripts/build_bylaw_reference_index.py` | regenerates the index from the live corpus; `--check` re-verifies without writing |

Guards: `tests/test_eval_bylaw_references.py` (pytest) and
`web/e2e/functional/abs463-bylaw-reference-validation.spec.ts` (Playwright).

These two run **independently** — the Playwright spec re-implements the
assertions against the same JSON rather than shelling out to pytest. Keep it
that way: an earlier revision invoked `pytest` via `spawnSync`, which blocked a
Playwright worker for 13-50s and starved the WebKit projects (`tablet-ipad`,
`mobile-iphone`) into timing out six unrelated smoke/a11y tests.

**After any re-ingest of the Regional Centre by-law, run:**

```bash
.venv/bin/python scripts/build_bylaw_reference_index.py --check   # detect drift
.venv/bin/python scripts/build_bylaw_reference_index.py           # accept it
```

## Reference grammar

Only four forms are allowed. Anything else is a hard error in the builder, which
is what stops free-text "references" nobody can check from accumulating again.

| Form | Resolves to |
| -- | -- |
| `Section 198` | `fragment_type='SECTION'`, `citation_label='198'`, non-NULL `citation_path`, not under an `Appendix` |
| `Section 9(1)(c)` | the innermost group — `citation_label='(c)'` whose `citation_path LIKE '%> 9 >%'` |
| `Table 1A` | `citation_label='Table 1A'` |
| `Schedule 15` / `Appendix 2` | `citation_label='Schedule 15'` / `'Appendix 2'` |

`Section N` excludes appendix-parented fragments because the by-law's operative
sections and the internal sections of Appendix 2 share the 1..N namespace —
`Section 9` is both a development-permit exemption (p10) and a shadow-diagram
rule (p445). A bare section citation always means the operative one.

Sections the ingest mis-parents onto a schedule (`Schedule 17 > 111`) still
resolve, because the *label* is the stable key and the `citation_path` prefix is
not trustworthy in this ingest (see "Residual ingest defects").

## What was wrong, and what the corpus actually says

Verified by reading the caption fragment of each table:

```sql
SELECT citation_label, page_start, text FROM source_fragment
WHERE document_id = 4 AND citation_label ILIKE 'Table%' ORDER BY page_start;
```

| Reference | The eval used it as | The corpus caption says | Was on |
| -- | -- | -- | -- |
| `Table 3` | setbacks | *Minimum lot area requirements for Established Residential Special Areas* (p96) | TC-001..005 |
| `Table 4` | setbacks / lot coverage | *Minimum lot area requirements for Schmidtville Heritage Conservation District* (p97) | TC-006..010 |
| `Table 5` | height | *Minimum lot frontage requirements* (p97) | TC-003..010 |
| `Table 1A` | permitted uses in ER / CEN / DD / DH / COR | *Permitted uses by zone (DD, DH, CEN-2, CEN-1, COR, HR-2, and HR-1)* (p45) | 1A/1B swapped on 9 cases |
| `Table 1B` | permitted uses in CEN / DD / DH / COR | *Permitted uses by zone (ER-3, ER-2, ER-1, CH-2, and CH-1)* (p48) | " |
| `Section 120(a)/(b)` | parking exemption | Section 120 is the **DD zone's** streetwall cantilever/recess rule (p122); clauses (a)/(b) have a NULL `citation_path` | TC-004..010 |
| `Section 111` / `112` | height / FAR overlay | DD front-flanking setback (p116) / DD underground-parking setback exemption (p116) | TC-006..008, 010 |
| `Section 9(a)` / `9(d)` | deck permit exemption | 9(1)(a) is accessory structures ≤20 m² (NULL path); 9(1)(d) is home office uses | TC-001 |

`Table 1C` — *Permitted uses by zone (CLI, LI, HRI, INS, UC-2, UC-1, DND, H,
PCF, RPK, and WA)* (p51) — was never referenced, even though TC-011 (INS) and
TC-012 (RPK) both turn on it. TC-012's turn 2 premise ("if RPK doesn't appear in
either Table 1A or 1B") is a trap: RPK is in Table 1C.

## The map that drives most of the fix: Part V is chaptered by zone

Setbacks, height, lot coverage and streetwall standards are **not** in tables.
They are per-zone chapters of Part V. Derived by listing the heading + section
fragments in each chapter's page range:

```sql
SELECT citation_label, fragment_type, page_start, text FROM source_fragment
WHERE document_id = 4 AND page_start BETWEEN :a AND :b
  AND fragment_type IN ('HEADING','SECTION') ORDER BY reading_order_start;
```

| Standard | DD | DH | CEN-2/1 | COR | HR-2/1 | ER-3/2/1 | INS | PCF/RPK |
| -- | -- | -- | -- | -- | -- | -- | -- | -- |
| Applicability | 107 | 129 | 156 | 176 | 195 | 226 | 253 | 305 |
| Max building height (→ Schedule 15) | 109 | 131 | 157 | 177 | 196 | 227 | 254 | 306 |
| Max FAR (→ Schedule 17) | **110** | — | **158** | — | — | — | — | — |
| Min front / flanking setback | 111 | 132 | 159 | 178 | 197 | 228 | 255 | 307 |
| Side setback | 115 | 135 | 162 | 181 | **198** | 229 | 256 | 308 |
| Rear setback | 116 | 136 | 163 | 182 | **199** | 230 | 257 | 309 |
| Max streetwall height | 117 | 137 | 164 | 183 | 200 | — | 258 | — |
| Min streetwall height | 118 | 138 | 165 | 184 | 201 | — | 259 | — |
| Streetwall stepback | 119 | 139 | 166 | 185 | 202 | — | 260 | — |
| Max lot coverage | 121 | 142 | 168 | 187 | 204 | 231 | 262 | 310 |
| Ground floor requirements | 122 | 143 | 169 | 188 | 205 | — | 263 | — |
| Side / rear stepbacks | 125 | — | 172 | 191 | 208 | — | 264 | — |

Two consequences worth flagging, both verified by reading the section text:

- **FAR exists in DD and CEN only.** `Schedule 17` is invoked by Sections 110
  and 158 and nowhere else (`SELECT ... WHERE text ~* 'Schedule 17'` returns
  those two operative sections plus the definition and the schedule list). So
  DH (TC-008, TC-015), COR (TC-010, TC-018) and HR-2 (TC-005, TC-013) have **no
  FAR limit** — density there is controlled by height (Schedule 15) alone. Those
  cases reference the height and lot-coverage sections; the correct advisor
  answer to "what's the FAR?" is that the by-law imposes none in that zone.
- **Lot coverage is unregulated in DD, DH, CEN and COR and HR.** Sections 121,
  142, 168, 187 and 204 each read "No maximum required lot coverage applies."
  Only ER (231), INS (262, 60%) and PCF/RPK (310, 40%) carry a number.

## Cross-cutting provisions

| Topic | Provisions | Evidence |
| -- | -- | -- |
| Permitted uses | `Table 1A` / `1B` / `1C`; `Section 32` (interpretation), `Section 33` (use must also meet all other requirements) | captions above; s.32 p39, s.33 p40 |
| Development permit exemptions | `Section 9`; `Section 9(1)(c)` uncovered structures <0.6 m (decks, patios); `Section 9(1)(r)` internal conversion of a DD/DH commercial building to multi-unit | p10–11 |
| Site plan approval | `Section 15` (the eight variations subject to SPA), `Section 16` (application contents) | p17–18 |
| Non-conforming | `Section 23` (structures), `Section 24` (uses) | p22 |
| Combination of uses in ER zones | `Section 49` | p63 |
| Home occupation / home office | `Section 51` / `Section 52` | p63, p65 |
| Backyard suite | `Section 56`; built form via `Sections 327–333` — `329` (1.25 m side/rear), `331` (7.7 m height), `332` (≤20 m² exempt from lot coverage) | p67, p236–238 |
| Internal conversion in ER-3 / ER-2 | `Section 63` | p70 |
| Ground floor / active use | `Section 38` (pedestrian-oriented commercial streets, DD and DH only), `Section 69` (multi-unit on non-POCS, 50% of ground floor) plus the per-zone section from the table above | p42, p77 |
| Heritage | `Section 79`, `Section 80`, `Section 81` (HCD regulated by its own by-law), `Section 88` (Part V does not apply inside an HCD), `Section 337` (Part VI applies instead), `Schedule 22` | p92, p101, p242 |
| Motor vehicle parking | `Section 433` + `Table 15` | p331–334 |
| Bicycle parking | `Section 449` + `Table 16` | p342–343 |
| Incentive / bonus zoning | `Sections 472, 473, 475, 479, 480` + `Table 18` + `Schedule 50` | p363–367 |
| View planes | `Section 398` (no protrusion), `Section 399` (Schedules 26/28) | p298–299 |
| Shadow impact | `Section 10` (permit application must include a shadow study), `Appendix 2`, `Schedule 51` | p14, p443–447 |

### Parking: the exemption premise in the old data was wrong

`Table 15` (p332, `source_table` id 1083) is a zone × use grid. Row 1, "Any
other residential use", reads **"Not required"** in every column where a
residential use is possible — `DD, DH, CEN-2, CEN-1, CDD-*`; `COR`;
`HR-2, HR-1`; `ER-3, ER-2, ER-1`; `CH-2, CH-1`; `INS, UC-2, UC-1` — and "Not
applicable" only for `CLI, LI, HRI` and `PCF, RPK`.

So there is no "downtown no-parking exemption" that COR misses out on. The old
`Section 120(b)` reference and TC-010's note ("COR does NOT get the no-parking
exemption") were both wrong. Every residential parking question in the suite now
resolves to `Section 433` + `Table 15`.

## TC-001's zone: the geo source of truth

The `zone` field said `ER-1`; the case's own `expected_answer_keywords` said
`HR-1`. The geo data agrees with the keywords.

`geocode_cache` holds `1234 Oxford Street` (resolver `google_maps`, confidence
0.85) at **(-63.5957318, 44.6344483)**. Intersecting that point with the
`halifax_zoning_boundaries` external dataset:

```sql
SELECT f.feature_key, f.canonical_attributes_json
FROM external_dataset_feature f
JOIN external_dataset d ON d.id = f.external_dataset_id
WHERE d.name = 'halifax_zoning_boundaries'
  AND ST_Intersects(f.geometry,
      ST_SetSRID(ST_GeomFromGeoJSON('{"type":"Point","coordinates":[-63.5957318,44.6344483]}'), 4326));
```

returns exactly one polygon:

- `feature_key` / GlobalID `5f05d7eb-5062-473c-b463-465c7443cd8d`
- `zone_code` **HR-1**, `zone_description` "Higher-Order Residential 1"
- `bylaw_area_id` 23, `effective_date` 2024-06-13

The same point falls on parcel PID `00078147` (`halifax_property_parcels`) and
in a 9-storey height precinct (`halifax_height_precincts`). It is in no FAR
precinct and no heritage district.

`zone` is now `HR-1`. **The turn-1 message still says ER-1 on purpose** — the
user misstating their own zone is what the case tests, and the advisor is
expected to correct it and apply the HR-1 setback sections (198 / 199). The
`notes` field says so, so nobody "fixes" the prompt later.

## Residual ingest defects that constrained this work

These are the ABS-463 sequencing dependency. None of them blocked the fix, but
each cost a reference that would otherwise be the precise one:

1. **NULL `citation_path` on `Section 9(1)(a)`, `9(1)(b)`, `120(a)`, `120(b)`.**
   Clause (a) of Section 9 — accessory structures ≤20 m², the exemption TC-012
   argues about — is unreachable. TC-012 references bare `Section 9` instead.
   When the ingest is fixed, tighten TC-012 to `Section 9(1)(a)`.
2. **`Part V > 2` phantom section.** Section 198's zone list wraps mid-line and
   the second line ("2, ER-1, CH-2, CH-1, PCF, or RPK zone:") is parsed as a
   SECTION labelled `2`. Same defect produces a phantom `3` on p103. Harmless
   here — nothing references them — but `Section 2` and `Section 3` are
   currently unsafe to cite.
3. **Part attribution is wrong outside Part I.** Sections 15, 49, 56, 63, 69,
   85, 86 all carry `Part I > N` paths despite living in Parts III/IV; 111, 112,
   113, 120 carry `Schedule 17 > N`. This is why the resolver keys on
   `citation_label` and treats `citation_path` as evidence rather than as a
   lookup key.
4. **`Schedule 17` resolves to a mis-parsed continuation fragment** (p116, a
   sentence fragment beginning "Schedule 17 for the subject property…") rather
   than to a schedule stub like Schedules 15/22/50/51/7 (p457). It resolves, so
   the reference is valid, but the excerpt is unhelpful. Worth regenerating the
   stub in the ingest fix.
5. **Schedules 18, 20, 26 and 28 have no fragment of their own.** They exist
   only as clauses of Section 29's list. They are therefore not citeable under
   the grammar, so cases reference the *section that invokes* the schedule
   (e.g. `Section 197` → Schedule 18) instead.

## Out of scope, but found while verifying — worth a follow-up ticket

1. **Two more addresses do not geocode to the zone the case claims.** The issue
   said TC-001 was the only mismatch; it is the only one whose *own keywords*
   disagree, but:
   - `1505 Barrington Street` (TC-006, claims **CEN-1**) intersects the **DH**
     polygon `5085528c-14b0-467b-af1b-06a6638e8077`.
   - `200 Bayers Road` (TC-004, claims **HR-1**) intersects **no** Regional
     Centre zoning polygon at all — it is outside the by-law's boundary, in
     Halifax Mainland (`document_id = 5`).

   Both cases' turn text and keywords consistently describe the claimed zone, so
   this is an address-realism problem, not a stale-field problem, and changing
   the `zone` would contradict the scenario. Left alone deliberately. The other
   17 addresses are not in `geocode_cache` and were not independently geocoded.

2. **Lot-coverage keywords contradict the corpus.** TC-007 and TC-009 expect
   `"80%"` and TC-010 expects `"70%"`, but CEN-2, DD and COR all read "No
   maximum required lot coverage applies" (Sections 168, 121, 187). ABS-265
   calibrated keywords empirically against transcripts, and this issue scoped
   keywords as trustworthy, so they were not touched.

3. **Two keywords point at the wrong provision.** TC-019 (ER-3 backyard suite)
   expects `"Section 344"`, which is the *Schmidtville HCD* height section;
   TC-020 (viewplanes) expects `"Schedule 50"`, which is the *bonus zoning rate
   districts* map — Halifax Citadel view planes are Schedule 26. Same
   out-of-scope reasoning as above.

4. **The by-law contains no demolition control.** A full-text search for
   `demolit` in `document_id = 4` returns only definitions and an unrelated
   rock-crusher clause. Heritage demolition (TC-008 turn 2, TC-015 turns 1 and
   5) is governed by the HRM Heritage Property By-law and the provincial
   Heritage Property Act, outside this corpus; `Section 81` is the by-law's own
   pointer to that. The correct advisor behaviour is to say so, and the
   reference sets reflect it.
