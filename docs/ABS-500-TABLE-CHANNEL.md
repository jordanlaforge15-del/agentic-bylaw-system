# The table channel, and how a ranked cell is cited (ABS-500)

## What was broken

`RetrievalService.search` ranked `source_fragment` rows and nothing else.

* `_text_channel_scores` scored fragments.
* `_spatial_channel_scores` scored linked geo datasets and resolved them back
  to `linked_fragment_id` — again a fragment.
* Tables reached the model only as `related_tables`, attached to a fragment
  that had **already** ranked, and gated behind `include_tables`.

So a standard that lives in a matrix was reachable only by luck: some prose
fragment near the table had to rank first on its own words. "What is the
maximum height in HR-2" — the archetypal product question, whose answer is a
cell reading `12.0 metres` — had no route to the cell that answers it.

## What the measurement actually said

The issue attributed the `dimensional` class's Recall@10 of 0.056 to the
missing table channel. Reading the ABS-486 labels says something narrower:
**17 of the 18 dimensional questions are answered by a prose section**, not a
table. What those sections have in common is that they never name the zone —
the Regional Centre declares it once, in the chapter heading:

```
Part V, Chapter 9: Built Form and Siting Requirements within the ER3, ER-2,
                   and ER-1 Zones
  231 (1) … the maximum required lot coverage shall be:
```

Meanwhile dozens of clauses elsewhere list `ER-1` among *abutting* zones
("where a lot abuts another lot, any portion of which, is zoned HR-2, HR-1,
ER-3, ER-2, ER-1 …"). The scorer paid **+4** for that passing mention (own
text) and **+2** for the governing chapter (inherited context, ABS-492), so a
landscaping clause about abutting land outranked the section stating the ER-1
standard. That inversion is what pinned the class at 0.056, and it is why every
arm of ABS-494's fusion matrix measured ~0.06: no re-weighting of channels can
fix a ranking that has the evidence backwards inside one channel.

Both halves of ABS-500 are therefore the same idea — **bind a zone-scoped query
to what the corpus says the zone governs**, rather than keyword-matching text
that happens to mention it:

| | prose | tables |
|---|---|---|
| what declares the scope | a container heading (`Part V, Chapter 9: … within the ER-1 Zones`) | a `table_axis_binding` row/column |
| implemented in | `retrieval/binding.py` | `retrieval/tables.py` |
| scored at | +12, the citation-path rung | +20, above the term-overlap ceiling |

## How a ranked cell is cited

A cell is not a `source_fragment`, and `RetrievalMatch` is fragment-shaped.
Three shapes were available:

1. **A parallel result list.** `RetrievalResponse.table_matches` beside
   `matches`, ranked separately. Rejected: the caller then has to interleave
   two rankings itself, and every consumer (advisor, compliance evaluator,
   compact transcript) would need to learn a second result shape.
2. **A synthetic fragment id.** Rejected outright — a fabricated
   `fragment_id` that resolves to nothing is a citation that cannot be
   verified, which is the one failure a retrieval artifact must not produce
   quietly.
3. **Cite through the table's anchor fragment.** Adopted.

### The rule

> A ranked cell is cited through the **provision that introduces its table**,
> and addressed by the row and column labels that name it.

That is how a by-law table is cited on paper — "section 94, Table 9, the HR-2
row, the Maximum Building Height column" — and it is the rule this codebase
already applies: `_table_citation` cites a permitted-use matrix through the
same anchor when `get_permitted_use` resolves a cell.

Concretely, a table-channel hit produces an ordinary `RetrievalMatch` whose
`fragment_id` is the anchor, with `"table"` in `retrieval_channels` and a
`TableCellMatch` in `table_matches`:

```json
{
  "fragment_id": 7357,
  "citation_path": "Part V > [Table 10]",
  "retrieval_channels": ["table", "text"],
  "table_matches": [{
    "table_id": 1076,
    "caption": "Table 10: Maximum required lot coverage for Established Residential Special Areas",
    "profile_type": "dimensional_matrix",
    "anchor_fragment_id": 7357,
    "citation_path": "Part V > [Table 10]",
    "citation_label": "Table 10",
    "page_start": 191, "page_end": 191,
    "row_index": 3, "col_index": 1,
    "row_label": "North End Halifax 2 (NEH-2)",
    "col_label": "Maximum Required Lot Coverage (%)",
    "text": "50%",
    "bound_by": ["row bound to zone 'NEH-2'"]
  }]
}
```

`bound_by` states *why* the cell was addressed. Empty means the cell was
reached by matching the caption and header text; a non-empty entry means
enrichment bound that axis to the entity the query named, which is a stronger
claim and the one a reader should be able to audit.

### Resolving the anchor

`source_table.parent_fragment_id` when the ingest set one. It usually did not —
63 of the 96 tables in the dev corpus are parentless, including *every* table
in the Halifax Mainland by-law — so the fallback reads the docling block
ordering both rows already carry: the anchor is the fragment with the greatest
`source_block_ids_json` entry strictly before the table's own
`metadata_json.source_block_id`. That is literally "the provision immediately
preceding this table", which is the provision a reader would cite it under.
Spot-checked against the corpus: the parentless Mainland dimensional matrix on
page 66 anchors to `Schedule A > 28C > 28AB(1)` — *"Buildings erected, altered
or used for R-1, R-2 and R-2P in an R-2P Zone shall comply with the following
requirements:"*.

A table that resolves to no anchor is **not ranked**. A match the model cannot
ground an answer in is worse than a miss; such a table is still reachable one
hop away through `related_tables`.

### `include_tables` does not gate the citation

`include_tables=False` suppresses `related_tables` — everything near the
fragment, whether or not it bore on the query. It does **not** suppress
`table_matches`: a match that ranked *because of* a cell is not groundable
without naming the cell. Gating the evidence behind a display flag is the bug
this issue was opened about, one level down.

## What ranks, and what does not

`table_channel_scores` requires a table to be addressed on **both** axes. One
axis alone ("something about lot coverage", "something about ER-1") identifies
a row or a column but not a cell, so promoting the table would be promoting an
arbitrary value from it. Such a table is attached, never ranked.

Two further guards, both of them cells the ingest produces in quantity:

* **Marker-only cells.** Permission matrices mark cells with private-use font
  glyphs (``) and circled footnote digits (`④`). Stripped of whitespace
  they are non-empty but carry nothing a reader can quote back; the marker's
  *meaning* is resolved by `get_permitted_use` through the permission-marker
  vocabulary, never by quoting the glyph.
* **Header cells.** A cell whose text repeats its own axis label is the
  question, not the answer.

Scoring uses only the query terms rare enough in the corpus to carry scope —
the same document-frequency cut the text channel applies, carried across on
`TextChannelScores.discriminating`. Measuring it separately over the ~800 axis
labels gave a different answer (nothing was cut), and the channel then ranked
on "in", "a" and "for", which every long row header carries: the longest header
won every query regardless of what it said.

## Measured effect

ABS-486 set, 68 questions, dev corpus (documents 4 + 5), k = 10:

| class | before | after |
|---|---|---|
| **dimensional** | **0.056** (1/18) | **0.500** (9/18) |
| citation_lookup | 0.375 | 0.375 |
| definition | 0.083 | 0.083 |
| permitted_use | 0.643 | 0.643 |
| spatial | 1.000 | 1.000 |
| zone_anchored | 1.000 | 1.000 |
| **overall** | **0.441** | **0.559** |

No query that passed before fails after.

**The cost, stated plainly.** MRR fell in two classes while their Recall@10
held: `permitted_use` 0.476 → 0.318, `zone_anchored` 0.678 → 0.608. Zone-scope
binding lifts *every* clause in the chapter the query's zone names, so on a
question whose answer lives outside that chapter the answer sits lower in a
top-10 it still reaches. Overall MRR still rose, 0.2775 → 0.3077, carried by
`dimensional` 0.008 → 0.284. The table channel is not implicated in either
direction: measured with it disabled, every per-class recall and MRR figure
above is identical.

Latency for one `search` call, from the same harness on the same host: p95
364 ms → 460 ms, mean 316 ms → 385 ms. The cost is the added ancestor-chain
walk in the text channel; the table index is four reads, built once and cached
per document scope.

## What is still missing

The nine dimensional questions still failing are not table questions either.
They fall into two groups:

* **Zone-declaring containers the Mainland by-law does not have.** Its zone
  headings ("R-1 ZONE: SINGLE FAMILY DWELLING ZONE") exist as `HEADING`
  fragments but are not *ancestors* of the sections they scope — the ingest
  left that tree flat, so there is nothing to bind through. This is an ingest
  gap, not a ranking one.
* **Questions whose zone is implied rather than named** ("Does the by-law
  impose a maximum rear setback anywhere?", "How tall can an accessory
  structure be?"), which no zone binding can help.

A third gap, not visible in the Recall@10 numbers above, was found later and
closed by ABS-518: crediting the *declaring* chapter says nothing about a
clause sitting under a chapter that declares **other** zones, and since no
built-form section names its own zone, that left the wrong-chapter comparison
decided by nothing. See
[ABS-518-ZONE-SCOPE-EXCLUSION.md](ABS-518-ZONE-SCOPE-EXCLUSION.md), which also
documents how a table with no axis bindings at all — `Table 9`, the ER zones'
side setback table — was addressed by an HR-1 question through the bare token
`1` in the row label "North End Halifax 1 (NEH-1)".
