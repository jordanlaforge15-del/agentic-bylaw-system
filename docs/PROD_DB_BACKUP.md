# Production database backup (ABS-131)

The production `layer1` Postgres is the only copy of every user, case,
purchase and audit row this system has. `bylaw_submissions` — the other
stateful volume — is deliberately excluded from the backup story
([DEPLOYMENT.md](DEPLOYMENT.md#submission-artefact-storage-abs-87)), so
this dump *is* the disaster-recovery position.

Three scripts, run on the production host as `deploy`:

| Script | What it answers |
|---|---|
| `scripts/backup-prod-db.sh` | Do we have a backup, and is it offsite? |
| `scripts/verify-prod-backup.sh` | Does it restore? |
| `scripts/install-prod-backup-cron.sh` | Does it happen without anyone remembering? |

This is the production sibling of [DEV_DB_BACKUP.md](DEV_DB_BACKUP.md).
The dev script protects a laptop's working copy and stops at local disk;
this one verifies, encrypts and ships.

## What a run does

```
pg_dump -Fc  →  <slot>.staging
                  ↓  pg_restore --list, in a throwaway container
              verify: readable? carries the four system-of-record tables?
                  ↓  gpg --symmetric --cipher-algo AES256
              encrypt
                  ↓  atomic rename
              layer1-prod-<Dow>.dump.gpg      ← the day's artifact, mode 0600
                  ↓  promote if first run this ISO week
              layer1-prod-weekly-<ISO week>.dump.gpg
                  ↓  prune to 4 weekly slots
                  ↓  rsync --archive --delete
              Hetzner Storage Box
```

The dump stays in a `.staging` file until it has passed verification.
That ordering is the whole design: `if pg_dump; then mv` accepts a
truncated dump, because `pg_dump`'s exit status says nothing about the
bytes that reached the far end of a pipe. A rejected dump leaves
yesterday's known-good artifact exactly where it was.

Verification runs `pg_restore --list` inside a throwaway container built
from the production Postgres image, with the backup directory mounted
read-only. **The live database is never touched by its own backup check.**

## Retention

Two tiers, 11 artifacts, identical on both sides:

| Tier | Name | Slots | Rotation |
|---|---|---|---|
| daily | `layer1-prod-<Dow>.dump[.gpg]` | 7 | overwrite in place; day 8 replaces day 1 |
| weekly | `layer1-prod-weekly-<ISO week>.dump[.gpg]` | `BYLAW_KEEP_WEEKLY` (4) | promoted on the first run of each ISO week, pruned oldest-first |

> **These numbers are published.** The privacy policy's §5.0 tells users
> deleted data can persist in backups for "roughly one month", derived
> from 7 daily + 4 weekly. Changing `BYLAW_KEEP_WEEKLY` means changing
> that page too — `web/e2e/functional/abs131-privacy-backup-retention.spec.ts`
> fails if they drift apart.

Weekly promotion keys off "first run of this ISO week", not a fixed
weekday, so a night the host was down costs nothing — the next run that
week promotes instead.

Retention is computed **once, locally**. The Storage Box is then an
`rsync --delete` mirror of the pruned local set, which is the only way to
guarantee the two sides never disagree about what exists. The `--delete`
is guarded: if today's artifact is missing or empty, the mirror is
refused rather than propagating a broken local state offsite.

Anything not matching those two patterns — a `.manual` snapshot taken by
hand before a risky migration, say — is never touched by the prune. It
*is* mirrored offsite, so name hand-taken snapshots deliberately.

## Encryption, and the way it can bite you

Encryption is on whenever `BYLAW_BACKUP_PASSPHRASE_FILE` points at a
readable file. With no passphrase configured, `backup-prod-db.sh`
**refuses to run the offsite step at all** unless
`BYLAW_BACKUP_ALLOW_PLAINTEXT=1` is set explicitly. These artifacts carry
`advisor_user` and `advisor_case_purchase`; sending them to a third-party
box in the clear should be a decision someone made on purpose.

> **Store the passphrase somewhere other than the production host.**
>
> The failure this warning exists for: the server dies, you reach for the
> Storage Box, and the only copy of the passphrase was in
> `/srv/bylaw/backup.pass` on the server that died. Offsite backups you
> cannot decrypt are not backups. Put it in the operator's password
> manager *before* installing cron.

`verify-prod-backup.sh` decrypts with the passphrase currently on the box
every week, so the other half of that failure — the artifact is fine and
the key on disk is not the key that made it — surfaces within seven days.

## One-time setup

### 1. Hetzner Storage Box

Create a Storage Box in the Hetzner console (BX11 is ample — the dumps
are single-digit GB compressed) and enable **SSH support** in its
settings. Storage Boxes speak SSH/rsync on **port 23**, not 22.

> **Pick an EU location** (Falkenstein or Helsinki, not Ashburn or
> Singapore). The privacy policy tells users their data — including
> backups — stays in the European Union, and `web/e2e/functional/
> abs131-privacy-backup-retention.spec.ts` holds that claim in place. A
> non-EU Storage Box would make the published policy untrue.

Generate a dedicated key on the production host and register it:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/storagebox -N '' -C 'bylaw-prod backups'

# Storage Boxes have no ssh-copy-id; append to their authorized_keys directly.
cat ~/.ssh/storagebox.pub | ssh -p 23 u123456@u123456.your-storagebox.de \
  install-ssh-key

# Create the target directory and confirm the key works without a password.
ssh -p 23 -i ~/.ssh/storagebox u123456@u123456.your-storagebox.de \
  "mkdir -p backups/prod"
```

### 2. Passphrase

```bash
umask 077
openssl rand -base64 48 > /srv/bylaw/backup.pass
chmod 600 /srv/bylaw/backup.pass
```

Copy the value into the operator's password manager now. See the warning
above.

### 3. `/srv/bylaw/backup.env`

Both cron jobs source this file — cron does not read a login shell's
profile, so anything not in here is not set when the jobs run.

```sh
# /srv/bylaw/backup.env   chmod 600, deploy:deploy
BYLAW_PROD_BACKUP_DIR=/srv/bylaw/backups
BYLAW_BACKUP_PASSPHRASE_FILE=/srv/bylaw/backup.pass
BYLAW_STORAGE_BOX_TARGET=u123456@u123456.your-storagebox.de:backups/prod
BYLAW_STORAGE_BOX_SSH_KEY=/home/deploy/.ssh/storagebox
# BYLAW_KEEP_WEEKLY=4          # override only if disk pressure demands it
```

```bash
chmod 600 /srv/bylaw/backup.env
```

### 4. Dry run, then install cron

```bash
set -a; . /srv/bylaw/backup.env; set +a

/srv/bylaw/scripts/backup-prod-db.sh --dry-run   # changes nothing; prints the plan
                                                 # (and fails on a bad config,
                                                 #  so it doubles as a preflight)
/srv/bylaw/scripts/backup-prod-db.sh             # the real thing
/srv/bylaw/scripts/verify-prod-backup.sh --restore

/srv/bylaw/scripts/install-prod-backup-cron.sh
crontab -l
```

The installer schedules:

- **02:30 daily** — `backup-prod-db.sh`
- **04:00 Sunday** — `verify-prod-backup.sh --restore`, 90 minutes behind
  that morning's dump

Re-running the installer replaces its own entries and leaves unrelated
cron jobs alone. `--show` lists what's installed; `--uninstall` removes
only ours.

## Checking on it

```bash
tail -n 40 /srv/bylaw/backups/backup.log     # what each run did
tail -n 40 /srv/bylaw/backups/cron.log       # what cron saw
ls -lh /srv/bylaw/backups/                   # 11 artifacts once saturated

# Fast check of the newest artifact (seconds):
/srv/bylaw/scripts/verify-prod-backup.sh

# Full restore-and-query check (minutes):
/srv/bylaw/scripts/verify-prod-backup.sh --restore
```

A healthy weekly line reads:

```
PASS: layer1-prod-Sun.dump.gpg (full restore) advisor_user=41 advisor_case=118 advisor_usage_event=2907 alembic_version=1
```

The counts are the point. `PASS` with `advisor_user=0` would mean the
restore produced a schema and no data.

## Restoring for real

Nothing here is automatic — restoring over the live database is a
decision, not a script.

```bash
# 1. Pick an artifact and prove it restores, before touching production.
ls -lt /srv/bylaw/backups/
/srv/bylaw/scripts/verify-prod-backup.sh --restore \
  --file /srv/bylaw/backups/layer1-prod-weekly-2026-W33.dump.gpg

# 2. Decrypt it.
gpg --batch --decrypt --passphrase-file /srv/bylaw/backup.pass \
  --output /tmp/restore.dump \
  /srv/bylaw/backups/layer1-prod-weekly-2026-W33.dump.gpg

# 3. Stop everything that writes, so the restore is not racing live traffic.
cd /srv/bylaw && docker compose stop web advisor monitor

# 4. Restore. --clean --if-exists drops and recreates each object; without
#    it you get constraint violations on top of surviving rows.
docker exec -i bylaw-postgres pg_restore --clean --if-exists --no-owner \
  --exit-on-error -U layer1 -d layer1 < /tmp/restore.dump

# 5. Confirm the schema pointer, then bring the stack back.
docker exec bylaw-postgres psql -U layer1 -d layer1 -c 'SELECT * FROM alembic_version'
docker compose start advisor web monitor
curl -sf https://api.agenticbylawsystems.com/healthz | jq .

# 6. Shred the plaintext.
shred -u /tmp/restore.dump
```

If the whole host is gone, pull the artifact from the Storage Box first:

```bash
rsync -e 'ssh -p 23 -i ~/.ssh/storagebox' \
  u123456@u123456.your-storagebox.de:backups/prod/layer1-prod-Sun.dump.gpg .
```

Then follow [DEPLOYMENT.md](DEPLOYMENT.md) to rebuild the stack, and
restore into the fresh Postgres at step 4.

## Restoring to a point *before* a migration

`pg_restore --clean` restores the schema the dump was taken with,
including its `alembic_version` row. Running `alembic upgrade head`
afterwards replays only what's actually missing. Do not hand-edit
`alembic_version` to "fix" a mismatch — see
[DEPLOYMENT.md](DEPLOYMENT.md) on the `version_num` column width, which
is the other way that table has bitten this project.

## Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `BYLAW_PROD_PG_CONTAINER` | `bylaw-postgres` | container to dump from |
| `BYLAW_PROD_PG_DB` / `_USER` | `layer1` | database / role |
| `BYLAW_PROD_BACKUP_DIR` | `/srv/bylaw/backups` | local staging + rotation dir |
| `BYLAW_PG_IMAGE` | `bylaw-postgres:latest` | image used for verification and restore tests |
| `BYLAW_KEEP_WEEKLY` | `4` | weekly slots |
| `BYLAW_BACKUP_PASSPHRASE_FILE` | *(unset)* | enables gpg symmetric encryption |
| `BYLAW_BACKUP_ALLOW_PLAINTEXT` | `0` | `1` permits an unencrypted offsite copy |
| `BYLAW_STORAGE_BOX_TARGET` | *(unset)* | rsync target; unset means local-only |
| `BYLAW_STORAGE_BOX_PORT` | `23` | Hetzner Storage Box SSH port |
| `BYLAW_STORAGE_BOX_SSH_KEY` | `$HOME/.ssh/storagebox` | identity file |
| `BYLAW_VERIFY_TIMEOUT` | `60` | seconds to wait for the scratch Postgres |
| `BYLAW_BACKUP_DATE` | *(unset)* | pretend "today" is this date; test hook only |

Flags: `backup-prod-db.sh [--dry-run] [--no-offsite]`,
`verify-prod-backup.sh [--restore] [--file PATH] [--quiet]`,
`install-prod-backup-cron.sh [--show] [--uninstall]`.

## Tests

`tests/test_backup_prod_db.py`, `tests/test_verify_prod_backup.py` and
`tests/test_install_prod_backup_cron.py` drive the real scripts with
`docker`, `gpg`, `rsync` and `crontab` simulators on `PATH`
(`tests/prod_backup_shims.py`). They are simulators rather than mocks on
purpose: the fake `docker` reads its own dump payload back through
`pg_restore --list`, and the fake `gpg` really keys on the passphrase, so
a corrupt dump genuinely fails verification and a stale passphrase
genuinely fails decryption — instead of a test asserting that they would.
