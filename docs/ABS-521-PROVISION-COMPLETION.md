# A provision arrives complete, or it is not evidence (ABS-521)

Sibling of [ABS-518-ZONE-SCOPE-EXCLUSION.md](ABS-518-ZONE-SCOPE-EXCLUSION.md)
and explicitly *not* the same defect. ABS-518 was cross-chapter ranking — an ER
table outranking the HR chapter for an HR-1 question — and its fix landed and
TC-027 now passes. TC-024 was run in the same pair, on the same code, and did
not move. This is what was left: sub-clause reachability inside a single
section, where the section and one child rank and a sibling child does not.

## The failure

TC-024 asked how large a garage-to-suite conversion could be at 1107 Lucknow
Street (ER-2). Two caps apply to a new accessory structure and they bind
together:

| provision | limit | zones |
|---|---|---|
| s.333(1)(a) | **60.0 m² footprint** | DD, DH, CEN-2, CEN-1, COR, HR-2, HR-1, ER-3, ER-2, ER-1, CH-2, CH-1 |
| s.333(1.5) | **93.0 m² floor area** | the same zones |

The advisor answered *"must not exceed 93.0 m² of floor area"* and never
mentioned 60. An owner told only the 93.0 figure can design a structure that
satisfies it and still fails the footprint cap — so this is a wrong answer, not
a thin one.

Five retrieval attempts, including a direct citation lookup, returned the same
two fragments:

| call | returned |
| -- | -- |
| `search_bylaw_evidence` "backyard suite accessory dwelling unit maximum size floor area ER-2" | `Part V > 333`, `Part V > 333 > (1.5)` |
| `search_bylaw_evidence` "backyard suite setback rear yard side yard separation from main building ER-2" | `Part V > 333`, `Part V > 333 > (1.5)` |
| `lookup_citation` `{"citation_path": "Part V > 333"}` | `Part V > 333` |
| `search_bylaw_evidence` "backyard suite maximum floor area square metres accessory structure dwelling" | `Part V > 333`, `Part V > 333 > (1.5)` |
| `search_bylaw_evidence` "accessory structure maximum height metres backyard suite" | — |

`Part V > 333 > (a)` is returned by none of them. The clause is in the corpus —
`citation_path_prefix="Part V > 333"` returns it verbatim — so this is
reachability, not ingest.

## Why no scorer change can fix it

Two facts, and they compound.

### 1. The clause has no topic words

s.333 is written as a stem plus limbs:

```
333 (1) Any new accessory structure shall have no restriction on the maximum
        size of its footprint, except:
  (a)   subject to Clause 333(1)(b), in any DD, DH, CEN-2, CEN1, COR, HR-2,
        HR-1, ER-3, ER-2, ER-1, CH-2, or CH-1 zone: 60.0 square metres; or
  (b)   in the Westmount Subdivision (WS) Special Area … 6.0 square metres
        within a front yard.
  (1.5) In any DD … zone, any new accessory structure shall not have a floor
        area greater than 93.0 square metres.
```

Every term a question would use — *accessory*, *structure*, *footprint*,
*maximum*, *size* — is in the stem. Strip the zone list and `(a)` is a number.
On the query it answers, scored by the shipped ranker:

| fragment | own text | + context | blended | rank |
|---|---|---|---|---|
| `Part V > 333` | 21.0 | 21.0 | 27.06 | **3** |
| `Part V > 333 > (1.5)` | 17.0 | 23.0 | 25.66 | **5** |
| `Part V > 333 > (a)` | 9.0 | 15.0 | 17.01 | **58** |
| `Part V > 333 > (b)` | 1.0 | 7.0 | 4.66 | 1107 |

`(1.5)` ranks because it is the one limb that states its own subject — *"any
new accessory structure shall not have a floor area…"*. `(a)` does not, and
cannot: no weight lifts a bare list item to rank 3 without lifting every bare
list item in the corpus with it. That is the same trade ABS-492 already paid
down when it moved bracketed container prose out of the citation-path haystack.

### 2. Its tree parent is not its section

`(a)` and `(b)` carry `citation_path` `"Part V > 333 > (a)"` and
`parent_fragment_id` **7874** — the heading *"Accessory Structure Footprint and
Area"* printed above s.333, a sibling of the section rather than the section:

```
[7847] PART       Part V, Chapter 19: Accessory Structures, Backyard Suite …
  [7873] SECTION    Part V > 332
  [7874] HEADING    Accessory Structure Footprint and Area
    [7876] CLAUSE     Part V > 333 > (a)     <- pathed under 333, parented here
    [7877] CLAUSE     Part V > 333 > (b)     <- likewise
  [7875] SECTION    Part V > 333
    [7878] SUBSECTION  Part V > 333 > (1.5)  <- pathed and parented consistently
```

Everything that walks `parent_fragment_id` therefore gets a different answer
from everything that reads `citation_path`. The context channel walks the tree,
so `(a)` inherits scope from a heading and never from the sentence it finishes;
`ancestor_chain` walks the tree, so the agent was shown a clause reading
"…60.0 square metres; or" with the sentence it completes nowhere in the payload.

### Blast radius

This is not one bad row. `scripts/audit_provision_parentage.py` measures it:

```
$ python scripts/audit_provision_parentage.py
fragments in scope         : 7100
  carrying a parent path   : 4030
  path and tree agree      :  412
  path and tree DISAGREE   : 2410
  parent path names nothing: 1208

operative clauses detached from their provision (the ABS-521 population): 1906
  1465  clause under heading
   441  subclause under heading
```

**1,906**, not 2,410. The other 482 are `section under part` — a section pathed
`Part V > 229` whose tree parent is the PART fragment `Part V, Chapter 9`, which
disagrees on the letter and agrees on the substance. The ticket's blast-radius
question — *"is this s.333, or every `(a)` clause?"* — answers **every**.

## The fix: completion, not ranking

`mcp/bylaw_retrieval/retrieval/provision.py` answers lineage by **citation
path** (the citable truth, which ABS-488 established) and completes a provision
in both directions:

* **downwards** — a section carries its own clauses, so `lookup_citation` on
  s.333 stops returning a sentence that ends on a colon;
* **sideways** — a clause carries its provision's *other* clauses.

Sideways is the one that matters here. On the footprint question the ranker
surfaces `(1.5)` and **not** the section, so completing downwards alone would
still have lost the 60.0 figure. Both arms are the same rule read from either
end, and a container (Part, Schedule, Appendix, heading) is never a provision —
one Part in the dev corpus has 297 direct path-children.

`RetrievalMatch.ancestor_chain` now walks the **union** of path and tree
lineage. The union rather than either alone: the heading really is what the
clauses were printed under and really does say what they are about, and s.333 is
the sentence they finish. A reader holding the page has both.

### Unconditional, not behind `include_context`

`CitationLookupRequest.include_context` defaults to **`False`**, and the call in
the TC-024 transcript passed nothing but the path. A completion gated on that
flag would have left the reported defect exactly where it was. The flag means
"drop the containers to save tokens"; an operative clause is not a container, it
is the other half of the sentence. Pinned by
`test_lookup_citation_defaults_carry_the_clauses`.

### Truncation is reported

`OPERATIVE_CLAUSE_LIMIT` is 12 — 96% of the dev corpus's 284 sections with
clauses fit; the largest has 60. When the cap bites, `operative_clauses_omitted`
carries the count and the compact payload adds a note naming the
`citation_path_prefix` call that reads the rest. A provision shown short reads
exactly like a provision that *is* short, which is the ABS-521 defect with a
different cause.

## What did not change: the ranking

Deliberately nothing. This is a payload guarantee, and the claim is checked
rather than asserted: re-recording `evals/retrieval/BASELINE.json` over the same
corpus, **zero of the 70 pre-existing questions changed their top-ten ranking**,
compared row by row rather than inferred from the totals. The headline moved
0.6857 → 0.6806 Recall@10 and 0.3384 → 0.3429 MRR entirely because the set
gained two questions: 48 hits over 70 became 49 over 72.

The fingerprint moved because the retrieval package did, which is exactly what
[ABS-502](../scripts/check_retrieval_baseline.py) exists to force.

## Where each guarantee is pinned

| guarantee | pinned by |
|---|---|
| `lookup_citation` on a section returns its clauses | `tests/bylaw_retrieval/test_operative_clauses.py::test_lookup_citation_on_the_section_returns_its_operative_clauses` |
| …with `include_context` at its default `False` | `…::test_lookup_citation_defaults_carry_the_clauses` |
| both caps reach a reader asking about size | `…::test_both_caps_reach_a_reader_asking_about_size` |
| a ranked subsection carries its siblings | `…::test_a_ranked_subsection_carries_its_siblings` |
| the ancestor chain reaches the stem | `…::test_the_ancestor_chain_reaches_the_stem_the_clause_finishes` |
| a Part is never completed | `…::test_a_container_is_never_completed` |
| truncation is counted, not swallowed | `…::test_a_capped_provision_says_how_much_it_dropped` |
| the clauses survive `compact_match` | `tests/advisor/chat/test_tools.py::test_operative_clauses_survive_the_compact_projection` |
| the tool descriptions tell the model to read them | `tests/advisor/chat/test_tools.py::test_lookup_citation_description_says_what_a_section_returns` |
| the blast-radius number | `tests/scripts/test_audit_provision_parentage.py` |
| the ranking did not move | `evals/retrieval/BASELINE.json`, and `web/e2e/functional/abs521-accessory-structure-caps.spec.ts` |

## The eval entries, and how to read them

`RQ-D21` (footprint, 60.0) is a **recorded miss**, and it is supposed to be. The
harness grades ranking, `(a)` is unreachable by rank, and the entry is the
standing record of that — so a future change that flips it to a hit reads as a
genuine improvement rather than a re-recording. `RQ-D22` (floor area, 93.0) is
its counterweight and passes both before and after: the asymmetry the ticket is
about, measured rather than asserted.

Neither entry demonstrates the fix. The harness cannot: it reads
`fragment_id`s off a ranking, and completion delivers a clause the ranking does
not contain. Changing what the harness counts as "retrieved" would have raised
every historical number at once and broken comparability with every baseline
since ABS-486, which is a worse trade than leaving one honest miss on the board.

## What this does not fix

* **The corpus.** 1,906 clauses still hang off the wrong parent. Retrieval is
  now robust to it, and the audit script exists so that a later ingest fix can
  be measured. Re-parenting them is an ingest change and belongs with DM-11's
  hierarchy work, not here.
* **`(a)`'s rank.** It is still 58th on the query it answers. Everything above
  is about making that not matter.
* **TC-024 itself.** The case is not in this repo — `evals/golden/golden_cases.json`
  holds six cases (TC-001, 002, 008, 009, 012, 014, all `awaiting a qualified
  human`) and `evals/regional_centre_test_prompts.json` holds TC-001…TC-020.
  Neither the TC-024 prompt nor the `evals/runs/iso-anthropic-2/` transcript the
  ticket quotes is committed here, and `evals/golden/README.md` is explicit that
  a model may not supply the attestation that would let a case be graded. So
  "TC-024 passes `verify_golden_cases.py`" is a human step, and the retrieval
  behaviour it depends on is pinned above instead.
