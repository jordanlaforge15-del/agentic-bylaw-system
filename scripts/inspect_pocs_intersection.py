"""Spatial-intersection probe for the Schedule 7 POCS layer (ABS-349).

Test-scaffolding seam for ``web/e2e/functional/schedule7-pocs-intersection.spec.ts``
(mirrors ``scripts/inspect_case_metadata.py``): it drives the *production*
spatial path — resolve the civic address through the geocode cache, buffer the
resolved point, then intersect it against the ingested POCS dataset via
``layer2.retrieval.spatial.query_features`` (PostGIS ``ST_Intersects`` on the
real ``geometry`` column). The retrieval-service overlay-role wiring that would
surface this in a normal answer is a separate ticket, so this probe is the
seam that lets the spec assert the layer is queryable without expanding the
public API surface.

Emits one JSON object on stdout::

    {"address": "...", "normalized": "civic:...", "resolved": true,
     "buffer_m": 15.0, "intersects": true, "segment_count": 1,
     "streets": ["Quinpool Road"]}

Usage::

    DATABASE_URL=... .venv/bin/python scripts/inspect_pocs_intersection.py \\
        --address "6184 Quinpool Road" \\
        --dataset-name e2e_pedestrian_oriented_commercial_streets
"""
from __future__ import annotations

import argparse
import json
import math
import sys

from shapely.geometry import mapping, shape
from sqlalchemy import select, text

from layer1.db.base import ExternalDataset
from layer1.db.session import session_scope
from seed_e2e_rclub_unified import CORPUS_ADVISORY_LOCK_KEY
from layer2.retrieval.geocode import resolve_location
from layer2.retrieval.location import extract_location_references
from layer2.retrieval.spatial import ResolvedLocation, query_features

# 1 degree of latitude ≈ 111_320 m; longitude shrinks by cos(lat). Buffering a
# geographic point in raw degrees would be ~40% narrower E-W than N-S at
# Halifax's latitude, so we scale each axis independently to a true metric
# radius before handing PostGIS a planar polygon in 4326.
_M_PER_DEG_LAT = 111_320.0


def _buffered_polygon(point_geojson: dict, buffer_m: float) -> dict:
    """Return a metric-radius circular buffer around a geographic point.

    shapely's ``buffer`` works in the geometry's own units (degrees here), so
    we build the circle in a local metres frame and convert back, correcting
    longitude by the cosine of the point's latitude.
    """
    point = shape(point_geojson)
    lat_rad = math.radians(point.y)
    m_per_deg_lon = _M_PER_DEG_LAT * max(math.cos(lat_rad), 1e-6)
    # Unit circle in degrees, then scale x/y so the radius is buffer_m in metres.
    unit = point.buffer(1.0, quad_segs=16)
    coords = [
        (
            point.x + (x - point.x) * (buffer_m / m_per_deg_lon),
            point.y + (y - point.y) * (buffer_m / _M_PER_DEG_LAT),
        )
        for x, y in unit.exterior.coords
    ]
    from shapely.geometry import Polygon

    return mapping(Polygon(coords))


def _resolve_point(session, address: str) -> dict | None:
    refs = extract_location_references(address)
    civic = next((r for r in refs if r.kind == "civic_address"), None)
    if civic is None:
        return None
    resolved = resolve_location(session, civic, use_cache=True)
    if resolved is None:
        return None
    return resolved.geometry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    # ABS-350 reconciliation: the shared seed renamed the POCS dataset so its
    # name contains "pedestrian" (RetrievalService._overlay_role maps that to the
    # pedestrian_street overlay role). Default to the current name so this probe —
    # and ABS-349's schedule7-pocs-intersection.spec.ts, which calls it with no
    # --dataset-name — resolves the same dataset the seed ingests.
    parser.add_argument(
        "--dataset-name", default="e2e_pedestrian_oriented_commercial_streets"
    )
    parser.add_argument("--buffer-m", type=float, default=15.0)
    args = parser.parse_args()

    with session_scope() as session:
        # Under READ COMMITTED each statement gets a fresh snapshot, so without
        # this lock a concurrent seed_e2e_pocs run (another spec file's
        # beforeAll) can commit its drop-and-reingest between the id lookup
        # below and query_features — leaving us querying features for a dataset
        # id that no longer exists (intersects=false flake in full-suite runs).
        # Sharing the corpus key with the seeds serialises the whole probe
        # transaction against any reseed.
        if session.bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(:k)").bindparams(
                    k=CORPUS_ADVISORY_LOCK_KEY
                )
            )
        dataset = session.scalar(
            select(ExternalDataset).where(ExternalDataset.name == args.dataset_name)
        )
        if dataset is None:
            print(json.dumps({"error": f"dataset {args.dataset_name!r} not found"}))
            return 1

        point = _resolve_point(session, args.address)
        if point is None:
            print(
                json.dumps(
                    {
                        "address": args.address,
                        "resolved": False,
                        "intersects": False,
                        "segment_count": 0,
                        "streets": [],
                    }
                )
            )
            return 0

        buffered = _buffered_polygon(point, args.buffer_m)
        location = ResolvedLocation(kind="shape", geometry=buffered, source="e2e_probe")
        matches = query_features(session, dataset_id=dataset.id, location=location)
        streets = sorted(
            {
                (m.feature.canonical_attributes_json or {}).get("street_name")
                for m in matches
                if (m.feature.canonical_attributes_json or {}).get("street_name")
            }
        )
        print(
            json.dumps(
                {
                    "address": args.address,
                    "resolved": True,
                    "buffer_m": args.buffer_m,
                    "intersects": len(matches) > 0,
                    "segment_count": len(matches),
                    "streets": streets,
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
