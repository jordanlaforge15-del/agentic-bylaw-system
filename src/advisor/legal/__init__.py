"""Terms-and-Conditions versioning + acceptance recording.

This module is the single place that knows three things:

1. **Where the canonical T&C document lives** —
   ``docs/TERMS_AND_CONDITIONS.md`` at the repo root. Treating the
   file in git as the source-of-truth means a wording change is
   reviewable in a PR like any other change, and the document is
   atomic with the code that serves it.

2. **What "the current version" is** — the sha256 hex of the file's
   bytes. Reading the file once at import and caching the hash keeps
   the request-time path cheap (a string compare) and makes "the
   document changed" detectable without an out-of-band version field
   that an operator could forget to bump. Edit the markdown → new
   hash → existing acceptance rows no longer match the current
   version → users get re-prompted on next login. That is the §16
   "material amendment" mechanic the document itself describes.

3. **Whether a given user has accepted the current version** — an
   ``EXISTS`` query against ``advisor_terms_acceptance`` filtered by
   ``(user_id, version_hash=CURRENT)``. Used by:

   * the FastAPI ``GET /v1/terms/current`` route to populate the
     ``accepted`` flag in the response body;
   * the ``require_accepted_current_terms`` FastAPI dependency that
     guards ``POST /v1/chat`` (and, when they ship, the future
     ``/v1/keys`` endpoints) — direct API callers can't bypass the
     Next.js redirect-to-/app/terms gate;
   * the Next.js ``/app`` server-side check that decides whether to
     redirect a freshly-signed-in user to ``/app/terms``.

The document body is loaded once at module import. In a long-running
production process this means editing the file requires a restart
before the new hash takes effect. That's intentional — we don't want
two web replicas serving different bodies during a rolling deploy. In
tests, ``_load_terms.cache_clear()`` resets the cache so a test can
override the document path via ``set_terms_document_path_for_tests``.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from advisor.db.models import TermsAcceptance, User

logger = logging.getLogger(__name__)


# Resolve to ``<repo_root>/docs/TERMS_AND_CONDITIONS.md``. This file
# lives at ``<repo_root>/src/advisor/legal/__init__.py``, so the repo
# root is three parents up from this file's directory.
_DEFAULT_TERMS_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "TERMS_AND_CONDITIONS.md"
)

# Module-level override used only by tests. Production code never
# touches this; use ``set_terms_document_path_for_tests`` from a test
# fixture and pair it with ``_load_terms.cache_clear()``.
_terms_path_override: Path | None = None


@dataclass(frozen=True)
class CurrentTerms:
    """Snapshot of the live T&C document.

    ``body`` is the verbatim markdown the user is shown on the
    acceptance screen. ``version_hash`` is the sha256 hex of that
    same body — the value the acceptance row gets written with.
    """

    body: str
    version_hash: str


def set_terms_document_path_for_tests(path: Path | None) -> None:
    """Test-only: override the path used by ``_load_terms``.

    Pass ``None`` to clear the override. Always pair with
    ``_load_terms.cache_clear()`` so the next call re-reads from the
    new path.
    """
    global _terms_path_override
    _terms_path_override = path


@lru_cache(maxsize=1)
def _load_terms() -> CurrentTerms:
    """Read the markdown body and compute its sha256 hex.

    Cached so the per-request path is a dictionary lookup. In tests,
    call ``_load_terms.cache_clear()`` after
    ``set_terms_document_path_for_tests`` to force a re-read.
    """
    path = _terms_path_override or _DEFAULT_TERMS_PATH
    try:
        body = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        # Failing loud is correct here — without the document the
        # acceptance screen would render empty and the user would
        # click I Agree on nothing. The deploy is misconfigured.
        raise RuntimeError(
            f"Terms document not found at {path}. The advisor cannot "
            "serve the T&C screen without it. Confirm the file is "
            "shipped in the container image."
        ) from exc
    version_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return CurrentTerms(body=body, version_hash=version_hash)


def get_current_terms() -> CurrentTerms:
    """Return the current T&C body + version hash (cached)."""
    return _load_terms()


def user_has_accepted_current_terms(db: Session, user: User) -> bool:
    """Has ``user`` accepted the live T&C version?

    Implemented as a single ``SELECT 1 WHERE EXISTS`` so it costs one
    indexed lookup on ``(user_id, version_hash)`` and never loads a
    full row into the session.
    """
    current = get_current_terms()
    stmt = select(
        exists().where(
            TermsAcceptance.user_id == user.id,
            TermsAcceptance.version_hash == current.version_hash,
        )
    )
    return bool(db.execute(stmt).scalar())


def record_acceptance(
    db: Session,
    *,
    user: User,
    version_hash: str,
    ip: str | None,
    user_agent: str | None,
) -> TermsAcceptance:
    """Insert an acceptance row, idempotent on ``(user_id, version_hash)``.

    The unique constraint catches a double-click race or a retried
    request: the second insert raises ``IntegrityError``, we roll back
    and return the row that the first request wrote. This keeps the
    acceptance API safe to retry without leaving duplicates.

    The caller is responsible for committing the surrounding
    transaction — keeping the commit out of this helper means it
    composes inside a larger unit of work (e.g. a future bundled
    "sign-in + accept" flow).
    """
    ua_clipped = user_agent[:500] if user_agent else None
    ip_clipped = ip[:64] if ip else None
    row = TermsAcceptance(
        user_id=user.id,
        version_hash=version_hash,
        ip=ip_clipped,
        user_agent=ua_clipped,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # Race / retry: another request inserted the same
        # (user, version) pair. Roll back this insert and return the
        # already-stored row so the caller sees a uniform success.
        db.rollback()
        existing = (
            db.query(TermsAcceptance)
            .filter(
                TermsAcceptance.user_id == user.id,
                TermsAcceptance.version_hash == version_hash,
            )
            .one()
        )
        return existing
    return row
