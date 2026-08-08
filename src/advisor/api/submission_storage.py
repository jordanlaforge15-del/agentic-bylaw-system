"""On-disk staging for uploaded submission artefacts (ABS-87).

Both the interactive submissions router (ABS-53) and the API-key
integrations router (ABS-59) have to stage an upload to a *real file*
before handing it to the pipeline — the IFC and APS/PDF extractors want
an on-disk path, not a stream. That makes ``SUBMISSION_STORAGE_DIR`` a
hard runtime dependency of the upload endpoints.

In production the advisor container runs with ``read_only: true`` and
only a small ``/tmp`` tmpfs writable, so the upload path works **only**
when a writable volume is mounted at ``SUBMISSION_STORAGE_DIR``. ABS-70
found this the hard way: the router used to ``mkdir`` its storage root
at app-construction time and the container crashed on boot with
``OSError [Errno 30] Read-only file system``. Commit 600f747 removed the
eager mkdir so the app boots, which moved the failure to the first real
upload.

This module closes that gap from the application side:

* :func:`stage_upload` is the single place either router writes an
  upload, so an unwritable storage root produces one clear
  ``503 submission_storage_unavailable`` naming the offending path
  instead of an opaque 500 with an ``OSError`` traceback.
* :func:`probe_storage_root` is a cheap, non-destructive writability
  check that ``GET /healthz`` reports as the ``submission_storage``
  check — so a missing volume is visible *before* a user finds it, and
  a post-deploy verification is a single curl.

The infrastructure half of the fix (the named volume + the env var that
points at it) lives in ``docker-compose.production.yml`` and
``docs/DEPLOYMENT.md``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO

from fastapi import HTTPException

from layer1.config import get_settings

logger = logging.getLogger(__name__)

#: Chunk size for streaming an upload to disk (1 MiB).
_CHUNK_BYTES = 1 << 20

#: Values :func:`probe_storage_root` can return.
STORAGE_OK = "ok"
STORAGE_UNWRITABLE = "unwritable"


def resolve_storage_root(storage_dir: Path | str | None = None) -> Path:
    """Return the submission storage root.

    ``storage_dir`` wins when supplied (tests pass a tmp path); otherwise
    the config-driven ``SUBMISSION_STORAGE_DIR`` is used.
    """
    if storage_dir is not None:
        return Path(storage_dir)
    return Path(get_settings().submission_storage_dir)


def probe_storage_root(root: Path | str | None = None) -> str:
    """Report whether uploads could be staged under ``root``.

    Returns :data:`STORAGE_OK` or :data:`STORAGE_UNWRITABLE`. The probe is
    read-only — it walks up to the nearest existing ancestor and asks the
    kernel via :func:`os.access`, which accounts for both permission bits
    and a read-only mount (``EROFS``). Nothing is created or written, so
    this is safe to call on every ``/healthz`` hit (the availability
    monitor polls it once a minute).

    The ancestor walk matters: with the volume mounted, ``root`` itself
    exists and is checked directly. Without it, ``root`` is missing and
    we end up asking whether the deepest existing parent — ``/app`` for
    the default relative ``data/submissions`` — would accept the
    ``mkdir``, which under ``read_only: true`` it will not.
    """
    probe = Path(resolve_storage_root(root))
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            # Walked to the filesystem root without finding anything.
            return STORAGE_UNWRITABLE
        probe = parent

    if not probe.is_dir():
        return STORAGE_UNWRITABLE
    # W_OK to create entries, X_OK to traverse into it.
    return STORAGE_OK if os.access(probe, os.W_OK | os.X_OK) else STORAGE_UNWRITABLE


def stage_upload(
    root: Path,
    *,
    user_id: int,
    filename: str,
    fileobj: IO[bytes],
) -> Path:
    """Stream ``fileobj`` to ``<root>/user-<user_id>/<filename>``.

    Creates the per-user directory lazily (never at import or app-build
    time — see the module docstring). Any :class:`OSError` from the
    ``mkdir`` or the write is translated into a
    ``503 submission_storage_unavailable``: it is an operator-fixable
    deployment fault (missing volume, full disk, wrong ownership), not a
    bad request, and the caller should retry once it's fixed.

    ``filename`` is reduced to its basename — a client-supplied
    ``../../etc/cron.d/x`` must not escape the user's directory.
    """
    safe_name = Path(filename).name
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_filename",
                "message": f"Upload filename is not usable: {filename!r}.",
            },
        )

    user_dir = root / f"user-{user_id}"
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
        target_path = user_dir / safe_name
        with target_path.open("wb") as out:
            while True:
                chunk = fileobj.read(_CHUNK_BYTES)
                if not chunk:
                    break
                out.write(chunk)
    except OSError as exc:
        logger.exception("submission storage is not writable: root=%s", root)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "submission_storage_unavailable",
                "message": (
                    f"Submission storage at {root} is not writable "
                    f"({exc.strerror or exc}). The deployment needs a writable "
                    "volume mounted at SUBMISSION_STORAGE_DIR — see "
                    "docs/DEPLOYMENT.md."
                ),
            },
        ) from exc

    return target_path


__all__ = [
    "STORAGE_OK",
    "STORAGE_UNWRITABLE",
    "probe_storage_root",
    "resolve_storage_root",
    "stage_upload",
]
