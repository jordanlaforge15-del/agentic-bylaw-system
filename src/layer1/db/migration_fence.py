"""Refuse to mutate the dev database without a labelled snapshot first (ABS-499).

Nothing used to take a snapshot before a data migration ran. During Data Model
3.0, ABS-488's repath rewrote citation paths corpus-wide and ABS-480's status
backfill touched 834 rows — both between two nightly dumps. The nightly job
(``scripts/backup-dev-db.sh``) writes into a 7-slot day-of-week rotation, so the
pre-change state of either run could have been overwritten inside a single
rotation cycle. It survived on timing luck alone.

This module is the fence. Call :func:`snapshot_before_migration` before the
first write of anything that mutates dev data; it shells out to
``scripts/snapshot-before-migration.sh``, which writes a rotation-exempt
labelled dump, and raises :class:`SnapshotFenceError` if that fails. Callers
must not proceed on failure — the whole point is that a lost pre-change state is
worse than a migration that did not run.

Scope gate
----------
The fence engages **only when the target DSN is the local dev database** (see
:func:`targets_dev_database`). The e2e stack (``layer1_test`` on its own
per-worktree port), throwaway clones, CI, and production all fall outside it:
their state is either disposable or backed up by something other than this
laptop's cron. That keeps ``alembic upgrade`` in the e2e boot path free of a
multi-hundred-megabyte dump it does not need.

Environment overrides
---------------------
``BYLAW_SKIP_MIGRATION_SNAPSHOT``
    Truthy (``1``/``true``/``yes``) disables the fence. Logged at WARNING —
    skipping is a deliberate, visible act, not a default.
``BYLAW_FORCE_MIGRATION_SNAPSHOT``
    Truthy engages the fence for *any* target, bypassing the scope gate — for
    fencing a clone, or for tests that need the fence without a dev DB.
    ``BYLAW_SKIP_MIGRATION_SNAPSHOT`` still wins.
``BYLAW_SNAPSHOT_SCRIPT``
    Path to the snapshot script. Defaults to the copy in this checkout.
``BYLAW_DEV_PG_PORT`` / ``BYLAW_PG_DB``
    What counts as "the dev database" for the scope gate (5432 / ``layer1``).
``BYLAW_SNAPSHOT_TIMEOUT_S``
    Seconds to allow ``pg_dump`` (default 1800).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_TIMEOUT_S = 1800
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
_TRUTHY = {"1", "true", "yes", "on"}

# src/layer1/db/migration_fence.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]


class SnapshotFenceError(RuntimeError):
    """The pre-migration snapshot could not be taken; do not proceed."""


def snapshot_script_path() -> Path:
    override = os.getenv("BYLAW_SNAPSHOT_SCRIPT")
    if override:
        return Path(override)
    return _REPO_ROOT / "scripts" / "snapshot-before-migration.sh"


def _resolve_url(database_url: str | None) -> str:
    if database_url:
        return database_url
    # Import lazily: config pulls in pydantic-settings and the .env file, and
    # callers that pass an explicit URL should not pay for that.
    from layer1.config import get_settings

    return get_settings().database_url


def targets_dev_database(database_url: str | None = None) -> bool:
    """True when ``database_url`` points at the local dev Postgres.

    Deliberately narrow: local host, the dev port, and the dev database name
    must *all* match. Anything else — e2e's ``layer1_test`` on a per-worktree
    port, a clone on 5440+, prod behind a container hostname — is out of scope
    and left unfenced.
    """
    try:
        url = make_url(_resolve_url(database_url))
    except (ArgumentError, ValueError):  # pragma: no cover - malformed DSN
        return False
    if not url.drivername.startswith("postgresql"):
        return False
    host = (url.host or "").lower()
    if host not in _LOCAL_HOSTS:
        return False
    dev_port = int(os.getenv("BYLAW_DEV_PG_PORT", "5432"))
    if (url.port or 5432) != dev_port:
        return False
    dev_db = os.getenv("BYLAW_PG_DB", "layer1")
    return (url.database or "") == dev_db


def _snapshots_disabled() -> bool:
    if os.getenv("BYLAW_SKIP_MIGRATION_SNAPSHOT", "").strip().lower() in _TRUTHY:
        return True
    # GitHub Actions' pytest job runs migrations against a DSN byte-identical
    # to the dev laptop's (localhost:5432/layer1) on a container that is
    # destroyed with the job. The workflow opts out explicitly too; this is the
    # belt to that pair of braces, so a future workflow that migrates does not
    # fail on a snapshot of a database nobody wants.
    return os.getenv("GITHUB_ACTIONS", "").strip().lower() in _TRUTHY


def _snapshots_forced() -> bool:
    return os.getenv("BYLAW_FORCE_MIGRATION_SNAPSHOT", "").strip().lower() in _TRUTHY


def warn_on_drift(database_url: str | None, log: logging.Logger) -> None:
    """Say so when the target DB is behind the migrations on this branch.

    Applying data migrations on top of a pending *schema* migration is exactly
    how DM3.0 produced its split state, and the moment a data migration is about
    to run is the moment that is worth knowing. A warning, not a block — the
    combination is sometimes deliberate, and the fence's job is to be loud.
    """
    from layer1.db.migration_drift import drift_report

    report = drift_report(_resolve_url(database_url))
    if report.error:
        log.debug("drift check inconclusive: %s", report.error)
        return
    if report.is_behind:
        log.warning("MIGRATION DRIFT: %s", report.summary_line())
        log.warning("  run `make check-migration-drift` for the full report")


def snapshot_before_migration(
    tag: str,
    *,
    database_url: str | None = None,
    log: logging.Logger | None = None,
    check_drift: bool = True,
) -> Path | None:
    """Take a labelled snapshot before a dev-data mutation.

    Returns the snapshot path, or ``None`` when the fence does not apply (the
    target is not the dev DB) or was explicitly disabled. Raises
    :class:`SnapshotFenceError` when a snapshot was required but could not be
    written — callers must abort rather than swallow it.
    """
    log = log or logger

    if _snapshots_disabled():
        log.warning(
            "BYLAW_SKIP_MIGRATION_SNAPSHOT is set — proceeding with %r "
            "WITHOUT a pre-migration snapshot",
            tag,
        )
        return None

    if not _snapshots_forced() and not targets_dev_database(database_url):
        log.debug("snapshot fence skipped for %r: target is not the dev database", tag)
        return None

    script = snapshot_script_path()
    if not script.exists():
        raise SnapshotFenceError(
            f"pre-migration snapshot script not found at {script}; refusing to "
            f"run {tag!r} against the dev database. Set BYLAW_SNAPSHOT_SCRIPT, or "
            f"BYLAW_SKIP_MIGRATION_SNAPSHOT=1 to proceed unfenced."
        )

    timeout_s = int(os.getenv("BYLAW_SNAPSHOT_TIMEOUT_S", str(DEFAULT_SNAPSHOT_TIMEOUT_S)))
    log.info("taking pre-migration snapshot for %r ...", tag)
    try:
        result = subprocess.run(
            [str(script), tag],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotFenceError(
            f"pre-migration snapshot for {tag!r} timed out after {timeout_s}s"
        ) from exc
    except OSError as exc:
        raise SnapshotFenceError(
            f"pre-migration snapshot for {tag!r} could not be started: {exc}"
        ) from exc

    if result.returncode != 0:
        raise SnapshotFenceError(
            f"pre-migration snapshot for {tag!r} failed (exit {result.returncode}); "
            f"refusing to mutate the dev database.\n{result.stderr.strip()}"
        )

    path_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not path_line:
        raise SnapshotFenceError(
            f"pre-migration snapshot for {tag!r} reported success but named no file"
        )

    snapshot = Path(path_line)
    log.info("pre-migration snapshot for %r written to %s", tag, snapshot)

    if check_drift:
        warn_on_drift(database_url, log)

    return snapshot


ABORT_EXIT_CODE = 3


def fence_or_abort(
    tag: str,
    *,
    database_url: str | None = None,
    log: logging.Logger | None = None,
    check_drift: bool = True,
) -> Path | None:
    """CLI wrapper for :func:`snapshot_before_migration`.

    Scripts call this immediately before their first write. On failure it
    prints a one-line reason and exits ``3`` — a clean abort before any row is
    touched, not a traceback.
    """
    try:
        return snapshot_before_migration(
            tag, database_url=database_url, log=log, check_drift=check_drift
        )
    except SnapshotFenceError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        raise SystemExit(ABORT_EXIT_CODE) from exc
