"""ABS-469 — resolve civic addresses authoritatively instead of hedging.

ABS-466 made a weak resolution visible. It did not make it correct: Google
answers "100 Robie Street" with a confident coordinate it produced by
interpolating along the street from the surrounding civic numbering, and that
point sits on somebody else's parcel. These tests pin the correctness half.

Fixtures are real. Every address range below is HRM's published data for the
named street, read out of the live corpus's ``halifax_street_centerlines``
ingest, and every address is one the issue measured:

    100 Robie Street     no segment covers it   -> does not exist
    567 Windsor Street   no segment covers it   -> does not exist
    89 Jubilee Road      Jubilee ROAD runs 6000-6770; Jubilee COURT starts at
                         2, so the street-type filter is what stops this
                         reading as a real address
    1222 Robie Street    covered                -> exists
    1234 Oxford Street   covered                -> exists

The two "no zone" states must stay distinguishable — an address that does not
exist and an address that resolved outside the mapped plan area are different
answers to the user — so both are asserted here, together.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from bylaw_retrieval.retrieval import RetrievalService

from advisor.chat.compact import compact_address_profile
from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    GeocodeCache,
    SourceFragment,
)
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer2.db.init_db import create_all as create_layer2
from layer2.retrieval.civic_address import format_ranges, verify_civic_address

# The zoning polygon and a point inside it, matching the other address-profile
# fixtures so the two suites describe the same synthetic Halifax.
TEST_POINT = {"type": "Point", "coordinates": [-63.59, 44.65]}
_BOX = [
    [-63.60, 44.64],
    [-63.58, 44.64],
    [-63.58, 44.66],
    [-63.60, 44.66],
    [-63.60, 44.64],
]
# A second zone, sharing the -63.58 edge with the first. Points placed just
# west of that edge sit in HR-2 with CEN-1 a few metres away — the geometry of
# a confidently wrong setback.
_EAST_BOX = [
    [-63.58, 44.64],
    [-63.56, 44.64],
    [-63.56, 44.66],
    [-63.58, 44.66],
    [-63.58, 44.64],
]
# ~9 m west of the shared edge at this latitude (1 deg lon ~ 79,200 m).
BOUNDARY_POINT = {"type": "Point", "coordinates": [-63.580115, 44.65]}
# A lot straddling the shared edge: ~34 m either side of it, ~22 m deep.
SPLIT_PARCEL = {
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
# A lot wholly inside HR-2 that merely touches the boundary — the sliver case
# the multi-zone test must NOT report.
SLIVER_PARCEL = {
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
SLIVER_POINT = {"type": "Point", "coordinates": [-63.58040, 44.6490]}


# HRM's published per-segment ranges, straight from the ingested layer.
# (feature_key, STR_NAME, STR_TYPE, FROM_LEFT, TO_LEFT, FROM_RIGHT, TO_RIGHT)
CENTERLINE_SEGMENTS: tuple[tuple[str, str, str, int, int, int, int], ...] = (
    ("ST14888", "ROBIE", "ST", 1200, 1298, 1201, 1299),
    ("ST4619", "ROBIE", "ST", 2454, 2526, 2453, 2525),
    ("ST1767", "OXFORD", "ST", 1222, 1388, 1223, 1389),
    ("ST-WINDSOR-1", "WINDSOR", "ST", 2000, 2006, 2001, 2007),
    ("ST-WINDSOR-2", "WINDSOR", "ST", 2008, 2088, 2009, 2089),
    ("ST802", "JUBILEE", "RD", 6600, 6648, 6601, 6649),
    ("ST-JUBILEE-2", "JUBILEE", "RD", 6000, 6046, 6001, 6045),
    # Jubilee Court shares the street name and starts at 2. Without the
    # street-type filter, "89 Jubilee Road" lands inside this range and the
    # check confirms an address that does not exist.
    ("ST-JUBILEE-CRT", "JUBILEE", "CRT", 2, 98, 1, 99),
    # A service lane with no addressing: HRM writes 0/0, which is a
    # placeholder, not a range that covers the number 0.
    ("ST-LANEWAY", "BACKLOT", "LANE", 0, 0, 0, 0),
)


def _bbox(geometry: dict) -> dict:
    def points(coords: object) -> list[list[float]]:
        if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
            return [coords]  # type: ignore[list-item]
        return [pt for part in coords for pt in points(part)]  # type: ignore[union-attr]

    pts = points(geometry["coordinates"])
    xs = [pt[0] for pt in pts]
    ys = [pt[1] for pt in pts]
    return {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}


def _add_dataset(
    session,
    *,
    name: str,
    features: list[tuple[str, dict, dict, dict]],
    role: str | None = None,
    fragment_id: int | None = None,
    citation: str | None = None,
) -> int:
    """Insert a dataset and its features.

    ``features`` is (feature_key, raw attributes, canonical attributes,
    geometry). Rows are built directly rather than through the ingest
    pipeline so each feature's attributes are pinned exactly.
    """
    dataset = ExternalDataset(
        name=name,
        publisher="Test",
        format="geojson",
        content_hash=f"hash-{name}",
        crs="EPSG:4326",
        feature_count=len(features),
        linked_fragment_id=fragment_id,
        linked_fragment_citation=citation,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={"role": role} if role else {},
    )
    session.add(dataset)
    session.flush()
    for feature_key, raw, canonical, geometry in features:
        session.add(
            ExternalDatasetFeature(
                external_dataset_id=dataset.id,
                feature_key=feature_key,
                attributes_json=raw,
                canonical_attributes_json=canonical,
                geometry_geojson=geometry,
                geometry_bbox_json=_bbox(geometry),
                parse_status=ParseStatus.PARSED,
                metadata_json={},
            )
        )
    session.flush()
    return dataset.id


def _centerline_features() -> list[tuple[str, dict, dict, dict]]:
    return [
        (
            key,
            {
                "STR_NAME": name,
                "STR_TYPE": street_type,
                "FROM_LEFT": from_left,
                "TO_LEFT": to_left,
                "FROM_RIGHT": from_right,
                "TO_RIGHT": to_right,
            },
            {},
            {"type": "LineString", "coordinates": [[-63.59, 44.64], [-63.59, 44.66]]},
        )
        for key, name, street_type, from_left, to_left, from_right, to_right in (
            CENTERLINE_SEGMENTS
        )
    ]


def _cache_row(normalized: str, raw: str, geometry: dict) -> GeocodeCache:
    return GeocodeCache(
        normalized_text=normalized,
        raw_text=raw,
        kind="civic_address",
        status="linked",
        resolver="test_seed",
        geometry_geojson=geometry,
        confidence=1.0,
        detail=None,
        metadata_json={},
    )


@pytest.fixture()
def seeded_db(tmp_path: Path) -> str:
    """A corpus with zoning, a parcel fabric, and HRM's real address ranges."""
    db_url = f"sqlite:///{tmp_path / 'civic_address.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/synthetic.pdf",
            file_hash="c" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            page_count=500,
        )
        session.add(document)
        session.flush()
        fragment = SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.SCHEDULE,
            citation_label="Zoning Schedule",
            citation_path="zoning_schedule",
            page_start=500,
            page_end=500,
            text="Zoning Schedule.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        session.add(fragment)
        session.flush()

        _add_dataset(
            session,
            name="halifax_zoning_boundaries",
            fragment_id=fragment.id,
            citation="Zoning Schedule",
            features=[
                (
                    "zone-hr2",
                    {},
                    {"zone_code": "HR-2"},
                    {"type": "Polygon", "coordinates": [_BOX]},
                ),
                (
                    "zone-cen1",
                    {},
                    {"zone_code": "CEN-1"},
                    {"type": "Polygon", "coordinates": [_EAST_BOX]},
                ),
            ],
        )
        _add_dataset(
            session,
            name="halifax_street_centerlines",
            role="road_centerlines",
            features=_centerline_features(),
        )
        _add_dataset(
            session,
            name="halifax_property_parcels",
            role="property_parcels",
            features=[
                ("parcel-split", {}, {"parcel_id": "SPLIT"}, SPLIT_PARCEL),
                ("parcel-sliver", {}, {"parcel_id": "SLIVER"}, SLIVER_PARCEL),
            ],
        )

        session.add(_cache_row("civic:1222 robie st", "1222 Robie Street", TEST_POINT))
        session.add(_cache_row("civic:1234 oxford st", "1234 Oxford Street", TEST_POINT))
        # Deliberately cached: the geocoder WOULD answer a fabricated address.
        # The profile must refuse it anyway, without consulting this row.
        session.add(_cache_row("civic:100 robie st", "100 Robie Street", TEST_POINT))
        session.add(
            _cache_row("civic:2454 robie st", "2454 Robie Street", BOUNDARY_POINT)
        )
        session.add(
            _cache_row("civic:2456 robie st", "2456 Robie Street", SLIVER_POINT)
        )
        # Resolves fine, but to a point outside every mapped polygon.
        session.add(
            _cache_row(
                "civic:2526 robie st",
                "2526 Robie Street",
                {"type": "Point", "coordinates": [-63.40, 44.80]},
            )
        )

    return db_url


# ---------------------------------------------------------------------------
# Tier 1 — the civic number against HRM's own street data
# ---------------------------------------------------------------------------


def test_fabricated_civic_number_is_reported_as_nonexistent(seeded_db: str) -> None:
    """100 Robie Street: linked by Google at 0.85, covered by no segment."""
    with session_scope(seeded_db) as session:
        verdict = verify_civic_address(
            session, civic_number="100", street="Robie Street"
        )

    assert verdict.status == "not_found"
    assert verdict.method == "street_centerline_ranges"
    assert format_ranges(verdict.valid_ranges) == ["1200-1298", "2454-2526"]
    # The correction is the near end of the nearest range, not its far end.
    assert verdict.suggestions[0] == 1200


def test_covered_civic_number_is_confirmed_with_its_segment(seeded_db: str) -> None:
    """1222 Robie Street exists, and the verdict says which segment proved it.

    The segment, range and side are the auditable half of local
    interpolation: the resolver can state how it knows, which a geocoder's
    opaque coordinate never could.
    """
    with session_scope(seeded_db) as session:
        verdict = verify_civic_address(
            session, civic_number="1222", street="Robie Street"
        )

    assert verdict.status == "confirmed"
    assert verdict.matched_segment == "ST14888"
    assert verdict.matched_range == (1200, 1298)
    assert verdict.matched_side == "left"


def test_street_type_separates_jubilee_road_from_jubilee_court(seeded_db: str) -> None:
    """89 Jubilee Road does not exist — but 89 Jubilee COURT would.

    Both share a STR_NAME. Aggregating them (or ignoring the type) confirms a
    fabricated address, which is the exact failure the per-segment rule
    exists to prevent.
    """
    with session_scope(seeded_db) as session:
        road = verify_civic_address(session, civic_number="89", street="Jubilee Road")
        court = verify_civic_address(session, civic_number="89", street="Jubilee Court")

    assert road.status == "not_found"
    # Odd-side ranges only: the parity of the number asked about is the parity
    # of the numbers worth suggesting.
    assert format_ranges(road.valid_ranges) == ["6001-6045", "6601-6649"]
    assert court.status == "confirmed"


def test_unknown_street_is_unverifiable_never_nonexistent(seeded_db: str) -> None:
    """A street the data has never heard of proves nothing about the address.

    Renamed streets are the live case: HRM's centerline ranges still had a
    gap over Nora Bernard Street's 5440-5549 stretch after the rename, and a
    'not_found' there would refuse a real address.
    """
    with session_scope(seeded_db) as session:
        verdict = verify_civic_address(
            session, civic_number="5531", street="Nora Bernard Street"
        )

    assert verdict.status == "unverifiable"
    assert verdict.valid_ranges == ()


def test_placeholder_zero_ranges_are_not_treated_as_coverage(seeded_db: str) -> None:
    """A 0/0 segment means 'no addressing here', not 'covers 0'."""
    with session_scope(seeded_db) as session:
        verdict = verify_civic_address(session, civic_number="0", street="Backlot Lane")

    assert verdict.status == "not_found"
    assert verdict.valid_ranges == ()


def test_civic_address_points_outrank_centerline_ranges(seeded_db: str) -> None:
    """An ingested civic-address register is authoritative; ranges are not.

    The centerline ranges say 100 Robie Street does not exist. A published
    civic-address point for it would mean it does, and the register wins —
    which is why the register is the tier that ends the interpolation
    question for good.
    """
    with session_scope(seeded_db) as session:
        _add_dataset(
            session,
            name="halifax_civic_addresses",
            role="civic_address",
            features=[
                (
                    "civic-100-robie",
                    {},
                    {"civic_number": "100", "street_name": "Robie"},
                    TEST_POINT,
                ),
                (
                    "civic-104-robie",
                    {},
                    {"civic_number": "104", "street_name": "Robie"},
                    TEST_POINT,
                ),
            ],
        )
        confirmed = verify_civic_address(
            session, civic_number="100", street="Robie Street"
        )
        missing = verify_civic_address(
            session, civic_number="102", street="Robie Street"
        )

    assert confirmed.status == "confirmed"
    assert confirmed.method == "civic_address_points"
    assert missing.status == "not_found"
    assert missing.method == "civic_address_points"
    assert missing.suggestions == (100, 104)


# ---------------------------------------------------------------------------
# The profile: three distinct "no zone" states
# ---------------------------------------------------------------------------


def test_profile_refuses_a_nonexistent_address_with_a_suggestion(
    seeded_db: str,
) -> None:
    """DoD 1 + 2 — non-existent is its own state, and it carries a correction.

    The geocode cache holds a linked point for 100 Robie Street, so a profile
    that consulted the geocoder would report HR-2 with a straight face. It
    must not: no zone, an explicit status, and the ranges that do exist.
    """
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("100 Robie Street")

    assert profile.civic_address_status == "not_found"
    assert profile.zone is None
    assert profile.overlays == []
    # Not the same as "we could not find it" and not the same as "outside the
    # mapped area" — both of those would send the user somewhere useless.
    assert profile.unresolvable is False
    assert profile.outside_mapped_area is False
    assert profile.valid_civic_number_ranges == ["1200-1298", "2454-2526"]
    assert profile.suggested_civic_numbers[0] == "1200"
    assert any("does not exist" in caveat for caveat in profile.caveats)
    assert "halifax_street_centerlines" in (profile.civic_address_evidence or "")


def test_profile_distinguishes_outside_mapped_area_from_nonexistent(
    seeded_db: str,
) -> None:
    """DoD 1 — the other zoneless state stays exactly what it was.

    2526 Robie Street is a real civic number that resolves to a point outside
    every mapped polygon. "This by-law does not map that location" and "that
    address does not exist" are different answers and must not collapse.
    """
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("2526 Robie Street")

    assert profile.civic_address_status == "confirmed"
    assert profile.outside_mapped_area is True
    assert profile.zone is None


def test_profile_confirms_a_real_address_and_still_reports_the_zone(
    seeded_db: str,
) -> None:
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("1222 Robie Street")

    assert profile.civic_address_status == "confirmed"
    assert profile.zone == "HR-2"
    assert profile.outside_mapped_area is False


def test_compact_projection_answers_a_nonexistent_address_and_nothing_else(
    seeded_db: str,
) -> None:
    """What the model actually sees. A zone in this payload would be read as
    a fact about a property that does not exist, so the projection carries the
    refusal, the correction, and no spatial verdicts at all."""
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("100 Robie Street")

    compact = compact_address_profile(profile)
    assert compact["civic_address_status"] == "not_found"
    assert "zone" not in compact
    assert "overlays" not in compact
    assert compact["valid_civic_number_ranges"] == ["1200-1298", "2454-2526"]
    assert "does not exist" in compact["instruction"]


# ---------------------------------------------------------------------------
# Tier 4 — a zone is only as safe as the parcel it names
# ---------------------------------------------------------------------------


def test_profile_reports_a_zone_boundary_a_few_metres_away(seeded_db: str) -> None:
    """DoD 3 — proximity to the zone line, independent of geocoder quality.

    Mirrors the live corpus: 6321 Quinpool Road resolves ROOFTOP inside CEN-2
    and sits 7.6 m from CEN-1. A perfect geocode does not make that answer
    safe, and the profile has to say so.
    """
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("2454 Robie Street")

    assert profile.zone == "HR-2"
    assert profile.nearest_other_zone == "CEN-1"
    assert profile.zone_boundary_distance_m is not None
    assert 5.0 <= profile.zone_boundary_distance_m <= 15.0
    assert any("boundary" in caveat for caveat in profile.caveats)


def test_profile_reports_a_parcel_split_between_two_zones(seeded_db: str) -> None:
    """DoD 3 — a lot straddling the line has no single governing zone."""
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("2454 Robie Street")

    assert profile.parcel_zones == ["HR-2", "CEN-1"] or profile.parcel_zones == [
        "CEN-1",
        "HR-2",
    ]
    assert any("split" in caveat for caveat in profile.caveats)


def test_a_parcel_touching_the_boundary_is_not_reported_as_split(
    seeded_db: str,
) -> None:
    """The guard that keeps the split-lot signal worth reading.

    Zone polygons share their edges, so a lot that merely abuts the next zone
    picks up a sliver of it from coordinate precision alone. Reporting those
    as split lots would fire on most of the fabric and train the reader to
    ignore the field.
    """
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("2456 Robie Street")

    assert profile.zone == "HR-2"
    assert profile.parcel_zones == []
