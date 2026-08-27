# ABS-420 — production rollout of `retrieval_enabled`

Migration `0024_document_retrieval_enabled` replaces production's recency-derived
retrieval scope (`latest_per_bylaw_resolver`) with an explicit per-document flag,
backfilled to exactly what that resolver already selected. **Nothing about the
answers changes if it goes right, and nothing looks different if it goes wrong** —
a backfill against a corpus that moved still answers questions, just from
documents nobody chose. Every step below is therefore a check with a verdict.

The procedure is `scripts/apply-abs420-retrieval-rollout.sh`; its drift gate is
`scripts/abs420_rollout_gate.py`, covered by `tests/test_abs420_rollout_gate.py`.

---

## Production as measured, 2026-08-27

Read over SSH from `bylaw-postgres`; the gate pins these exact rows.

| id | by-law | ingested | parser | fragments |
|----|--------|----------|--------|-----------|
| 1 | Halifax Peninsula Land Use Bylaw | 2026-04-29 00:12:49Z | `pymupdf-fallback` | 540 |
| 2 | Halifax Peninsula Land Use Bylaw | 2026-04-29 00:18:08Z | `docling` | 540 |
| 4 | Regional Centre Land Use By-Law | 2026-05-03 19:26:21Z | `docling:halifax` | 4340 |
| 5 | Halifax Mainland Land Use By-law | 2026-05-23 23:45:55Z | `docling:manifest:hrm-mainland` | 2763 |

* `alembic_version` = `0023_token_wallet`. The `document.retrieval_enabled`
  column does not exist yet; the deploy that ships 0024 creates it.
* Documents 1 and 2 are the same by-law ingested six minutes apart — the first
  by the pymupdf fallback parser, the second by docling. **The backfill's
  newest-wins rule picks 2, which is also the better ingest**, so recency and
  quality agree and no curation is required. That is a fact about this corpus,
  not a property of the rule, which is why the gate asserts it rather than
  assuming it.
* All six ingested geo datasets link to document 4, which the backfill enables.
  No overlay is pinned to a document that ends up disabled.

**Predicted enabled set: {2, 4, 5}. Intended enabled set: {2, 4, 5}.**

## Two things the checklist assumed that are not true

### 1. `/v1/monitoring/corpus-coherence` has never checked anything in production

Production answers `{"status":"ok","checked_roles":0}` — and would answer that
however broken the corpus was. The audit reads its overlay declarations from
`src/layer1/datasets/*.yaml` resolved module-relative, which in the deployed
wheel resolves to `/opt/venv/lib/python3.11/src/layer1/datasets`: a path no
install creates. `Path.glob()` on a missing directory returns nothing, so the
audit declared zero roles and reported green.

That is exactly the tripwire this rollout leans on — the ABS-350 shape
(a geo dataset falling out of retrieval scope) is what changing the scope risks.

Fixed on this branch: the config directory now resolves through the installed
`layer1.datasets` package, the YAMLs ship as package data
(`tests/test_package_data.py` builds a real wheel and asserts it), a missing
directory raises, and an audit that loads zero declarations returns 503 instead
of "ok". **The fix only reaches production with the new advisor image**, so
`checked_roles` is the first thing `verify` asserts: green from an audit that
checked nothing is not evidence.

### 2. Production has never had the Schedule 7 pedestrian-street layer ingested

Seven overlay configs declare a role; production's `external_dataset` table
holds six. `halifax_pedestrian_oriented_commercial_streets` was never ingested
there — so the moment the audit starts working (see above), it will correctly
report `pedestrian_street` as **unlinked**, and the checklist's spot-check
(POCS facet present for 6321 Quinpool Road) cannot pass.

This is a pre-existing corpus gap, not something the migration causes. But it
sits between this rollout and its own acceptance criteria, so ingesting it is
part of the procedure below.

## Mechanics correction: no tunnel is needed

The issue says the slim advisor image cannot run layer1 scripts and suggests a
docker-bridge tunnel from a dev box. That is no longer true — verified against
the running 0.9.3 container:

* `/opt/venv/bin/layer1` exists, with `list-documents`, `enable-retrieval`,
  `disable-retrieval`, `ingest-dataset`;
* `DATABASE_URL` is already in the container's environment;
* `Dockerfile.advisor` copies `scripts/` to `/app/scripts`, so
  `corpus_coherence_audit.py` runs in place.

Everything below therefore runs inside the production containers. The database
password never leaves the host and no local port is left listening on a
production DSN.

---

## The procedure

Container-touching steps (the deploy itself) wait for the **23:00 AST**
maintenance window. `preflight` is read-only and can run any time.

### 1. Preflight — before the deploy

```bash
scripts/apply-abs420-retrieval-rollout.sh preflight
```

Passes when production's document table still matches the table above. If it
stops, the corpus moved: re-measure, update `EXPECTED_INVENTORY` in the gate,
and re-reason about the enabled set. Do not force it — the whole
behaviour-preservation claim is a claim about those four rows.

### 2. Deploy

The normal path (`/deploy-bylaw`, or `test-and-deploy-bylaw` from dev). Alembic
runs as part of it. This ships both 0024 and the packaging fix that makes the
coherence audit real.

Confirm the migration landed:

```bash
ssh bylaw-prod 'docker exec bylaw-postgres psql -U layer1 -d layer1 -At \
  -c "select version_num from alembic_version"'
```

### 3. Ingest the Schedule 7 corridors

Required before the audit can be green or the spot-check can pass. The config
now ships inside the image; the GeoJSON does not, and `source_path` resolves
relative to the working directory (`/app`).

```bash
scp data/geo-datasets/pedestrian_oriented_commercial_streets_schedule7.geojson \
    bylaw-prod:/tmp/
ssh bylaw-prod '
  docker exec bylaw-advisor mkdir -p /app/data/geo-datasets &&
  docker cp /tmp/pedestrian_oriented_commercial_streets_schedule7.geojson \
    bylaw-advisor:/app/data/geo-datasets/ &&
  docker exec -w /app bylaw-advisor layer1 ingest-dataset \
    /opt/venv/lib/python3.11/site-packages/layer1/datasets/halifax_pedestrian_oriented_commercial_streets.yaml
'
```

Expect `link_status: linked` against document 4's `Schedule 7` fragment. If it
comes back orphaned, `layer1 relink-datasets` re-attempts linkage; an orphan
that persists means the fragment citation moved and the ingest needs its own
issue rather than a retry.

### 4. Verify

```bash
scripts/apply-abs420-retrieval-rollout.sh verify
```

This is the checklist, mechanised:

1. `alembic_version` is at or past `0024_document_retrieval_enabled`;
2. the enabled set is exactly `{2, 4, 5}` over an unchanged inventory — and if
   it is not, the output names the precise `layer1 enable-retrieval` /
   `disable-retrieval` commands to reach it;
3. `scripts/corpus_coherence_audit.py`, scoped to the enabled set, is coherent;
4. `get_address_profile('6321 Quinpool Road')` returns zone `CEN-2` with a
   `pedestrian_street` overlay — through the real geocode + retrieval path, not
   a re-implementation in SQL;
5. `/v1/monitoring/corpus-coherence` is `ok` **and** reports
   `checked_roles = 7`.

### 5. Curate, only if step 4 asks for it

```bash
scripts/apply-abs420-retrieval-rollout.sh curate --disable 1 --enable 2   # dry run
scripts/apply-abs420-retrieval-rollout.sh curate --disable 1 --enable 2 --apply
```

Dry run by default; `--apply` writes, then re-runs the gate. Enabling uses
`--replace`, so a same-by-law sibling is disabled in the same transaction —
the state where two versions of one by-law are both searchable is never left
behind by this path.

## Rollback

Roll the image back and leave the migration in place. `retrieval_enabled_resolver`
exists only in the new code; the deployed 0.9.x image scopes with
`latest_per_bylaw_resolver` (deleted from the codebase by ABS-413, still present
in that image), which reads no flag and would re-derive the same winners from
recency. An image rollback alone therefore restores the previous behaviour
exactly, and the column it leaves behind is inert — `NOT NULL DEFAULT false`, so
the old code's inserts still work.

`0024`'s `downgrade()` drops the column if it has to come out, but only run it
after the image is back: the new code fails closed on a missing column, not
gracefully.

Note the one thing a rollback does **not** undo: the Schedule 7 ingest from
step 3 is a corpus addition, not part of the migration. It is the state the
corpus should have been in already, and the old resolver serves it identically.
