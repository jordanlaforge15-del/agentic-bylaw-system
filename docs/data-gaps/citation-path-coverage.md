# Data gap: 720 citable clauses have no citation path

**Raised by:** ABS-461, DoD 6 (*"The NULL-`citation_path` population is
characterised: a written breakdown of how many are legitimately unpathed vs.
citable-but-missing. Fix the citable-but-missing class, or file a follow-up
with the count if it is large."*)
**Status:** follow-up ticket — the count is large (16.6% of the document) and
the fix is a parser change with corpus-wide blast radius, not something to
land inside a page-break repair.
**Measured:** 2026-08-11, dev Postgres, `document_id=4` (HRM Regional Centre
Land Use By-law), after the ABS-461 page-break repair.

Reproduce with:

```
python scripts/audit_citation_path_coverage.py --document-id 4
```

## The breakdown

| Class | Count | % of doc |
|---|---:|---:|
| `citation_path` set | 1,872 | 43.2% |
| **`citation_path` NULL** | **2,465** | **56.8%** |
| ├─ legitimately unpathed (no `citation_label`) | 1,745 | 40.2% |
| └─ **citable but missing** | **720** | **16.6%** |

So the headline "57% have no path" resolves to: about 40 points of it is
correct, and about 17 points is a defect.

### Legitimately unpathed — 1,745

| `fragment_type` | Count |
|---|---:|
| `list_item` | 743 |
| `heading` | 568 |
| `prose` | 433 |
| `footnote` | 1 |

None of these carry a `citation_label`, because nothing in the text asserts
one. Headings, body prose and footnotes are not citable provisions and should
stay unpathed.

**Caveat on the `list_item` count.** A bylaw's auto-numbered subsections
frequently reach the parser as bare list items with the number stripped by the
PDF renderer — fragment 7134 ("Underground parking structures are not required
to have a minimum side setback…") is subsection 198(2) in the published bylaw,
but nothing in the extracted block says "(2)". Some unknown share of these 743
is therefore citable-in-the-bylaw despite being uncitable-from-the-database.
Sizing that needs the source PDF rather than the database, so it is called out
here rather than counted. It is a separate problem from the one below.

### Citable but missing — 720

Every one of these has a `citation_label` **and** a
`metadata_json.duplicate_citation_path` recording the path it would have had.
`_clear_duplicate_citation_paths` (`src/layer1/pipeline/hierarchy.py`) blanked
it because two or more fragments computed the identical path, and the
`uq_fragment_citation_path` constraint permits only one. The count of
fragments with a label but *no* recorded collision is **zero** — the collision
rule accounts for the entire class.

| `fragment_type` | Count |
|---|---:|
| `clause` | 680 |
| `part` | 32 |
| `subclause` | 8 |

275 distinct paths are collided on. The two shapes:

**1. Clauses lose their subsection.** Section 9 has two subsections, each with
its own clauses (a) and (b). Both compute
`Part I > 9 > [Development Permit Exemptions] > (a)`, because the path carries
the *heading* the clause sits under, not the subsection that scopes it:

```
5453  (a)  would be 'Part I > 9 > [Development Permit Exemptions] > (a)'
           "(a) accessory structures that are 20.0 square metres of floor area or …"
5472  (a)  would be 'Part I > 9 > [Development Permit Exemptions] > (a)'
           "(a) uncovered structures less than 0.6 metre in height, such as balcon…"
```

These are the `9(a)`, `9(b)`, `120(a)`, `120(b)` cases the ABS-461 ticket
named. The worst offender is `Part X > 499 > (61.5) > (a)`, computed by 14
different fragments.

**2. Parts lose their chapter.** All eight of Part I's chapter headings parse
as label `Part I`, so all eight collide:

```
5421  "Part I: Administration"
5422  "Part I, Chapter 1: General Administration"
5450  "Part I, Chapter 2: Development Permit"
…
```

20 fragments collide on `Part V` alone.

## Why this is not fixed in ABS-461

The fix is to put the missing discriminator back into the path — the
subsection for clauses, the chapter for parts — which changes the citation
path of a large share of the corpus, not just the collided ones. That means:

- every stored `citation_path` shifts shape, so `lookup_citation` behaviour,
  the ABS-261 suggestion ranking, cached answers and any recorded citation in
  `answer_log` all move with it;
- dev and prod both need a repath migration, and unlike ABS-461's four-fragment
  splice this one touches thousands of rows;
- the collision rule itself needs revisiting: silently blanking a path is what
  turned a *naming* bug into an *unreachable clause*, and a disambiguating
  suffix would be a better failure mode than a NULL.

That is a ticket's worth of work with its own migration and its own evaluation,
and bundling it into a page-break repair would make both harder to review.

## Proposed follow-up ticket

> **Title:** Ingest: 720 clauses (16.6% of document 4) are unreachable because
> their citation path omits the subsection that disambiguates them
>
> **Body:** `_clear_duplicate_citation_paths` blanks the `citation_path` of any
> fragment whose computed path collides with another's. On document 4 that is
> 720 fragments across 275 distinct paths, every one of them a labelled,
> citable provision. The root cause is that a clause's path carries the heading
> it sits under instead of the subsection that scopes it, so 9(1)(a) and
> 9(2)(a) both compute `Part I > 9 > [Development Permit Exemptions] > (a)`;
> and a Part's path carries no chapter, so all eight of Part I's chapters
> compute `Part I`.
>
> Scope: put the discriminator back into the path, decide whether a collision
> should blank the path or disambiguate it, and ship the corpus repath
> migration for dev and prod. Measure with
> `scripts/audit_citation_path_coverage.py --document-id 4`; the
> citable-but-missing count should go to zero.
>
> Separately worth sizing: how many of the 743 unlabelled `list_item`
> fragments are auto-numbered subsections whose number the PDF renderer
> stripped (e.g. fragment 7134 is 198(2)). That needs the source PDF.
