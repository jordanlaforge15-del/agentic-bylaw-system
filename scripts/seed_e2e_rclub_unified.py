"""Seed the SINGLE unified Regional Centre LUB e2e document (ABS-433).

Prod carries ONE comprehensive Regional Centre Land Use By-Law document.
Before ABS-433 the e2e corpus fragmented that content across three fixture
documents — permission tables (``seed_e2e_permission_tables``), schedule/geo
anchors (``seed_e2e_address_profile`` + ``seed_e2e_pocs``), and zone-profile
text (``seed_e2e_zone_profile``) — so anything sensitive to document scoping
(the ABS-413 enabled flag, sibling detection, relink) could pass in e2e
against a corpus shape prod never has. This composed entry point replaces all
four seeds with ONE document (one ``file_hash``, one bylaw name under the
ABS-431 convention) carrying:

* **Permission tables 1A/1B** (``SourceTable`` + cells), including the
  ABS-277 symbol-font PUA-marker cells, each with a ``permission_matrix``
  semantic profile and the marker-recovery pass applied.
* **Schedule anchor fragments** — ``Zoning Schedule``, ``Schedule 15``
  (height), ``Schedule 17`` (FAR), ``Schedule 22`` (heritage), ``Schedule 7``
  (pedestrian-oriented commercial streets), plus ``Schedule 14`` as the
  anchor for the bonus-zoning layer — so the dataset linker can bind each
  overlay to its citing fragment by ``citation_label``.
* **Six linked ``e2e_*`` geo datasets**, one per ``get_address_profile``
  overlay role: zone, height precinct, FAR precinct, heritage district,
  bonus zoning, and the Schedule 7 pedestrian-street corridors (LINE
  geometry, exercised via the *abuts* predicate). All are ingested through
  ``ingest_geo_dataset`` so the PostGIS ``geometry`` column is populated (the
  ``ST_Intersects`` path needs the real geometry column, not just
  ``geometry_geojson`` — see the e2e PostGIS gotcha).
* **Zone-profile fragments** — Table 5 (height/coverage), Table 3
  (setbacks), Table 1A/1B use rows, Part II zone establishment rows, and the
  Part V §120 parking rule — the corpus ``get_zone_profile`` composes over.
* **A parcel fabric** (``e2e_rclub_parcels``, ``role: property_parcels``)
  carrying the two ABS-435 lot-depth fixtures, plus the ABS-469 pair against
  the HR-2 / CEN-1 line: a lot that straddles it and a lot that merely
  touches it. Base geography, not an overlay — it exists so the abuts
  predicate can measure from the lot boundary instead of the geocoded
  rooftop point.
* **A street network** (``e2e_rclub_street_centerlines``, ``role:
  road_centerlines``, ABS-469) carrying HRM's real published address ranges
  for Windsor and Oxford Streets. It is what lets the civic-address verifier
  answer "there is no 567 Windsor Street" instead of geocoding one. Robie
  Street is deliberately absent so the corpus's primary fixture address
  stays ``unverifiable`` and every pre-ABS-469 expectation about it holds.
* **Geocode-cache rows** for ``100 Robie Street`` (inside every polygon
  overlay), ``6184 Quinpool Road`` (~10 m off the Schedule 7 Quinpool
  centreline, so only the buffered abuts query matches), the
  ``500 Nowhere Road`` definitive-negative control, and the ABS-435 pair —
  ``6321 Quinpool Road`` (deep lot: rooftop 37 m out, front lot line 9 m out,
  must abut) and ``12 Backlot Lane`` (rooftop 28 m out but front lot line
  25 m out, must not).

The document name comes from the ABS-431 naming convention
(``scripts/e2e_fixture_names.py``): it references the real bylaw but carries
the explicit ``E2E`` marker, so the migration-0024 backfill and the ABS-355
relink pass can never confuse it with the real RC-LUB.

Retired fragmented identities (the three legacy document hashes and the
pre-fix standalone POCS document) are purged on every run so a mid-iteration
database left over from an older checkout self-heals — with the ABS-428
ephemeral e2e instance a fresh run never sees them at all.

Idempotent and safe under concurrent Playwright workers: the whole seed runs
under the shared Regional Centre corpus advisory lock, keyed upserts find
their rows on re-run, and datasets are only dropped-and-reingested when their
content hash diverges — an unchanged fixture never churns dataset ids
mid-suite (the ABS-414 live-API-reader concern).

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5433/layer1_test \\
        .venv/bin/python scripts/seed_e2e_rclub_unified.py
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select, text

from e2e_fixture_names import fixture_bylaw_name_violation
from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    GeocodeCache,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableSemanticProfile,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer1.semantic.permission_markers import (
    PERMISSION_MATRIX_PROFILE,
    annotate_permission_matrix_table,
)


DOCUMENT_FILE_HASH = "e2e-rclub-unified-doc-1"
DOCUMENT_MUNICIPALITY = "HRM"
# ABS-431: never the bare real name. The unified fixture references the real
# RC-LUB but must carry the explicit E2E marker; asserted below via the
# single-source guard so a rename that drops the marker fails fast here, not
# only in tests/test_e2e_fixture_bylaw_names.py.
DOCUMENT_BYLAW_NAME = "Regional Centre Land Use By-Law (Unified RC-LUB E2E)"
_violation = fixture_bylaw_name_violation(DOCUMENT_BYLAW_NAME)
assert _violation is None, _violation

# One lock key for every writer AND reader of the shared Regional Centre
# corpus (this seed and inspect_pocs_intersection). Distinct per-script keys
# would only serialise a script against itself; a probe could read a dataset
# id by name, lose a drop-and-reingest commit between statements under READ
# COMMITTED, and then query features for an id that no longer exists — the
# historical intermittent zone=null / intersects=false flakes.
CORPUS_ADVISORY_LOCK_KEY = 2604601273


# ---------------------------------------------------------------------------
# Retired fragmented identities (pre-ABS-433). Purged every run so a
# persistent mid-iteration DB from an older checkout self-heals; on the
# ABS-428 ephemeral instance these never exist in the first place.
# ---------------------------------------------------------------------------

RETIRED_FILE_HASHES: tuple[str, ...] = (
    "e2e-permission-tables-1",  # seed_e2e_permission_tables (retired)
    "e2e-address-profile-doc-1",  # seed_e2e_address_profile + seed_e2e_pocs (retired)
    "e2e-zone-profile-bylaw-1",  # seed_e2e_zone_profile (retired)
    "e2e-pocs-schedule7-doc-1",  # pre-ABS-350 standalone POCS doc
)
RETIRED_IDENTITIES: tuple[tuple[str, str], ...] = (
    ("HRM", "Regional Centre Land Use By-law (Permission Tables E2E)"),
    ("HRM", "Regional Centre Land Use By-Law (Address Profile E2E)"),
    ("Halifax Regional Municipality", "Regional Centre Land Use By-Law (Zone Profile E2E)"),
    ("Halifax Regional Municipality", "Regional Centre Land Use By-Law (POCS Schedule 7 E2E)"),
)
# The ABS-349 seed ingested the POCS features under a name with no
# "pedestrian" keyword (so it never resolved to the overlay role); drop it if
# a stale volume still carries it.
_RETIRED_DATASET_NAMES: tuple[str, ...] = ("e2e_pocs_schedule7",)


# ---------------------------------------------------------------------------
# Permission tables (from the retired seed_e2e_permission_tables, ABS-163/277)
# ---------------------------------------------------------------------------

# ABS-277: the authoritative "permitted" dot in the real bylaw is the embedded
# symbol-font ●, stored as a Private Use Area codepoint (U+F098) that reads as
# blank downstream. Seed it verbatim (plus a U+F020 symbol-space padding
# example and a circled-number conditional) so the e2e exercises the recovery
# into metadata_json.permission_marker.
PUA_DOT = ""  # symbol-font ● "permitted as-of-right"
PUA_SPACE = ""  # symbol-font space (padding)

TABLE_1A_CAPTION = "Table 1A: Permitted uses by zone — Residential"
TABLE_1B_CAPTION = "Table 1B: Permitted uses by zone — Mixed Use"

TABLE_1A_CELLS = [
    # Header row
    (0, 0, "Use", None, "Use"),
    (0, 1, "DD", None, "DD"),
    (0, 2, "DH", None, "DH"),
    (0, 3, "ER-3", None, "ER-3"),
    # Data rows
    (1, 0, "Low-density dwelling use", "Low-density dwelling use", None),
    (1, 1, "●", "Low-density dwelling use", "DD"),
    (1, 2, "●", "Low-density dwelling use", "DH"),
    (1, 3, "●", "Low-density dwelling use", "ER-3"),
    (2, 0, "Multi-unit dwelling use", "Multi-unit dwelling use", None),
    (2, 1, "●", "Multi-unit dwelling use", "DD"),
    (2, 2, "○", "Multi-unit dwelling use", "DH"),
    (2, 3, "●", "Multi-unit dwelling use", "ER-3"),
    (3, 0, "Restaurant use", "Restaurant use", None),
    (3, 1, "○", "Restaurant use", "DD"),
    (3, 2, "●", "Restaurant use", "DH"),
    (3, 3, "○", "Restaurant use", "ER-3"),
    # ABS-277: row whose markers come from the symbol font. DD = U+F098 dot
    # (permitted), DH = circled-three conditional (footnote 3), ER-3 = empty
    # (not permitted). These are the cells the recovery pass must classify.
    (4, 0, "Home occupation use", "Home occupation use", None),
    (4, 1, PUA_SPACE + PUA_DOT + PUA_SPACE, "Home occupation use", "DD"),
    (4, 2, "③", "Home occupation use", "DH"),
    (4, 3, "", "Home occupation use", "ER-3"),
]

TABLE_1B_CELLS = [
    (0, 0, "Use", None, "Use"),
    (0, 1, "CEN-2", None, "CEN-2"),
    (0, 2, "COR", None, "COR"),
    (1, 0, "Office use", "Office use", None),
    (1, 1, "●", "Office use", "CEN-2"),
    (1, 2, "●", "Office use", "COR"),
    (2, 0, "Restaurant use", "Restaurant use", None),
    (2, 1, "●", "Restaurant use", "CEN-2"),
    (2, 2, "○", "Restaurant use", "COR"),
]


# ---------------------------------------------------------------------------
# Zone-profile fragments (from the retired seed_e2e_zone_profile, ABS-272)
# ---------------------------------------------------------------------------

# Per-zone rows lifted from tests/fixtures/halifax_regional_centre_lub.txt.
# COR / CEN-2 height is governed by Schedule 15 (no inline number) — the
# correct None outcome for max_height_m.
_ZONE_ROWS = {
    "HR-2": {
        "full_name": "Higher Order Residential 2",
        "use_table": "Table 1A",
        "height": "HR-2 Maximum Height 25.0 m Maximum Lot Coverage 65%",
        "setbacks": "HR-2 Front Setback 3.0 m Side Setback 3.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions HR-2 single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
    },
    "HR-1": {
        "full_name": "Higher Order Residential 1",
        "use_table": "Table 1A",
        "height": "HR-1 Maximum Height 20.0 m Maximum Lot Coverage 60%",
        "setbacks": "HR-1 Front Setback 3.0 m Side Setback 3.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions HR-1 single-unit dwelling P secondary suite P "
            "multi-unit dwelling P home occupation P daycare P"
        ),
    },
    "COR": {
        "full_name": "Corridor",
        "use_table": "Table 1B",
        "height": "COR Maximum Height as per Schedule 15 Maximum Lot Coverage 70%",
        "setbacks": "COR Front Setback 3.0 m Side Setback 0.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions COR single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
    },
    "CEN-2": {
        "full_name": "Centre 2",
        "use_table": "Table 1B",
        "height": "CEN-2 Maximum Height as per Schedule 15 Maximum Lot Coverage 80%",
        "setbacks": "CEN-2 Front Setback 0.0 m Side Setback 0.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions CEN-2 single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
    },
}

_PARKING_TEXT = (
    "Off-Street Parking Requirements. A residential development shall "
    "provide a minimum of 1 parking space per dwelling unit. Despite "
    "that, no off-street parking is required for a development in the "
    "CEN-1, CEN-2, DH, or DD zone. A non-residential development shall "
    "provide parking at the ratios in Table 8."
)


# ---------------------------------------------------------------------------
# Geo overlays (from the retired seed_e2e_address_profile + seed_e2e_pocs,
# plus the bonus-zoning layer that completes the six overlay roles)
# ---------------------------------------------------------------------------

TEST_ADDRESS_RAW = "100 Robie Street"
TEST_ADDRESS_NORMALIZED = "civic:100 robie st"

# ABS-466: an address the geocoder could NOT match to a building — it
# estimated the position along the street from the surrounding civic
# numbering (Google's RANGE_INTERPOLATED). Deliberately parked on the same
# point as TEST_ADDRESS so the zone still resolves: the behaviour under test
# is that the profile says the point was estimated, not that it fails.
INTERPOLATED_ADDRESS_RAW = "1234 Oxford Street"
INTERPOLATED_ADDRESS_NORMALIZED = "civic:1234 oxford st"

# ABS-469: an address the municipality's own street data says does not exist.
# Windsor Street's ingested centerline segments (below) publish 2000-2089, so
# 567 is past the low end of everything the street carries. The geocode-cache
# row parks it on TEST_POINT — squarely inside the HR-2 polygon — on purpose:
# the profile must refuse it because the CIVIC NUMBER is not real, not because
# the point happened to land somewhere unhelpful.
NONEXISTENT_ADDRESS_RAW = "567 Windsor Street"
NONEXISTENT_ADDRESS_NORMALIZED = "civic:567 windsor st"

# A point inside every seeded polygon overlay, and the box that contains it.
TEST_POINT: dict[str, Any] = {"type": "Point", "coordinates": [-63.59, 44.65]}
_BOX = [
    [-63.60, 44.64],
    [-63.58, 44.64],
    [-63.58, 44.66],
    [-63.60, 44.66],
    [-63.60, 44.64],
]
# The CEN-1 box east of it, sharing the -63.58 edge.
_EAST_BOX = [
    [-63.58, 44.64],
    [-63.56, 44.64],
    [-63.56, 44.66],
    [-63.58, 44.66],
    [-63.58, 44.64],
]

# (dataset_name, fragment citation_label, raw properties, canonical YAML block)
# dataset_name carries the keyword overlay_role_for_name classifies on: the
# five polygon layers here plus the pedestrian-street line layer below give
# the unified document all six get_address_profile overlay roles.
POLYGON_OVERLAYS: list[dict[str, Any]] = [
    {
        "name": "e2e_rclub_zoning",
        "citation": "Zoning Schedule",
        "feature_key_field": "GLOBALID",
        "properties": {"GLOBALID": "rclub-zone-1", "ZONE": "HR-2", "DESCRIPTION": "High-Rise Residential"},
        # ABS-469: a second zone sharing the HR-2 box's eastern edge. A zone
        # line is what makes an otherwise perfect resolution unsafe — the
        # point can sit metres from land governed by different standards, or
        # the lot can straddle the line — and neither can be tested against a
        # corpus containing exactly one zone.
        "extra": (
            (
                {
                    "GLOBALID": "rclub-zone-2",
                    "ZONE": "CEN-1",
                    "DESCRIPTION": "Centre",
                },
                {"type": "Polygon", "coordinates": [_EAST_BOX]},
            ),
        ),
        "canonical": (
            "    zone_code: { from: ZONE, type: string }\n"
            "    zone_description: { from: DESCRIPTION, type: string, optional: true }\n"
        ),
    },
    {
        "name": "e2e_rclub_height_precincts",
        "citation": "Schedule 15",
        "feature_key_field": "GlobalID",
        "properties": {"GlobalID": "rclub-height-1", "MAXBLDHGT": 25.0},
        "canonical": ("    max_height_m: { from: MAXBLDHGT, type: float, optional: true }\n"),
    },
    {
        "name": "e2e_rclub_far_precincts",
        "citation": "Schedule 17",
        "feature_key_field": "GLOBALID",
        "properties": {"GLOBALID": "rclub-far-1", "FAR": 3.5},
        "canonical": ("    max_far: { from: FAR, type: float }\n"),
    },
    {
        "name": "e2e_rclub_heritage_districts",
        "citation": "Schedule 22",
        "feature_key_field": "GLOBALID",
        "properties": {
            "GLOBALID": "rclub-heritage-1",
            "HCDNAME": "Schmidtville",
            "STATUS": "Active",
        },
        "canonical": (
            "    district_name: { from: HCDNAME, type: string }\n"
            "    district_status: { from: STATUS, type: string, optional: true }\n"
        ),
    },
    # ABS-433: the sixth overlay role. The real RC-LUB maps its incentive
    # (bonus) zoning areas on Schedule 14; the polygon covers the test point
    # so get_address_profile resolves bonus_zoning_eligible=true with a
    # citation from the SAME unified document.
    {
        "name": "e2e_rclub_bonus_zoning_areas",
        "citation": "Schedule 14",
        "feature_key_field": "GLOBALID",
        "properties": {"GLOBALID": "rclub-bonus-1", "DISTRICT": "Centre Plan Bonus Area"},
        "canonical": ("    district_name: { from: DISTRICT, type: string }\n"),
    },
]

# Schedule 7 POCS layer (LINE geometry — abuts predicate, ABS-349/350). The
# name MUST contain "pedestrian" so overlay_role_for_name maps it to the
# pedestrian_street role and the retrieval service applies the abuts buffer.
POCS_DATASET_NAME = "e2e_pedestrian_oriented_commercial_streets"
POCS_CITATION = "Schedule 7"

# 6184 Quinpool Rd — the dev-DB address that motivated ABS-349/350. The
# Quinpool corridor runs ~constant-latitude here; the seeded point sits ~10 m
# north of the centreline so a zero-radius point misses and only the ~30 m
# abuts buffer intersects (the whole reason the overlay needs a distance
# predicate).
QUINPOOL_ADDRESS_RAW = "6184 Quinpool Road"
QUINPOOL_ADDRESS_NORMALIZED = "civic:6184 quinpool rd"
_QUINPOOL_LINE_LAT = 44.64610
_QUINPOOL_POINT: dict[str, Any] = {
    "type": "Point",
    "coordinates": [-63.6070, _QUINPOOL_LINE_LAT + 0.00009],
}

# ABS-435: the lot-depth fixtures. A civic geocode returns a rooftop point, so
# its distance to the centreline is dominated by how deep the lot is, not by
# whether the lot fronts the street — which is why abutment is measured from
# the parcel polygon. These two lots pin both directions of that:
#
#   6321 Quinpool Rd — deep commercial lot. Rooftop 37 m off the corridor
#     (outside the 30 m point buffer), front lot line 9 m off. Must abut.
#     This is the ABS-435 false negative, mirroring the real dev-DB address.
#   12 Backlot Lane  — lot on the block behind. Rooftop 28 m off (INSIDE the
#     point buffer), front lot line 25 m off. Must NOT abut — the guard that
#     keeps this a measurement fix rather than a threshold bump.
DEEP_LOT_ADDRESS_RAW = "6321 Quinpool Road"
DEEP_LOT_ADDRESS_NORMALIZED = "civic:6321 quinpool rd"
DEEP_LOT_LON = -63.6055

BACK_LOT_ADDRESS_RAW = "12 Backlot Lane"
BACK_LOT_ADDRESS_NORMALIZED = "civic:12 backlot ln"
BACK_LOT_LON = -63.6045

PARCELS_DATASET_NAME = "e2e_rclub_parcels"

# ---------------------------------------------------------------------------
# ABS-469 fixtures — does the address exist, and is the zone safe to rely on?
# ---------------------------------------------------------------------------

# HRM's own published per-segment address ranges, copied from the live
# corpus's halifax_street_centerlines ingest. Windsor Street is here so
# "567 Windsor Street" (the issue's measured example) is provably past the low
# end of the street; Oxford Street so the already-seeded interpolated fixture
# "1234 Oxford Street" is provably covered. Robie Street is deliberately
# ABSENT: the corpus's primary fixture address is 100 Robie Street, and a
# street the data has never heard of yields "unverifiable", which is what
# keeps every pre-ABS-469 expectation about that address intact.
CENTERLINES_DATASET_NAME = "e2e_rclub_street_centerlines"
CENTERLINE_SEGMENTS: tuple[tuple[str, str, str, int, int, int, int], ...] = (
    ("E2E-ST-WINDSOR-1", "WINDSOR", "ST", 2000, 2006, 2001, 2007),
    ("E2E-ST-WINDSOR-2", "WINDSOR", "ST", 2008, 2088, 2009, 2089),
    ("E2E-ST-OXFORD-1", "OXFORD", "ST", 1222, 1388, 1223, 1389),
)

# ABS-469 tier 4. Two lots hard against the HR-2 / CEN-1 line at -63.58:
#
#   7 Boundary Street — rooftop point ~9 m west of the line, on a lot that
#     straddles it. Both signals fire: proximity AND a split parcel. This is
#     the shape of 6321 Quinpool Road in the live corpus (ROOFTOP, inside
#     CEN-2, 7.6 m from CEN-1) — a perfect geocode that is still not a safe
#     answer.
#   9 Boundary Street — a lot wholly inside HR-2 whose eastern edge touches
#     the line. Zone polygons share edges, so this picks up a sliver of CEN-1
#     from coordinate precision alone and must NOT be reported as split.
BOUNDARY_ADDRESS_RAW = "7 Boundary Street"
BOUNDARY_ADDRESS_NORMALIZED = "civic:7 boundary st"
_BOUNDARY_POINT: dict[str, Any] = {"type": "Point", "coordinates": [-63.580115, 44.65]}
SLIVER_ADDRESS_RAW = "9 Boundary Street"
SLIVER_ADDRESS_NORMALIZED = "civic:9 boundary st"
_SLIVER_POINT: dict[str, Any] = {"type": "Point", "coordinates": [-63.58040, 44.6490]}
_SPLIT_PARCEL: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [
            [-63.58043, 44.6499],
            [-63.57957, 44.6499],
            [-63.57957, 44.6501],
            [-63.58043, 44.6501],
            [-63.58043, 44.6499],
        ]
    ],
}
_SLIVER_PARCEL: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [
        [
            [-63.58080, 44.6489],
            [-63.57999, 44.6489],
            [-63.57999, 44.6491],
            [-63.58080, 44.6491],
            [-63.58080, 44.6489],
        ]
    ],
}

_M_PER_DEG_LAT = 111_320.0
# Half-width of the synthetic lots in degrees of longitude at Quinpool's
# latitude (cos 44.646° ≈ 0.712). Narrow enough that neither lot overlaps the
# other or swallows the 6184 Quinpool fixture point.
_LOT_HALF_WIDTH_DEG = 15.0 / (_M_PER_DEG_LAT * 0.712)


def _lat_north_of_quinpool(metres: float) -> float:
    return _QUINPOOL_LINE_LAT + metres / _M_PER_DEG_LAT


def _lot_polygon(lon: float, *, front_m: float, rear_m: float) -> dict[str, Any]:
    """A rectangular lot fronting the Quinpool corridor.

    ``front_m`` / ``rear_m`` are the front and rear lot lines' distances north
    of the (constant-latitude) corridor centreline.
    """
    west, east = lon - _LOT_HALF_WIDTH_DEG, lon + _LOT_HALF_WIDTH_DEG
    south, north = _lat_north_of_quinpool(front_m), _lat_north_of_quinpool(rear_m)
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


_DEEP_LOT_POINT: dict[str, Any] = {
    "type": "Point",
    "coordinates": [DEEP_LOT_LON, _lat_north_of_quinpool(37.0)],
}
_BACK_LOT_POINT: dict[str, Any] = {
    "type": "Point",
    "coordinates": [BACK_LOT_LON, _lat_north_of_quinpool(28.0)],
}

# Negative control — far from every designated corridor and outside every
# polygon overlay.
CONTROL_ADDRESS_RAW = "500 Nowhere Road"
CONTROL_ADDRESS_NORMALIZED = "civic:500 nowhere rd"
_CONTROL_POINT: dict[str, Any] = {"type": "Point", "coordinates": [-63.5500, 44.6800]}

# The five schedule anchors the fragmented corpus carried (Zoning Schedule,
# 15, 17, 22, 7) plus Schedule 14 for the bonus layer. Order fixes the page
# numbering, keeping fragment upserts deterministic.
SCHEDULE_CITATIONS: tuple[str, ...] = (
    "Zoning Schedule",
    "Schedule 15",
    "Schedule 17",
    "Schedule 22",
    "Schedule 7",
    "Schedule 14",
)
_SCHEDULE_TEXT: dict[str, str] = {
    "Schedule 7": "Schedule 7: Pedestrian-Oriented Commercial Streets.",
    "Schedule 14": "Schedule 14: Bonus Zoning Areas.",
}


def _polygon() -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [_BOX]}


def _polygon_feature_collection(
    props: dict[str, Any],
    *,
    extra: tuple[tuple[dict[str, Any], dict[str, Any]], ...] = (),
) -> dict[str, Any]:
    """The overlay's polygon over the test point, plus any extra features.

    ``extra`` is (properties, geometry) pairs — used by the zoning layer,
    which needs a second polygon carrying a different zone code so the
    zone-boundary and split-parcel signals have a boundary to be near
    (ABS-469).
    """
    features = [{"type": "Feature", "geometry": _polygon(), "properties": props}]
    features.extend(
        {"type": "Feature", "geometry": geometry, "properties": extra_props}
        for extra_props, geometry in extra
    )
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": features,
    }


def _centerlines_feature_collection() -> dict[str, Any]:
    """HRM-shaped street segments: a name, a type, and per-side ranges."""
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "ASSETID": key,
                    "STR_NAME": name,
                    "STR_TYPE": street_type,
                    "FROM_LEFT": from_left,
                    "TO_LEFT": to_left,
                    "FROM_RIGHT": from_right,
                    "TO_RIGHT": to_right,
                },
                # Geometry is incidental here — the address ranges are the
                # payload — but every feature needs one to ingest.
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-63.5950, 44.6480], [-63.5950, 44.6520]],
                },
            }
            for (
                key,
                name,
                street_type,
                from_left,
                to_left,
                from_right,
                to_right,
            ) in CENTERLINE_SEGMENTS
        ],
    }


def _centerlines_config_yaml(geojson_path: Path) -> str:
    # Base geography like the parcels: no links_to, and the role tag is what
    # layer2.retrieval.civic_address looks for.
    return (
        f"name: {CENTERLINES_DATASET_NAME}\n"
        "publisher: e2e_seed\n"
        "format: geojson\n"
        f"source_path: {geojson_path}\n"
        "crs: EPSG:4326\n"
        "role: road_centerlines\n"
        "attributes:\n"
        "  feature_key: ASSETID\n"
        "  canonical:\n"
        "    street_name: { from: STR_NAME, type: string, optional: true }\n"
        "    street_type: { from: STR_TYPE, type: string, optional: true }\n"
        "    civic_from_left: { from: FROM_LEFT, type: int, optional: true }\n"
        "    civic_to_left: { from: TO_LEFT, type: int, optional: true }\n"
        "    civic_from_right: { from: FROM_RIGHT, type: int, optional: true }\n"
        "    civic_to_right: { from: TO_RIGHT, type: int, optional: true }\n"
    )


def _pocs_feature_collection() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "SEGMENT_ID": "E2E-S7-QUINPOOL",
                    "STREET": "Quinpool Road",
                    "SCHEDULE": "Schedule 7",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-63.6100, _QUINPOOL_LINE_LAT],
                        [-63.6070, _QUINPOOL_LINE_LAT],
                        [-63.6040, _QUINPOOL_LINE_LAT],
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "SEGMENT_ID": "E2E-S7-GOTTINGEN",
                    "STREET": "Gottingen Street",
                    "SCHEDULE": "Schedule 7",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-63.5864, 44.6524],
                        [-63.5879, 44.6579],
                    ],
                },
            },
        ],
    }


def _parcels_feature_collection() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"PID": "E2E-RCLUB-DEEP", "CIVIC": DEEP_LOT_ADDRESS_RAW},
                "geometry": _lot_polygon(DEEP_LOT_LON, front_m=9.0, rear_m=49.0),
            },
            {
                "type": "Feature",
                "properties": {"PID": "E2E-RCLUB-BACK", "CIVIC": BACK_LOT_ADDRESS_RAW},
                "geometry": _lot_polygon(BACK_LOT_LON, front_m=25.0, rear_m=65.0),
            },
            {
                "type": "Feature",
                "properties": {"PID": "E2E-RCLUB-SPLIT", "CIVIC": BOUNDARY_ADDRESS_RAW},
                "geometry": _SPLIT_PARCEL,
            },
            {
                "type": "Feature",
                "properties": {"PID": "E2E-RCLUB-SLIVER", "CIVIC": SLIVER_ADDRESS_RAW},
                "geometry": _SLIVER_PARCEL,
            },
        ],
    }


def _parcels_config_yaml(geojson_path: Path) -> str:
    # Base geography, not a bylaw overlay: no links_to, and the role tag is
    # what RetrievalService._parcels_dataset_ids looks for.
    return (
        f"name: {PARCELS_DATASET_NAME}\n"
        "publisher: e2e_seed\n"
        "format: geojson\n"
        f"source_path: {geojson_path}\n"
        "crs: EPSG:4326\n"
        "role: property_parcels\n"
        "attributes:\n"
        "  feature_key: PID\n"
        "  canonical:\n"
        "    parcel_id: { from: PID, type: string }\n"
        "  ignore: [CIVIC]\n"
    )


def _polygon_config_yaml(overlay: dict[str, Any], geojson_path: Path) -> str:
    return (
        f"name: {overlay['name']}\n"
        "publisher: e2e_seed\n"
        "format: geojson\n"
        f"source_path: {geojson_path}\n"
        "crs: EPSG:4326\n"
        "links_to:\n"
        "  document_match:\n"
        f"    municipality: {DOCUMENT_MUNICIPALITY}\n"
        f"    bylaw_name: {DOCUMENT_BYLAW_NAME}\n"
        f"  fragment_citation: {overlay['citation']}\n"
        "attributes:\n"
        f"  feature_key: {overlay['feature_key_field']}\n"
        "  canonical:\n"
        f"{overlay['canonical']}"
    )


def _pocs_config_yaml(geojson_path: Path) -> str:
    return (
        f"name: {POCS_DATASET_NAME}\n"
        "publisher: e2e_seed\n"
        "format: geojson\n"
        f"source_path: {geojson_path}\n"
        "crs: EPSG:4326\n"
        "links_to:\n"
        "  document_match:\n"
        f"    municipality: {DOCUMENT_MUNICIPALITY}\n"
        f"    bylaw_name: {DOCUMENT_BYLAW_NAME}\n"
        f"  fragment_citation: {POCS_CITATION}\n"
        "attributes:\n"
        "  feature_key: SEGMENT_ID\n"
        "  canonical:\n"
        "    street_name: { from: STREET, type: string }\n"
        "  ignore: [SCHEDULE]\n"
    )


def _dataset_specs() -> list[tuple[str, str]]:
    """Every (dataset_name, geojson_text) pair the unified corpus ingests."""
    specs = [
        (
            overlay["name"],
            json.dumps(
                _polygon_feature_collection(
                    overlay["properties"], extra=overlay.get("extra", ())
                )
            ),
        )
        for overlay in POLYGON_OVERLAYS
    ]
    specs.append((POCS_DATASET_NAME, json.dumps(_pocs_feature_collection())))
    return specs


# Geocode rows: (normalized, raw, point, confidence, detail, location_type).
# ``location_type`` is Google's own quality enum, persisted in the cache row's
# metadata (ABS-466) so a cache hit can still tell an interpolated point from
# a rooftop one; None for rows standing in for in-database resolutions.
_GEOCODE_ROWS: tuple[
    tuple[str, str, dict[str, Any], float, str, str | None], ...
] = (
    (
        TEST_ADDRESS_NORMALIZED,
        TEST_ADDRESS_RAW,
        TEST_POINT,
        1.0,
        "seeded for the unified RC-LUB e2e corpus (address profile)",
        None,
    ),
    (
        INTERPOLATED_ADDRESS_NORMALIZED,
        INTERPOLATED_ADDRESS_RAW,
        TEST_POINT,
        0.85,
        "seeded for ABS-466 (interpolated match — position estimated along the street)",
        "RANGE_INTERPOLATED",
    ),
    (
        QUINPOOL_ADDRESS_NORMALIZED,
        QUINPOOL_ADDRESS_RAW,
        _QUINPOOL_POINT,
        0.95,
        "seeded for the unified RC-LUB e2e corpus (Schedule 7 POCS)",
        None,
    ),
    (
        CONTROL_ADDRESS_NORMALIZED,
        CONTROL_ADDRESS_RAW,
        _CONTROL_POINT,
        0.95,
        "seeded for the unified RC-LUB e2e corpus (negative control)",
        None,
    ),
    (
        DEEP_LOT_ADDRESS_NORMALIZED,
        DEEP_LOT_ADDRESS_RAW,
        _DEEP_LOT_POINT,
        0.95,
        "seeded for the unified RC-LUB e2e corpus (ABS-435 deep lot on corridor)",
        None,
    ),
    (
        BACK_LOT_ADDRESS_NORMALIZED,
        BACK_LOT_ADDRESS_RAW,
        _BACK_LOT_POINT,
        0.95,
        "seeded for the unified RC-LUB e2e corpus (ABS-435 back lot control)",
        None,
    ),
    # ABS-469: parked on the zoned test point deliberately. If the profile
    # ever answers this one with a zone, it is answering a civic number that
    # does not exist from a point that belongs to someone else's property.
    (
        NONEXISTENT_ADDRESS_NORMALIZED,
        NONEXISTENT_ADDRESS_RAW,
        TEST_POINT,
        0.85,
        "seeded for ABS-469 (civic number outside every published street range)",
        "RANGE_INTERPOLATED",
    ),
    (
        BOUNDARY_ADDRESS_NORMALIZED,
        BOUNDARY_ADDRESS_RAW,
        _BOUNDARY_POINT,
        0.95,
        "seeded for ABS-469 (rooftop match ~9 m from the HR-2/CEN-1 line)",
        "ROOFTOP",
    ),
    (
        SLIVER_ADDRESS_NORMALIZED,
        SLIVER_ADDRESS_RAW,
        _SLIVER_POINT,
        0.95,
        "seeded for ABS-469 (lot touching the zone line — sliver control)",
        "ROOFTOP",
    ),
)


# ---------------------------------------------------------------------------
# Purge of retired fragmented identities
# ---------------------------------------------------------------------------


def _purge_retired_documents(session) -> int:
    """Drop every pre-ABS-433 fragmented RC-LUB fixture document.

    ORM deletes so the ``all, delete-orphan`` relationships cascade to
    fragments, tables, and cells. ``external_dataset`` FKs
    (``linked_document_id`` / ``linked_fragment_id``) are ``ON DELETE SET
    NULL`` and every unified dataset is re-ingested against the unified
    document below, so nothing is left dangling. No-op on a clean database.
    """
    conditions = [Document.file_hash.in_(RETIRED_FILE_HASHES)]
    conditions.extend(
        and_(Document.municipality == muni, Document.bylaw_name == name)
        for muni, name in RETIRED_IDENTITIES
    )
    stale_docs = session.scalars(select(Document).where(or_(*conditions))).all()
    for doc in stale_docs:
        session.delete(doc)
    if stale_docs:
        session.flush()
    return len(stale_docs)


def _purge_retired_datasets(session) -> None:
    for name in _RETIRED_DATASET_NAMES:
        _drop_existing_dataset(session, name)


# ---------------------------------------------------------------------------
# Document + tables + fragments
# ---------------------------------------------------------------------------


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH))
        .scalars()
        .first()
    )
    if document is not None:
        # Converge the publish flag on re-seed: a row left disabled by an
        # earlier spec run (e.g. the ABS-433 disable-retrieval probe) must
        # end up retrieval-enabled again.
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/regional_centre_lub_unified.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=500,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_table(
    session, document_id: int, caption: str, page_start: int, page_end: int,
    cell_specs: list[tuple[int, int, str, str | None, str | None]],
) -> SourceTable:
    existing = (
        session.execute(
            select(SourceTable).where(
                SourceTable.document_id == document_id,
                SourceTable.caption == caption,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        _ensure_cells(session, existing, cell_specs)
        session.flush()
        return existing
    table = SourceTable(
        document_id=document_id,
        caption=caption,
        page_start=page_start,
        page_end=page_end,
        parse_status=ParseStatus.PARSED,
        metadata_json={"parser": "docling", "seed": "e2e-rclub-unified"},
    )
    session.add(table)
    session.flush()
    _ensure_cells(session, table, cell_specs)
    session.flush()
    return table


def _ensure_cells(
    session, table: SourceTable,
    cell_specs: list[tuple[int, int, str, str | None, str | None]],
) -> None:
    existing_positions = {
        (cell.row_index, cell.col_index)
        for cell in session.execute(
            select(SourceTableCell.row_index, SourceTableCell.col_index).where(
                SourceTableCell.table_id == table.id
            )
        ).all()
    }
    for row_idx, col_idx, cell_text, row_header, col_header in cell_specs:
        if (row_idx, col_idx) in existing_positions:
            continue
        session.add(
            SourceTableCell(
                table_id=table.id,
                row_index=row_idx,
                col_index=col_idx,
                row_header_path=row_header,
                col_header_path=col_header,
                text=cell_text,
            )
        )


def _ensure_permission_matrix_profile(session, table: SourceTable) -> None:
    """Attach a ``permission_matrix`` profile to ``table`` if absent."""
    existing = (
        session.execute(
            select(TableSemanticProfile.id).where(
                TableSemanticProfile.table_id == table.id,
                TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return
    session.add(
        TableSemanticProfile(
            table_id=table.id,
            profile_type=PERMISSION_MATRIX_PROFILE,
            row_axis_type="use",
            column_axis_type="zone",
            value_type="permission_marker",
            confidence=0.9,
            review_status="auto_accepted",
            metadata_json={"seed": "e2e-rclub-unified"},
        )
    )
    session.flush()


def _seed_permission_tables(session, document_id: int) -> dict[str, int]:
    table_1a = _ensure_table(session, document_id, TABLE_1A_CAPTION, 42, 43, TABLE_1A_CELLS)
    table_1b = _ensure_table(session, document_id, TABLE_1B_CAPTION, 44, 45, TABLE_1B_CELLS)
    session.flush()
    # ABS-281: detection keys off the permission_matrix semantic profile, not
    # the caption. ABS-277: marker recovery into
    # metadata_json.permission_marker. Idempotent — expire the relationships
    # first so the recovery pass sees every row on a re-run.
    for table in (table_1a, table_1b):
        _ensure_permission_matrix_profile(session, table)
        session.expire(table, ["cells", "semantic_profiles"])
        annotate_permission_matrix_table(table)
    session.flush()
    return {"table_1a_id": table_1a.id, "table_1b_id": table_1b.id}


def _ensure_fragment(
    session,
    *,
    document_id: int,
    citation_path: str,
    citation_label: str,
    fragment_type: FragmentType,
    text: str,
    page: int,
    order: int,
) -> None:
    existing = (
        session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_path == citation_path,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return
    session.add(
        SourceFragment(
            document_id=document_id,
            fragment_type=fragment_type,
            citation_label=citation_label,
            citation_path=citation_path,
            page_start=page,
            page_end=page,
            reading_order_start=order,
            reading_order_end=order,
            text=text,
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
            source_block_ids_json=[],
            metadata_json={},
        )
    )


def _seed_schedule_fragments(session, document_id: int) -> None:
    page = 100
    for order, citation in enumerate(SCHEDULE_CITATIONS, start=100):
        _ensure_fragment(
            session,
            document_id=document_id,
            citation_path=citation.lower().replace(" ", "_"),
            citation_label=citation,
            fragment_type=FragmentType.SCHEDULE,
            text=_SCHEDULE_TEXT.get(citation, f"{citation}."),
            page=page,
            order=order,
        )
        page += 1


def _seed_zone_profile_fragments(session, document_id: int) -> None:
    order = 0
    for zone, row in _ZONE_ROWS.items():
        order += 1
        _ensure_fragment(
            session,
            document_id=document_id,
            citation_path=f"Part II > 30 > {zone}",
            citation_label=zone,
            fragment_type=FragmentType.SECTION,
            text=f"{zone} {row['full_name']} Zone",
            page=2,
            order=order,
        )
        order += 1
        _ensure_fragment(
            session,
            document_id=document_id,
            citation_path=f"Table 5 > {zone}",
            citation_label=zone,
            fragment_type=FragmentType.SECTION,
            text=row["height"],
            page=4,
            order=order,
        )
        order += 1
        _ensure_fragment(
            session,
            document_id=document_id,
            citation_path=f"Table 3 > {zone}",
            citation_label=zone,
            fragment_type=FragmentType.SECTION,
            text=row["setbacks"],
            page=4,
            order=order,
        )
        order += 1
        _ensure_fragment(
            session,
            document_id=document_id,
            citation_path=f"{row['use_table']} > {zone}",
            citation_label=zone,
            fragment_type=FragmentType.SECTION,
            text=row["uses"],
            page=3,
            order=order,
        )
    order += 1
    _ensure_fragment(
        session,
        document_id=document_id,
        citation_path="Part V > 120",
        citation_label="Section 120",
        fragment_type=FragmentType.SECTION,
        text=_PARKING_TEXT,
        page=6,
        order=order,
    )
    session.flush()


# ---------------------------------------------------------------------------
# Datasets + geocode cache
# ---------------------------------------------------------------------------


def _drop_existing_dataset(session, name: str) -> None:
    existing = session.scalar(select(ExternalDataset).where(ExternalDataset.name == name))
    if existing is None:
        return
    session.query(ExternalDatasetFeature).filter(
        ExternalDatasetFeature.external_dataset_id == existing.id
    ).delete(synchronize_session=False)
    session.delete(existing)
    session.flush()


def dataset_converged(session, *, name: str, geojson_text: str) -> bool:
    """True if the named dataset already carries exactly this fixture content.

    Convergence = same ``content_hash`` (sha256 of the geojson bytes, the same
    hash ``parse_geojson`` writes), clean parse, and linkage bound. When every
    seed-owned object converges the seed exits without writing anything: an
    unchanged fixture then never churns dataset ids mid-suite, which is the
    only thing the live-API readers (ABS-414) are vulnerable to — they cannot
    take the corpus advisory lock, so a reseed commit between their statements
    strands them on dead dataset ids. A no-op reseed commits nothing, so after
    the first seeding the race cannot occur.
    """
    expected = hashlib.sha256(geojson_text.encode("utf-8")).hexdigest()
    row = session.scalar(select(ExternalDataset).where(ExternalDataset.name == name))
    return (
        row is not None
        and row.content_hash == expected
        and row.parse_status == ParseStatus.PARSED
        and row.linked_fragment_id is not None
    )


def role_dataset_converged(
    session, *, name: str, geojson_text: str, role: str = "property_parcels"
) -> bool:
    """``dataset_converged`` for a base-geography dataset (parcels, centerlines).

    Same content-hash check, minus the linkage requirement: a ``role`` dataset
    is not bound to a bylaw fragment, so ``linked_fragment_id`` is always None
    and the linked check would force a pointless re-ingest on every run.
    """
    expected = hashlib.sha256(geojson_text.encode("utf-8")).hexdigest()
    row = session.scalar(select(ExternalDataset).where(ExternalDataset.name == name))
    return (
        row is not None
        and row.content_hash == expected
        and row.parse_status == ParseStatus.PARSED
        and (row.metadata_json or {}).get("role") == role
    )


def _ensure_geocode_cache(
    session, *, normalized: str, raw: str, point: dict[str, Any],
    confidence: float, detail: str, location_type: str | None = None,
) -> None:
    metadata = {"location_type": location_type} if location_type else {}
    existing = (
        session.execute(
            select(GeocodeCache).where(GeocodeCache.normalized_text == normalized)
        )
        .scalars()
        .first()
    )
    if existing is not None:
        # Force status=linked so the cache short-circuit returns a resolved
        # location (a stale "resolved" row from a prior shape would miss).
        existing.status = "linked"
        existing.geometry_geojson = point
        existing.confidence = confidence
        # ABS-466: a row seeded before the type was carried must not keep
        # reading as a typeless (confidence-only) resolution.
        existing.metadata_json = metadata
        session.flush()
        return
    session.add(
        GeocodeCache(
            normalized_text=normalized,
            raw_text=raw,
            kind="civic_address",
            status="linked",
            resolver="e2e_seed",
            geometry_geojson=point,
            confidence=confidence,
            detail=detail,
            metadata_json=metadata,
        )
    )
    session.flush()


def _geocode_converged(session) -> bool:
    for normalized, _raw, point, confidence, _detail, location_type in _GEOCODE_ROWS:
        geocode = session.scalar(
            select(GeocodeCache).where(GeocodeCache.normalized_text == normalized)
        )
        if (
            geocode is None
            or geocode.status != "linked"
            or geocode.geometry_geojson != point
            or geocode.confidence != confidence
            # ABS-466: a row carrying the wrong (or no) location_type resolves
            # at the wrong quality, so it has NOT converged.
            or (geocode.metadata_json or {}).get("location_type") != location_type
        ):
            return False
    return True


def _corpus_converged(session, dataset_specs: list[tuple[str, str]]) -> bool:
    """True when the whole unified corpus already matches this fixture.

    Any lingering retired identity must still trigger a full (purging) run.
    """
    conditions = [Document.file_hash.in_(RETIRED_FILE_HASHES)]
    conditions.extend(
        and_(Document.municipality == muni, Document.bylaw_name == name)
        for muni, name in RETIRED_IDENTITIES
    )
    if session.scalar(select(Document).where(or_(*conditions))) is not None:
        return False
    for name in _RETIRED_DATASET_NAMES:
        if session.scalar(
            select(ExternalDataset).where(ExternalDataset.name == name)
        ) is not None:
            return False

    document = session.scalar(
        select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
    )
    if document is None or not document.retrieval_enabled:
        return False
    for caption in (TABLE_1A_CAPTION, TABLE_1B_CAPTION):
        if session.scalar(
            select(SourceTable).where(
                SourceTable.document_id == document.id,
                SourceTable.caption == caption,
            )
        ) is None:
            return False
    for citation in SCHEDULE_CITATIONS:
        if session.scalar(
            select(SourceFragment).where(
                SourceFragment.document_id == document.id,
                SourceFragment.citation_path == citation.lower().replace(" ", "_"),
            )
        ) is None:
            return False
    if session.scalar(
        select(SourceFragment).where(
            SourceFragment.document_id == document.id,
            SourceFragment.citation_path == "Part V > 120",
        )
    ) is None:
        return False
    for name, geojson_text in dataset_specs:
        if not dataset_converged(session, name=name, geojson_text=geojson_text):
            return False
    # The parcel fabric is base geography (no linked fragment), so it converges
    # on its own predicate rather than through ``dataset_specs``.
    if not role_dataset_converged(
        session,
        name=PARCELS_DATASET_NAME,
        geojson_text=json.dumps(_parcels_feature_collection()),
    ):
        return False
    if not role_dataset_converged(
        session,
        name=CENTERLINES_DATASET_NAME,
        geojson_text=json.dumps(_centerlines_feature_collection()),
        role="road_centerlines",
    ):
        return False
    return _geocode_converged(session)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        with session_scope() as session:
            # Serialise against concurrent Playwright workers so two seed
            # runs can't interleave on the shared document or race the
            # drop-then-reingest dataset path.
            if session.bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)").bindparams(
                        k=CORPUS_ADVISORY_LOCK_KEY
                    )
                )

            dataset_specs = _dataset_specs()
            if _corpus_converged(session, dataset_specs):
                document = session.scalar(
                    select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
                )
                summary = {
                    "document_id": document.id,
                    "datasets_linked": len(dataset_specs),
                    "converged": True,
                }
                print(f"seed_e2e_rclub_unified summary: {json.dumps(summary)}")
                return 0

            purged = _purge_retired_documents(session)
            _purge_retired_datasets(session)

            document = _get_or_create_document(session)
            table_ids = _seed_permission_tables(session, document.id)
            _seed_schedule_fragments(session, document.id)
            _seed_zone_profile_fragments(session, document.id)
            session.flush()

            linked = 0
            for overlay in POLYGON_OVERLAYS:
                geojson_text = json.dumps(
                    _polygon_feature_collection(
                        overlay["properties"], extra=overlay.get("extra", ())
                    )
                )
                if dataset_converged(
                    session, name=overlay["name"], geojson_text=geojson_text
                ):
                    linked += 1
                    continue
                _drop_existing_dataset(session, overlay["name"])
                geojson_path = work_dir / f"{overlay['name']}.geojson"
                geojson_path.write_text(geojson_text, encoding="utf-8")
                cfg_path = work_dir / f"{overlay['name']}.yaml"
                cfg_path.write_text(
                    _polygon_config_yaml(overlay, geojson_path), encoding="utf-8"
                )
                result = ingest_geo_dataset(session, cfg_path)
                if result.link_result.status == "linked":
                    linked += 1

            pocs_geojson_text = json.dumps(_pocs_feature_collection())
            if dataset_converged(
                session, name=POCS_DATASET_NAME, geojson_text=pocs_geojson_text
            ):
                linked += 1
            else:
                _drop_existing_dataset(session, POCS_DATASET_NAME)
                geojson_path = work_dir / f"{POCS_DATASET_NAME}.geojson"
                geojson_path.write_text(pocs_geojson_text, encoding="utf-8")
                cfg_path = work_dir / f"{POCS_DATASET_NAME}.yaml"
                cfg_path.write_text(_pocs_config_yaml(geojson_path), encoding="utf-8")
                result = ingest_geo_dataset(session, cfg_path)
                if result.link_result.status == "linked":
                    linked += 1

            # ABS-435 parcel fabric. Base geography, so it is not counted in
            # ``linked`` (nothing to link to) — it exists so the abuts
            # predicate can measure from the lot boundary instead of the
            # geocoded rooftop point.
            parcels_geojson_text = json.dumps(_parcels_feature_collection())
            if not role_dataset_converged(
                session, name=PARCELS_DATASET_NAME, geojson_text=parcels_geojson_text
            ):
                _drop_existing_dataset(session, PARCELS_DATASET_NAME)
                geojson_path = work_dir / f"{PARCELS_DATASET_NAME}.geojson"
                geojson_path.write_text(parcels_geojson_text, encoding="utf-8")
                cfg_path = work_dir / f"{PARCELS_DATASET_NAME}.yaml"
                cfg_path.write_text(
                    _parcels_config_yaml(geojson_path), encoding="utf-8"
                )
                ingest_geo_dataset(session, cfg_path)

            # ABS-469 street centerlines. Also base geography, and also
            # uncounted in ``linked``: it exists so the civic-address verifier
            # can ask whether a civic number is real before the geocoder gets
            # a chance to invent a position for it.
            centerlines_geojson_text = json.dumps(_centerlines_feature_collection())
            if not role_dataset_converged(
                session,
                name=CENTERLINES_DATASET_NAME,
                geojson_text=centerlines_geojson_text,
                role="road_centerlines",
            ):
                _drop_existing_dataset(session, CENTERLINES_DATASET_NAME)
                geojson_path = work_dir / f"{CENTERLINES_DATASET_NAME}.geojson"
                geojson_path.write_text(centerlines_geojson_text, encoding="utf-8")
                cfg_path = work_dir / f"{CENTERLINES_DATASET_NAME}.yaml"
                cfg_path.write_text(
                    _centerlines_config_yaml(geojson_path), encoding="utf-8"
                )
                ingest_geo_dataset(session, cfg_path)

            for (
                normalized,
                raw,
                point,
                confidence,
                detail,
                location_type,
            ) in _GEOCODE_ROWS:
                _ensure_geocode_cache(
                    session,
                    normalized=normalized,
                    raw=raw,
                    point=point,
                    confidence=confidence,
                    detail=detail,
                    location_type=location_type,
                )

            summary = {
                "document_id": document.id,
                "table_1a_id": table_ids["table_1a_id"],
                "table_1b_id": table_ids["table_1b_id"],
                "datasets_linked": linked,
                "purged_retired_documents": purged,
            }
    print(f"seed_e2e_rclub_unified summary: {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
