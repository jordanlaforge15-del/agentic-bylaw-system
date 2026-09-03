"""Rebuild the Schedule 7 POCS corridor geometry from HRM street centrelines (ABS-435).

Why
---
``data/geo-datasets/pedestrian_oriented_commercial_streets_schedule7.geojson``
was hand-digitized off Schedule 7 / Map 19 (ABS-349) because HRM publishes the
schedule cartographically and never as a machine-readable street list. Tracing
a raster map by eye is only as good as the tracer: the committed lines drift up
to ~200 m from the true roadway. Near civic 6321 Quinpool Road the drift is
130 m, so the ``abuts`` predicate reported ``abuts_pedestrian_street=false`` for
an address that sits squarely on the designated corridor — and that flag picks
between s.38(2) (ground-floor office prohibited) and s.69(d) (permitted).

What this does
--------------
HRM *does* publish surveyor-grade street centrelines
(``halifax_street_centerlines``, role ``road_centerlines``). Schedule 7 does not
invent new geometry — every designated corridor is an ordinary HRM street over a
stated extent — so the corridors can be *derived* rather than traced: take the
authoritative centreline segments for the named street and keep the run between
the two cross-streets the schedule's extent names. The result is one
MultiLineString per corridor, keyed by the same stable ``SEGMENT_ID`` the
hand-digitized file used, so re-ingest stays idempotent for consumers that key
off ``feature_key``.

Extent selection
----------------
For each corridor we resolve two *anchors* — the points where the bounding
cross-streets meet the corridor — then keep every centreline segment whose
midpoint projects strictly between them on the anchor-to-anchor axis. Straight
projection (rather than clipping to a bbox) keeps real geometry intact: no
truncated jogs, no synthetic vertices. Where a cross-street touches the corridor
more than once (Dutch Village Road meets Joseph Howe Drive at both ends) the
anchor is the intersection *farthest* from the opposite anchor, so the extent
spans the corridor instead of collapsing to a point.

Usage
-----
    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1 \\
        .venv/bin/python scripts/rebuild_pocs_from_centerlines.py

Requires PostGIS and an ingested ``halifax_street_centerlines`` (the dev
database). This is a *generator* run by hand when the centreline layer is
refreshed, not part of the ingest pipeline — its output is the committed
GeoJSON, which is what production and the test suite read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from layer1.db.base import ExternalDataset
from layer1.db.session import session_scope


CENTERLINES_DATASET = "halifax_street_centerlines"
DEFAULT_OUT = Path("data/geo-datasets/pedestrian_oriented_commercial_streets_schedule7.geojson")

# The six corridors Schedule 7 designates, each expressed as an HRM street name
# (``full_name`` matches the centreline layer's FULL_NAME) plus the two
# cross-streets that bound the designated extent.
#
# ``extent`` is the human-readable label carried into the GeoJSON properties and
# is what the schedule states; ``from_street``/``to_street`` are the centreline
# FULL_NAMEs used to resolve it. The two differ in two places, both recorded
# here so the mapping is auditable rather than silently fudged:
#   * Cornwallis Street was renamed Nora Bernard Street (HRM, 2024); the
#     schedule text predates the rename.
#   * Chebucto Road does not physically reach Dutch Village Road — it continues
#     west as Bayers Road, which is the street that actually meets the Dutch
#     Village corridor at its south end.
CORRIDORS: tuple[dict[str, str], ...] = (
    {
        "segment_id": "S7-QUINPOOL-01",
        "street": "Quinpool Road",
        "full_name": "QUINPOOL RD",
        "extent": "Armdale Rotary to Robie Street",
        "from_street": "ARMDALE ROT",
        "to_street": "ROBIE ST",
    },
    {
        "segment_id": "S7-SPRINGGARDEN-01",
        "street": "Spring Garden Road",
        "full_name": "SPRING GARDEN RD",
        "extent": "Robie Street to Barrington Street",
        "from_street": "ROBIE ST",
        "to_street": "BARRINGTON ST",
    },
    {
        "segment_id": "S7-GOTTINGEN-01",
        "street": "Gottingen Street",
        "full_name": "GOTTINGEN ST",
        "extent": "Cogswell Street to North Street",
        "from_street": "COGSWELL ST",
        "to_street": "NORTH ST",
    },
    {
        "segment_id": "S7-AGRICOLA-01",
        "street": "Agricola Street",
        "full_name": "AGRICOLA ST",
        "extent": "North Street to Young Street",
        "from_street": "NORTH ST",
        "to_street": "YOUNG ST",
    },
    {
        "segment_id": "S7-BARRINGTON-01",
        "street": "Barrington Street",
        "full_name": "BARRINGTON ST",
        "extent": "Spring Garden Road to Cornwallis Street",
        "from_street": "SPRING GARDEN RD",
        # Cornwallis Street was renamed Nora Bernard Street in 2024.
        "to_street": "NORA BERNARD ST",
    },
    {
        "segment_id": "S7-DUTCHVILLAGE-01",
        "street": "Dutch Village Road",
        "full_name": "DUTCH VILLAGE RD",
        "extent": "Joseph Howe Drive to Chebucto Road",
        # Chebucto Road continues west as Bayers Road, which is the street that
        # actually meets Dutch Village Road at its south end.
        "from_street": "BAYERS RD",
        "to_street": "JOSEPH HOWE DR",
    },
)

# The centreline layer covers all of HRM, so a bare FULL_NAME match would pull
# in same-named streets in Dartmouth, Sackville, etc. Every Schedule 7 corridor
# is in the Regional Centre, so restrict to the Halifax geographic service area.
COMMUNITY = "HALIFAX"

SOURCE_NOTE = (
    "RCLUB Schedule 7 (Map 19) extent, geometry derived from "
    "halifax_street_centerlines (HRM StreetNetwork) — ABS-435"
)

# Coordinate output precision. 1e-6 degrees is ~0.11 m at Halifax's latitude —
# well below the centreline layer's own accuracy, and it keeps the committed
# GeoJSON an order of magnitude smaller than full float repr.
COORD_PRECISION = 6


_SEGMENTS_SQL = text(
    """
    WITH corridor AS (
      SELECT ST_Union(edf.geometry) AS g
      FROM external_dataset_feature edf
      WHERE edf.external_dataset_id = :ds_id
        AND edf.geometry IS NOT NULL
        AND edf.attributes_json->>'FULL_NAME' = :full_name
        AND (edf.attributes_json->>'GSA_LEFT' = :community
             OR edf.attributes_json->>'GSA_RIGHT' = :community)
    ),
    from_street AS (
      SELECT ST_Union(edf.geometry) AS g
      FROM external_dataset_feature edf
      WHERE edf.external_dataset_id = :ds_id
        AND edf.geometry IS NOT NULL
        AND edf.attributes_json->>'FULL_NAME' = :from_street
    ),
    to_street AS (
      SELECT ST_Union(edf.geometry) AS g
      FROM external_dataset_feature edf
      WHERE edf.external_dataset_id = :ds_id
        AND edf.geometry IS NOT NULL
        AND edf.attributes_json->>'FULL_NAME' = :to_street
    ),
    anchor_from AS (
      SELECT ST_ClosestPoint(corridor.g, from_street.g) AS p,
             ST_Distance(corridor.g::geography, from_street.g::geography) AS gap_m
      FROM corridor, from_street
    ),
    -- Where the "to" cross-street meets the corridor more than once, take the
    -- touch point farthest from the "from" anchor: that is the one that spans
    -- the corridor rather than collapsing the extent onto a single block.
    to_touches AS (
      SELECT (ST_DumpPoints(ST_ClosestPoint(corridor.g, to_street.g))).geom AS p
      FROM corridor, to_street
      UNION ALL
      SELECT (ST_DumpPoints(ST_Intersection(corridor.g, to_street.g))).geom AS p
      FROM corridor, to_street
    ),
    anchor_to AS (
      SELECT t.p,
             ST_Distance(t.p::geography, to_street.g::geography) AS gap_m
      FROM to_touches t, anchor_from af, to_street
      ORDER BY ST_Distance(t.p::geography, af.p::geography) DESC
      LIMIT 1
    ),
    axis AS (
      SELECT ST_MakeLine(af.p, at.p) AS g, af.gap_m AS from_gap_m, at.gap_m AS to_gap_m
      FROM anchor_from af, anchor_to at
    )
    SELECT
      edf.feature_key,
      ST_AsGeoJSON(ST_SnapToGrid(edf.geometry, :grid)) AS geojson,
      ST_Length(edf.geometry::geography) AS length_m,
      axis.from_gap_m,
      axis.to_gap_m
    FROM external_dataset_feature edf
    CROSS JOIN axis
    WHERE edf.external_dataset_id = :ds_id
      AND edf.geometry IS NOT NULL
      AND edf.attributes_json->>'FULL_NAME' = :full_name
      AND (edf.attributes_json->>'GSA_LEFT' = :community
           OR edf.attributes_json->>'GSA_RIGHT' = :community)
      AND ST_LineLocatePoint(
            axis.g, ST_LineInterpolatePoint(ST_LineMerge(edf.geometry), 0.5)
          ) > 0.0
      AND ST_LineLocatePoint(
            axis.g, ST_LineInterpolatePoint(ST_LineMerge(edf.geometry), 0.5)
          ) < 1.0
    ORDER BY edf.feature_key
    """
)


def _round_coords(node: Any) -> Any:
    """Round every coordinate in a nested GeoJSON coordinate array."""
    if isinstance(node, (int, float)):
        return round(float(node), COORD_PRECISION)
    return [_round_coords(child) for child in node]


def _corridor_feature(session, *, dataset_id: int, corridor: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = session.execute(
        _SEGMENTS_SQL,
        {
            "ds_id": dataset_id,
            "full_name": corridor["full_name"],
            "from_street": corridor["from_street"],
            "to_street": corridor["to_street"],
            "community": COMMUNITY,
            "grid": 10.0 ** -COORD_PRECISION,
        },
    ).all()
    if not rows:
        raise SystemExit(
            f"no centreline segments matched corridor {corridor['segment_id']} "
            f"(FULL_NAME={corridor['full_name']!r}, "
            f"{corridor['from_street']!r} -> {corridor['to_street']!r})"
        )

    parts: list[list[list[float]]] = []
    for row in rows:
        geom = json.loads(row.geojson)
        if geom["type"] == "LineString":
            parts.append(_round_coords(geom["coordinates"]))
        elif geom["type"] == "MultiLineString":
            parts.extend(_round_coords(line) for line in geom["coordinates"])
        else:  # pragma: no cover — the centreline layer is polyline-only
            raise SystemExit(
                f"unexpected centreline geometry {geom['type']!r} in "
                f"{corridor['segment_id']}"
            )

    feature = {
        "type": "Feature",
        "properties": {
            "SEGMENT_ID": corridor["segment_id"],
            "STREET": corridor["street"],
            "SCHEDULE": "Schedule 7",
            "EXTENT": corridor["extent"],
            "SOURCE": SOURCE_NOTE,
        },
        "geometry": {"type": "MultiLineString", "coordinates": parts},
    }
    stats = {
        "segment_id": corridor["segment_id"],
        "street": corridor["street"],
        "centreline_segments": len(rows),
        "parts": len(parts),
        "length_m": round(sum(float(r.length_m) for r in rows), 1),
        "from_anchor_gap_m": round(float(rows[0].from_gap_m), 1),
        "to_anchor_gap_m": round(float(rows[0].to_gap_m), 1),
    }
    return feature, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output GeoJSON path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the per-corridor summary without writing the GeoJSON",
    )
    args = parser.parse_args()

    with session_scope() as session:
        if session.bind.dialect.name != "postgresql":
            print("this generator requires PostGIS (set DATABASE_URL to the dev DB)")
            return 1
        dataset = session.scalar(
            select(ExternalDataset).where(ExternalDataset.name == CENTERLINES_DATASET)
        )
        if dataset is None:
            print(
                f"dataset {CENTERLINES_DATASET!r} is not ingested; run the layer1 "
                "dataset ingest first"
            )
            return 1

        features: list[dict[str, Any]] = []
        stats: list[dict[str, Any]] = []
        for corridor in CORRIDORS:
            feature, stat = _corridor_feature(
                session, dataset_id=dataset.id, corridor=corridor
            )
            features.append(feature)
            stats.append(stat)

    collection = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": features,
    }

    for stat in stats:
        # A non-zero anchor gap means the named cross-street never touches the
        # corridor, so the extent was resolved by nearest approach — worth
        # seeing rather than silently accepting.
        print(json.dumps(stat))

    if args.dry_run:
        return 0

    args.out.write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
