# ABS-461 production impact: the phantom sections are live

**Raised by:** ABS-461, DoD 7 (*"Production impact assessed. State explicitly
whether prod needs a re-ingest or a data migration, and how it will be
applied — a fix that only lands on dev leaves users getting the wrong
setback."*)
**Verified:** 2026-08-11, read-only queries against `bylaw-prod`.
**Status:** migration written and tested; **not yet applied to production.**
Applying it is a data change to the live corpus and wants explicit sign-off.

## Production carries the identical defect

Prod's document 4 was ingested by the same parser build as dev's
(`parser_version = docling:halifax`), and the damage is byte-identical —
same fragments, same ids, same phantom paths:

```
select count(*) from source_fragment
where document_id=4 and citation_path like 'Part V > 2 >%';
-- prod: 7
```

Running ABS-461's detection rule against prod read-only finds the same four
splits dev had, at the same fragment ids:

| head | tail | phantom created |
|---:|---:|---|
| 5791 | 5792 | — (tail parsed as unaddressed prose) |
| 6070 | 6071 | — (tail parsed as unaddressed prose) |
| 6393 | 6394 | `Part V > 3` |
| 7121 | 7122 | `Part V > 2` |

So the wrong-answer path from eval case TC-001 — clause `198(1)(f)`,
"2.5 metres elsewhere", sitting under a section that does not exist — is
live for users right now. Documents 1, 2 and 5 are unaffected (the Mainland
LUB's eight hyphen-ended amendment-log cells are excluded by the block-type
rule; see `src/layer1/pipeline/page_break_repair.py`).

## Migration, not re-ingest

**Re-ingesting document 4 is the wrong instrument.** Three reasons:

1. **It cannot run where the data is.** The prod advisor image carries no
   `layer1` package (`ModuleNotFoundError: No module named 'layer1'` inside
   `bylaw-advisor`) and no docling. A re-ingest would mean shipping a fat
   image or running a 457-page docling parse off-box and loading the result.
2. **It renumbers everything.** Re-ingest allocates fresh `source_fragment`
   ids for all ~4,300 fragments in the document. Every foreign key pointing
   at one moves with it, and every citation already recorded in `answer_log`
   stops pointing at the row it described.
3. **It would change far more than the defect.** The parser has moved since
   `docling:halifax` was current. Re-parsing today would reshape citation
   paths across the document — a much larger, much less reviewable diff than
   the four fragments that are actually wrong.

**The migration is `scripts/repair_page_break_splits.py`.** On dev it made
exactly this change, and prod's identical starting state means it will make
the same one:

```
page-break splits: 4 joined, 2 phantom section(s) removed,
                   10 citation_path(s) rewritten, 0 unresolved,
                   0 embedding(s) invalidated
```

That is 4 text splices, 10 `citation_path` updates, 2 row deletions and 1
reparent. No schema change — prod is at alembic `0023_token_wallet`, behind
dev, and the repair needs nothing newer.

## How to apply it

The script needs `layer1` importable and a connection to prod Postgres. The
container has neither, so run it from a dev checkout through an SSH tunnel to
the Postgres container's bridge address (the pattern in
`docs/DEPLOYMENT.md`-adjacent runbooks; `bylaw-postgres` sits at
`172.18.0.2:5432`, credentials `layer1/layer1` from the container env):

```bash
# 1. tunnel (leave running in its own shell)
ssh -N -L 15432:172.18.0.2:5432 bylaw-prod

# 2. from the repo, dry run first — writes nothing
DATABASE_URL="postgresql+psycopg://layer1:layer1@127.0.0.1:15432/layer1" \
  .venv/bin/python scripts/repair_page_break_splits.py --document-id 4 --dry-run

# 3. apply, keeping the revert sidecar somewhere durable
DATABASE_URL="postgresql+psycopg://layer1:layer1@127.0.0.1:15432/layer1" \
  .venv/bin/python scripts/repair_page_break_splits.py \
    --document-id 4 --sidecar-dir ~/abs461-prod-sidecars
```

The known caveat about this tunnel — it drops on long write transactions —
does not bite here: the whole repair is sub-second and commits in one
transaction. The dry run must print the four splits above before you apply;
if it prints anything else, stop, because prod has drifted from what this
document measured.

**No container is touched**, so this does not need the 23:00 AST maintenance
window. It should still land *after* the ABS-461 code merge, so the parser
guard is in place before anything re-ingests this bylaw and reintroduces the
split.

### Verifying

```bash
ssh bylaw-prod 'docker exec bylaw-postgres sh -lc "psql -U \$POSTGRES_USER \
  -d \$POSTGRES_DB -tAc \"select count(*) from source_fragment \
  where document_id=4 and citation_path like '\''Part V > 2 >%'\''\""'
# expect: 0
```

### Rolling back

```bash
DATABASE_URL="postgresql+psycopg://layer1:layer1@127.0.0.1:15432/layer1" \
  .venv/bin/python scripts/repair_page_break_splits.py \
    --revert ~/abs461-prod-sidecars/page_break_repair_sidecar_<stamp>.json
```

The sidecar carries the deleted phantom rows verbatim, original ids included,
so a revert restores the pre-repair state exactly. Covered by
`tests/test_page_break_repair.py::test_revert_restores_the_pre_repair_state`.

## What the repair does not fix on prod

`lookup_citation`'s compact-citation ranking (`198(1)(f)` → the right clause)
is a **code** change in `mcp/bylaw_retrieval/retrieval/service.py`, not a data
one. It reaches prod with the next advisor image deploy, not with this
migration. Until both have landed, the corpus is correct but the model still
has to guess at the path format — so the two should ship together.

Separately, the 720 citable-but-missing clauses in
[citation-path-coverage.md](citation-path-coverage.md) are present on prod
too and are **not** addressed by this migration. That is the follow-up ticket.
