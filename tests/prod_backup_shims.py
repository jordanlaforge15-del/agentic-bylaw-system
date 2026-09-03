"""Fake `docker` / `gpg` / `rsync` binaries for the ABS-131 backup tests.

`scripts/backup-prod-db.sh` and `scripts/verify-prod-backup.sh` only run on the
production host, against a live Postgres and a real Hetzner Storage Box. What
they *do* is nonetheless ordinary, testable logic — rotate slots, reject a bad
dump, refuse to ship plaintext — and that logic is the part that has to be
right the one time anyone reads a backup.

So the tests put shims for the three external commands on PATH and drive the
real scripts. The shims are not mocks that record calls and return canned
strings; they are small simulators:

* ``docker`` streams a payload that encodes which tables the dump contains, and
  its ``pg_restore --list`` reads that payload back — so a truncated or
  table-less dump genuinely fails verification rather than being asserted to.
* ``gpg`` really wraps and unwraps the bytes with the passphrase, so a
  wrong-passphrase restore genuinely fails.
* ``rsync`` really mirrors one directory onto another and really honours
  ``--delete``, so retention drift between the two sides would show up.

Payload format written by the fake pg_dump (two lines, then padding):

    PGDMP-<date>
    tables=advisor_user,advisor_case,...
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup-prod-db.sh"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify-prod-backup.sh"
CRON_SCRIPT = REPO_ROOT / "scripts" / "install-prod-backup-cron.sh"

# Must match REQUIRED_TABLES in both scripts.
REQUIRED_TABLES = ["advisor_user", "advisor_case", "advisor_usage_event", "alembic_version"]

# Must match the defaults documented in docs/PROD_DB_BACKUP.md.
KEEP_DAILY = 7
KEEP_WEEKLY = 4
MAX_SLOTS = KEEP_DAILY + KEEP_WEEKLY  # 11


_DOCKER_SHIM = r"""#!/usr/bin/env bash
# Fake docker for tests/test_backup_prod_db.py and tests/test_verify_prod_backup.py.
set -u
STATE="${FAKE_DOCKER_STATE:?FAKE_DOCKER_STATE must be set}"
mkdir -p "$STATE"
printf '%s\n' "$*" >> "$STATE/calls.log"

TABLES="${FAKE_PGDUMP_TABLES:-advisor_user,advisor_case,advisor_usage_event,alembic_version}"
MODE="${FAKE_PGDUMP_MODE:-ok}"
RUNNING="${FAKE_CONTAINER_RUNNING:-true}"

# Emit a pg_restore --list style table of contents for a payload file.
emit_toc() {
  local payload="$1" line tables
  if [ ! -s "$payload" ]; then
    echo "pg_restore: error: input file is too short (read 0, expected 5)" >&2
    return 1
  fi
  head -n 1 "$payload" | grep -q '^PGDMP-' || {
    echo "pg_restore: error: did not find magic string in file header" >&2
    return 1
  }
  tables="$(sed -n '2p' "$payload" | sed 's/^tables=//')"
  echo ";"
  echo "; Archive created at 2026-08-27 02:30:00 ADT"
  echo ";"
  local n=200
  local IFS=,
  for t in $tables; do
    [ -n "$t" ] || continue
    printf '%d; 1259 %d TABLE public %s layer1\n' "$n" "$((16000 + n))" "$t"
    printf '%d; 0 %d TABLE DATA public %s layer1\n' "$((n + 1))" "$((16000 + n))" "$t"
    n=$((n + 2))
  done
}

# Resolve a container path like /backups/foo.dump back to the host path, using
# the -v mount recorded for this invocation (or for a named container).
resolve() {
  local mount="$1" path="$2"
  printf '%s/%s\n' "$mount" "$(basename "$path")"
}

verb="$1"; shift

case "$verb" in
  inspect)
    echo "$RUNNING"
    exit 0
    ;;

  exec)
    # docker exec [-i] <container> <cmd> ...
    while [ "${1:-}" = "-i" ] || [ "${1:-}" = "-t" ]; do shift; done
    cname="$1"; shift
    cmd="$1"; shift
    case "$cmd" in
      pg_dump)
        if [ "$RUNNING" != "true" ]; then
          echo "Error: No such container: $cname" >&2
          exit 1
        fi
        case "$MODE" in
          fail)     echo "pg_dump: error: connection failed" >&2; exit 1 ;;
          truncated) printf 'not-a-postgres-archive\n' ;;
          empty)    : ;;  # zero bytes
          notables) printf 'PGDMP-%s\ntables=\n' "${BYLAW_BACKUP_DATE:-today}" ;;
          *)        printf 'PGDMP-%s\ntables=%s\n' "${BYLAW_BACKUP_DATE:-today}" "$TABLES" ;;
        esac
        exit 0
        ;;
      pg_isready)
        [ -f "$STATE/$cname.up" ] && exit 0
        exit 1
        ;;
      pg_restore)
        mount="$(cat "$STATE/$cname.mount" 2>/dev/null || true)"
        target="${@: -1}"
        payload="$(resolve "$mount" "$target")"
        emit_toc "$payload" >/dev/null || exit 1
        # FAKE_RESTORE_DROP lets a test make the restored database disagree
        # with the archive's table of contents — the case where only a query
        # against the restored data can tell you the recovery is incomplete.
        sed -n '2p' "$payload" | sed 's/^tables=//' \
          | tr ',' '\n' | grep -vxF "${FAKE_RESTORE_DROP:-__none__}" | paste -sd, - \
          > "$STATE/$cname.restored"
        exit 0
        ;;
      psql)
        query="${@: -1}"
        table="$(printf '%s' "$query" | sed -n 's/.*FROM \([a-z_]*\).*/\1/p')"
        restored="$(cat "$STATE/$cname.restored" 2>/dev/null || true)"
        if ! printf '%s' ",$restored," | grep -q ",$table,"; then
          echo "ERROR:  relation \"$table\" does not exist" >&2
          exit 1
        fi
        if [ "$table" = "alembic_version" ]; then
          echo "${FAKE_ALEMBIC_ROWS:-1}"
        else
          echo "${FAKE_ROW_COUNT:-3}"
        fi
        exit 0
        ;;
      *)
        echo "fake docker exec: unsupported command $cmd" >&2
        exit 2
        ;;
    esac
    ;;

  run)
    mount=""; name=""; detach=0; image=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --rm) ;;
        -d|--detach) detach=1 ;;
        --name) shift; name="$1" ;;
        --network) shift ;;
        -e) shift ;;
        -v) shift; mount="${1%%:*}" ;;
        -*) ;;
        *) image="$1"; shift; break ;;
      esac
      shift
    done
    if [ "$detach" -eq 1 ]; then
      [ -n "$name" ] || name="anon-$$"
      printf '%s\n' "$mount" > "$STATE/$name.mount"
      if [ "${FAKE_SCRATCH_READY:-1}" = "1" ]; then touch "$STATE/$name.up"; fi
      echo "fake-container-id-$name"
      exit 0
    fi
    # Foreground: only `pg_restore --list <path>` is used this way.
    cmd="${1:-}"
    target="${@: -1}"
    if [ "$cmd" != "pg_restore" ]; then
      echo "fake docker run: unsupported command $cmd" >&2
      exit 2
    fi
    emit_toc "$(resolve "$mount" "$target")"
    exit $?
    ;;

  rm)
    for a in "$@"; do
      case "$a" in
        -*) ;;
        *) rm -f "$STATE/$a.up" "$STATE/$a.mount" "$STATE/$a.restored" ;;
      esac
    done
    exit 0
    ;;

  *)
    echo "fake docker: unsupported verb $verb" >&2
    exit 2
    ;;
esac
"""


_GPG_SHIM = r"""#!/usr/bin/env bash
# Fake gpg: really wraps/unwraps bytes keyed by the passphrase, so a
# wrong-passphrase decrypt genuinely fails the way the real thing would.
set -u
STATE="${FAKE_DOCKER_STATE:?}"
printf 'gpg %s\n' "$*" >> "$STATE/calls.log"

MODE=""
PASSFILE=""
OUT=""
IN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --symmetric) MODE=encrypt ;;
    --decrypt|-d) MODE=decrypt ;;
    --passphrase-file) shift; PASSFILE="$1" ;;
    --output|-o) shift; OUT="$1" ;;
    --batch|--yes|--quiet) ;;
    --cipher-algo) shift ;;
    -*) ;;
    *) IN="$1" ;;
  esac
  shift
done

if [ "${FAKE_GPG_MODE:-ok}" = "fail" ]; then
  echo "gpg: encryption failed: No such file or directory" >&2
  exit 2
fi

PASS="$(cat "$PASSFILE" 2>/dev/null || true)"
if [ "$MODE" = "encrypt" ]; then
  { printf 'GPGWRAP[%s]\n' "$PASS"; cat "$IN"; } > "$OUT"
  exit 0
fi

header="$(head -n 1 "$IN")"
if [ "$header" != "GPGWRAP[$PASS]" ]; then
  echo "gpg: decryption failed: Bad session key" >&2
  exit 2
fi
tail -n +2 "$IN" > "$OUT"
exit 0
"""


_RSYNC_SHIM = r"""#!/usr/bin/env bash
# Fake rsync: mirrors SRC onto DEST, honouring --delete and --exclude, where
# DEST is `fakebox:<real local path>`. Retention drift between the local set
# and the offsite set therefore shows up as a real file-listing difference.
set -u
STATE="${FAKE_DOCKER_STATE:?}"
printf 'rsync %s\n' "$*" >> "$STATE/calls.log"

if [ "${FAKE_RSYNC_MODE:-ok}" = "fail" ]; then
  echo "rsync: connection unexpectedly closed" >&2
  exit 12
fi

DELETE=0
EXCLUDES=""
POSITIONAL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --delete) DELETE=1 ;;
    --exclude=*) EXCLUDES="$EXCLUDES ${1#--exclude=}" ;;
    --exclude) shift; EXCLUDES="$EXCLUDES $1" ;;
    -e) shift ;;
    -*) ;;
    *) POSITIONAL="$POSITIONAL $1" ;;
  esac
  shift
done

set -- $POSITIONAL
SRC="$1"; DEST="$2"
DEST="${DEST#fakebox:}"
mkdir -p "$DEST"

if [ "$DELETE" -eq 1 ]; then
  find "$DEST" -mindepth 1 -delete
fi

for path in "$SRC"/*; do
  [ -e "$path" ] || continue
  base="$(basename "$path")"
  skip=0
  for pattern in $EXCLUDES; do
    case "$base" in $pattern) skip=1 ;; esac
  done
  [ "$skip" -eq 0 ] || continue
  cp -p "$path" "$DEST/$base"
done
exit 0
"""


_CRONTAB_SHIM = r"""#!/usr/bin/env bash
# Fake crontab backed by a plain file, so the installer's read/filter/write
# cycle is exercised against something that behaves like the real thing.
set -u
FILE="${FAKE_CRONTAB_FILE:?FAKE_CRONTAB_FILE must be set}"

case "${1:-}" in
  -l)
    [ -s "$FILE" ] || { echo "no crontab for tester" >&2; exit 1; }
    cat "$FILE"
    ;;
  -r)
    rm -f "$FILE"
    ;;
  -|"")
    cat > "$FILE"
    ;;
  *)
    echo "fake crontab: unsupported flag $1" >&2
    exit 2
    ;;
esac
exit 0
"""


SHIMS = {
    "docker": _DOCKER_SHIM,
    "gpg": _GPG_SHIM,
    "rsync": _RSYNC_SHIM,
    "crontab": _CRONTAB_SHIM,
}


def write_shims(bin_dir: Path) -> None:
    """Drop the fake docker/gpg/rsync onto a directory destined for PATH."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in SHIMS.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)


def base_env(*, bin_dir: Path, state_dir: Path, backup_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["FAKE_DOCKER_STATE"] = str(state_dir)
    env["BYLAW_PROD_BACKUP_DIR"] = str(backup_dir)
    env["BYLAW_PROD_PG_CONTAINER"] = "fake-bylaw-postgres"
    env["BYLAW_PG_IMAGE"] = "fake-bylaw-postgres:latest"
    # Neutralise anything the developer happens to have exported.
    for leaked in (
        "BYLAW_BACKUP_PASSPHRASE_FILE",
        "BYLAW_BACKUP_ALLOW_PLAINTEXT",
        "BYLAW_STORAGE_BOX_TARGET",
        "BYLAW_KEEP_WEEKLY",
        "BYLAW_BACKUP_DATE",
    ):
        env.pop(leaked, None)
    return env


def run_script(
    script: Path, env: dict[str, str], args: list[str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script), *(args or [])],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def artifacts(backup_dir: Path) -> list[str]:
    """Every rotation artifact, encrypted or not, in sorted order."""
    return sorted(
        p.name
        for p in backup_dir.glob("layer1-prod-*")
        if p.suffix in {".dump", ".gpg"}
    )
