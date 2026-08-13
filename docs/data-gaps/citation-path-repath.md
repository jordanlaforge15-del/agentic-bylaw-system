# ABS-488 citation-path repath: what changed, and how it reaches production

**Resolves:** the data gap written up in
[citation-path-coverage.md](citation-path-coverage.md) — 720 labelled, citable
provisions of document 4 (16.6% of the by-law) with no `citation_path` at all.
**Measured:** 2026-08-12, dev Postgres.
**Status:** parser fixed, dev corpus migrated; **not yet applied to production.**
Applying it is a data change to the live corpus and wants explicit sign-off.

## What moved

Two path shapes carried no discriminator, so two or more provisions computed one
address and `_clear_duplicate_citation_paths` blanked every one of them.

**Clauses now carry the container that scopes them.** A clause's path used to
carry the *sticky heading* the parser had last seen, not the subsection, list
stem or definition the clause actually sits under, so section 9's two clause
groups both computed `Part I > 9 > [Development Permit Exemptions] > (a)`. The
walk in `src/layer1/pipeline/citation_repath.py` anchors each clause to the
nearest preceding fragment of a lower level:

| | before | after |
|---|---|---|
| 9(1)(a) | *blanked* | `Part I > 9 > (a)` |
| 9(2)(a) | *blanked* | `Part I > 9 > [On a registered heritage property or on a lot ... from a development permit] > (a)` |
| 198(1)(f) | `Part V > 198 > [Side Setback Requirements] > (f)` | `Part V > 198 > (f)` |
| 198(1)(a)(i) | `Part V > 198 > (i)` (collided) | `Part V > 198 > (a) > (i)` |
| 499(62)(a) | *blanked* | `Part X > 499 > (61.5) > [Dwelling Unit means living quarters that] > (a)` |

Two rules keep that from over-firing, and both are things this corpus does:
prose that *wraps* mid-list does not open a new scope (`(a)` then `(b)`
continues; `(r)` then `(a)` restarts), and a list the flat label parser
flattened — `(i)` reading as the ninth letter, `(A)` case-folding onto `(a)` —
is recognised as one level deeper.

**Part headings now carry their chapter.** All eight of Part I's chapters parsed
as the bare label `Part I`. They are now `Part I`, `Part I, Chapter 1`,
`Part I, Chapter 2`, …

The chapter is **not** pushed onto descendants. `Part I > 9` stays exactly
`Part I > 9`, which is what keeps every already-stored section path — and every
`citation_path` anchor in `evals/retrieval/queries.json` — valid.

### What is still unpathed, and why

Document 4's remaining 1,745 NULL paths are the *legitimately unpathed* class
from the original write-up: headings, body prose, footnotes and unlabelled list
items, none of which asserts a citation. That count did not change.

The Halifax Mainland LUB (document 5) goes from 114 citable-but-missing to 63.
The residue there is a **different defect** and out of ABS-488's scope: repeated
section numbers across schedules (`Schedule A > 28` is computed by six
fragments, `Appendix A > (1)` by six). Those are section-level collisions, not
clause-level ones, and want their own ticket.

### An honest caveat on the definitions section

Section 499's defined terms mostly reach the parser with their subsection number
stripped by the renderer ("Dwelling Unit means living quarters that:" arrives as
prose), so their clauses anchor to the last *numbered* subsection still in
scope. `499(62)(a)` therefore reads
`Part X > 499 > (61.5) > [Dwelling Unit means living quarters that] > (a)` — the
`(61.5)` is a stale ancestor. The bracketed segment names the right definition
and the path is unique and citable, which is a strict improvement on being
unreachable, but the numeric ancestor is not the clause's legal citation.
Fixing that means recovering the stripped subsection numbers from the source
PDF, which is the same open question the original write-up raised about the 743
unlabelled list items.

## Results on dev

```
$ python scripts/audit_citation_path_coverage.py --document-id 4
  citation_path set :   2592 (59.8%)     # was 1,872 (43.2%)
  citation_path NULL:   1745 (40.2%)     # was 2,465 (56.8%)
  [citable, missing] path blanked as a duplicate: 0    # was 720
  [citable, missing] label but no path built:      0
  citable-but-missing total: 0 (0.0% of the document)
```

`uq_fragment_citation_path` holds — corpus-wide there are zero
`(document_id, citation_path)` pairs with more than one row.

### Retrieval eval (DM-09 harness, `scripts/eval_retrieval_recall.py`)

| | Recall@10 | set-Recall@10 | MRR@10 |
|---|---|---|---|
| `evals/retrieval/BASELINE.json` | 0.1029 | 0.1029 | 0.0667 |
| dev corpus immediately before this change | 0.1324 | 0.1324 | 0.0711 |
| dev corpus after the repath | **0.1618** | **0.1618** | **0.0877** |

No regression on the headline metric against either reference. Per category the
movement is `zone_anchored` 0.00 → 0.30 and `permitted_use` 0.143 → 0.071 — a
net gain of two questions. Every content-addressed anchor still resolved
one-to-one, so the harness scored rather than refusing: no label drifted.

**These numbers grade ranking, never correctness.** The query set is
agent-drafted and unreviewed; see `evals/retrieval/README.md`.

## Migration, not re-ingest

Same instrument and the same three reasons as ABS-461
([abs461-production-impact.md](abs461-production-impact.md)): the prod advisor
image carries no `layer1` package and no docling, a re-ingest reallocates every
`source_fragment` id so every foreign key and every citation recorded in
`answer_log` stops pointing at the row it described, and re-parsing today would
reshape far more of the document than the defect.

`scripts/repath_citation_paths.py` replays the *same* pure walk the parser runs,
over rows already in the database. Two details make the replay faithful:

* a collided row's would-be path was recorded in
  `metadata_json.duplicate_citation_path`, and the walk is fed that rather than
  the `NULL` the collision rule left, so it sees the document the builder saw;
* Part rows are re-labelled from their own text first, and that relabel is
  idempotent, so a re-run is a no-op rather than
  `Part I, Chapter 2, Chapter 2`.

Writes are two-phase because `uq_fragment_citation_path` is a plain
(non-deferrable) unique constraint: every moving row is blanked and flushed
before any row takes a new path. Embeddings are **not** invalidated —
`layer2.pipeline.service` embeds `fragment.text` alone and no text changes.

### Verified on a dev-DB clone

```
$ pg_dump -U layer1 -d layer1 | psql -U layer1 -d layer1_abs488   # clone
$ python scripts/repath_citation_paths.py --document-id 4 --database-url $CLONE --dry-run
doc 4: 4337 fragments, would rewrite 2008 row(s); citable-but-missing 720 -> 0
$ python scripts/repath_citation_paths.py --document-id 4 --database-url $CLONE
citation-path repath: 1 document(s), 2008 row(s) rewritten, 720 citable provision(s) recovered
$ python scripts/repath_citation_paths.py --revert citation_repath_sidecar_<stamp>.json --database-url $CLONE
reverted 2008 row(s)
```

After the revert, an md5 over `id|citation_label|citation_path|parse_status|
confidence` for every document-4 row matched the un-migrated dev database
exactly. Covered by
`tests/test_corpus_repath.py::test_revert_restores_the_pre_repath_state`.

The dev database itself was then migrated across all documents
(2,975 rows, 771 provisions recovered); its sidecar is in
`~/abs488-dev-sidecars/`.

## How to apply it to production

The script needs `layer1` importable and a connection to prod Postgres. The
container has neither, so run it from a dev checkout through an SSH tunnel to
the Postgres container's bridge address — the same pattern ABS-461 documents
(`bylaw-postgres` at `172.18.0.2:5432`, credentials `layer1/layer1` from the
container env):

```bash
# 1. tunnel (leave running in its own shell)
ssh -N -L 15432:172.18.0.2:5432 bylaw-prod

# 2. from the repo, dry run first — writes nothing
DATABASE_URL="postgresql+psycopg://layer1:layer1@127.0.0.1:15432/layer1" \
  .venv/bin/python scripts/repath_citation_paths.py --document-id 4 --dry-run

# 3. apply, keeping the revert sidecar somewhere durable
DATABASE_URL="postgresql+psycopg://layer1:layer1@127.0.0.1:15432/layer1" \
  .venv/bin/python scripts/repath_citation_paths.py \
    --document-id 4 --sidecar-dir ~/abs488-prod-sidecars
```

**The dry run must report `citable-but-missing 720 -> 0` on document 4.** If it
reports anything else, stop: prod has drifted from what this document measured,
most likely because ABS-461's page-break repair has (or has not) been applied
there. This repath should land *after* that one — it reads the section anchors
the page-break repair corrects.

Prod is expected to carry the identical defect: its document 4 was ingested by
the same parser build (`parser_version = docling:halifax`) as dev's. Confirm
before applying:

```bash
ssh bylaw-prod 'docker exec bylaw-postgres sh -lc "psql -U \$POSTGRES_USER \
  -d \$POSTGRES_DB -tAc \"select count(*) from source_fragment where \
  document_id=4 and citation_label is not null and citation_path is null\""'
# expect: 720
```

No schema change, so no Alembic step. **No container is touched**, so this does
not need the 23:00 AST maintenance window. The whole rewrite commits in one
sub-second transaction, so the known tunnel caveat (drops on long write
transactions) does not bite.

### Verifying

```bash
ssh bylaw-prod 'docker exec bylaw-postgres sh -lc "psql -U \$POSTGRES_USER \
  -d \$POSTGRES_DB -tAc \"select count(*) from source_fragment where \
  document_id=4 and citation_label is not null and citation_path is null\""'
# expect: 0
```

### Rolling back

```bash
DATABASE_URL="postgresql+psycopg://layer1:layer1@127.0.0.1:15432/layer1" \
  .venv/bin/python scripts/repath_citation_paths.py \
    --revert ~/abs488-prod-sidecars/citation_repath_sidecar_<stamp>.json
```

The sidecar carries every changed row's label, path, parse status, confidence
and metadata verbatim, so a revert restores the pre-repath state exactly.

## What a migrated corpus does not fix by itself

Citations already written into `answer_log` keep the old strings. They still
name the right fragment ids, but a path quoted in a historical answer
(`Part V > 198 > [Side Setback Requirements] > (f)`) will no longer resolve
through `lookup_citation`. Nothing rewrites them: an answer is a record of what
was said at the time, and editing it to match a corpus that has since moved
would be worse than a stale quote. New answers cite the new shape.

`lookup_citation`'s compact-citation ranking is unchanged — it compares
structure token by token, so it was never sensitive to the heading segment's
presence — but the ranking is *code*, and reaches prod with the next advisor
image deploy rather than with this migration. The two are independent here; a
migrated corpus is correct with either image.
