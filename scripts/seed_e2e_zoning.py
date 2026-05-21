"""Seed a synthetic Halifax-zoning feature into the e2e test database.

Covers the ABS-66 fix end-to-end through the real ingest pipeline: we
load the production ``halifax_zoning.yaml`` (with its ``lookups`` block
mapping BYLAW_ID → bylaw_area_code + bylaw_area_name), swap the live
ArcGIS ``source_url`` for a tiny local fixture GeoJSON, and run
``ingest_geo_dataset`` against the test database. The Playwright spec
then asserts that ``canonical_attributes_json`` on the resulting row
carries the resolved code/name pair — not just the raw integer that
used to drive the agent's hallucinated bylaw name.

The fixture's BYLAW_ID is 9 (Halifax Mainland), the exact case from
13 Rosemount Ave on the issue. We don't pretend the polygon is
geographically meaningful — it just has to exist with the right
attributes for the ingest path to exercise the lookup.

Idempotent — re-running drops the prior fixture dataset before
re-seeding. Called from ``web/e2e/functional/bylaw-area-name.spec.ts``
via execSync.

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \\
        .venv/bin/python scripts/seed_e2e_zoning.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer1.db.session import session_scope
from layer1.pipeline.ingest_dataset import ingest_geo_dataset


# Resolve from the script's own location so callers can run this from
# anywhere (the Playwright spec invokes via execSync from web/).
REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_ZONING_YAML = REPO_ROOT / "src" / "layer1" / "datasets" / "halifax_zoning.yaml"
FIXTURE_DATASET_NAME = "e2e_test_zoning"
FIXTURE_BYLAW_ID = 9  # Halifax Mainland — ABS-66 anchor
FIXTURE_ZONE = "R-1"
FIXTURE_GLOBALID = "e2e-zoning-abs66-mainland"


def _fixture_geojson() -> dict[str, Any]:
    """A single-polygon FeatureCollection with the BYLAW_ID under test."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-63.6303, 44.6451],
                        [-63.6302, 44.6451],
                        [-63.6302, 44.6452],
                        [-63.6303, 44.6452],
                        [-63.6303, 44.6451],
                    ]],
                },
                "properties": {
                    "OBJECTID": 1,
                    "GLOBALID": FIXTURE_GLOBALID,
                    "ZONE": FIXTURE_ZONE,
                    "DESCRIPTION": "Single Family (e2e fixture)",
                    "BYLAW_ID": FIXTURE_BYLAW_ID,
                    "SOURCE": "e2e_seed",
                    "SDATE": 1000339200000,
                    "FCODE": "CDZN",
                    "SACC": "IN",
                },
            }
        ],
    }


def _derive_config(work_dir: Path) -> Path:
    """Write a YAML next to a fixture GeoJSON that points at it via source_path.

    The lookups block (and every canonical mapping) is preserved verbatim
    from production so the ingest path under test is exactly what runs
    when the live ArcGIS pull happens — only the source swaps.
    """
    raw = yaml.safe_load(REAL_ZONING_YAML.read_text(encoding="utf-8"))
    raw["name"] = FIXTURE_DATASET_NAME
    raw["publisher"] = "e2e_seed"
    raw.pop("source_url", None)
    geojson_path = work_dir / "zoning.geojson"
    geojson_path.write_text(json.dumps(_fixture_geojson()), encoding="utf-8")
    raw["source_path"] = str(geojson_path)
    cfg_path = work_dir / "halifax_zoning_e2e.yaml"
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return cfg_path


def _drop_existing(db) -> None:
    """Idempotent re-seed: delete any prior e2e-zoning fixture rows."""
    existing = db.scalar(
        select(ExternalDataset).where(ExternalDataset.name == FIXTURE_DATASET_NAME)
    )
    if existing is None:
        return
    db.query(ExternalDatasetFeature).filter(
        ExternalDatasetFeature.external_dataset_id == existing.id
    ).delete(synchronize_session=False)
    db.delete(existing)
    db.flush()
    print(f"dropped prior e2e_test_zoning dataset id={existing.id}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        cfg_path = _derive_config(work_dir)
        with session_scope() as db:
            _drop_existing(db)
            result = ingest_geo_dataset(db, cfg_path)
            summary = {
                "dataset_id": result.dataset.id,
                "feature_count": result.dataset.feature_count,
                "globalid": FIXTURE_GLOBALID,
                "bylaw_id": FIXTURE_BYLAW_ID,
            }
            print(f"seed_e2e_zoning summary: {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
