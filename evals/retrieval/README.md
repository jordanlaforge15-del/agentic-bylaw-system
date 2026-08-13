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
| Question set | `queries.json` — 68 labelled questions |
| Harness | `scripts/eval_retrieval_recall.py` |
| Baseline | `BASELINE.json` |
| Unit test | `tests/scripts/test_eval_retrieval_recall.py` (stubbed service, no DB) |
| Offline guard | `web/e2e/functional/abs486-retrieval-eval.spec.ts` |

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

68 questions across six categories, all six required by the loader (a set that
silently loses a category fails to load, which a count check would not catch):

| Category | n | What it stresses |
|---|---|---|
| `dimensional` | 18 | Setbacks, lot coverage, heights. Sibling zones state these in near-identical language, so this is where a zone-blind ranker shows. |
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
python scripts/eval_retrieval_recall.py                          # re-measure
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

`BASELINE.json` currently records **Recall@10 = 0.4412, set-Recall@10 = 0.4412,
MRR@10 = 0.2775** over 68 questions against the two-document dev corpus.

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

### What is still broken, and why it is not a ranking problem

`dimensional` (18 questions) and `definition` (12) remain near zero, and
probing them shows why neither is reachable from the scorer:

* **`dimensional`** — `_tokenize` splits "ER-3" into `er-3`, `er`, `3`, and the
  bare `3` is a genuine word-boundary hit on any path ending `(3)`, worth +12.
  Chapter headings also write the zone unhyphenated ("ER3"), which `er-3` never
  matches and `er` cannot reach across a word boundary. ABS-478 explicitly left
  the tokenizer's zone handling to **DM-16**; this is that work, not this one's.
* **`definition`** — Part XVII terms ingest as PROSE with a NULL `citation_path`
  *and* a parent pointing at an unrelated chapter, so there is no path to score
  and the ancestor chain supplies the wrong scope. That is a chunking and
  hierarchy defect for **DM-11**, not something a scorer can outrank.
