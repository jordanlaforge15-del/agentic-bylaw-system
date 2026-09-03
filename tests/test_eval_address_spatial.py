"""ABS-471 (G1): every eval address, asserted against the zoning polygons.

``evals/regional_centre_test_prompts.json`` pairs a ``zone`` with an ``address``
on every case, and both were operator inputs. 17 of 20 disagreed: 7 resolved
into a different zone, 5 named addresses that do not exist, 3 geocoded 5-12 m
off into the road right-of-way and matched no polygon at all. A case claiming
CEN-1 whose address is in DH grades a correct DH answer against CEN-1
expectations — it marks right answers wrong and hides real regressions behind a
"known failure".

ABS-467 established the zone half of this through ``get_address_profile``. This
module is the assertion the ticket asks for, one layer lower and one layer
stricter:

1. **The civic number exists** — checked against HRM's own street-segment
   ranges (``halifax_street_centerlines``: FROM_LEFT/TO_LEFT/FROM_RIGHT/
   TO_RIGHT). No network, and it is the cheapest high-value check here: it
   catches a fabricated address *before* a geocoder invents a plausible point
   for it by interpolating from the surrounding numbering.
2. **The geocode is not an estimate** — a resolution at 0.60 picks its zoning
   polygon by luck. Fail, not warn.
3. **The point is inside a zoning polygon** — raw ``ST_Intersects`` against
   ``halifax_zoning_boundaries``, filtered to the Regional Centre by-law area,
   and it must return exactly the zone the case declares. A point in the road
   right-of-way matches nothing and fails; it does not fall back to a nearby
   parcel.

The rules live in ``scripts/verify_eval_corpus_integrity.py`` so an operator can
run them as one command; this module is their ``make test`` form.

Skips cleanly — never fails — where the ~180k-parcel Halifax ingest is absent,
which is CI and every e2e worktree, mirroring
``tests/test_bylaw_reference_index_check.py``. Where the data *is* present these
are the gate.

The offline half of the address story (the recorded ``address_resolution``
block, turn text agreeing with the address, no two cases sharing one) belongs to
``tests/test_eval_address_zones.py`` and its Playwright mirror.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from scripts.verify_eval_corpus_integrity import (
    DEFAULT_DB_URL,
    MIN_GEOCODE_CONFIDENCE,
    REGIONAL_CENTRE_BYLAW_AREA_ID,
    check_case_spatially,
    regional_centre_zoning_polygons,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"

CASES: list[dict[str, Any]] = json.loads(PROMPTS_FILE.read_text())
CASE_IDS = [case["id"] for case in CASES]


# ---------------------------------------------------------------------------
# Offline: the recorded resolution's own confidence floor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_recorded_resolution_clears_the_confidence_floor(case: dict) -> None:
    """No database needed, and it is the check that was missing entirely.

    TC-002/003/004 sat at 0.60 — an interpolated or block-centre point — and
    were accepted without comment; TC-005's 0.60 point landed 32 km outside
    HRM. The floor is asserted against the block the case itself records, so a
    hand-edited case is caught in CI, before anyone reaches a database.
    """
    resolution = case.get("address_resolution") or {}
    confidence = resolution.get("location_confidence")
    assert confidence is not None, (
        f"{case['id']} records no location_confidence — re-run "
        "scripts/verify_eval_address_zones.py --repair"
    )
    assert confidence >= MIN_GEOCODE_CONFIDENCE, (
        f"{case['id']}: {case['address']!r} resolved at {confidence} "
        f"({resolution.get('resolution_quality')}), below the {MIN_GEOCODE_CONFIDENCE} "
        "floor. A point the geocoder estimated selects its zoning polygon by "
        "luck, and every setback and height expectation on this case rests on "
        "that polygon."
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_address_is_one_the_municipality_puts_on_that_parcel(case: dict) -> None:
    """ABS-474. The check the confidence floor above cannot make.

    Five cases resolved to a real, correctly-zoned parcel under an address the
    municipality does not assign to it: "251 Stairs Street" on a parcel HRM
    registers as 249/251/257 Windmill Road, "1462 Birchdale Avenue" on one it
    registers as 1462 Thornvale Avenue. Every earlier check passed them —
    ROOFTOP confidence, correct zone — because a string composed from a
    parcel's own interior point geocodes straight back onto that parcel.

    Offline: ``registered_civics`` is the register's answer for the parcel,
    snapshotted onto the case by ``--backfill-civics``. ABS-475 replaces the
    snapshot with a live lookup once the register is ingested.
    """
    resolution = case.get("address_resolution") or {}
    registered = resolution.get("registered_civics")
    assert registered, (
        f"{case['id']} records no registered_civics — re-run "
        "scripts/verify_eval_address_zones.py --backfill-civics"
    )
    normalized = {" ".join(a.replace(",", " ").lower().split()) for a in registered}
    target = " ".join(case["address"].replace(",", " ").lower().split())
    assert target in normalized, (
        f"{case['id']}: {case['address']!r} is not a civic address the "
        f"municipality registers on parcel {resolution.get('parcel_pid')!r}. "
        f"It registers: {', '.join(registered)}. The zone may still be right — "
        "that is the trap — but the address names a property that does not "
        "exist. Re-derive with scripts/verify_eval_address_zones.py --repair."
    )


# ---------------------------------------------------------------------------
# Live: the spatial assertion
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def zoning_session():
    """A session over the real Halifax zoning ingest, or a clean skip.

    The probe asks specifically for Regional Centre polygons rather than for
    "some dataset called zoning": an e2e worktree database carries seeded
    ``e2e_*`` overlay datasets that satisfy the looser question but contain
    none of these addresses, so pointing DATABASE_URL at one would fail every
    case for a reason that has nothing to do with the eval file.
    """
    sa = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
    db_url = os.environ.get("EVAL_ADDRESS_DB_URL") or os.environ.get(
        "DATABASE_URL", DEFAULT_DB_URL
    )
    from sqlalchemy.orm import Session

    try:
        engine = sa.create_engine(db_url, connect_args={"connect_timeout": 3})
        with Session(engine) as session:
            polygons = regional_centre_zoning_polygons(session)
            if not polygons:
                pytest.skip(
                    "no Regional Centre zoning polygons "
                    f"(bylaw_area_id={REGIONAL_CENTRE_BYLAW_AREA_ID}) at {db_url}"
                )
            yield session
    except sa.exc.SQLAlchemyError as exc:
        # Narrow on purpose. A bare `except Exception` would turn a typo in
        # this fixture into a permanent silent skip — the always-green guard
        # this module exists to remove.
        pytest.skip(f"no zoning corpus reachable at {db_url}: {type(exc).__name__}: {exc}")


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_address_intersects_the_zone_the_case_claims(case: dict, zoning_session) -> None:
    """The whole point: `zone` is derived and checkable, not asserted."""
    result = check_case_spatially(zoning_session, case)
    if result.skipped:
        pytest.skip(result.skipped)
    assert result.ok, "\n".join(result.failures)
    assert result.intersecting_zones == [case["zone"]]


# --- the guard has to bite, not just exist ---------------------------------
#
# Real addresses from the pre-ABS-470 corpus, each a defect the audit found and
# each one the live data can still be asked about today. Without these, the
# tests above could pass on a guard that never actually evaluates anything.

NONEXISTENT_ADDRESSES = [
    ("100 Robie Street, Halifax, NS", "820-2180"),
    ("567 Windsor Street, Halifax, NS", "2001-3799"),
    ("200 Bayers Road, Halifax, NS", "6260-7150"),
    ("2563 Maitland Street, Halifax, NS", "2081-2385"),
    ("89 Jubilee Road, Halifax, NS", "6001-6769"),
]


@pytest.mark.parametrize("address,expected_range", NONEXISTENT_ADDRESSES)
def test_a_fabricated_address_is_refused_with_the_numbers_that_do_exist(
    address, expected_range, zoning_session
):
    """The five addresses ABS-470 replaced, refused without any network access.

    The message has to carry the valid ranges: "this address does not exist" on
    its own does not tell the next person whether the street was renamed, the
    number was a typo, or the case belongs somewhere else entirely.
    """
    result = check_case_spatially(
        zoning_session, {"id": "PROBE", "address": address, "zone": "HR-1"}
    )
    assert not result.ok, f"{address} does not exist and must not pass G1"
    assert result.civic_status == "not_found"
    reason = "\n".join(result.failures)
    assert "does not exist" in reason and expected_range in reason, reason


def test_a_point_outside_the_regional_centre_fails_rather_than_matching_something(
    zoning_session,
):
    """TC-005's old address geocoded to Truro, 32 km outside HRM.

    The failure must be "confirms no zone", never a zone code borrowed from
    another by-law area — which is why the intersection query is filtered to
    bylaw_area_id 23.
    """
    result = check_case_spatially(
        zoning_session,
        {"id": "PROBE", "address": "100 Queen Street, Halifax, NS", "zone": "HR-2"},
    )
    if result.skipped:
        pytest.skip(result.skipped)
    assert not result.ok
    assert result.intersecting_zones == []


def test_a_wrong_zone_is_named_expected_versus_actual(zoning_session):
    """1801 Hollis Street is DH. Claiming CEN-1 for it must say both."""
    result = check_case_spatially(
        zoning_session,
        {"id": "PROBE", "address": "1801 Hollis Street, Halifax, NS", "zone": "CEN-1"},
    )
    if result.skipped:
        pytest.skip(result.skipped)
    assert not result.ok
    reason = "\n".join(result.failures)
    assert "DH" in reason, "name the zone the address is actually in"
    assert "CEN-1" in reason, "name the zone the case claimed"
    assert result.intersecting_zones == ["DH"]
