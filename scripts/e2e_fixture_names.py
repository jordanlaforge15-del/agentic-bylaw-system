"""Fixture bylaw-name guard: real bylaw names e2e fixtures must never claim.

ABS-431. Both the migration-0024 retrieval_enabled backfill and the ABS-355
relink pass (``relink_superseded_datasets``) match documents on
``(municipality, bylaw_name)``. A fixture document seeded under a real
bylaw's name therefore competes with the real document for the enabled flag
and can actively pull ``e2e_*`` geo datasets onto (or off) the real
document. The fix is naming discipline, enforced in one place:

* ``REAL_BYLAW_NAMES`` — the single source of truth for the real bylaw
  names in the corpus. Extend this tuple when a new real bylaw is ingested.
* ``has_e2e_marker`` — the marker predicate: a fixture name referencing a
  real bylaw must carry an explicit ``E2E`` token (e.g. ``"Regional Centre
  Land Use By-Law (Address Profile E2E)"``).
* ``fixture_bylaw_name_violation`` — the guard: no fixture ``bylaw_name``
  may *equal* a real bylaw name (case/hyphen/whitespace-insensitive), and
  any fixture name that *references* a real bylaw name must carry the E2E
  marker.

Enforced by ``tests/test_e2e_fixture_bylaw_names.py``, which AST-scans every
``scripts/seed_e2e_*.py`` for declared ``bylaw_name`` string constants.
ABS-433 (unified RC-LUB fixture) must name its document through this
convention — import this module rather than restating the list.

This module is import-side-effect free (safe from unit tests).
"""
from __future__ import annotations

import re

# The real bylaws present in the production corpus. Comparison is
# normalized (casefold; "By-Law"/"By-law"/"Bylaw"/"by law" all collide), so
# casing/hyphenation here is documentation, not load-bearing.
REAL_BYLAW_NAMES: tuple[str, ...] = (
    "Regional Centre Land Use By-law",
    "Halifax Peninsula Land Use By-law",
    "Halifax Mainland Land Use By-law",
)

E2E_MARKER = "E2E"


def _normalize(name: str) -> str:
    """Collapse the spellings under which two bylaw names collide.

    Casefold, then drop hyphens and whitespace outright so ``By-Law``,
    ``Bylaw`` and ``By law`` all collide. The (municipality, bylaw_name)
    match in migration-0024 and the relink pass is literal equality, but the
    guard is deliberately stricter: a fixture named ``"regional centre land
    use bylaw"`` is a collision waiting for a data-entry variation, not a
    distinct name.
    """
    return re.sub(r"[\s\-]+", "", name.casefold())


def has_e2e_marker(name: str) -> bool:
    """True when *name* carries the explicit E2E fixture marker."""
    return E2E_MARKER.casefold() in name.casefold()


def fixture_bylaw_name_violation(name: str) -> str | None:
    """Return a description of why *name* is an illegal fixture bylaw_name.

    Returns ``None`` when the name is safe. Two failure modes:

    * the name equals (normalized) a real bylaw name — always illegal,
      marker or not, because equality is exactly what the backfill and
      relink passes match on;
    * the name contains (normalized) a real bylaw name but lacks the E2E
      marker — it would read as the real document to a human and to any
      future fuzzy matching.
    """
    norm = _normalize(name)
    for real in REAL_BYLAW_NAMES:
        real_norm = _normalize(real)
        if norm == real_norm:
            return (
                f"fixture bylaw_name {name!r} equals real bylaw name {real!r} "
                f"(case/hyphen-insensitive); rename it with an "
                f"{E2E_MARKER!r} marker, e.g. {real + ' (My Feature E2E)'!r}"
            )
        if real_norm in norm and not has_e2e_marker(name):
            return (
                f"fixture bylaw_name {name!r} references real bylaw name "
                f"{real!r} but carries no {E2E_MARKER!r} marker"
            )
    return None
