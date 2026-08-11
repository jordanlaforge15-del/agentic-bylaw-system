"""ABS-467: every eval case's address must resolve to the zone it claims.

`evals/regional_centre_test_prompts.json` pairs a `zone` with an `address` on
every case. Nothing ever connected them: the operator supplied both, and
17 of 20 disagreed — 7 resolved into a *different* zone and 10 resolved
outside every mapped boundary. A case claiming CEN-1 whose address is in DH
grades a correct DH answer against CEN-1 expectations, marking right answers
wrong and masking real regressions.

Fixing the file once is not the fix. Zoning data is re-ingested, Google
re-geocodes, boundaries get redrawn — the file rots again on the next refresh
unless something re-derives the claim. This module is that something.

Two tiers, because the guard has to be useful in both worlds:

* **Offline** — the file's own internal agreement. Every case records the
  resolution it was verified against (`address_resolution`), every case names
  its address and its zone in turn 1, and no case claims a zone the Regional
  Centre schedule does not map. These need no database and run everywhere.
* **Live** — the address is pushed back through the production
  `get_address_profile` path, the same call the advisor makes, and must come
  back with the case's zone. These skip cleanly when Postgres is unreachable
  or the zoning dataset is absent, so `make test` stays runnable offline, and
  fail loudly wherever the data *is* present and a case has drifted.

The offline tier is mirrored in `web/e2e/functional/abs467-eval-address-zone.spec.ts`
so a Playwright run catches a hand-edited case without a database either.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"

DEFAULT_DB_URL = "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"

# The Regional Centre Land Use By-law's area id in the HRM zoning dataset,
# matching scripts/zone_address_picker.REGIONAL_CENTRE_BYLAW_AREA_ID.
REGIONAL_CENTRE_BYLAW_AREA_ID = "23"

# ABS-466's vocabulary. "rooftop" is the only value that means the point was
# matched to a building; everything else is an estimate of some kind.
RESOLUTION_QUALITIES = {"rooftop", "interpolated", "centroid", "approximate", "unknown"}

# Every zone code the Regional Centre schedule maps (bylaw_area_id 23). A case
# claiming anything outside this set can never be confirmed — which is exactly
# what TC-017 did for ER-1, a zone the by-law defines but the map never places.
MAPPED_ZONES = {
    "CDD-1", "CDD-2", "CEN-1", "CEN-2", "CH-1", "CH-2", "CLI", "COR", "DD",
    "DH", "DND", "ER-2", "ER-3", "H", "HCD-SV", "HR-1", "HR-2", "HRI", "INS",
    "LI", "PCF", "RPK", "UC-1", "UC-2", "WA",
}

# TC-001 misstates its own zone on purpose — the advisor is expected to correct
# the user. It is the one case whose turn-1 text must NOT name its zone field.
ADVERSARIAL_ZONE_PREMISE = {"TC-001"}


def load_cases() -> list[dict[str, Any]]:
    return json.loads(PROMPTS_FILE.read_text())


CASES = load_cases()
CASE_IDS = [case["id"] for case in CASES]


# ---------------------------------------------------------------------------
# Offline: what the file says about itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_every_case_records_the_resolution_it_was_verified_against(case: dict) -> None:
    """The evidence lives on the case, not in someone's shell history."""
    resolution = case.get("address_resolution")
    assert isinstance(resolution, dict), (
        f"{case['id']} has no address_resolution block — re-run "
        "scripts/verify_eval_address_zones.py --repair"
    )
    assert resolution["resolved_zone"] == case["zone"], (
        f"{case['id']} records a resolved_zone of {resolution['resolved_zone']!r} "
        f"but claims {case['zone']!r}"
    )
    assert resolution["resolution_quality"] in RESOLUTION_QUALITIES


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_every_case_rests_on_a_matched_building_not_an_estimate(case: dict) -> None:
    """ROOFTOP is the bar; anything looser is an estimated point.

    An interpolated address is not forbidden — the geocoder cannot always do
    better — but it has to be *declared*, because a point the geocoder guessed
    along the street can select the neighbouring parcel's zone on the next
    data refresh. Declaring it means saying so in the case's notes, which is
    what this assertion's message asks for.
    """
    quality = case["address_resolution"]["resolution_quality"]
    if quality == "rooftop":
        return
    assert "resolution" in case.get("notes", "").lower(), (
        f"{case['id']} resolves at {quality!r}, not ROOFTOP. That is allowed "
        "only when no rooftop address exists in the zone, and the case's notes "
        "must say so — the eval must not silently depend on an estimated point."
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_every_case_claims_a_zone_the_schedule_maps(case: dict) -> None:
    assert case["zone"] in MAPPED_ZONES, (
        f"{case['id']} claims {case['zone']!r}, which the Regional Centre "
        "zoning schedule does not map anywhere, so no address can confirm it"
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_turn_one_names_the_case_address(case: dict) -> None:
    """A conversation that names a different property tests a different case."""
    civic = case["address"].split(",")[0].strip()
    first_turn = case["turns"][0]["message"]
    assert civic.lower() in first_turn.lower(), (
        f"{case['id']} turn 1 does not name {civic!r} — the address moved and "
        "the conversation did not follow it"
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_turn_one_and_the_zone_field_agree(case: dict) -> None:
    """Except where disagreeing is the test."""
    first_turn = case["turns"][0]["message"]
    if case["id"] in ADVERSARIAL_ZONE_PREMISE:
        assert case["zone"] not in first_turn, (
            f"{case['id']} is the adversarial-premise case; its turn 1 must "
            "keep misstating the zone"
        )
        return
    assert case["zone"] in first_turn, (
        f"{case['id']} turn 1 never names {case['zone']!r}"
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_zone_appears_in_the_expected_keywords(case: dict) -> None:
    """The grader looks for the zone code; a stale one grades the wrong zone."""
    assert case["zone"] in case["expected_answer_keywords"], (
        f"{case['id']} expects keywords {case['expected_answer_keywords']} "
        f"but claims zone {case['zone']!r}"
    )


def test_no_two_cases_share_an_address() -> None:
    """Distinct cases should exercise distinct parcels.

    TC-009 and TC-016 were both anchored on "100 Wyse Road" — an address in no
    zone at all — so a single bad geocode silently governed two cases.
    """
    addresses = [case["address"].strip().lower() for case in CASES]
    duplicates = {a for a in addresses if addresses.count(a) > 1}
    assert not duplicates, f"shared addresses: {sorted(duplicates)}"


# ---------------------------------------------------------------------------
# Live: what the production path says
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def retrieval_session():
    """A session over the real Regional Centre zoning corpus, or a clean skip.

    Skips — never fails — when Postgres is unreachable or the schedule is not
    ingested, so the offline suite runs on a plane. Where the data *is*
    present, the tests below are the gate.

    The probe asks specifically for Regional Centre polygons rather than for
    "some dataset called zoning". An e2e worktree database carries seeded
    ``e2e_*`` overlay datasets that satisfy the looser question but contain
    none of these addresses, so pointing DATABASE_URL at one would fail every
    case for a reason that has nothing to do with the eval file.
    """
    sa = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
    pytest.importorskip("shapely", reason="shapely not installed")
    db_url = os.environ.get("EVAL_ADDRESS_DB_URL") or os.environ.get(
        "DATABASE_URL", DEFAULT_DB_URL
    )
    try:
        engine = sa.create_engine(db_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            regional_centre_polygons = conn.execute(
                sa.text(
                    "SELECT COUNT(*) FROM external_dataset_feature f "
                    "JOIN external_dataset d ON d.id = f.external_dataset_id "
                    "WHERE d.name ILIKE '%zoning%' "
                    "AND f.canonical_attributes_json->>'bylaw_area_id' = "
                    ":area"
                ),
                {"area": REGIONAL_CENTRE_BYLAW_AREA_ID},
            ).scalar()
    except sa.exc.SQLAlchemyError as exc:
        # Narrow on purpose: a bare `except Exception` would turn a typo in
        # this fixture into a permanent silent skip, which is the same
        # always-green failure this module exists to remove.
        pytest.skip(f"no zoning corpus reachable at {db_url}: {type(exc).__name__}: {exc}")
    if not regional_centre_polygons:
        pytest.skip(f"no Regional Centre zoning schedule ingested at {db_url}")

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        yield session


@pytest.fixture(scope="module")
def retrieval_service(retrieval_session):
    service_module = pytest.importorskip(
        "bylaw_retrieval.retrieval.service",
        reason="the MCP retrieval package is not importable",
    )
    return service_module.RetrievalService(retrieval_session)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_address_resolves_to_the_claimed_zone(case: dict, retrieval_service) -> None:
    """The whole point: `zone` is derived and checkable, not asserted."""
    profile = retrieval_service.get_address_profile(case["address"])
    assert not profile.unresolvable, (
        f"{case['id']}: {case['address']!r} does not resolve to a point at all"
    )
    assert not profile.outside_mapped_area, (
        f"{case['id']}: {case['address']!r} resolves outside every mapped "
        "zoning boundary, so it cannot confirm any zone"
    )
    assert profile.zone == case["zone"], (
        f"{case['id']}: {case['address']!r} resolves to {profile.zone!r}, but "
        f"the case claims {case['zone']!r}. The eval would grade a correct "
        f"{profile.zone} answer against {case['zone']} expectations. Re-run "
        "scripts/verify_eval_address_zones.py --repair."
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_recorded_resolution_still_matches_the_live_one(
    case: dict, retrieval_service
) -> None:
    """Catches a case that still lands in its zone but on a worse point.

    The zone assertion above can pass while the resolution quietly degrades
    from ROOFTOP to interpolated — same zone today, a coin flip after the next
    boundary redraw. The recorded block is what makes that visible.
    """
    profile = retrieval_service.get_address_profile(case["address"])
    recorded = case["address_resolution"]
    assert recorded["resolution_quality"] == profile.resolution_quality, (
        f"{case['id']}: recorded {recorded['resolution_quality']!r} but the "
        f"live resolution is {profile.resolution_quality!r}"
    )
    assert recorded["location_type"] == profile.location_type
    assert recorded["location_resolver"] == profile.location_resolver
