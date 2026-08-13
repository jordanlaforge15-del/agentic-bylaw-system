# Local dev database backup

The local dev database is the `layer1` Postgres in the
`agentic-bylaw-system-postgres-1` container (named volume `layer1-postgres`).
Two scripts back it up nightly, on a daily / weekly / monthly rotation
(see [Retention](#retention)):

- `scripts/backup-dev-db.sh` — one-shot dump
- `scripts/install-backup-cron.sh` — install / remove the daily cron entry

A third takes a **labelled, rotation-exempt** snapshot immediately before
anything migrates the data — see
[The migration fence](#the-migration-fence-abs-499) below:

- `scripts/snapshot-before-migration.sh` — tagged pre-migration dump
- `scripts/check_migration_drift.py` — is the DB behind this branch?

## Where backups live

```
$HOME/backups/agentic-bylaw-system/
  layer1-Mon.dump              # daily tier, keyed by day-of-week
  layer1-Tue.dump
  ...
  layer1-Sun.dump
  layer1-weekly-2026-W30.dump  # weekly tier, keyed by ISO week
  layer1-weekly-2026-W31.dump
  layer1-weekly-2026-W32.dump
  layer1-weekly-2026-W33.dump
  layer1-monthly-2026-03.dump  # monthly tier, keyed by calendar month
  ...
  layer1-monthly-2026-08.dump
  backup.log
```

Labelled pre-migration snapshots live in their own subdirectory, under
their own timestamped names, and are **never** touched by the rotation
below — see [The migration fence](#the-migration-fence-abs-499):

```
$HOME/backups/agentic-bylaw-system/labelled/
  layer1-alembic-upgrade-from-0025_signup_grant_unique-20260813T104400.dump
  layer1-repath-citation-paths-20260813T104512.dump
```

The subdirectory is not cosmetic: `clone-dev-db.sh` restores the most recent
`layer1-*.dump` in the *top-level* directory, so a hand-made dump left up there
(there is one — `layer1-pre-data-model-3.0-20260812.dump`, from the DM3.0
post-mortem, which predates this convention) silently becomes the source every
clone is built from. Snapshots under `labelled/` never shadow the rotation that
way.

Dumps use `pg_dump -Fc` (custom format) so they restore with
`pg_restore` and support partial / parallel restore.

## Retention

Three tiers. Each nightly run writes one daily dump and then, if this is
the first run of the current ISO week or calendar month, *promotes* a
copy of that dump into the weekly / monthly tier.

| Tier    | Filename                      | Slots | Horizon      | Env override         |
| ------- | ----------------------------- | ----- | ------------ | -------------------- |
| daily   | `layer1-<Dow>.dump`           | 7     | last 7 days  | — (fixed)            |
| weekly  | `layer1-weekly-<YYYY>-W<WW>.dump` | 4 | ~1 month     | `BYLAW_KEEP_WEEKLY`  |
| monthly | `layer1-monthly-<YYYY>-<MM>.dump` | 6 | ~6 months    | `BYLAW_KEEP_MONTHLY` |

**Disk ceiling: 17 dump files.** At the current dev corpus size
(~380 MiB per `-Fc` dump, 2026-08) that is roughly **6.5 GiB**. The
ceiling is a deliberate product of the prune, not a side effect of
overwriting: after promotion, each tier's files are listed, sorted (tier
names sort chronologically), and everything past the newest N slots is
deleted, one `PRUNED:` log line each. Raising `BYLAW_KEEP_WEEKLY` /
`BYLAW_KEEP_MONTHLY` raises the ceiling by the same count.

Two properties worth knowing:

- **Promotion keys off "first run of the week/month", not a fixed
  weekday.** cron doesn't fire while the Mac is asleep, so a
  Sunday-only rule would silently skip whole weeks.
- **The prune only ever touches files matching its own tier prefix**
  (`layer1-weekly-*.dump`, `layer1-monthly-*.dump`). A hand-copied
  snapshot such as `layer1-pre-data-model-3.0-20260812.dump` is invisible
  to it and survives indefinitely, and so is everything under
  `labelled/`. Daily slots are never pruned either — they rotate in
  place.

### Preview the plan without touching anything

```bash
scripts/backup-dev-db.sh --dry-run
```

Prints the daily slot it would write, any promotions, and every file the
prune would delete — and changes nothing, not even `backup.log`. Run this
after changing a retention setting to see what the next real run will do.

## Install the cron job

```bash
scripts/install-backup-cron.sh
```

Installs an entry that runs the backup nightly at 03:00 local time.
The script is idempotent — re-running replaces the prior entry. Confirm
with `crontab -l`.

To remove:

```bash
scripts/install-backup-cron.sh --uninstall
```

### macOS sleep caveat

`cron` does **not** fire while the Mac is asleep. If the laptop is
closed at 03:00 the backup that night is skipped (no catch-up). Either
leave the machine awake on a schedule (Energy Saver), run the script
manually after long sleeps, or switch to a `launchd` agent with
`StartCalendarInterval` + `RunAtLoad` if wake-on-fire matters.

## Restore from a backup

Every tier is a plain `pg_dump -Fc` file, so the recipe is identical for
all three — only the filename changes. Stop anything writing to the DB,
then:

```bash
# Pick one:
DUMP=$HOME/backups/agentic-bylaw-system/layer1-Mon.dump              # daily
DUMP=$HOME/backups/agentic-bylaw-system/layer1-weekly-2026-W33.dump  # weekly
DUMP=$HOME/backups/agentic-bylaw-system/layer1-monthly-2026-08.dump  # monthly

# Wipe the current DB and replay the dump.
docker exec -i agentic-bylaw-system-postgres-1 \
  dropdb -U layer1 --if-exists layer1
docker exec -i agentic-bylaw-system-postgres-1 \
  createdb -U layer1 layer1
docker exec -i agentic-bylaw-system-postgres-1 \
  pg_restore -U layer1 -d layer1 --no-owner < "$DUMP"
```

To see what you actually have before choosing, `ls -lt` the backup dir —
weekly and monthly copies preserve the mtime of the day they captured, so
the listing is in true data-age order.

If you need a dump to outlive its tier (a known-good pre-migration
snapshot, say), copy it to a name outside the tier prefixes — the prune
will then leave it alone forever:

```bash
cd $HOME/backups/agentic-bylaw-system
cp -p layer1-Wed.dump layer1-pre-<change>-$(date +%Y%m%d).dump
```

Those keepsakes are *not* counted in the 17-slot ceiling; budget their
disk yourself. For the pre-migration case specifically you no longer have
to remember to do this by hand — [the migration
fence](#the-migration-fence-abs-499) takes a labelled snapshot into
`labelled/` before anything mutates the data.

To restore into a throwaway Postgres instead of clobbering dev, use
[the clone script](#isolated-clone-db-for-experiments).

## Run an ad-hoc backup

```bash
scripts/backup-dev-db.sh
```

Overrides via env vars (defaults shown):

| Variable               | Default                                       |
| ---------------------- | --------------------------------------------- |
| `BYLAW_PG_CONTAINER`   | `agentic-bylaw-system-postgres-1`             |
| `BYLAW_PG_DB`          | `layer1`                                      |
| `BYLAW_PG_USER`        | `layer1`                                      |
| `BYLAW_BACKUP_DIR`     | `$HOME/backups/agentic-bylaw-system`          |
| `BYLAW_KEEP_WEEKLY`    | `4`                                           |
| `BYLAW_KEEP_MONTHLY`   | `6`                                           |
| `BYLAW_BACKUP_DATE`    | unset (test hook — pretend today is `YYYY-MM-DD`) |

## Tests

`tests/test_backup_dev_db.py` fakes the `docker` CLI via a temp PATH
shim and asserts: per-DOW filename, in-place overwrite on re-run,
nonzero exit + log line when the container isn't running, and — by
driving `BYLAW_BACKUP_DATE` one simulated day at a time — that a
day-1 snapshot is still restorable on day 30, that total dump count
holds at the 17-slot ceiling across ~260 simulated days, that the prune
keeps the newest slots, that hand-copied dumps survive it, and that
`--dry-run` mutates nothing.

```bash
.venv/bin/pytest tests/test_backup_dev_db.py
```

---

## The migration fence (ABS-499)

### Why

The rotation is time-keyed, not event-keyed: the daily tier holds seven
slots, and the weekly / monthly tiers are promoted copies of whichever daily
dump happened to land first that week or month. A data migration that runs
between two nightly dumps can therefore have its pre-change state overwritten
inside a single seven-day cycle, and whether any longer-lived tier happens to
preserve it is pure calendar luck — nothing about the rotation knows a
migration happened.

During Data Model 3.0 that nearly happened twice: ABS-488's repath rewrote
citation paths corpus-wide (720 citable-but-unpathed fragments → 0) and
ABS-480's status backfill touched 834 rows. Both landed 22:17–23:29, *after*
that day's 03:00 dump. A clean pre-change snapshot exists only because of that
timing. At 02:00 it would have been gone.

So: **nothing mutates dev data without a labelled snapshot landing first.** If
the snapshot cannot be taken, the migration does not run. A migration that
didn't run is recoverable; a pre-change state that was never captured is not.

### What is fenced

| Entry point | Tag | Fired when |
| ----------- | --- | ---------- |
| `alembic upgrade` / `downgrade` (`alembic/env.py`) | `alembic-<cmd>-from-<current>` | Before the first DDL, and only when a migration is actually pending |
| `scripts/repath_citation_paths.py` | `repath-citation-paths[-revert]` | Apply and `--revert`; **not** `--dry-run` |
| `scripts/backfill_*.py` (whole family) | `backfill-<name>[-revert]` | Apply and `--revert`; **not** `--dry-run` |

Read-only alembic subcommands (`current`, `history`) are not fenced, and an
`alembic upgrade` on a DB already at head skips the dump — there is nothing to
protect. Every fenced path fails the same way: `ABORT: …` on stderr and exit
**3**, before the first write.

Adding a new script that mutates dev data? Two lines:

```python
from layer1.db.migration_fence import fence_or_abort

if not args.dry_run:
    fence_or_abort("my-new-backfill", database_url=args.database_url)
```

`fence_or_abort` prints `ABORT: …` and exits **3** if the snapshot fails —
before your first write, and without a traceback.

### Scope: dev only

The fence engages only when the target DSN is the local dev database: local
host, port 5432, database `layer1`. Everything else is deliberately out of
scope — the e2e stack (`layer1_test` on a per-worktree port), clone DBs, CI,
and production all have disposable or separately-backed-up state, and dumping
hundreds of megabytes on every `alembic upgrade` in the e2e boot path would be
pure cost.

### Take one by hand

```bash
scripts/snapshot-before-migration.sh abs-512-something-risky
# => $HOME/backups/agentic-bylaw-system/labelled/layer1-abs-512-something-risky-<stamp>.dump
```

The path is the only thing on stdout, so it can be captured:

```bash
snap="$(scripts/snapshot-before-migration.sh abs-512-something-risky)"
```

### Restore from a labelled snapshot

Same as any other dump — the file is a plain `pg_dump -Fc`:

```bash
# Newest labelled snapshot for a given tag:
snap="$(ls -t "$HOME"/backups/agentic-bylaw-system/labelled/layer1-repath-citation-paths-*.dump | head -1)"

docker exec -i agentic-bylaw-system-postgres-1 dropdb -U layer1 --if-exists layer1
docker exec -i agentic-bylaw-system-postgres-1 createdb -U layer1 layer1
docker exec -i agentic-bylaw-system-postgres-1 \
  pg_restore -U layer1 -d layer1 --no-owner < "$snap"
```

Restoring rewinds the schema too, so re-check for drift afterwards
(`make check-migration-drift`) and `make migrate` if it reports pending work.

Labelled snapshots are never pruned automatically. They are the record of what
the corpus looked like before each migration; delete them deliberately, once
the migration they fence is known good.

### Opting out

```bash
BYLAW_SKIP_MIGRATION_SNAPSHOT=1 python scripts/backfill_parcels.py
```

Logged at WARNING every time. Use it when you have *just* taken a snapshot by
hand, not to make a red run go green.

| Variable                          | Effect |
| --------------------------------- | ------ |
| `BYLAW_SKIP_MIGRATION_SNAPSHOT=1` | Disable the fence (wins over everything) |
| `GITHUB_ACTIONS=true`             | Disable the fence — CI's pytest job migrates a throwaway container on a DSN byte-identical to the dev laptop's. `.github/workflows/ci.yml` also sets the skip flag explicitly. |
| `BYLAW_FORCE_MIGRATION_SNAPSHOT=1`| Fence *any* target, not just the dev DB (e.g. a clone) |
| `BYLAW_SNAPSHOT_SCRIPT`           | Path to the snapshot script |
| `BYLAW_DEV_PG_PORT` / `BYLAW_PG_DB` | What counts as "the dev database" (5432 / `layer1`) |
| `BYLAW_SNAPSHOT_TIMEOUT_S`        | `pg_dump` timeout (default 1800) |

## Migration drift check

Data migrations applied on top of a *pending schema* migration is how DM3.0
ended up with the dev DB stamped `0025_signup_grant_unique` while
`0026_drop_parcel_zone_code` had never run. The split state was silent until
someone went looking. This makes it loud:

```bash
make check-migration-drift            # or: python scripts/check_migration_drift.py
```

You do not have to remember to run it. Every fenced script also checks for
drift right after its snapshot lands and warns before it writes:

```
WARNING MIGRATION DRIFT: database is at 0025_signup_grant_unique but this branch
head is 0026_drop_parcel_zone_code — 1 migration(s) pending: 0026_drop_parcel_zone_code
WARNING   run `make check-migration-drift` for the full report
```

A warning, not a block — the combination is occasionally deliberate. (`alembic
upgrade` skips the warning: being behind is why it is running.)

```
alembic_version : 0025_signup_grant_unique
branch head     : 0026_drop_parcel_zone_code
status          : BEHIND — 1 migration(s) pending
  - 0026_drop_parcel_zone_code  (alembic/versions/0026_drop_parcel_zone_code.py)
      Drop the write-only ``parcel.zone_code`` column (ABS-481).
```

Exit codes: `0` in sync, `1` behind, `2` undeterminable (unreachable DB, or a
revision recorded in the DB that does not exist on this branch — meaning the
database is *ahead of* or diverged from the checkout). `--exit-zero` reports
without failing the caller. Run it before a data migration, and after
restoring a snapshot.

### Tests

- `tests/test_snapshot_before_migration.py` — labelled path and captured
  stdout, **rotation exemption** (eight nightly runs leave the labelled dump
  byte-identical), tag sanitisation, and nonzero-exit-with-no-artifact when
  `pg_dump` fails.
- `tests/test_migration_fence.py` — the dev-only scope gate, refusal semantics,
  and every wired entry point run as a subprocess: each snapshots with its own
  tag, each aborts with exit 3 before opening a connection when the snapshot
  fails, dry runs are not fenced, and `alembic upgrade` never reaches its first
  DDL unsnapshotted while `alembic current` is not fenced at all.
- `tests/scripts/test_check_migration_drift.py` — the DM3.0 split state,
  in-sync, never-stamped, and DB-ahead-of-branch cases, plus the read path
  against a real stamped database and the CLI's exit codes.

```bash
.venv/bin/pytest tests/test_snapshot_before_migration.py tests/test_migration_fence.py \
  tests/scripts/test_check_migration_drift.py
```

---

## Isolated clone DB for experiments

Running experiments (new-city intake, schema changes, expensive enrichment
runs) against a copy of the dev DB requires an isolated Postgres that you
can throw away without touching the real dev database.

### Create a clone

```bash
scripts/clone-dev-db.sh <experiment-name> [<host-port>]
```

| Argument          | Description                                              |
| ----------------- | -------------------------------------------------------- |
| `experiment-name` | Becomes the Docker Compose project name (e.g. `mainland-intake`). |
| `host-port`       | Optional. If omitted, a free port in 5440-5480 is auto-selected. |

**What happens:**

1. Starts an isolated `postgres` container via `docker compose -p <name>`.
2. Waits up to 30 s for `pg_isready`.
3. Restores the most-recent `~/backups/agentic-bylaw-system/layer1-*.dump`.
4. Writes `.env.clone.<name>` in the current directory with `DATABASE_URL` exported.
5. Prints the URL and teardown one-liner.

**Example:**

```bash
scripts/clone-dev-db.sh mainland-intake
# => .env.clone.mainland-intake written
source .env.clone.mainland-intake
# DATABASE_URL now points at the isolated clone
python -m advisor.ingest ...
```

Override the backup directory with `BYLAW_BACKUP_DIR` if your dumps live
elsewhere.

### Tear down the clone

```bash
scripts/destroy-clone-db.sh <experiment-name>
```

Runs `docker compose -p <name> down -v`, removing both the container and
its data volume. Safe to run even if the containers are already stopped.

### Tests

`tests/test_clone_dev_db.py` fakes the `docker` CLI via a temp PATH shim
and asserts: env file written, port reflected in URL, most-recent dump
selected, nonzero exit when no backups exist, and `down -v` called by the
destroy script.

```bash
.venv/bin/pytest tests/test_clone_dev_db.py
```
