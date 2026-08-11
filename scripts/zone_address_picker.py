#!/usr/bin/env python3
"""Derive a real address from a zone — the inverse of what the eval used to do.

ABS-467. Every case in ``evals/regional_centre_test_prompts.json`` carries a
``zone`` and an ``address``. Those were independent operator inputs: the
address was invented to sound plausible for the zone, and nothing ever
checked it. 17 of 20 were wrong — 7 resolved into a *different* zone and 10
resolved outside every mapped boundary. A case claiming CEN-1 whose address
is in DH grades a correct DH answer against CEN-1 expectations.

The fix is to invert the order. Ask the zoning dataset for a parcel that is
unambiguously in the target zone, reverse-geocode that parcel to the civic
address on it, then push the address back through the *production*
``get_address_profile`` path and keep it only if it resolves to the zone we
asked for. ``zone`` stops being an assertion and becomes a derived,
re-checkable property.

Three properties make a candidate acceptable, in order of how much they cost
to check:

1. **Unambiguously zoned.** The parcel must lie wholly inside a polygon of
   the target zone and touch no polygon of any other zone. The zoning
   dataset carries several by-law areas whose polygons overlap (a point in a
   Regional Centre CEN-2 polygon can also sit under a legacy Downtown
   Halifax DH-1 polygon, which is how TC-014 came to resolve as DH-1), and
   ``_resolve_zone_at_point`` returns the first match. A parcel with one and
   only one zone over it cannot be decided by that ordering.
2. **Reverse-geocodable to a street address.** Google's reverse geocoder
   turns the parcel's interior point into a civic address. This is the step
   that makes the address *real* rather than plausible.
3. **Round-trips through production.** The composed address is fed to
   ``RetrievalService.get_address_profile`` — the same call the advisor
   makes — and must come back with the target zone. Anything else is
   discarded, including the case where Google forward-geocodes the address
   to a different city (the production geocoder queries civic-number +
   street with only a country filter, so that is a live risk).

ROOFTOP resolutions are preferred and searched for first; an interpolated
match is only returned when ``--allow-interpolated`` is passed, and it is
labelled as such so the caller can record the estimate on the case rather
than silently depend on it.

CLI:
    python scripts/zone_address_picker.py --zone CEN-1
    python scripts/zone_address_picker.py --zone ER-2 --candidates 40 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Protocol

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (os.path.join(REPO_ROOT, "src"), os.path.join(REPO_ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from bylaw_retrieval.retrieval.service import (  # noqa: E402
    RetrievalService,
    overlay_role_for_name,
)
from layer1.db.base import ExternalDataset  # noqa: E402

DEFAULT_DB_URL = "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"

# The Regional Centre Land Use By-law's area id in the HRM zoning dataset.
# The dataset spans every HRM by-law; scoping to this one keeps candidates
# inside the corpus the eval actually exercises (25 zone codes; see
# docs/ABS-467-EVAL-ADDRESS-DERIVATION.md for the coverage note).
REGIONAL_CENTRE_BYLAW_AREA_ID = "23"

PARCELS_ROLE = "property_parcels"
CENTERLINES_ROLE = "road_centerlines"

# How close a parcel must sit to a named centreline to count as fronting it.
# Wide enough to clear the road allowance and a deep front yard, narrow
# enough that the parcel behind the one on the corner does not qualify.
STREET_FRONTAGE_DISTANCE_M = 40.0
# The same distance in degrees, generously rounded up, for the index-friendly
# bounding-box prefilter that runs before the exact geography test. At 44.6°N
# one degree of longitude is ~79 km, so 0.0006° comfortably covers 40 m.
STREET_FRONTAGE_DISTANCE_DEG = 0.0006

# Parcel area window, in m². Below the floor are slivers and rights-of-way
# whose interior point is effectively on the street; above the ceiling are
# campuses and parks whose interior point can be hundreds of metres from any
# civic address, so the reverse geocode drifts off the parcel.
MIN_PARCEL_AREA_M2 = 120.0
MAX_PARCEL_AREA_M2 = 20000.0

_GEOCODE_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"

# Street suffixes layer2.retrieval.location.RegexLocationExtractor can parse.
# An address it cannot parse is unresolvable in production no matter how real
# it is, so candidates carrying any other suffix are dropped before we spend a
# forward geocode on them.
_EXTRACTABLE_SUFFIXES = frozenset(
    {
        "street", "st", "avenue", "ave", "rd", "road", "blvd", "boulevard",
        "drive", "dr", "lane", "ln", "way", "crescent", "cres", "place", "pl",
        "court", "ct", "highway", "hwy", "parkway", "pkwy", "terrace", "terr",
    }
)


@dataclass(frozen=True)
class ParcelPoint:
    """An interior point of a parcel that is unambiguously in one zone."""

    pid: str | None
    lat: float
    lon: float
    area_m2: float


@dataclass(frozen=True)
class ZoneAddress:
    """A real address that provably resolves to the zone it was derived for."""

    zone: str
    address: str
    resolved_zone: str
    resolution_quality: str
    location_type: str | None
    location_confidence: float | None
    location_resolver: str | None
    parcel_pid: str | None

    @property
    def is_rooftop(self) -> bool:
        return self.resolution_quality == "rooftop"


class ReverseGeocoder(Protocol):
    """Point -> candidate civic addresses. Injected so tests need no network."""

    def reverse(self, lat: float, lon: float) -> list[str]: ...


class GoogleReverseGeocoder:
    """Google's reverse geocoder, narrowed to ``street_address`` results.

    Returns addresses in the eval file's own shape — ``"1801 Hollis Street,
    Halifax, NS"`` — rather than Google's formatted string, which carries a
    postal code and country the production extractor would have to strip
    anyway. Never raises: a network blip yields no candidates, which the
    caller treats as "try the next parcel".
    """

    def __init__(self, api_key: str, *, timeout_s: float = 10.0) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s

    def reverse(self, lat: float, lon: float) -> list[str]:
        try:
            response = httpx.get(
                _GEOCODE_ENDPOINT,
                params={
                    "latlng": f"{lat},{lon}",
                    "key": self._api_key,
                    "result_type": "street_address",
                },
                timeout=self._timeout_s,
            )
            payload = response.json()
        except (httpx.HTTPError, OSError, ValueError):
            return []
        if payload.get("status") != "OK":
            return []
        addresses: list[str] = []
        for result in payload.get("results", []):
            address = _address_from_components(result.get("address_components") or [])
            if address and address not in addresses:
                addresses.append(address)
        return addresses


def _address_from_components(components: list[dict[str, Any]]) -> str | None:
    """Compose ``"<civic> <street>, <city>, NS"`` from Google's components.

    Returns None when the result has no civic number or no route (a
    street-segment or intersection result), or when the street's suffix is
    one the production extractor cannot parse.
    """
    by_type: dict[str, str] = {}
    for component in components:
        for type_name in component.get("types") or []:
            by_type.setdefault(type_name, component.get("long_name") or "")
    number = by_type.get("street_number", "").strip()
    route = by_type.get("route", "").strip()
    if not number.isdigit() or not route:
        # Rejects hyphenated multi-civic numbers ("2073-2075-2077 Brunswick
        # Street"). They are real addresses, but the production extractor
        # parses only the trailing number out of one, so the address in the
        # eval file and the address actually geocoded would differ.
        return None
    suffix = route.split()[-1].strip(".").lower()
    if suffix not in _EXTRACTABLE_SUFFIXES:
        return None
    city = (
        by_type.get("locality")
        or by_type.get("sublocality")
        or by_type.get("administrative_area_level_3")
        or "Halifax"
    ).strip()
    return f"{number} {route}, {city}, NS"


def zoning_dataset_id(session: Session) -> int | None:
    """The linked dataset whose name classifies as the zoning overlay.

    Uses ``overlay_role_for_name`` — the same classifier
    ``get_address_profile`` uses — so the picker and the verifier can never
    disagree about which dataset "the zone" comes from.
    """
    for dataset_id, name in session.execute(
        select(ExternalDataset.id, ExternalDataset.name)
    ).all():
        if overlay_role_for_name(name) == "zone":
            return dataset_id
    return None


def parcels_dataset_id(session: Session) -> int | None:
    """The dataset tagged ``role: property_parcels`` in its metadata."""
    for dataset_id, metadata in session.execute(
        select(ExternalDataset.id, ExternalDataset.metadata_json)
    ).all():
        if (metadata or {}).get("role") == PARCELS_ROLE:
            return dataset_id
    return None


def zone_codes(
    session: Session, *, bylaw_area_id: str | None = REGIONAL_CENTRE_BYLAW_AREA_ID
) -> dict[str, int]:
    """Every zone code mapped in ``bylaw_area_id``, with its polygon count."""
    dataset_id = zoning_dataset_id(session)
    if dataset_id is None:
        return {}
    rows = session.execute(
        text(
            """
            SELECT canonical_attributes_json->>'zone_code' AS zone_code,
                   COUNT(*) AS n
            FROM external_dataset_feature
            WHERE external_dataset_id = :dataset_id
              AND canonical_attributes_json->>'zone_code' IS NOT NULL
              AND (
                    CAST(:area AS text) IS NULL
                    OR canonical_attributes_json->>'bylaw_area_id' = :area
                  )
            GROUP BY 1
            ORDER BY 2 DESC
            """
        ),
        {"dataset_id": dataset_id, "area": bylaw_area_id},
    ).all()
    return {row.zone_code: int(row.n) for row in rows}


def centerlines_dataset_id(session: Session) -> int | None:
    """The dataset tagged ``role: road_centerlines`` in its metadata."""
    for dataset_id, metadata in session.execute(
        select(ExternalDataset.id, ExternalDataset.metadata_json)
    ).all():
        if (metadata or {}).get("role") == CENTERLINES_ROLE:
            return dataset_id
    return None


def candidate_parcels(
    session: Session,
    zone: str,
    *,
    bylaw_area_id: str | None = REGIONAL_CENTRE_BYLAW_AREA_ID,
    limit: int = 25,
    on_street: str | None = None,
) -> list[ParcelPoint]:
    """Interior points of parcels that lie in ``zone`` and in no other zone.

    ``ST_Within`` against the union of the zone's polygons keeps the whole
    parcel inside — a parcel merely *intersecting* the zone straddles a
    boundary, which is the exact geometry that makes an eval address
    fragile. The ``NOT EXISTS`` clause then drops parcels that any
    differently-coded polygon also covers, so the zone the production
    resolver reports cannot depend on which overlapping polygon it happens
    to hit first.

    ``on_street`` restricts candidates to parcels fronting a named street
    (matched against the centerline dataset's ``FULL_NAME``, case-insensitive
    prefix). Several eval cases lean on the street itself — Spring Garden as
    an arterial, Mumford as a transit corridor, Brunswick inside a viewplane
    — so an address on the same street keeps the scenario intact while the
    zone becomes true. It is a preference, not a guarantee: the caller falls
    back to the unrestricted search when the street carries no parcel in the
    target zone.

    Ordered largest-parcel-first: a bigger parcel puts its interior point
    further from the street, so the reverse geocode is more likely to land
    on the parcel itself rather than the property across the road. ``pid``
    breaks ties so the same database always yields the same candidates.
    """
    zoning_id = zoning_dataset_id(session)
    parcels_id = parcels_dataset_id(session)
    if zoning_id is None or parcels_id is None:
        return []
    centerlines_id = centerlines_dataset_id(session) if on_street else None
    if on_street and centerlines_id is None:
        return []
    rows = session.execute(
        text(
            """
            -- Collapsed to a single geometry up front. Left as a correlated
            -- EXISTS over the 18k-row centreline table, the planner joins it
            -- against every candidate parcel and the query blows past the
            -- 2-minute statement timeout. ST_Union over an empty match yields
            -- NULL, which the predicate below reads as "no such street".
            WITH street AS (
                SELECT ST_Union(geometry) AS geom
                FROM external_dataset_feature
                WHERE external_dataset_id = :centerlines_id
                  AND CAST(:street AS text) IS NOT NULL
                  AND UPPER(attributes_json->>'FULL_NAME')
                        LIKE UPPER(:street) || '%'
            )
            SELECT p.canonical_attributes_json->>'parcel_id' AS pid,
                   ST_Y(ST_PointOnSurface(p.geometry)) AS lat,
                   ST_X(ST_PointOnSurface(p.geometry)) AS lon,
                   ST_Area(p.geometry::geography) AS area_m2
            FROM external_dataset_feature p, street s
            WHERE p.external_dataset_id = :parcels_id
              AND (
                    CAST(:street AS text) IS NULL
                    OR (
                        s.geom IS NOT NULL
                        -- Bounding-box prefilter first so the GiST index does
                        -- the work; the geography test then makes the metre
                        -- distance exact.
                        AND p.geometry && ST_Expand(s.geom, :street_distance_deg)
                        AND ST_DWithin(
                              p.geometry::geography,
                              s.geom::geography,
                              :street_distance_m
                        )
                    )
              )
              AND EXISTS (
                    SELECT 1
                    FROM external_dataset_feature z
                    WHERE z.external_dataset_id = :zoning_id
                      AND z.canonical_attributes_json->>'zone_code' = :zone
                      AND (
                            CAST(:area AS text) IS NULL
                            OR z.canonical_attributes_json->>'bylaw_area_id'
                                 = :area
                          )
                      AND ST_Contains(z.geometry, p.geometry)
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM external_dataset_feature o
                    WHERE o.external_dataset_id = :zoning_id
                      AND o.canonical_attributes_json->>'zone_code' <> :zone
                      AND ST_Intersects(o.geometry, p.geometry)
              )
              AND ST_Area(p.geometry::geography)
                    BETWEEN :min_area AND :max_area
            ORDER BY area_m2 DESC, pid
            LIMIT :limit
            """
        ),
        {
            "zoning_id": zoning_id,
            "parcels_id": parcels_id,
            "centerlines_id": centerlines_id,
            "zone": zone,
            "area": bylaw_area_id,
            "street": on_street,
            "street_distance_m": STREET_FRONTAGE_DISTANCE_M,
            "street_distance_deg": STREET_FRONTAGE_DISTANCE_DEG,
            "min_area": MIN_PARCEL_AREA_M2,
            "max_area": MAX_PARCEL_AREA_M2,
            "limit": limit,
        },
    ).all()
    return [
        ParcelPoint(
            pid=row.pid, lat=float(row.lat), lon=float(row.lon),
            area_m2=float(row.area_m2),
        )
        for row in rows
    ]


def verify_address(
    service: RetrievalService, zone: str, address: str
) -> ZoneAddress | None:
    """Push ``address`` through production and keep it only if it lands in ``zone``.

    This is the whole point of the module: the returned object is evidence,
    not a claim. A mismatch, an unresolvable address, or a point outside
    every mapped boundary all return None.
    """
    profile = service.get_address_profile(address)
    if profile.unresolvable or profile.zone != zone:
        return None
    return ZoneAddress(
        zone=zone,
        address=address,
        resolved_zone=profile.zone,
        resolution_quality=profile.resolution_quality,
        location_type=profile.location_type,
        location_confidence=profile.location_confidence,
        location_resolver=profile.location_resolver,
        parcel_pid=profile.pid,
    )


def pick_address_for_zone(
    session: Session,
    zone: str,
    *,
    reverse_geocoder: ReverseGeocoder,
    service: RetrievalService | None = None,
    bylaw_area_id: str | None = REGIONAL_CENTRE_BYLAW_AREA_ID,
    candidates: int = 25,
    allow_interpolated: bool = False,
    exclude: Iterable[str] = (),
    on_street: str | None = None,
) -> ZoneAddress | None:
    """Return a verified address in ``zone``, preferring a ROOFTOP match.

    Every candidate is checked; the first ROOFTOP one wins immediately. A
    verified but interpolated match is held back and returned only if no
    ROOFTOP candidate exists and ``allow_interpolated`` is set — the caller
    is then responsible for recording the estimate on the case (ABS-466's
    resolution-quality vocabulary is what it records).

    ``on_street``, when given, is tried first and the unrestricted search is
    the fallback, so a case keeps its narrative street where the zone allows
    one and still gets a true address where it does not.

    ``exclude`` skips addresses already spoken for, so a batch run does not
    hand the same address to two cases.
    """
    service = service or RetrievalService(session)
    excluded = {a.strip().lower() for a in exclude}
    fallback: ZoneAddress | None = None
    seen: set[str] = set()

    for street in ([on_street] if on_street else []) + [None]:
        for parcel in candidate_parcels(
            session, zone, bylaw_area_id=bylaw_area_id, limit=candidates,
            on_street=street,
        ):
            for address in reverse_geocoder.reverse(parcel.lat, parcel.lon):
                key = address.strip().lower()
                if key in seen or key in excluded:
                    continue
                seen.add(key)
                verified = verify_address(service, zone, address)
                if verified is None:
                    continue
                verified = ZoneAddress(**{**asdict(verified), "parcel_pid": parcel.pid})
                if verified.is_rooftop:
                    return verified
                if fallback is None:
                    fallback = verified
    return fallback if allow_interpolated else None


def _build_reverse_geocoder() -> ReverseGeocoder:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "GOOGLE_MAPS_API_KEY is not set. Address derivation reverse-geocodes "
            "parcel interior points, so it needs the same key production uses."
        )
    return GoogleReverseGeocoder(api_key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--zone", required=True, help="Target zone code, e.g. CEN-1")
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL", DEFAULT_DB_URL))
    parser.add_argument(
        "--bylaw-area-id",
        default=REGIONAL_CENTRE_BYLAW_AREA_ID,
        help="Restrict to one by-law area; pass '' for every area.",
    )
    parser.add_argument("--candidates", type=int, default=25)
    parser.add_argument(
        "--on-street",
        help="Prefer parcels fronting this street (e.g. 'SPRING GARDEN').",
    )
    parser.add_argument(
        "--allow-interpolated",
        action="store_true",
        help="Accept an interpolated match when no ROOFTOP address exists.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    engine = create_engine(args.db_url)
    with Session(engine) as session:
        picked = pick_address_for_zone(
            session,
            args.zone,
            reverse_geocoder=_build_reverse_geocoder(),
            bylaw_area_id=args.bylaw_area_id or None,
            candidates=args.candidates,
            allow_interpolated=args.allow_interpolated,
            on_street=args.on_street,
        )
        session.commit()

    if picked is None:
        print(f"No verified address found in zone {args.zone}.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(picked), indent=2))
    else:
        print(f"{picked.address}  [{picked.resolution_quality}, PID {picked.parcel_pid}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
