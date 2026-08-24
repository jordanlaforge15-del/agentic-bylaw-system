# ABS-494 — scoring & fusion: SHIP the FTS hybrid, KEEP the max() fusion

**Status:** decided, shipped · **re-measured 2026-08-23 (ABS-518) and 2026-08-24 (ABS-521), decision unchanged both times**
**Date:** 2026-08-13
**Evidence:** [`evals/retrieval/experiments/RESULTS.md`](../../evals/retrieval/experiments/RESULTS.md) (17 arms, k=10, 72 labelled questions as re-measured; 68 when decided)
**Supersedes:** the first run of the same matrix (commit `702cb4c`), whose conclusion no longer holds — see *The matrix that expired* below.

> **Reading the numbers in this doc.** Everything below the two re-measurement
> sections is the evidence **as it stood on 2026-08-13**, quoted against a
> control scoring 0.5588. That control no longer exists. See *Re-measured under
> ABS-521* for the current figures and *Re-measured under ABS-518* for the
> intermediate ones; the three sets are not interchangeable, and the oldest are
> kept because they are what the decision was actually made on.

## Verdict

Two decisions, and the ticket's headline hypothesis lost.

| | Decision | Result |
|---|---|---|
| **Text channel** | **SHIP** a Postgres FTS ranking blended into the text channel, 50/50 with the existing path/context ladder | Recall@10 **0.5588 → 0.6618** (+0.1030) |
| **Fusion** | **KEEP** `max()` + the spatial agreement bonus. **RRF is refuted.** | RRF-only fusion scored **0.5441** (−0.0147) |

The issue was framed as "RRF vs FTS-hybrid vs keep" and expected fusion to be
the defect. On the retriever we actually ship, fusion is not the defect: the
text channel's *coverage* is. Replacing `max()` with Reciprocal Rank Fusion
makes things slightly worse, and adding a full-text channel beside the ladder
makes them substantially better.

## Re-measured under ABS-521 (2026-08-24) — decision unchanged

ABS-521 did not touch the scorer. It added two labelled questions — `RQ-D21` and
`RQ-D22`, the footprint and floor-area caps of Regional Centre s.333 (see
[`docs/ABS-521-PROVISION-COMPLETION.md`](../ABS-521-PROVISION-COMPLETION.md)) —
taking the set from 70 to 72. That alone was enough to invalidate this matrix,
and it is worth being precise about why, because the cause is different from
ABS-518's.

ABS-518 moved the *retriever* out from under a frozen table. ABS-521 moved the
**grading instrument**: `BASELINE.json` and `arms/*.json` read the same
`queries.json`, so adding a question re-bases every number in this file at once
while the committed arms go on describing a 70-question set that no longer
exists. Two tables of recall over different question sets are not comparable,
and `abs494-scoring-fusion-decision.spec.ts` refused them — which is the
assertion earning its keep on a failure mode nobody had anticipated in advance.
**The whole matrix was re-derived** rather than reconciled by hand:

```bash
python scripts/eval_retrieval_experiment.py --database-url …   # all 17 arms, k=10, 72 questions
```

| | Recall@10 | SetRecall@10 | MRR@10 |
|---|---|---|---|
| `current` (production, the new control) | **0.6806** | 0.6736 | 0.3429 |
| `fts_hybrid_50` (shipped) | **0.6806** | 0.6736 | 0.3429 |

The two rows coincide to the digit again, and again that is the check rather
than a tautology: `current` runs production untouched, so their agreement says
the shipped blend and the measured arm are still the same program.

**The headline fell 0.6857 → 0.6806 and nothing regressed.** That is worth
stating carefully, because a matrix whose control drops is exactly the shape of
a regression. Diffing the new `current.json` against the previous one
question-by-question rather than by total: **0 of the 70 pre-existing questions
changed their top-ten ranking, and 0 changed their hit flag.** The move is
arithmetic — 48 hits over 70 became 49 over 72, and 48/70 > 49/72. MRR rose
(0.3384 → 0.3429) for the same reason, `RQ-D22` landing at rank 1.

`RQ-D21` is a **recorded miss, and expected to stay one**: the clause carrying
the 60.0 m² footprint cap is a zone list and a number, scoring 9 against its own
section's 21 on the very query it answers, and no weight lifts it without
lifting every bare list item in the corpus with it. It is logged in `queries.json`
as the standing record of a clause unreachable by rank, so a future change that
flips it to a hit reads as a real improvement. It drags `dimensional` from 0.60
(12/20) to 0.59 (13/22) — a harder set, not a worse retriever.

**Both decisions survive.** `fts_hybrid_50` is still the best arm; RRF is still
refuted, its best arm (`rrf_all_channels_ts_rank`) at 0.6389, −0.0417 behind.
The sweep keeps its shape and its peak, with 0.40 and 0.50 still tied on recall
and 0.50 still taking it on MRR (0.3429 vs 0.3219):

| text / fts | Recall@10 (2026-08-23, control 0.6857) | Recall@10 (2026-08-24, control 0.6806) |
|---|---|---|
| 0.25 / 0.75 | 0.5714 | 0.5694 |
| 0.35 / 0.65 | 0.6571 | 0.6528 |
| **0.40 / 0.60** | **0.6857** | **0.6806** |
| **0.50 / 0.50** ← shipped | **0.6857** | **0.6806** |
| 0.60 / 0.40 | 0.6429 | 0.6389 |
| 0.70 / 0.30 | 0.6143 | 0.6111 |
| 0.85 / 0.15 | 0.5857 | 0.5833 |

The ABS-518 observation still stands and is still not acted on:
`rrf_all_channels_ts_rank` leads on dimensional recall (0.68 vs the shipped
0.59) and on MRR (0.4146 vs 0.3429) while trailing on the headline.

## Re-measured under ABS-518 (2026-08-23) — decision unchanged

ABS-518 changed the text ladder (zone-scope exclusion, a locator-segment rule on
the citation-path rung, and a tokenizer fix — see
[`docs/ABS-518-ZONE-SCOPE-EXCLUSION.md`](../ABS-518-ZONE-SCOPE-EXCLUSION.md))
and added two labelled questions, taking the set from 68 to 70. Both moves put
the retriever past the control this matrix was quoted against, which is exactly
the staleness `abs494-scoring-fusion-decision.spec.ts` exists to catch — and it
caught it. **The whole matrix was re-derived** rather than reconciled by hand:

```bash
python scripts/eval_retrieval_experiment.py --database-url …   # all 17 arms, k=10, 70 questions
```

| | Recall@10 | SetRecall@10 | MRR@10 |
|---|---|---|---|
| `current` (production, the new control) | **0.6857** | 0.6857 | 0.3384 |
| `fts_hybrid_50` (shipped) | **0.6857** | 0.6857 | 0.3384 |

The two rows coincide to the digit — zero questions gained, zero lost — which is
the steady state the spec describes: `current` tracks production, and production
*is* the shipped blend, so re-running the matrix after a ship measures it against
itself. That agreement is the check, not a tautology: it is the harness's design
premise (each arm is production's `search()` with one seam swapped) still
holding after the ladder underneath it moved.

**Both decisions survive the re-measurement.** `fts_hybrid_50` is still the best
arm; RRF is still refuted, with its best arm (`rrf_all_channels_ts_rank`) at
0.6429, −0.0428 behind.

The weight sweep still peaks where it did, though the plateau has narrowed —
worth knowing before quoting the old one:

| text / fts | Recall@10 (2026-08-13, control 0.5588) | Recall@10 (2026-08-23, control 0.6857) |
|---|---|---|
| 0.25 / 0.75 | 0.5441 | 0.5714 |
| 0.35 / 0.65 | 0.6324 | 0.6571 |
| **0.40 / 0.60** | **0.6618** | **0.6857** |
| **0.50 / 0.50** ← shipped | **0.6618** | **0.6857** |
| 0.60 / 0.40 | 0.6176 | 0.6429 |
| 0.70 / 0.30 | 0.5882 | 0.6143 |
| 0.85 / 0.15 | 0.5294 | 0.5857 |

Same shape, same peak, and 0.50 still posts the better MRR of the two settings
tied for best recall. What changed is that "every weight in [0.35, 0.70] beats
the control" is no longer a meaningful sentence post-ship — the control *is*
0.50 — so read the curve for its shape, not for a sign.

Two things the re-run surfaces that were not visible in August, recorded rather
than acted on:

* **`rrf_all_channels_ts_rank` now leads on dimensional recall (0.70 vs the
  shipped 0.60) and on MRR (0.4122 vs 0.3384)**, while trailing on headline
  recall. If the dimensional-MRR concern in *What to watch* ever becomes
  load-bearing, that arm — not weight 0.60 — is the first place to look.
* **Latency drifted with the ladder change**: mean 444.2 → 450.3 ms, p95 575.8 →
  531.3 ms. Host-dependent, recorded in `BASELINE.json`, never used to fail a run.

## The matrix that expired

This issue was measured once before and reached the opposite conclusion. That
run graded a control scoring Recall@10 = 0.1618 and found RRF and FTS-hybrid
arms beating it by +0.34. Before it could ship, two changes landed on `dev`:

* **ABS-492** — provision-in-context scoring, so a clause inherits the scope
  its containers supply.
* **ABS-500** — the table channel, which ranks `source_table_cell` directly.

Together they moved the shipped retriever to **0.5588** — past every arm the
old matrix had recommended. The measurement was never wrong; it had simply
stopped describing the program it claimed to describe.

`scripts/check_retrieval_baseline.py` (ABS-502) names this failure in its own
docstring — *"ABS-494 argued its case ('+0.34, zero regressions') against a
control that had already moved"* — and exists to prevent it. Every arm here was
therefore re-derived against the current retriever, and the control was
verified to reproduce `BASELINE.json` exactly (0.5588 / 0.5588 / 0.3077, all six
categories) before any candidate was read.

**The old numbers are not comparable to these and are not carried forward.**

## Why RRF lost

RRF is scale-free — it reads each channel's *order* and never its units — which
is a genuine virtue when channels are comparably reliable, and its weakness
when they are not.

The four channels are not comparably reliable. ABS-500's table channel is what
carries dimensional recall, and it is precise: it either binds a cell to the
zone the query named or it ranks nothing. Giving it an equal vote with a path
ladder that scores thousands of fragments — whose ranks past the first page are
noise, mostly ties broken on primary key — dilutes exactly the signal that was
working:

| Arm | Recall@10 | dimensional | citation_lookup |
|---|---|---|---|
| `current` | 0.5588 | 0.50 | 0.38 |
| `rrf_fusion_only` | 0.5441 (−0.0147) | 0.44 | 0.50 |

RRF does buy ranking quality where it wins — `rrf_all_channels` posts the best
MRR in the matrix (0.3944, +0.0867) — but never enough recall to justify
replacing a fusion rule that is working. `rrf_all_channels` reaches 0.6029,
still below the hybrid's 0.6618, while *also* being a larger change.

The one thing RRF was supposed to fix turns out not to need fixing here. The
argument for it was that `max(text, spatial) + 10.0` compares incommensurable
units. True — but ABS-493 already removed the only consumer that read those
units as a magnitude (`min(1.0, score / 40.0)`, replaced by an ordinal
`EvidenceClass`). What remains is a *ranking* rule, and a ranking rule is
judged by the ranking it produces.

## Why the hybrid won, and why it is not a fitted constant

The shipped change adds a Postgres full-text ranking over
`to_tsvector('english', citation_label || ' ' || text)` — verbatim the
expression `ix_source_fragment_text_tsv` already indexes (0002:243) — and
blends it into the text channel 50/50.

It is a **hybrid rather than a replacement** because that index cannot see
`citation_path`, which is the entire basis of the ladder's structural scoring.
Neither channel can be dropped for the other, and the diagnostics confirm it:
`fts_only` scores 0.4706, *below* the control, collapsing dimensional recall
from 0.50 to 0.06.

### The knob was swept, not chosen

`fts_hybrid_50` posting the best number in the matrix while `fts_hybrid_25`
posts a worse-than-control one is the signature of a constant fitted to the
eval set — which is the defect this issue exists to remove, not a fix for it.
So the weight was swept:

| text / fts | Recall@10 | Δ vs control | MRR@10 |
|---|---|---|---|
| 0.25 / 0.75 | 0.5441 | −0.0147 | 0.2716 |
| 0.35 / 0.65 | 0.6324 | +0.0736 | 0.3016 |
| **0.40 / 0.60** | **0.6618** | **+0.1030** | 0.3291 |
| **0.50 / 0.50** ← shipped | **0.6618** | **+0.1030** | 0.3388 |
| 0.60 / 0.40 | 0.6176 | +0.0588 | **0.4003** |
| 0.70 / 0.30 | 0.5882 | +0.0294 | 0.3916 |
| 0.85 / 0.15 | 0.5294 | −0.0294 | 0.3254 |

A smooth, unimodal curve with a broad plateau: **every weight in [0.35, 0.70]
beats the control.** An effect that survives its knob moving ±40% is an effect,
not an artifact of 68 unreviewed labels. 0.50 is the midpoint of the peak and
posts the better MRR of the two settings tied for best recall.

Note the recall/MRR tension the sweep exposes: recall peaks at 0.40–0.50 while
MRR peaks at 0.60. 0.50 buys three more answered questions at some cost to how
far down the agent reads. That trade is revisitable — see *What to watch*.

## What it costs

**Per class** (`current` → shipped):

| Class | n | Recall@10 | MRR@10 |
|---|---|---|---|
| citation_lookup | 8 | 0.38 → **0.62** | 0.188 → 0.353 |
| definition | 12 | 0.08 → **0.33** | 0.017 → 0.206 |
| dimensional | 18 | 0.50 → **0.56** | 0.284 → **0.130** |
| permitted_use | 14 | 0.64 → **0.79** | 0.318 → 0.382 |
| spatial | 6 | 1.00 → 1.00 | 0.597 → 0.597 |
| zone_anchored | 10 | 1.00 → **0.90** | 0.608 → 0.648 |

**Nine questions gained, two lost.** The two losses are structural rather than
a weighting artifact — *every* FTS arm loses exactly these two, including
`fts_hybrid_85`, which gains nothing at all:

* `RQ-D01` (ER-3 side setback) — ranked **9** under the control.
* `RQ-Z10` (R-2 requirements, Halifax Mainland) — ranked **8** under the control.

Both were bottom-of-window marginals displaced off the edge of the top ten, not
confident results destroyed. Nine questions moved in the other direction.

**Two costs are real and are not being hidden:**

1. **dimensional MRR halves** (0.284 → 0.130) even as its recall rises. The
   right fragment is found more often but sits lower. `get_zone_profile`
   extracts a value from whichever fragment ranks *first*, so this is the
   number to watch — see below.
2. **Latency**: mean 394.6 → **444.2 ms** per `search()` (+12.6%), p95 483.9 →
   575.8 ms. One extra index-eligible query per search. Recorded in
   `BASELINE.json`, host-dependent, never used to fail a run.
3. **Retrieval scores are now length-sensitive.** `ts_rank_cd(..., 1)`
   normalises by token count, so between two fragments that match a query
   equally well, the shorter one now ranks higher. That is the intended
   behaviour of a text ranker and it is most of where the +0.10 comes from,
   but it is a genuinely new property of `score`: before this change, two
   fragments matching the same tokens scored the same however long they were.

   It surfaced immediately, in an unrelated place. `abs480-citation-collision`
   asserts that a citation-path collision costs a provision nothing, and it
   proved that by comparing a collided clause's score against an uncollided
   sibling's. The probe corpus's five lines had never been length-matched
   because length had never been observable — they ran 9 / 9 / 8 / 5 tokens.
   Under the blend the control outscored its collided sibling by 0.42 and the
   deliberately-uncertain bullet outscored both, so the spec failed while the
   behaviour it guards was entirely intact.

   The fixture was equalised at nine tokens per provision, and the seed now
   asserts that parity itself so a reworded line fails naming the cause. Worth
   knowing generally: **any test that compares two retrieval scores must now
   control for length**, or it is measuring brevity.

## The zone-profile gate, and what it could not tell us

The ticket's ship gate is "Recall@10 improves **without zone-profile
regression**". The gate was run over all 19 zones the query set names:

```bash
python scripts/eval_retrieval_experiment.py --database-url … --zone-gate \
    --arms current,fts_hybrid_50,rrf_all_channels,rrf_all_channels_top50,fts_hybrid_rrf_text --dry-run
```

Result for the shipped arm: **no zone became unknown, and no zone's
permitted-use count moved.** (Run *before* the ship, when `current` was still
the 0.5588 retriever — that is the comparison the decision turned on. Re-running
it now compares the hybrid against itself, since the `current` arm tracks
production; to reproduce the original diff, run it at the parent of the commit
that shipped the blend.)

**But the field comparison it also reports is vacuous on this corpus, and the
script now says so rather than printing a clean-looking pass.** The control
populates **zero** dimensional/parking fields across all 19 zones, so no
candidate can lose one — `0 field(s) lost` carried no information. This was
caught by running the gate against `fts_only`, an arm that collapses dimensional
recall to 0.06 and *still* reported zero fields lost.

So the honest statement of the gate's verdict is:

> No zone-profile regression was detected on the signals that have power here
> (unknown-zone flips, permitted-use counts). The field-level comparison could
> not have detected one either way, because `get_zone_profile` extracts no
> dimensional values from this corpus under **any** arm, including the shipped
> one.

That is a pre-existing gap in a different surface, not a cost of this change —
nothing regressed, because nothing was working. It does mean the dimensional
MRR drop above is unverifiable against the product surface it would most
plausibly affect, which is why it is called out rather than waved through. A
corpus that populates zone-profile fields is a prerequisite for using this as
the ship gate the ticket intends.

## Thresholds

The ticket requires "all dependent thresholds re-derived with tests". **There
are none left to re-derive**, and that is a finding rather than an omission.

Every live threshold used to be denominated in ladder units, and the load-bearing
one was `min(1.0, score / 40.0) >= 0.5` gating zone-profile fields. **ABS-493
removed it** — the gate now reads an ordinal `EvidenceClass` derived from
*where* a query's terms land (citation path, label, body), never from how many
land or what they sum to. That is precisely what makes this change cheap to
ship: the blend rescales the text side back onto the ladder's own scale, so
`_merge_channel_scores` sees the magnitudes it always saw, and no downstream
consumer reads a score as a magnitude at all.

`_TEXT_CHANNEL_THRESHOLD` still applies to the ladder, unchanged and upstream
of the blend.

## What shipped

`mcp/bylaw_retrieval/retrieval/service.py`:

* `_fts_channel_scores` — `ts_rank_cd` over the indexed tsvector, scoped by the
  same `_fragment_scope_statement` the text channel uses, disjunctive tsquery.
  Returns `{}` off Postgres, so the sqlite suites still measure the ladder alone.
* `_blend_fts_into_text` — max-normalise both sub-channels, weight 50/50,
  rescale onto the ladder's top score, carry `discriminating` through.
* `FTS_VECTOR_SQL` / `fts_or_query` — module constants, imported by the eval
  harness rather than restated in it, so the arm that gets measured and the
  channel that gets served cannot drift into two different programs.

The blend happens in `search()` **before** fusion, not as a fourth peer channel,
because FTS is not independent of the ladder — both read the fragment's own
words. Paying an agreement bonus for a clause both matched would count one piece
of evidence twice and lift every verbose clause above the terse one stating the
standard. This is the same argument ABS-500 made for not paying text and table
a joint bonus.

Verified: the shipped implementation reproduces the `fts_hybrid_50` arm
**exactly** — 0.6618 / 0.6618 / 0.3388 when decided, 0.6857 / 0.6857 / 0.3384 on
the 2026-08-23 re-run and 0.6806 / 0.6736 / 0.3429 on the 2026-08-24 one, every
category matching in all three. That is the
harness's design premise (each arm is production's `search()` with one seam
swapped) discharged as a check rather than left as a hope, and
`web/e2e/functional/abs494-scoring-fusion-decision.spec.ts` asserts it holds.

## What to watch

* **dimensional MRR (0.130).** If `get_zone_profile` starts populating fields on
  a future corpus, re-run the zone gate *before* trusting it. Weight 0.60 is the
  ready-made retreat: +0.0588 recall with MRR 0.4003 and dimensional MRR 0.307,
  above the control's 0.284. The sweep is in `RESULTS.md`; this is a re-decision,
  not a tuning knob.
* **The label tier.** All 72 questions are `review_status: unreviewed`,
  agent-drafted. These numbers grade **ranking, never correctness**, and are
  never averaged with a golden pass rate. A human spot-check could move +0.1030.
* **`definition` is still the worst class at 0.33.** The hybrid helped it most
  in relative terms (0.08 → 0.33) precisely because definitions are ingested as
  PROSE with a NULL `citation_path` and the body tsvector is the only channel
  that can see them at all. There is more left there than anywhere else.
* **Baseline freshness.** `make check-retrieval-baseline` fires whenever the
  watched retrieval files move. It correctly caught this change; re-record with
  `make eval-retrieval-baseline EVAL_DB_URL=…` against the dev corpus.
