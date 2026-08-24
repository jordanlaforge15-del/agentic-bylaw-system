# Reading a chapter heading as evidence *against* a clause (ABS-518)

Companion to [ABS-500-TABLE-CHANNEL.md](ABS-500-TABLE-CHANNEL.md), which
introduced zone-scope binding. This documents the half that was missing, plus
two token-level defects that were feeding the same failure.

## The failure

TC-027 asked, for a property at 5261 Kent Street in the HR-1 zone: what is the
side setback, the rear setback, the streetwall height and the maximum height?
The advisor answered the height and the streetwall correctly and then wrote its
own heading — *"What I Could NOT Retrieve — Side and Rear Setbacks for the Main
Building"*:

> I was unable to locate the specific HR-1 side setback and rear setback
> provisions for main buildings. The searches returned:
> * ER-zone setback tables (Table 9 — not applicable to HR-1)
> * Rooftop feature setbacks (Table 8 — not what you need)
> * General definitions and stepback provisions

The provisions exist and are reachable by citation path: `citation_path_prefix=
"Part V > 198"` returns s.198(1)(f) "2.5 metres elsewhere" and s.199(1)(b)
"3.0 metres elsewhere" directly. So this was a ranking failure, not an ingest
gap.

**The advisor's handling was correct and no fix here should regress it.** It
stated the gap, named what it had retrieved, identified where the answer lives
and refused to guess a number. The defect was the retrieval.

## Three causes, not one

Each was paying at a different rung, and each is fixed at that rung.

### 1. "HR-1" split into a token that means nothing

`_tokenize` kept every hyphenated compound *and* its parts, so `HR-1` became
`hr-1`, `hr`, `1`. As a whole word, `1` matches a great deal of a by-law.
Concretely, it matches this, from the ER zones' side setback table:

```
Table 9: Minimum required side setbacks for Established Residential Special Areas
  | Established Residential Special Area | Minimum Required Side Setback |
  | Grant Street (GS)                    | 1.5 metres                    |
  | North End Halifax 1 (NEH-1)          | 1.5 metres on one side …      |
```

Table 9 carries **no axis bindings at all** — enrichment never resolved "North
End Halifax 1" to anything, because it is a special area, not a zone — so the
axis-binding half of ABS-500 had nothing to say about it. What addressed it was
the keyword path: `1` matched the row label, `side`/`setback` matched the
column label, and with both axes matched the table cleared the channel's
two-axis admission bar and ranked. Measured on the dev corpus, `Table 9` scored
38 as a table hit for an HR-1 side-setback query, offering the cell
`1.5 metres on one side` — a Grant Street / North End Halifax standard — as an
answer about HR-1.

Bare ordinals and single-letter stems no longer survive a hyphen split. The
compound survives, so every place the by-law actually writes `HR-1` still
matches; ordinary compounds like `single-family` are untouched, because a
by-law may genuinely write those parts apart. The length floor mirrors
`binding._MIN_ZONE_CODE_LENGTH`, which drops the same codes from the zone
vocabulary on the identical argument.

### 2. "Part", "Schedule" and "Appendix" are prose, not addresses

The per-token rung tested every query token against the whole stored
`citation_path` and paid **+12** for a hit — the citation rung, three times what
a section earns for stating the standard in its own text. The Halifax Mainland
by-law paths all begin `Schedule A > …`, so the indefinite article in an
ordinary question banked +12 on roughly 4,000 fragments. That is how an
amendment stamp —

```
Schedule A > 31 > 38BI(1)
  38BI(1) Oct 4/16 Nov 26/16 Case Plan Dutch Village Road - Added 38BJ(1) …
```

— came back **first**, at 65.0, for "What is the minimum required side setback
for a main building in the HR-1 zone?", with s.198 fifth.

Locator segments are now stripped for the per-token rung only. The phrase rungs
(exact path +100, substring +35) keep the full path, because `Part V > 198` is
a citation someone would type.

### 3. A heading that names other zones was silent

ABS-500 credited a clause **+12** for sitting under a container declaring the
query's zone. It said nothing about one sitting under a container declaring
*other* zones. That gap matters because the Regional Centre states the same
rule shape once per built-form chapter, over different numbers, and no section
names its own zone:

```
Part V, Chapter 7: … within the HR-2 and HR-1 Zones
  198 (1) … the minimum required side setback for any main building shall be:
Part V, Chapter 9: … within the ER3, ER-2, and ER-1 Zones
  229 (1) … the minimum required side setback for any main building shall be:
```

On words alone an HR-1 question matches s.229 exactly as well as s.198. The
binding breaks that tie in the HR chapter's favour — but only by +12, and the
ER side carries extra surface the HR side does not: `Part V > [Table 9]`, a
prose fragment whose text *is* the caption "Minimum required side setbacks for
Established Residential Special Areas", which repeats most of the question.

A heading that names ER and not HR is a positive statement that its sections do
not govern HR-1. It is now debited at the same structural rung the binding is
credited at, **-12 against +12**, so an off-chapter clause lands a full rung
below an on-chapter one rather than merely losing a tie-break.

Three properties keep this from overreaching:

* **Silence is not adversity.** `containers_excluding` is the complement of
  `containers_declaring` over the *declaring* containers only. `Part V,
  Chapter 1: General Built Form and Siting Requirements` names no zone, so it
  is in neither set and moves nothing — it does govern HR-1, and the by-law
  never says otherwise.
* **Binding wins any conflict.** Nesting can put a clause under both a
  declaring and an excluding container; the one that declares the zone is the
  specific statement.
* **It is symmetric.** Asked about ER-1, the same corpus answers with the ER
  chapter, and Table 9 — which really is that chapter's table — ranks for the
  zones it governs.

The debit applies to the table channel's anchors too. A table is cited through
the provision that introduces it, and that provision sits in a chapter like any
other; `Table 9` is off-chapter for HR-1 for exactly the reason s.229 is. This
is the only zone signal available for a table whose axes are special-area names
rather than zone codes — precisely the table that mis-ranked.

## Measured effect

Dev corpus (documents 4 and 5, 7,100 fragments), `evals/retrieval/queries.json`.

On the **original 68 questions**, like for like:

| | before | after |
|---|---|---|
| Recall@10 | 0.6618 | 0.6765 |
| MRR@10 | 0.3388 | 0.3398 |

One question gained (RQ-F09), none regressed. The committed baseline now
records 70 questions at Recall@10 0.6857 / MRR 0.3384 — the headline MRR is
slightly lower than the 68-question figure because the two new questions enter
at ranks 3 and 4 and pull the mean down, not because anything got worse.

Ablated one change at a time; all three earn their keep:

| arm | Recall@10 (68q) | MRR |
|---|---|---|
| all three | 0.6765 | 0.3398 |
| without the chapter debit | 0.6765 | 0.3366 |
| without the locator strip | 0.6618 | 0.3399 |
| without the tokenizer fix | 0.6765 | 0.3346 |

The debit's aggregate effect is small and its specific effect is the point: the
other two changes *promote* the ER Table 9 caption fragment into the top ten
for an HR-1 query (rank 16 of 25 without the debit, absent with it). Fixing
(1) and (2) without (3) would have traded one wrong-chapter neighbour for
another.

On the failing question itself, s.198 moved from rank 5 to 3 and s.199 from 20
to 14; on tighter phrasings ("HR-1 side setback and rear setback main
building") the pair now sits at 2 and 3.

### The ABS-494 matrix was re-derived, not reconciled

Moving the ladder moves the control that
[`docs/decisions/ABS-494-SCORING-FUSION-DECISION.md`](decisions/ABS-494-SCORING-FUSION-DECISION.md)
was argued against, and
`web/e2e/functional/abs494-scoring-fusion-decision.spec.ts` fails the moment it
does — by design. That spec exists because ABS-494's *first* run shipped a
conclusion measured against a retriever `dev` had already overtaken, and the
only honest answer to it is to re-measure, never to edit the numbers into
agreement.

So all 17 arms were re-run over the 70-question set against this retriever
(`python scripts/eval_retrieval_experiment.py --database-url …`), rewriting
`evals/retrieval/experiments/RESULTS.md` and every `arms/*.json`. **Both of
ABS-494's decisions survive**: `fts_hybrid_50` is still the best arm (0.6857,
reproducing `BASELINE.json` to the digit and to every category), and RRF is
still refuted, its best arm 0.0428 behind. The weight sweep keeps its shape and
its peak. Details and the two findings the re-run surfaced are in that doc's
*Re-measured under ABS-518* section.

## Coverage

* `tests/bylaw_retrieval/test_zone_scope_exclusion.py` — the reproduction. It
  seeds both chapters with their near-identical prose, the real Table 9 with
  its real row labels and no axis bindings, and a zone-silent general chapter.
  Five of its eight tests fail without the fix; the other three are the
  regression guards (symmetry, the table still being reachable by a question
  that addresses it, and silence staying silent).
* `tests/bylaw_retrieval/test_score_fragment_tokens.py` — the token rungs.
* `evals/retrieval/queries.json` RQ-D19 / RQ-D20 — the HR-1 side and rear
  setback pair. **These do not reproduce the defect**: s.198 ranked 5th before
  the fix and 3rd after, so they passed at k=10 either way. They exist to stop
  a future ranking change from pushing the pair out of the top ten. Their
  labels say so, so a passing RQ-D19 is not misread later as evidence the bug
  was caught there.

## The sibling that is *not* this bug: s.333(1)(a)

ABS-518 asked whether TC-024's missing 60.0 sq m accessory footprint cap shares
the cause. **It does not.** Investigated on the dev corpus:

```
Part V, Chapter 19: Accessory Structures, Backyard Suite Uses, and Shipping Containers
  333 (1) Any new accessory structure shall have no restriction on the maximum
          size of its footprint, except:
    (a) … in any DD, DH, CEN-2, CEN1, COR, HR-2, HR-1, ER-3, ER-2, ER-1, CH-2,
        or CH-1 zone: 60.0 square metres; or
```

Chapter 19 declares no zone, so neither the binding nor the debit touches this
subtree — the zone-scope mechanism is not involved either way. Scored against
"What is the maximum footprint of an accessory structure in the HR-1 zone?":

| fragment | score | tokens it matched |
|---|---|---|
| `Part V > 333` | 25.0 | accessory, footprint, maximum, of, structure, the |
| `Part V > 333 > (a)` | 13.0 | hr, in, zone |
| `Part V > 333 > (1.5)` | 25.0 | accessory, hr, hr-1, in, structure, zone |

Nothing in the subtree reaches the ~34 needed for the top twenty. The cause is
that **the question is answered by a parent and a child jointly and by neither
alone**: the stem carries "maximum … footprint" and the clause carries the
number and the zone list. `_CONTEXT_TOKEN_SCORE` exists for this and pays +2
per inherited token against the +4 own-text rung, which is not enough to close
a nine-point gap.

That is a split-provision scoring problem, not a wrong-chapter one. It wants
its own ticket and its own measurement; the fix here neither helps nor hurts
it.
