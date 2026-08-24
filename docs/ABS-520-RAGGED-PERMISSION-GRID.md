# A prohibition that reads as "undetermined" (ABS-520)

ABS-483 and ABS-484 established that "we could not read this cell" and "the
by-law prohibits this" are different claims, and gave the first its own value
(`unknown`) and its own list (`uses.undetermined`); the confidence rungs those
readings sit on are in
[decisions/ABS-493-CONFIDENCE-DEFINITION.md](decisions/ABS-493-CONFIDENCE-DEFINITION.md).
This documents a defect that was *hiding inside* that distinction: a class of
real prohibitions the corpus had already lost, which the `unknown` vocabulary
then faithfully reported as unreadable.

## The failure

TC-026 asks whether a homeowner at 6051 Oakland Road (ER-2) may build four
townhouses. The advisor issued the most direct query available —

```json
{"name": "lookup_citation",
 "input": {"structured": {"kind": "permitted_use",
                          "use": "Townhouse dwelling use",
                          "zone": "ER-2"}},
 "result_citations": []}
```

— got nothing back, and told the user:

> **I cannot confirm this from the ingested bylaw source.** The permission cell
> for "Townhouse dwelling use" in zone ER-2 could not be extracted (unreadable
> matrix cell).

The by-law is not silent. Table 1B prints the cell blank, and a blank cell in a
symbol matrix *is* the prohibition. The founder-attested answer for TC-026 is
"Table 1B shows that townhouses are not permitted in an ER-2 zone."

This is the expensive direction for the error. "Cannot confirm" reads as
"possibly allowed", and a homeowner can spend money designing a use the by-law
forbids.

## Root cause: the parser emits a ragged grid

Not a glyph problem, not column drift. The table parser materializes a
`source_table_cell` only where a text run landed, and a blank cell has no text
run. Page 48's townhouse row survives extraction as:

```
Residential                | ER-3 | ER-2 | ER-1 | CH-2 | CH-1
Single-unit dwelling use   | ⑮    | ⑮    | ⑮    | ●    | ●
Semi-detached dwelling use | ⑮    | ⑮
Duplex apartment use       | ⑮
Townhouse dwelling use     | ⑮
Two-unit dwelling use      | ⑮    | ⑥⑭⑮ | ⑥⑭
```

Every row is right-truncated at its last marker. `resolve_permission_cell`
addresses (Townhouse dwelling use, ER-2) through the bound axes, finds no cell
at the intersection, and returns `unknown` — which is the correct behaviour
under ABS-483's rules, applied to a corpus that had silently discarded the
answer.

Two facts settle that the surviving cells are *positionally* trustworthy, so
the absences really are blanks and not lost content:

* every value cell's x-centre falls inside its own column's band, derived from
  the header row — across all 12 matrix tables, zero mismatches;
* blanks that the parser *did* store (369 of them) hold `U+F020`, the symbol
  font's space glyph, exactly where the by-law prints nothing.

## What was NOT wrong, and must stay that way

Page 48 also contains a genuine extraction failure, which the repair must not
paper over. **"Cluster housing use" is missing from Table 1B entirely** — no row
label at all — and its two ● dots were absorbed into the following section
header, where they sit in a y-band matching no row. Filling that region would
fabricate prohibitions for a use the by-law permits.

So the repair is not "treat a missing cell as blank". It is "treat a missing
cell as blank **where the geometry shows the row lost nothing**".

## The repair

`layer1.semantic.permission_grid` materializes the missing intersections of a
permission matrix as explicit blank cells, gated on five geometric refusals:

| refusal | what it catches |
| --- | --- |
| `column_drift` | column x-bands overlap → no cell position is trustworthy; the whole table is refused |
| `foreign_content` | a value cell holding words, not markers → a reprinted column header, not data |
| `unlabelled_row` | value cells on a row with no label → a use row was lost here |
| `orphan_cell` | a value cell whose y-band misses its own row label → content attached to the wrong row |
| `row_pitch_gap` | consecutive row labels further apart than the table's row pitch → a label was dropped between them |
| `no_geometry` | no bboxes → nothing can be shown, so nothing is filled |

`orphan_cell` and `row_pitch_gap` refuse the rows *bracketing* the damage, not
just the row that owns it — a dropped row lives in the gap, and its neighbours
are the ones that may have absorbed its content.

Every materialized cell carries `metadata_json.grid_fill = "absent_cell"`, so
the fabrication is greppable, countable and reversible, and so a second pass
skips them when re-reading the geometry.

Enrichment runs this on every ingest (`_enrich_table`, in the
`permission_matrix` branch, before the axis bindings make the intersections
addressable). `scripts/backfill_permission_grid.py` applies the identical code
to an already-ingested corpus without a re-parse.

## Blast radius

Measured against the dev corpus (Regional Centre LUB, 12 permission-matrix
tables) inside a rolled-back transaction:

```
filled=1103  refused=227
refusal reasons: foreign_content=144  unlabelled_row=16  row_pitch_gap=14  orphan_cell=8
```

Undetermined uses per zone, before → after:

| zone | before | after |
| --- | --- | --- |
| ER-2 | 72 | 3 |
| ER-1 | 72 | 3 |
| ER-3 | 71 | 3 |
| CH-1 | 76 | 3 |
| CH-2 | 76 | 3 |
| DD | 10 | 2 |
| HR-1 | 40 | 25 |
| COR | 14 | 5 |
| CEN-1 | 21 | 12 |

The residue is not noise to be driven to zero. HR-1 keeps 25 — 14 from Table
1A's page-45 slice and 11 from page 46 — because both slices carry dropped rows
and reprinted headers around their commercial sections. Those uses genuinely
cannot be read, and must keep saying so.

## The guard

`scripts/verify_permission_grid_integrity.py` runs three checks against the live
corpus:

* **G1** — no intersection the geometry vouches for may still be missing. A
  non-zero count means a re-ingest or re-parse dropped blanks again, and
  prohibitions are being served as "undetermined".
* **G2** — three named cells, each attested outside this codebase, must resolve
  to the permission the by-law prints. The anchor is
  (Townhouse dwelling use, ER-2) = `not_permitted`.
* **G3** — the refused residue is printed with its reason. Advisory only: it is
  the extraction debt this repair deliberately declines to guess at, and it
  belongs on the record rather than being mistaken for coverage.

`tests/scripts/test_verify_permission_grid_integrity.py` runs the guard over a
ragged corpus twice — as a blank-dropping parser leaves it, then after the
backfill — and requires it to fail before it passes.

## Operating it

```bash
# Report, and see the blast radius, without writing:
.venv/bin/python scripts/backfill_permission_grid.py --dry-run \
    --zone ER-2 --zone HR-1

# Repair an ingested corpus (migration-fenced):
.venv/bin/python scripts/backfill_permission_grid.py

# Guard it:
.venv/bin/python scripts/verify_permission_grid_integrity.py --zone ER-2
```

A fresh ingest needs neither: enrichment densifies as it classifies.

## Known follow-ups

* **The rows the parser lost are still lost.** "Cluster housing use" does not
  appear in ER-2's zone profile at all — not as `undetermined`, not as
  anything, because no axis binding exists for a row with no label. G3 reports
  the damage; recovering the rows themselves is a parser/re-ingest problem.
* **TC-026 grading.** `verify_golden_cases.py` grades a recorded advisor run,
  and TC-026's attestation currently lives on the unmerged
  `docs/zone-typology-test-questions` branch. The deterministic precondition
  the case turns on — the ER-2 townhouse cell resolving to `not_permitted` with
  a citation instead of "undetermined" — is asserted by G2 and by
  `web/e2e/functional/abs520-ragged-permission-grid.spec.ts`. Grading the case
  end to end needs that branch merged and an eval run.
