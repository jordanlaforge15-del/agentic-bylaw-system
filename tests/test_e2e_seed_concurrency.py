"""ABS-207: meta-test that every e2e seed script callable from a spec's
per-worker ``beforeAll`` acquires a Postgres advisory lock before
mutating shared state.

Playwright runs four viewport projects in parallel; each project owns
its own worker, and any spec's ``beforeAll`` therefore fires once per
worker concurrently. Seed scripts that don't take a transaction-scoped
advisory lock race the unique-key inserts they depend on — one worker
wins, the others raise ``IntegrityError`` (or the equivalent) and the
spec fails with an unhelpful ``Command failed: <seed>.py`` shape.

This test enumerates the e2e seeds and asserts every spec-callable one
carries ``SELECT pg_advisory_xact_lock(...)``. Seeds called only from
``web/e2e/global-setup.ts`` (single-threaded) are exempt — that hook
runs once per ``npx playwright test`` invocation, before any worker
spins up.

If a new seed script is added without a lock, this test fires; the fix
is to mirror the pattern used by ``seed_e2e_evaluator_bylaws.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


# Seeds invoked from any spec's ``beforeAll`` — these run once per
# Playwright worker, so they must serialise via advisory lock.
SPEC_BEFORE_ALL_SEEDS: tuple[str, ...] = (
    "seed_e2e_abs270_structured.py",
    "seed_e2e_abs478_tokenization.py",
    # ABS-480 / ABS-488: both rebuild their fragments from the real parser, so
    # the lock also keeps one worker's get-or-create from overlapping another's.
    "seed_e2e_abs480_citation_collision.py",
    "seed_e2e_abs488_citation_repath.py",
    "seed_e2e_amendment_relink.py",
    "seed_e2e_axis_binding.py",
    "seed_e2e_contamination_marker.py",
    "seed_e2e_corpus_coherence.py",
    "seed_e2e_evaluator_bylaws.py",
    "seed_e2e_groundtruth_bylaws.py",
    "seed_e2e_mainland_permitted_use.py",
    # ABS-434: the enabled-name-collision pair fixture (per-test, uniquely
    # slugged, so races are impossible by construction; the lock keeps it
    # within the fleet-wide pattern anyway).
    "seed_e2e_name_collision.py",
    # ABS-461: rebuilds its fragments from the real parser on every run, so
    # the lock also keeps one worker's delete-and-rebuild from overlapping
    # another's.
    "seed_e2e_page_break_split.py",
    "seed_e2e_parcels.py",
    "seed_e2e_permitted_use.py",
    # ABS-433: the unified RC-LUB corpus seed — replaces the retired
    # seed_e2e_permission_tables / seed_e2e_address_profile / seed_e2e_pocs /
    # seed_e2e_zone_profile quartet.
    "seed_e2e_rclub_unified.py",
    "seed_e2e_submission_pdf.py",
    "seed_e2e_table_citations.py",
    "seed_e2e_tc005_permitted_use.py",
    "seed_e2e_tier_credits.py",
    # ABS-484: the matrix-with-holes zone corpus (undetermined uses).
    "seed_e2e_undetermined_uses.py",
    "seed_e2e_unknown_permission.py",
    "seed_e2e_user.py",
    "seed_e2e_verify_coverage.py",
    "seed_e2e_zoning.py",
)

# Seeds called only from ``web/e2e/global-setup.ts`` (single Playwright
# process, runs before any worker). These don't need the lock but having
# one is harmless — the test simply doesn't require it.
GLOBAL_SETUP_ONLY_SEEDS: tuple[str, ...] = (
    "seed_e2e_submission_ifc.py",
)


@pytest.mark.parametrize("seed_name", SPEC_BEFORE_ALL_SEEDS)
def test_spec_seed_uses_advisory_lock(seed_name: str) -> None:
    path = REPO_ROOT / "scripts" / seed_name
    assert path.exists(), f"Missing seed script: {path}"
    source = path.read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in source, (
        f"{seed_name} is invoked from a Playwright spec's beforeAll "
        f"(runs once per worker, concurrently across viewport "
        f"projects) but does not acquire pg_advisory_xact_lock. "
        f"Two workers will race the unique-key inserts and one will "
        f"fail with an IntegrityError that bubbles up as 'Command "
        f"failed: {seed_name}'. Mirror the advisory-lock pattern from "
        f"seed_e2e_evaluator_bylaws.py."
    )


def test_seed_inventory_is_exhaustive() -> None:
    """Catch a new e2e seed script slipping in unclassified.

    Every ``scripts/seed_e2e_*.py`` must appear in either
    ``SPEC_BEFORE_ALL_SEEDS`` (then carry an advisory lock) or
    ``GLOBAL_SETUP_ONLY_SEEDS`` (then explicitly opted out). A new seed
    added without classification will fail here, forcing the author to
    declare which bucket it belongs to.
    """
    on_disk = sorted(
        p.name for p in (REPO_ROOT / "scripts").glob("seed_e2e_*.py")
    )
    classified = set(SPEC_BEFORE_ALL_SEEDS) | set(GLOBAL_SETUP_ONLY_SEEDS)
    unclassified = [name for name in on_disk if name not in classified]
    assert not unclassified, (
        f"New e2e seed(s) found without a classification: "
        f"{unclassified}. Add each to SPEC_BEFORE_ALL_SEEDS (and give "
        f"it a pg_advisory_xact_lock) or to GLOBAL_SETUP_ONLY_SEEDS."
    )
