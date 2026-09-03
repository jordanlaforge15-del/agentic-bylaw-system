"""Normalized document-identity comparison (ABS-434).

The doc-15/38 double-enable happened because two enabled documents shared a
bylaw name modulo casing ("By-law" vs "By-Law"). Every exact-match pass —
the migration-0024 ``retrieval_enabled`` backfill, the ``enable-retrieval``
sibling detection, the ABS-355 relink — compares ``(municipality,
bylaw_name)`` with literal equality, so casing/whitespace/hyphenation drift
silently fragments the enabled corpus into names no pass can see as the
same bylaw.

This module is the single definition of "the same name modulo drift",
shared by:

* the enabled-name-collision audit
  (``bylaw_retrieval.retrieval.coherence_audit.audit_enabled_name_collisions``),
* the ``layer1 enable-retrieval`` normalized-sibling warning
  (``layer1.pipeline.publish.set_retrieval_enabled``),
* the ABS-431 e2e fixture-name guard (``scripts/e2e_fixture_names.py``),
  which extracted its normalizer here so the guard and the audit can never
  disagree about what collides.

Import-side-effect free (safe from unit tests and AST-scanned scripts).
"""
from __future__ import annotations

import re

_COLLAPSE = re.compile(r"[\s\-]+")


def normalize_bylaw_name(name: str) -> str:
    """Collapse the spellings under which two bylaw names collide.

    Casefold, then drop hyphens and whitespace outright so ``By-Law``,
    ``By-law``, ``Bylaw`` and ``By law`` all normalize identically. The
    exact-match passes compare literal equality; this is deliberately
    stricter — two names that differ only by case, hyphenation, or
    whitespace are one bylaw drifted apart, not two bylaws.

    Also applied to ``municipality`` when normalizing a document identity
    (see :func:`normalized_document_identity`) — the same drift fragments
    that column too.
    """
    return _COLLAPSE.sub("", name.casefold())


def normalized_document_identity(municipality: str, bylaw_name: str) -> tuple[str, str]:
    """The case/hyphen/whitespace-insensitive ``(municipality, bylaw_name)`` key.

    Two documents whose identities normalize equal are versions of the same
    bylaw as far as every publish/backfill/relink pass *should* be
    concerned — even where those passes' literal equality misses them.
    """
    return (normalize_bylaw_name(municipality), normalize_bylaw_name(bylaw_name))
