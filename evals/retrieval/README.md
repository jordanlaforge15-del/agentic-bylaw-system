# The retrieval eval — Recall@10 and MRR for `search_bylaw_evidence`

This directory holds the first falsifiability artifact for *retrieval* in this
project. Before it, no Recall@k or MRR number existed anywhere in the repo, so
a change to scoring, channel fusion, chunking or citation paths could only be
judged by whether an end-to-end answer still read well — a signal that mixes
the retriever's behaviour with the model's and moves for reasons neither of
them controls. Everything here measures the retriever alone: one
`search_bylaw_evidence` call per question, graded on the ranking it returns.

| | |
|---|---|
| Question set | `queries.json` — 70 labelled questions |
| Harness | `scripts/eval_retrieval_recall.py` |
| Baseline | `BASELINE.json` |
| Regenerate | **`make eval-retrieval-baseline`** |
| Freshness gate | `scripts/check_retrieval_baseline.py` / `make check-retrieval-baseline` |
| Unit tests | `tests/scripts/test_eval_retrieval_recall.py`, `tests/scripts/test_check_retrieval_baseline.py` (no DB) |
| Offline guards | `web/e2e/functional/abs486-retrieval-eval.spec.ts`, `web/e2e/functional/abs502-retrieval-baseline-freshness.spec.ts` |

## Tier: agent-drafted, pending human spot-check

**This is not the golden tier.** `evals/golden/README.md` exists because a
generated expectation that passes only establishes that the advisor agrees with
what a model guessed; the golden subset is the one artifact in this project
authored by a qualified human. The same discipline applies here, and the
`provenance` header at the top of `queries.json` states the tier in the file
itself so it cannot be quoted out of context.

What that means in practice:

* Every number derived from this set describes **ranking**, never correctness.
  "Recall@10 = 0.10" says the retriever put a usable fragment in the top ten for
  10% of these questions. It says nothing about whether an answer built on that
  fragment would be right under the by-law.
* These numbers are **never summed or averaged with a golden pass rate**, and
  never reported as deploy evidence. They gate ranking work — DM-11, DM-15,
  DM-16, DM-17 — and nothing else.
* `review_status` is `unreviewed`. A human spot-check flips it; a model may not.

### Spot-checking an entry

Read the question, open every fragment named in `acceptable`, and ask two
things only:

1. Could a competent reader answer this question from at least one of them?
2. Is there an obviously better clause that should have been listed and wasn't?

A label that fails (1) makes the retriever look worse than it is; a label that
fails (2) makes it look better. Both are worth fixing; neither requires
re-deriving the by-law. Record the outcome in `provenance.review_status`.

## What the metrics mean

The labels are a set of **acceptable** fragments, not a single right answer: a
by-law standard is usually stated once but reachable through the section, a
subsection and sometimes a table row, and any of those lets a reader answer.
Three numbers fall out of that:

* **`recall_at_k`** (headline) — the share of questions with *at least one*
  acceptable fragment in the top *k*. A hit rate.
* **`set_recall_at_k`** — the mean of `|acceptable ∩ topk| / |acceptable|`.
  Reported beside the hit rate rather than instead of it, because it drops when
  the ranking surfaces one of three acceptable fragments, which is not a defect.
  Treat it as a sensitivity signal.
* **`mrr`** — mean reciprocal rank of the first acceptable fragment, 0 on a
  miss. Answers "how far down does the agent have to read", which a hit rate
  cannot.

Beside them, and deliberately not among them, `latency_ms` records wall-clock
for the same `search` call the metrics grade (ABS-492). A ranking change can
always buy recall by scoring more per fragment, and the number that says what
that cost has to sit next to the number that says what it bought. It is
host-dependent, it is never used to fail a run, and it is the one block of
`BASELINE.json` that moves between two runs over an unchanged corpus.

## Coverage

70 questions across six categories, all six required by the loader (a set that
silently loses a category fails to load, which a count check would not catch):

| Category | n | What it stresses |
|---|---|---|
| `dimensional` | 20 | Setbacks, lot coverage, heights. Sibling zones state these in near-identical language, so this is where a zone-blind ranker shows. |
| `permitted_use` | 14 | "Which zones permit X", the permission tables, and lay phrasings ("can I run a home office"). |
| `definition` | 12 | Part XVII terms. Most are ingested as PROSE with a **NULL citation_path**, so they are invisible to every path-based route — worth measuring on its own. |
| `zone_anchored` | 10 | Questions naming a zone but no dimension; targets are chapter headings and zone-scoped sections. |
| `spatial` | 6 | Overlay lookups driven by a literal `location.geometry` point. |
| `citation_lookup` | 8 | "Section 198", "Table 15" — the gap between how a user writes a citation and how the ingest stores it (`Part X > [Table 15]`). |

Two corpora are covered: the Regional Centre LUB and the Halifax Mainland LUB.
Several pairs are deliberate — `RQ-P11`/`RQ-P13` ask near-identical hen-keeping
questions against the two by-laws, and `RQ-D15`/`RQ-C01` reach for the same
clause by subject and by citation.

## Labels are content-addressed, not id-addressed

`source_fragment.id` is a sequence value that a re-ingest reassigns wholesale,
so a query set labelled with raw ids would silently start grading the wrong
clauses the first time the corpus is rebuilt. Every label is therefore an
**anchor**:

* `citation_path` — unique per document by database constraint.
* `text_prefix` — an exact leading substring, for the fragments the ingest
  leaves without a path (most definitions, all chapter headings).

`fragment_ids` on each entry is a *snapshot* of resolving those anchors against
the corpus recorded in `BASELINE.json`. The harness re-resolves every anchor on
every run and refuses to score if the snapshot has drifted. An anchor that
resolves to **zero or more than one** fragment is a hard error, not a miss: a
label that has quietly stopped pointing at its clause would otherwise be
indistinguishable from a retrieval regression, and this artifact must never
fabricate one of those.

After a re-ingest:

```bash
python scripts/eval_retrieval_recall.py --refresh-fragment-ids   # review the diff
make eval-retrieval-baseline                                     # re-measure
```

The re-measured baseline is a **new measurement, not a regression** against the
old one — `BASELINE.json` records the corpus fingerprint (document ids, by-law
names, fragment counts) precisely so the two are not confused.

## Determinism

Two rules make a re-run byte-identical **in every field but `latency_ms`**,
which measures the host and is expected to differ:

1. **No geocoder.** Spatial questions carry a literal `location.geometry`
   point, which short-circuits address resolution before it can reach the
   in-database civic dataset or the Google fallback. A baseline that moved with
   a network round-trip would be worthless.
2. **Deterministic tie-breaks.** `_merge_channel_scores` sorts by
   `(-score, fragment_id)`, so ties break on the primary key rather than on set
   iteration order. This matters more than it sounds: at the current scoring the
   top of most rankings is a block of tied scores.

## Regenerating the baseline, and the gate that makes you

```bash
make eval-retrieval-baseline
```

That is the whole regeneration path: it runs the harness against the dev corpus,
rewrites `BASELINE.json`, and stamps into it a fingerprint of every file that can
move a ranking. Override the database with
`make eval-retrieval-baseline EVAL_DB_URL=…`; it defaults to the dev DSN rather
than the ambient `DATABASE_URL` for the reason in **Running it** below.

```bash
make check-retrieval-baseline        # no database needed
```

fails when the retrieval code has moved and this baseline has not. It is run by
`pytest` (`tests/scripts/test_check_retrieval_baseline.py::TestThisRepo`) and by
the Playwright suite (`abs502-retrieval-baseline-freshness.spec.ts`), so a
retrieval-affecting merge either re-records the baseline or is stopped — which
is what did not happen at ABS-478 and ABS-488, and cost ABS-494 its control.

### What the verdict is computed from

Not the file's commit date. The obvious check — "does `BASELINE.json` predate
the newest commit touching `mcp/bylaw_retrieval/retrieval/**`?" — would have
fired on ABS-500's `eb613cf`, which touched `service.py` to reword a comment. A
gate that fails on a reworded comment gets acknowledged reflexively within a
week, and an acknowledgement habit is this same failure one level up.

So the verdict comes from the **content** of the watched files, normalised so
that comments, docstrings, blank lines and JSON formatting cannot move it:

| Watched | Because |
|---|---|
| `mcp/bylaw_retrieval/retrieval/**.py` | scoring, fusion, zone binding, the table channel |
| `src/layer1/pipeline/hierarchy.py` | the ancestor chain context and binding walk |
| `src/layer1/pipeline/{citation,corpus}_repath.py`, `scripts/repath_citation_paths.py` | ABS-488 moved the baseline from here |
| `scripts/eval_retrieval_recall.py` | the harness defines what the number means |
| `queries.json` (its `queries` key) | the graded questions and their labels |

The retrieval package is watched by glob, so a channel added tomorrow is watched
the day it lands. `queries.json` is watched by its `queries` key only, so a human
spot-check flipping `review_status` does not fire the gate. The fingerprint is
interpreter-independent — 3.11 (CI), 3.12 and 3.14 agree byte-for-byte.

Being content-based rather than commit-based buys three things a commit
comparison cannot: prose-only edits never fire, *uncommitted* edits do (so the
gate answers before the commit rather than after the merge), and a rebase or a
squash does not invalidate it.

### Acknowledging drift instead of re-measuring

```bash
python scripts/check_retrieval_baseline.py --acknowledge "why this cannot move a ranking"
```

This is the narrow escape hatch for a change that genuinely cannot move a
ranking when the corpus is not to hand. It writes the decision into
`BASELINE.json` — so it lands as a reviewable line in the diff, not as a flag in
someone's shell — and it is pinned to the exact fingerprint it was granted for,
so the next edit to any watched file fails the gate again. The next
regeneration drops it.

## Running it

```bash
# default: dev database from DATABASE_URL, k=10, writes BASELINE.json
python scripts/eval_retrieval_recall.py

# print only
python scripts/eval_retrieval_recall.py --dry-run

# explicit database, sensitivity check at a different cut-off
python scripts/eval_retrieval_recall.py \
    --database-url postgresql+psycopg://layer1:layer1@localhost:5432/layer1 \
    --k 25 --dry-run
```

**Pass `--database-url` explicitly from a worktree shell.** The default comes
from settings, which a worktree's `PG_PORT` / `DATABASE_URL` exports rewrite —
so a shell set up for a parallel e2e run points the harness at that worktree's
*ephemeral* e2e database (empty corpus, every anchor unresolvable) rather than
at the dev corpus this baseline was measured against.

The harness needs a corpus. The unit test does not: it drives the same pure
scoring and loading code through a stubbed retrieval service, so the metric
arithmetic, the category/duplicate/threshold validation and the
ambiguous-anchor failure are all covered without a database.

## The baseline, and how to read it

`BASELINE.json` currently records **Recall@10 = 0.6857, set-Recall@10 = 0.6857,
MRR@10 = 0.3384** over 70 questions against the two-document dev corpus.

### The floor it is measured against

The first measurement, at ABS-486, was **Recall@10 = 0.1029, MRR@10 = 0.0667**,
with `dimensional`, `permitted_use`, `definition` and `zone_anchored` all at
**0.00** and only `spatial` (1.00) working. Probing the misses showed the
acceptable fragments were in scope and scored — they simply ranked far down
(the ER-3 side-setback clause at rank 573 of 6,935) — so it was a *ranking*
failure, not a scoping or ingest failure. That is what the artifact was for.

Two changes have moved it since, and the middle number matters when reading the
jump: ABS-478 and ABS-488 landed after that measurement and lifted it to
**0.1618** without anyone re-recording it, so the honest before/after for
ABS-492 is **0.1618 → 0.4412**, not 0.1029 → 0.4412.

That silent drift is what **`make check-retrieval-baseline`** (ABS-502) now
prevents: from here on a retrieval-affecting change that does not re-record this
file fails `pytest` and the Playwright suite. The gate was added against a
baseline that turned out to be numerically current — re-measuring at ABS-502
reproduced every figure below exactly, and only the host-dependent `latency_ms`
block moved — but "it happened to be current" is precisely the property that
cannot be relied on twice.

### What ABS-492 changed

Two things, one channel — see `mcp/bylaw_retrieval/retrieval/context.py`.

*Container prose stopped earning path weight.* ABS-488 repathed clauses onto
the container that scopes them, which folded the container's whole sentence
into the child's `citation_path` as a bracketed segment. Every token of that
sentence was then banking +12 on the leaf, and the phrase +35 — three and nine
times what the fragment actually stating the rule earns for its own text. So
`Part V > 135 > [The maximum required side setback for any main building shall
be] > (a)`, whose own text is "(a) on lots located within Downtown Halifax
Central Blocks…", outranked section 229, which states the standard. Only the
structural steps of a path earn path weight now.

*The ancestor chain became readable.* 2,968 of the corpus's 7,100 fragments
carry no `citation_path` at all, so their containers are the only place their
scope is stated. Bracketed path prose and every ancestor's heading and text now
form one context channel at +2 a token, for tokens the fragment does not
already state itself, dropping any token that matches more than 15% of the
request's own scope as describing the corpus rather than the clause.

Per-category, before → after: `zone_anchored` 0.30 → **1.00**, `permitted_use`
0.07 → **0.64**, `citation_lookup` 0.125 → **0.375**, `definition` 0.00 →
**0.083**, `dimensional` 0.00 → **0.056**, `spatial` 1.00 → 1.00. Nothing
regressed. p95 for one `search` call went 358ms → 364ms.

Be precise about which half did the work: on **this** query set the path
re-weighting accounts for essentially the whole lift, and zeroing the ancestor
weight changes Recall@10 by one question. That is a fact about the labels, not
a verdict on the mechanism — the set anchors its labels on sections and
definitions, so it has almost no way to reward surfacing a stripped list item,
which is the case the ancestor channel exists for. The unit that does grade it
is `tests/bylaw_retrieval/test_provision_in_context.py`.

### What ABS-500 changed: 0.4412 → 0.5588

`dimensional` **0.056 → 0.500** (1/18 → 9/18). Every other class holds its
Recall@10 exactly, and no question that passed before fails after. Overall
MRR@10 0.2775 → 0.3077.

ABS-500 was written as "tables are never independently ranked", and it added
the table channel that observation calls for — `source_table_cell` is now
scored and fused directly, cited through the provision that introduces its
table (`docs/ABS-500-TABLE-CHANNEL.md`). But the table channel is *not* what
moved this number, and the difference is worth recording because it is a fact
about the labels:

**17 of the 18 dimensional questions are answered by a prose section, not a
table.** What those sections have in common is that they never name the zone —
the by-law declares it once, in the chapter heading ("Part V, Chapter 9: Built
Form and Siting Requirements within the ER3, ER-2, and ER-1 Zones") — while
dozens of unrelated clauses list the same zone among *abutting* land. The
scorer paid +4 for the passing mention (own text) and +2 for the governing
chapter (inherited context), so a landscaping clause about abutting land
outranked the section that states the ER-1 standard. That inversion is why
every arm of ABS-494's fusion matrix measured ~0.06: the evidence was backwards
*inside* a channel, and no weighting of channels against each other can fix
that.

`mcp/bylaw_retrieval/retrieval/binding.py` re-states the rule: a clause
governed by a container that declares the query's zone states that zone as
surely as if it carried it in its own citation path, and scores at the
citation-path rung (+12). The declaring container is bound to its own zone too,
which is what keeps a question asking *for the chapter* from being buried under
the sections it scopes — without that, `zone_anchored` measured 1.00 → 0.90.

The cost, stated plainly: **MRR fell in two classes** while their Recall@10
held — `permitted_use` 0.476 → 0.318, `zone_anchored` 0.678 → 0.608. Binding
lifts every clause in the right chapter, so on a query whose answer is *not* in
that chapter the answer sits lower in a top-10 it still reaches. Overall MRR
still rose, carried by `dimensional` 0.008 → 0.284. p95 for one `search` call
went 364ms → 460ms on the same host, from the added ancestor walk; the table
index is built once and cached per document scope.

### What ABS-494 changed

**0.5588 → 0.6618** (+0.1030), MRR 0.3077 → 0.3388. A Postgres full-text
ranking now joins the text channel, blended 50/50 with the path/context ladder
(`_fts_channel_scores`, `_blend_fts_into_text`).

The issue was posed as "RRF vs FTS-hybrid vs keep" and expected the uncalibrated
`max()` fusion to be the defect. It was not: **RRF fusion measured worse than
the shipped rule** (0.5441, −0.0147) and was rejected. The text channel's
coverage was the defect. Full reasoning, including the weight sweep that shows
this is a plateau rather than a constant fitted to 68 unreviewed labels, is in
[`docs/decisions/ABS-494-SCORING-FUSION-DECISION.md`](../../docs/decisions/ABS-494-SCORING-FUSION-DECISION.md);
the 17-arm matrix is in [`experiments/RESULTS.md`](experiments/RESULTS.md).

The cost, stated plainly: **`dimensional` MRR halved** (0.284 → 0.130) while its
recall rose (0.50 → 0.56), and `zone_anchored` recall went 1.00 → 0.90. Two
questions were lost, both of which the control ranked 8th and 9th — bottom-of-
window marginals — against nine gained. p95 for one `search` call went 484ms →
576ms from the extra index-eligible query.

### What ABS-518 changed

**0.6618 → 0.6765 on the original 68 questions** (MRR 0.3388 → 0.3398), and
**0.6857 / 0.6857 / 0.3384 on the 70-question set this file now describes.**
Read the two the right way round: the first is the like-for-like effect of the
scoring change, the second is the current baseline over a set that also gained
two questions. They are not a before/after pair — the ablation behind the
first is in
[`docs/ABS-518-ZONE-SCOPE-EXCLUSION.md`](../../docs/ABS-518-ZONE-SCOPE-EXCLUSION.md).

Three ladder changes, each paying at a different rung: a chapter that declares
*other* zones than the query's is now debited rather than left neutral; the
citation-path rung scores only a path's structural segments, so the `a` in
every Halifax Mainland `Schedule A > …` path stopped banking a citation-strength
hit; and a hyphenated code's numeric tail (`HR-1` → `1`) no longer matches as a
free-standing token, which is what let an Eastern Residential table row labelled
`North End Halifax 1` answer an HR-1 question.

The set gained `RQ-D19`/`RQ-D20` (HR-1 side and rear setback). **Both already
passed at k=10 before the fix** — ranks 5 → 3 and 5 → 4. They are regression
guards, not reproductions of the defect, and their labels say so; a green
`RQ-D19` is not evidence the bug was caught. The reproduction lives in
`tests/bylaw_retrieval/test_zone_scope_exclusion.py`.

The 17-arm ABS-494 matrix was **re-derived** against this retriever rather than
left quoting a control that had moved — the failure mode ABS-502 exists to
prevent. Both of ABS-494's decisions survive unchanged; see the
*Re-measured under ABS-518* section of its decision doc.

### What is still broken, and why it is not a ranking problem

`definition` (12 questions) is still the worst class, and eight `dimensional`
questions still miss. Neither is reachable from the scorer:

* **`definition`** — Part XVII terms ingest as PROSE with a NULL `citation_path`
  *and* a parent pointing at an unrelated chapter, so there is no path to score
  and the ancestor chain supplies the wrong scope. That is a chunking and
  hierarchy defect for **DM-11**, not something a scorer can outrank.

  **ABS-494 partly refuted this.** The claim was too strong: no *path-based*
  route can reach these fragments, but their body text is indexed, and adding a
  full-text channel over it lifted `definition` from 0.08 to 0.33 without any
  ingest change. The class is still the worst of the six, and the hierarchy
  defect above is still real and still DM-11's — but "not reachable from the
  scorer" was a statement about the channels that existed, not about the
  fragments.
* **the remaining `dimensional` misses** split two ways. The Halifax Mainland
  by-law *has* zone-declaring headings ("R-1 ZONE: SINGLE FAMILY DWELLING
  ZONE") but the ingest left its tree flat, so those headings are not ancestors
  of the sections they scope and there is nothing to bind through — an ingest
  gap. The rest are questions whose zone is implied rather than named ("Does
  the by-law impose a maximum rear setback anywhere?"), which no zone binding
  can help.
