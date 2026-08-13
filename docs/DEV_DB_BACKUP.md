# Local dev database backup

The local dev database is the `layer1` Postgres in the
`agentic-bylaw-system-postgres-1` container (named volume `layer1-postgres`).
Two scripts back it up nightly, on a daily / weekly / monthly rotation
(see [Retention](#retention)):

- `scripts/backup-dev-db.sh` — one-shot dump
- `scripts/install-backup-cron.sh` — install / remove the daily cron entry

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
  to it and survives indefinitely. Daily slots are never pruned either —
  they rotate in place.

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
disk yourself.

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
