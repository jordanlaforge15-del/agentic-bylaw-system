# ABS-461 production impact: the phantom sections are live

**Raised by:** ABS-461, DoD 7 (*"Production impact assessed. State explicitly
whether prod needs a re-ingest or a data migration, and how it will be
applied — a fix that only lands on dev leaves users getting the wrong
setback."*)
**Verified:** 2026-08-11, read-only queries against `bylaw-prod`;
re-verified 2026-08-27 (ABS-465) — unchanged.
**Status:** migration written, tested, and gated behind
`scripts/apply-abs461-prod-repair.sh`; **not yet applied to production.**
Applying it is a data change to the live corpus and wants explicit sign-off.

## Production carries the identical defect

Prod's document 4 was ingested by the same parser build as dev's
(`parser_version = docling:halifax`), and the damage is byte-identical —
same fragments, same ids, same phantom paths:

```
select count(*) from source_fragment
where document_id=4 and citation_path like 'Part V > 2 >%';
-- prod: 7
select count(*) from source_fragment
where document_id=4 and citation_path like 'Part V > 3 >%';
-- prod: 3
```

Ten fragments under **two** phantoms. The `Part V > 3` one is easy to miss —
it never appeared in an eval case — and it governs permitted encroachments
into setbacks, which is what a deck or balcony question resolves to.

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

**Use `scripts/apply-abs461-prod-repair.sh` (ABS-465).** It runs the procedure
below, tears the tunnel down on any exit including Ctrl-C, and — the part that
matters — *gates* on the dry run rather than asking you to eyeball it:

```bash
scripts/apply-abs461-prod-repair.sh            # dry run + drift gate; writes nothing
scripts/apply-abs461-prod-repair.sh --apply    # repair, verify both prefixes, print revert
```

The gate is covered by `tests/test_abs461_prod_repair_gate.py`, which drives it
with the real production transcript and with each way prod could have drifted
(a fifth split, a renumbered fragment, a differently sized change, a clause the
repair declines to place). Any of those stops the run.

### The manual path, if you need it

The script needs `layer1` importable and a connection to prod Postgres. The
container has neither, so run it from a dev checkout through an SSH tunnel to
the Postgres container's bridge address; `bylaw-postgres` sits at
`172.18.0.2:5432`.

**The password is not `layer1`.** An earlier revision of this document said it
was, and following that verbatim fails at the connect step with
`FATAL: password authentication failed for user "layer1"`. Read it from the
container's own environment and never write it down:

```bash
# 1. credentials, straight from the container
PGPW=$(ssh bylaw-prod 'docker exec bylaw-postgres printenv POSTGRES_PASSWORD')

# 2. tunnel (leave running in its own shell)
ssh -N -L 15442:172.18.0.2:5432 bylaw-prod

# 3. from the repo, dry run first — writes nothing
DATABASE_URL="postgresql+psycopg://layer1:${PGPW}@127.0.0.1:15442/layer1" \
  .venv/bin/python scripts/repair_page_break_splits.py --document-id 4 --dry-run

# 4. apply, keeping the revert sidecar somewhere durable
DATABASE_URL="postgresql+psycopg://layer1:${PGPW}@127.0.0.1:15442/layer1" \
  .venv/bin/python scripts/repair_page_break_splits.py \
    --document-id 4 --sidecar-dir ~/abs461-prod-sidecars
```

The password is generated and may contain characters a URL would eat; the
script percent-encodes it, so do the same if you build the DSN by hand.

The known caveat about this tunnel — it drops on long write transactions —
does not bite here: the whole repair is sub-second and commits in one
transaction. The dry run must print the four splits above before you apply;
if it prints anything else, stop, because prod has drifted from what this
document measured.

### Re-verified 2026-08-27 (ABS-465)

The dry run was re-run against production sixteen days after this document was
written. Prod has not drifted — same four splits, same fragment ids, same
change size:

```
doc 4: would join fragment 5791 + 5792     tail was unaddressed prose
doc 4: would join fragment 6070 + 6071     tail was unaddressed prose
doc 4: would join fragment 6393 + 6394     phantom 'Part V > 3' -> 'Part V > 94 > 94.5' (3 paths)
doc 4: would join fragment 7121 + 7122     phantom 'Part V > 2' -> 'Part V > 198'       (7 paths)
page-break splits: 4 joined, 2 phantom section(s) removed,
                   10 citation_path(s) rewritten, 0 unresolved,
                   0 embedding(s) invalidated
```

The reparent targets are worth reading twice, because they are what the fix is
*for*: `198(1)(b)`–`(f)` come home to section 198 (that is the "2.5 metres
elsewhere" catch-all TC-001 could not reach), and the three
permitted-encroachment clauses come home to subsection 94.5.

**No container is touched**, so this does not need the 23:00 AST maintenance
window. It should still land *after* the ABS-461 code merge, so the parser
guard is in place before anything re-ingests this bylaw and reintroduces the
split.

### Verifying

**Both prefixes, not just the one the eval surfaced.** `Part V > 2` is the
phantom TC-001 walked into; `Part V > 3` is the one over the encroachment
clauses, which no eval case ever exercised. A run that clears only the first
has done half the job:

```bash
for prefix in 'Part V > 2 >' 'Part V > 3 >'; do
  ssh bylaw-prod "docker exec bylaw-postgres sh -lc \"psql -U \\\$POSTGRES_USER \
    -d \\\$POSTGRES_DB -tAc \\\"select count(*) from source_fragment \
    where document_id=4 and citation_path like '\$prefix%'\\\"\""
done
# expect: 0 and 0   (pre-repair: 7 and 3)
```

`--apply` runs both of these itself and fails the run if either comes back
non-zero. It asks over `psql` in the container rather than through the tunnel,
so the connection that reports success is not the one that did the writing.

### Rolling back

```bash
PGPW=$(ssh bylaw-prod 'docker exec bylaw-postgres printenv POSTGRES_PASSWORD')
DATABASE_URL="postgresql+psycopg://layer1:${PGPW}@127.0.0.1:15442/layer1" \
  .venv/bin/python scripts/repair_page_break_splits.py \
    --revert ~/abs461-prod-sidecars/page_break_repair_sidecar_<stamp>.json
```

`--apply` prints this command with the real sidecar path filled in, and refuses
to write the sidecar into a temp directory — recovery must not depend on
something `/tmp` may have swept.

The sidecar carries the deleted phantom rows verbatim, original ids included,
so a revert restores the pre-repair state exactly. Covered by
`tests/test_page_break_repair.py::test_revert_restores_the_pre_repair_state`.
That test runs on SQLite; the round trip has not been rehearsed against a
Postgres copy of document 4, so the sidecar, not the rehearsal, is what
recovery rests on.

## What the repair does not fix on prod

`lookup_citation`'s compact-citation ranking (`198(1)(f)` → the right clause)
is a **code** change in `mcp/bylaw_retrieval/retrieval/service.py`, not a data
one. It reaches prod with the next advisor image deploy, not with this
migration. Until both have landed, the corpus is correct but the model still
has to guess at the path format — so the two should ship together.

As of 2026-08-27 production runs `bylaw-advisor:0.9.3` and `bylaw-web:0.9.2`.
`main` is still at the 2026-07-20 promotion (v0.9.3), which predates the
ABS-461 merge on `dev` — so **no built image carries the code half yet**. The
image has to be cut from a promotion that includes ABS-461 before the data
repair is worth its full value.

Separately, the 720 citable-but-missing clauses in
[citation-path-coverage.md](citation-path-coverage.md) are present on prod
too and are **not** addressed by this migration. That is ABS-488, whose own
runbook is [citation-path-repath.md](citation-path-repath.md). Apply that one
*after* this one: the repath reads the section anchors this repair corrects.
