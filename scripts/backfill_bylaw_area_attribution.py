"""Backfill per-feature by-law attribution onto already-ingested geo layers.

Retrieval cites each feature to the by-law the *feature* names, not the one
its layer is linked to (ABS-472). Two things have to be in the database for
that to happen, and both are written at ingest:

  * the dataset's ``metadata_json['links_to']`` must carry
    ``governing_bylaw_from``, which is what tells retrieval to consult the
    feature at all;
  * each feature's ``canonical_attributes_json`` must carry the resolved
    by-law name that declaration points at.

So a layer ingested before its YAML gained either one keeps mis-attributing
until it is refreshed — silently, because the fallback is the old behaviour.
This script does both in one pass, in place, without re-pulling the live
ArcGIS layers. Without the metadata refresh the backfilled names would sit on
the features unused.

Driven by the configs rather than hardcoded to one layer (ABS-473): it walks
every dataset YAML that declares ``governing_bylaw_from`` and resolves each
feature's attributes exactly the way ingest would, from the raw source
properties the parser preserved. ABS-472 needed this for
``halifax_zoning_boundaries``; ABS-473 needs the same for
``halifax_height_precincts``, whose 48 Suburban Housing Accelerator precincts
were being served as Schedule 15 of the Regional Centre LUB. Hardcoding it
twice is how the third layer gets missed.

Idempotent. Re-running on already-backfilled rows is a no-op. Safe to run
inside a maintenance window or any time an ingest YAML's lookup table or
``links_to`` block changes — a re-run picks up edits without a full re-ingest.

Usage::

    DATABASE_URL=postgresql+psycopg://... \\
        .venv/bin/python scripts/backfill_bylaw_area_attribution.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from layer1.datasets.config import DatasetConfig, load_dataset_config
from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer1.db.migration_fence import fence_or_abort
from layer1.db.session import session_scope

DATASET_CONFIG_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "layer1" / "datasets"
)


@dataclass
class BackfillReport:
    features_updated: int = 0
    features_skipped: int = 0
    links_to_refreshed: int = 0
    datasets_seen: int = 0
    # Raw source values with no row in the config's lookup table. These are
    # the dangerous ones: a feature that resolves to no by-law name falls
    # back to the dataset-level link, which is the mis-attribution itself.
    unknown_area_codes: dict[str, set[Any]] = field(default_factory=dict)

    def summary(self) -> str:
        line = (
            f"backfill_bylaw_area_attribution: datasets={self.datasets_seen} "
            f"updated={self.features_updated} skipped={self.features_skipped} "
            f"links_to_refreshed={self.links_to_refreshed}"
        )
        for name, codes in sorted(self.unknown_area_codes.items()):
            line += f" unknown[{name}]={sorted(map(str, codes))}"
        return line


def attributed_configs(config_dir: Path = DATASET_CONFIG_DIR) -> list[DatasetConfig]:
    """Every dataset config that declares a per-feature governing by-law."""
    configs = []
    for path in sorted(config_dir.glob("*.yaml")):
        config = load_dataset_config(path)
        if config.links_to is not None and config.links_to.governing_bylaw_from:
            configs.append(config)
    return configs


def _resolve_attributes(
    config: DatasetConfig, raw: dict[str, Any]
) -> tuple[dict[str, Any], set[Any]]:
    """The by-law attributes ingest would derive from one feature's raw props.

    Only the fields the ``governing_bylaw_from`` declaration depends on, plus
    any sibling resolved off the same source field — deliberately not a full
    re-run of the canonical mapping, which would need the parser's coercion
    rules and is what a re-ingest is for.
    """
    governing = config.links_to.governing_bylaw_from
    wanted = {governing.name_attribute}
    if governing.code_attribute:
        wanted.add(governing.code_attribute)

    resolved: dict[str, Any] = {}
    unknown: set[Any] = set()
    for canonical_name in sorted(wanted):
        mapping = config.attributes.canonical.get(canonical_name)
        if mapping is None or mapping.lookup is None or not mapping.from_field:
            continue
        source_value = raw.get(mapping.from_field)
        if source_value is None:
            continue
        table = config.lookups.get(mapping.lookup, {})
        # The publisher's codes are integers; a JSON round-trip can leave
        # them as strings, and YAML keys them as integers. Try both.
        row = table.get(source_value)
        if row is None:
            try:
                row = table.get(int(source_value))
            except (TypeError, ValueError):
                row = None
        if row is None:
            row = table.get(str(source_value))
        if row is None:
            unknown.add(source_value)
            continue
        value = row.get(mapping.lookup_field)
        if value is not None:
            resolved[canonical_name] = value
    return resolved, unknown


def backfill(session: Session, configs: list[DatasetConfig]) -> BackfillReport:
    report = BackfillReport()
    for config in configs:
        declared_links_to = config.links_to.model_dump()
        datasets = session.scalars(
            select(ExternalDataset).where(ExternalDataset.name == config.name)
        ).all()
        for dataset in datasets:
            report.datasets_seen += 1
            # Refresh the persisted declaration first: the per-feature names
            # below are only consulted when the dataset says to consult them.
            metadata = dict(dataset.metadata_json or {})
            if metadata.get("links_to") != declared_links_to:
                metadata["links_to"] = declared_links_to
                dataset.metadata_json = metadata
                flag_modified(dataset, "metadata_json")
                report.links_to_refreshed += 1

            features = session.scalars(
                select(ExternalDatasetFeature).where(
                    ExternalDatasetFeature.external_dataset_id == dataset.id
                )
            ).all()
            for feature in features:
                resolved, unknown = _resolve_attributes(
                    config, dict(feature.attributes_json or {})
                )
                if unknown:
                    report.unknown_area_codes.setdefault(config.name, set()).update(
                        unknown
                    )
                attrs = dict(feature.canonical_attributes_json or {})
                if not resolved or all(
                    attrs.get(k) == v for k, v in resolved.items()
                ):
                    report.features_skipped += 1
                    continue
                attrs.update(resolved)
                feature.canonical_attributes_json = attrs
                # JSONB column with MutableDict: assignment is detected, but
                # flag_modified is the explicit belt-and-braces for callers
                # who re-bind a fresh dict (which we just did).
                flag_modified(feature, "canonical_attributes_json")
                report.features_updated += 1
    return report


def main() -> int:
    configs = attributed_configs()
    if not configs:
        print(
            "error: no dataset config declares links_to.governing_bylaw_from",
            file=sys.stderr,
        )
        return 1
    # ABS-499: this script has no dry-run mode — it always writes.
    fence_or_abort("backfill-bylaw-area-attribution")

    with session_scope() as db:
        report = backfill(db, configs)
        print(report.summary())
    # A source value with no lookup row means that feature still resolves to
    # no by-law and still falls back to the dataset-level link. Loud, because
    # the whole failure mode here is a silent fallback.
    if report.unknown_area_codes:
        print(
            "warning: the codes above resolve to no by-law name; add them to "
            "the shared lookup table before trusting this layer's citations",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
