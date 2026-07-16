# Local dev database backup

The local dev database is the `layer1` Postgres in the
`agentic-bylaw-system-postgres-1` container (named volume `layer1-postgres`).
Two scripts back it up on a daily rotation:

- `scripts/backup-dev-db.sh` — one-shot dump
- `scripts/install-backup-cron.sh` — install / remove the daily cron entry

## Where backups live

```
$HOME/backups/agentic-bylaw-system/
  layer1-Mon.dump
  layer1-Tue.dump
  ...
  layer1-Sun.dump
  backup.log
```

Each day's dump is keyed by short day-of-week (`Mon`..`Sun`), so day 8
overwrites day 1 in place. Disk usage stays bounded with no separate
prune step. Dumps use `pg_dump -Fc` (custom format) so they restore
with `pg_restore` and support partial / parallel restore.

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

Stop anything writing to the DB, then:

```bash
# Wipe the current DB and replay the dump.
docker exec -i agentic-bylaw-system-postgres-1 \
  dropdb -U layer1 --if-exists layer1
docker exec -i agentic-bylaw-system-postgres-1 \
  createdb -U layer1 layer1
docker exec -i agentic-bylaw-system-postgres-1 \
  pg_restore -U layer1 -d layer1 --no-owner \
  < $HOME/backups/agentic-bylaw-system/layer1-Mon.dump
```

Swap `layer1-Mon.dump` for whichever day you want to restore from.

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

## Tests

`tests/test_backup_dev_db.py` fakes the `docker` CLI via a temp PATH
shim and asserts: per-DOW filename, in-place overwrite on re-run, and
nonzero exit + log line when the container isn't running.

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
