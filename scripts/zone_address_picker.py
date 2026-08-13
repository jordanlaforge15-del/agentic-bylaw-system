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
   turns the parcel's interior point into candidate civic addresses.
3. **Registered on that parcel.** ABS-474: a reverse geocode returns the
   *nearest* street address to a point, which is not the same thing as the
   address the municipality assigns to the parcel. On a corner lot it takes
   the civic number from one street and the route from another; on a large
   multi-frontage parcel it interpolates a number between two real ones.
   Three of the twenty cases this module authored were fabricated that way —
   parcel 40811085 is registered 249/251/257 Windmill Road and became "251
   Stairs Street". Every candidate is now checked against HRM's
   ``CivicAddresses`` register by the parcel's PID, and the register's own
   list is recorded on the result so a later guard can re-assert it offline.
4. **Round-trips through production.** The composed address is fed to
   ``RetrievalService.get_address_profile`` — the same call the advisor
   makes — and must come back with the target zone. Anything else is
   discarded, including the case where Google forward-geocodes the address
   to a different city (the production geocoder queries civic-number +
   street with only a country filter, so that is a live risk). Note this
   step cannot substitute for step 3: a fabricated address composed from a
   corner lot forward-geocodes back onto the same parcel, so it passes.

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
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (os.path.join(REPO_ROOT, "src"), os.path.join(REPO_ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from bylaw_retrieval.retrieval.service import (
    RetrievalService,
    overlay_role_for_name,
)

from layer1.db.base import ExternalDataset

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

_PROVINCE_CODES = frozenset(
    {"NS", "NB", "PE", "NL", "QC", "ON", "MB", "SK", "AB", "BC", "YT", "NT", "NU"}
)

_GEOCODE_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"
# HRM's authoritative civic-address register. Same layer as
# src/layer1/datasets/halifax_civic_addresses.yaml, queried live because that
# config is not ingested yet (ABS-475).
_CIVIC_REGISTER_ENDPOINT = (
    "https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services"
    "/CivicAddresses/FeatureServer/0/query"
)
# HRM writes the street type abbreviated; the corpus and Google's address
# components both spell it out. A type with no entry here composes without a
# suffix, which only makes the comparison stricter.
_REGISTER_STREET_TYPES = {
    "ST": "Street",
    "RD": "Road",
    "AVE": "Avenue",
    "AV": "Avenue",
    "DR": "Drive",
    "PL": "Place",
    "LANE": "Lane",
    "LN": "Lane",
    "BLVD": "Boulevard",
    "CRES": "Crescent",
    "CRT": "Court",
    "CT": "Court",
    "TERR": "Terrace",
    "HWY": "Highway",
    "PKWY": "Parkway",
    "WAY": "Way",
}

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
    # Every civic address the municipality registers to ``parcel_pid``, in the
    # eval file's own shape. Recorded on the case so the offline guard can
    # assert ``address`` is one of them without the 155k-point register being
    # ingested (ABS-475 replaces this snapshot with a live lookup).
    registered_civics: tuple[str, ...] = ()

    @property
    def is_rooftop(self) -> bool:
        return self.resolution_quality == "rooftop"


class ReverseGeocoder(Protocol):
    """Point -> candidate civic addresses. Injected so tests need no network."""

    def reverse(self, lat: float, lon: float) -> list[str]: ...


class CivicRegister(Protocol):
    """Parcel id -> the civic addresses the municipality registers on it.

    Injected so tests need no network, exactly like ``ReverseGeocoder``.
    """

    def civics_for_pid(self, pid: str) -> list[str]: ...

    def pid_for_address(self, address: str) -> str | None: ...


class HrmCivicRegister:
    """HRM's ``CivicAddresses`` layer, queried by parcel ``PID``.

    This is the authority a reverse geocoder is not. Google returns the
    *nearest* street address to a point, which on a corner lot or a
    multi-frontage parcel belongs to a different street: parcel 40811085 is
    registered 249/251/257 Windmill Road, and reverse-geocoding its interior
    point produced "251 Stairs Street" — the civic number from Windmill and
    the route from the nearer centerline. The zone round-trip cannot catch
    that, because forward-geocoding the composed string lands back on the same
    parcel (ABS-474).

    Queried live rather than from the database on purpose: this is an offline
    authoring script that already calls Google, and ingesting the layer has
    prerequisites in the production resolver that are tracked separately
    (ABS-475). Never raises — an unreachable service yields no civics, which
    the caller treats as "cannot confirm, skip this candidate".
    """

    def __init__(self, *, timeout_s: float = 15.0) -> None:
        self._timeout_s = timeout_s

    def civics_for_pid(self, pid: str) -> list[str]:
        if not pid:
            return []
        rows = self._query(f"PID='{pid}'")
        civics: list[str] = []
        for attributes in rows:
            composed = _address_from_register_row(attributes)
            if composed and composed not in civics:
                civics.append(composed)
        return civics

    def pid_for_address(self, address: str) -> str | None:
        """The parcel an address is registered on, or None.

        The inverse lookup, for a case that predates parcel provenance being
        recorded. Matching is on the register's own columns rather than a
        string compare, so "1801 Hollis Street, Halifax, NS" finds the row
        HRM stores as CIV_NUM 1801 / STR_NAME HOLLIS / STR_TYPE ST.
        """
        parsed = _parse_composed_address(address)
        if parsed is None:
            return None
        number, street, community = parsed
        # Doubling is how a literal quote is escaped in an ArcGIS where
        # clause; it belongs with the clause, not with address parsing.
        where = f"CIV_NUM={number} AND STR_NAME='{_sql_quote(street)}'"
        if community:
            where += f" AND GSA_NAME='{_sql_quote(community)}'"
        for attributes in self._query(where):
            pid = str(attributes.get("PID") or "").strip()
            if pid:
                return pid
        return None

    def _query(self, where: str) -> list[dict[str, Any]]:
        """Rows matching ``where``, or [] for any failure. Never raises."""
        try:
            response = httpx.get(
                _CIVIC_REGISTER_ENDPOINT,
                params={
                    "where": where,
                    "outFields": "PID,CIV_NUM,STR_NAME,STR_TYPE,GSA_NAME",
                    "returnGeometry": "false",
                    "f": "json",
                },
                timeout=self._timeout_s,
            )
            payload = response.json()
        except (httpx.HTTPError, OSError, ValueError):
            return []
        return [f.get("attributes") or {} for f in payload.get("features") or []]


def _sql_quote(value: str) -> str:
    """Escape a literal for an ArcGIS ``where`` clause."""
    return value.replace("'", "''")


def _parse_composed_address(address: str) -> tuple[int, str, str | None] | None:
    """``"1801 Hollis Street, Halifax, NS"`` -> ``(1801, "HOLLIS", "HALIFAX")``.

    Drops the street type, because the register stores it in its own column
    and this is only building a query filter — the type is not needed to find
    the row, and guessing its abbreviation would exclude real matches.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return None
    head = parts[0].split()
    if len(head) < 2 or not head[0].isdigit():
        return None
    number = int(head[0])
    words = head[1:]
    # Trailing street type, when it is one we recognise, is not part of the name.
    if len(words) > 1 and words[-1].title() in _REGISTER_STREET_TYPES.values():
        words = words[:-1]
    street = " ".join(words).upper()
    community = parts[1].upper() if len(parts) >= 3 else None
    if community in _PROVINCE_CODES:
        community = None
    return number, street, community


def _address_from_register_row(attributes: dict[str, Any]) -> str | None:
    """Compose the eval file's address shape from one register row.

    HRM stores the street name and its type in separate columns and writes the
    type abbreviated (``ST``, ``RD``, ``AVE``), so the abbreviation is expanded
    back to the word the corpus and Google's components both use.
    """
    number = attributes.get("CIV_NUM")
    name = str(attributes.get("STR_NAME") or "").strip()
    if number in (None, "") or not name:
        return None
    suffix = _REGISTER_STREET_TYPES.get(
        str(attributes.get("STR_TYPE") or "").strip().upper(), ""
    )
    community = str(attributes.get("GSA_NAME") or "").strip().title()
    street = " ".join(part for part in (name.title(), suffix) if part)
    return ", ".join(part for part in (f"{number} {street}", community, "NS") if part)


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
    civic_register: CivicRegister,
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

    Every candidate must also be a civic the municipality registers on the
    parcel it came from. The reverse geocoder proposes; ``civic_register``
    disposes. Without that check a corner lot yields the civic number from one
    street and the route from another, and the zone round-trip below confirms
    it because the composed string geocodes back to the same parcel.

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
            # What the municipality actually registers on this parcel. A
            # reverse geocode is a guess at this; the register is the fact.
            registered = (
                civic_register.civics_for_pid(parcel.pid) if parcel.pid else []
            )
            if not registered:
                # Cannot confirm any candidate against this parcel, so nothing
                # derived from it is evidence. Skipping is the conservative
                # move: the next parcel is free, a fabricated address is not.
                continue
            for address in reverse_geocoder.reverse(parcel.lat, parcel.lon):
                key = address.strip().lower()
                if key in seen or key in excluded:
                    continue
                seen.add(key)
                if not _is_registered(address, registered):
                    # The composed string names a civic the municipality does
                    # not put on this parcel — a corner lot's neighbouring
                    # street, or a number interpolated between two real ones.
                    continue
                verified = verify_address(service, zone, address)
                if verified is None:
                    continue
                verified = ZoneAddress(
                    **{
                        **asdict(verified),
                        "parcel_pid": parcel.pid,
                        "registered_civics": tuple(registered),
                    }
                )
                if verified.is_rooftop:
                    return verified
                if fallback is None:
                    fallback = verified
    return fallback if allow_interpolated else None


def _normalize_for_match(address: str) -> str:
    """Collapse an address to the form two sources can be compared on."""
    return " ".join(address.replace(",", " ").lower().split())


def _is_registered(address: str, registered: Iterable[str]) -> bool:
    """True when ``address`` is one of the parcel's registered civics.

    Compared on the normalized string rather than parsed parts: both sides are
    composed by this module into the same shape, so a mismatch means a genuine
    difference in number, street or community — which is exactly what must be
    rejected.
    """
    target = _normalize_for_match(address)
    return any(target == _normalize_for_match(known) for known in registered)


def _build_reverse_geocoder() -> ReverseGeocoder:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "GOOGLE_MAPS_API_KEY is not set. Address derivation reverse-geocodes "
            "parcel interior points, so it needs the same key production uses."
        )
    return GoogleReverseGeocoder(api_key)


def _build_civic_register() -> CivicRegister:
    """HRM's register needs no key — it is an open ArcGIS service."""
    return HrmCivicRegister()


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
            civic_register=_build_civic_register(),
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
