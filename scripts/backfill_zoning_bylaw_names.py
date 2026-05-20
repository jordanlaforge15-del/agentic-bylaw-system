"""Backfill bylaw_area_code + bylaw_area_name onto existing zoning rows.

ABS-66 added publisher-prefixed code + human-readable name to the
canonical attributes emitted by ``halifax_zoning.yaml``. Features that
were ingested before that change carry only the raw ``bylaw_area_id``
integer, which lets the chat agent fall back to hallucinating a bylaw
name. Triggering a fresh ingest fixes new rows; this script fixes the
existing ones in place without re-pulling the live ArcGIS layer.

Idempotent. Re-running on already-backfilled rows is a no-op. Safe to
run inside a maintenance window or any time the ingest YAML's lookup
table changes (a re-run picks up edits without a full re-ingest).

Usage::

    DATABASE_URL=postgresql+psycopg://... \\
        .venv/bin/python scripts/backfill_zoning_bylaw_names.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from layer1.datasets.config import load_dataset_config
from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer1.db.session import session_scope


REAL_ZONING_YAML = Path(__file__).resolve().parent.parent / "src" / "layer1" / "datasets" / "halifax_zoning.yaml"
ZONING_DATASET_NAME = "halifax_zoning_boundaries"


def main() -> int:
    cfg = load_dataset_config(REAL_ZONING_YAML)
    table = cfg.lookups.get("bylaw_area_subtypes", {})
    if not table:
        print("error: halifax_zoning.yaml has no bylaw_area_subtypes lookup", file=sys.stderr)
        return 1

    updated = 0
    skipped = 0
    unknown_codes: set[int] = set()
    with session_scope() as db:
        datasets = db.scalars(
            select(ExternalDataset).where(ExternalDataset.name == ZONING_DATASET_NAME)
        ).all()
        for dataset in datasets:
            features = db.scalars(
                select(ExternalDatasetFeature).where(
                    ExternalDatasetFeature.external_dataset_id == dataset.id
                )
            ).all()
            for feature in features:
                attrs = dict(feature.canonical_attributes_json or {})
                bylaw_id = attrs.get("bylaw_area_id")
                if bylaw_id is None:
                    skipped += 1
                    continue
                row = table.get(bylaw_id) or table.get(str(bylaw_id))
                if row is None:
                    unknown_codes.add(bylaw_id)
                    skipped += 1
                    continue
                if (
                    attrs.get("bylaw_area_code") == row.get("code")
                    and attrs.get("bylaw_area_name") == row.get("name")
                ):
                    skipped += 1
                    continue
                attrs["bylaw_area_code"] = row["code"]
                attrs["bylaw_area_name"] = row["name"]
                feature.canonical_attributes_json = attrs
                # JSONB column with MutableDict: assignment is detected, but
                # flag_modified is the explicit belt-and-braces for callers
                # who re-bind a fresh dict (which we just did).
                flag_modified(feature, "canonical_attributes_json")
                updated += 1
        print(
            f"backfill_zoning_bylaw_names: updated={updated} skipped={skipped}"
            + (
                f" unknown_bylaw_ids={sorted(unknown_codes)}"
                if unknown_codes
                else ""
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
