"""ABS-473 — the fix does nothing to an already-ingested layer until this runs.

Retrieval reads the ``governing_bylaw_from`` declaration off the dataset's
persisted ``metadata_json``, and the resolved by-law name off each feature's
``canonical_attributes_json``. Both are written at ingest. Editing the YAML
therefore changes nothing for a corpus that is already loaded — and it fails
*silently*, because the fallback when either is missing is precisely the old
mis-attributing behaviour.

ABS-472 shipped a zoning-specific backfill for this. ABS-473 needs the same
for ``halifax_height_precincts``, which is why the script is now driven by
the configs: it walks every layer that declares per-feature attribution.
These tests pin the shape that made the height-precinct case different — its
pre-ABS-473 rows carry no ``bylaw_area_id`` to resolve from at all, only the
raw ``BYLAW_AREA`` the parser preserved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from layer1.datasets.config import load_dataset_config
from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from scripts.backfill_bylaw_area_attribution import (
    attributed_configs,
    backfill,
)

HEIGHTS_YAML = Path("src/layer1/datasets/halifax_height_precincts.yaml")
SHA_BYLAW = "Suburban Housing Accelerator Land Use By-law"

# Exactly what the pre-ABS-473 ingest wrote: the raw properties preserved in
# full, and a canonical projection that recorded the by-law area as a bare
# string nothing consulted.
_LEGACY_ROWS = [
    ("height-rc", 23, {"max_height_storeys": 20, "bylaw_area": "23"}),
    ("height-sha", 24, {"max_height_storeys": 5, "bylaw_area": "24"}),
]


def _seed(tmp_path: Path, *, links_to: dict | None = None) -> str:
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        dataset = ExternalDataset(
            name="halifax_height_precincts",
            publisher="Halifax Regional Municipality",
            format="geojson",
            content_hash="hash-heights",
            crs="EPSG:4326",
            feature_count=len(_LEGACY_ROWS),
            linked_fragment_citation="Schedule 15",
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            ingestion_timestamp=datetime.now(timezone.utc),
            metadata_json={
                "links_to": links_to
                if links_to is not None
                else {
                    # The pre-ABS-473 declaration: no governing_bylaw_from.
                    "document_match": {
                        "municipality": "HRM",
                        "bylaw_name": "Regional Centre Land Use By-Law",
                    },
                    "fragment_citation": "Schedule 15",
                }
            },
        )
        session.add(dataset)
        session.flush()
        for key, area, canonical in _LEGACY_ROWS:
            session.add(
                ExternalDatasetFeature(
                    external_dataset_id=dataset.id,
                    feature_key=key,
                    attributes_json={"GlobalID": key, "BYLAW_AREA": area},
                    canonical_attributes_json=dict(canonical),
                    geometry_geojson={"type": "Point", "coordinates": [-63.58, 44.65]},
                    geometry_bbox_json={
                        "minx": -63.58,
                        "miny": 44.65,
                        "maxx": -63.58,
                        "maxy": 44.65,
                    },
                    parse_status=ParseStatus.PARSED,
                    metadata_json={},
                )
            )
    return db_url


def _run(db_url: str, configs=None):
    with session_scope(db_url) as session:
        return backfill(session, configs or [load_dataset_config(HEIGHTS_YAML)])


def _attrs(db_url: str) -> dict[str, dict]:
    with session_scope(db_url) as session:
        return {
            f.feature_key: dict(f.canonical_attributes_json)
            for f in session.query(ExternalDatasetFeature).all()
        }


def test_backfill_names_the_bylaw_from_the_raw_source_property(tmp_path: Path):
    """The height-precinct rows have no ``bylaw_area_id`` to resolve from —
    the pre-ABS-473 config never mapped one. Resolving from the raw
    ``BYLAW_AREA`` the parser preserved is what makes the layer backfillable
    without re-pulling it from ArcGIS."""
    db_url = _seed(tmp_path)
    report = _run(db_url)

    assert report.features_updated == 2
    assert report.unknown_area_codes == {}
    attrs = _attrs(db_url)
    assert attrs["height-sha"]["bylaw_area_name"] == SHA_BYLAW
    assert attrs["height-sha"]["bylaw_area_code"] == "hrm:SHA"
    assert attrs["height-rc"]["bylaw_area_name"] == "Regional Centre Land Use By-law"


def test_backfill_refreshes_the_declaration_retrieval_reads(tmp_path: Path):
    """Names on the features are inert until the dataset says to consult
    them. Refreshing one without the other is the silent half-fix."""
    db_url = _seed(tmp_path)
    report = _run(db_url)

    assert report.links_to_refreshed == 1
    with session_scope(db_url) as session:
        dataset = session.query(ExternalDataset).one()
        governing = dataset.metadata_json["links_to"]["governing_bylaw_from"]
    assert governing["name_attribute"] == "bylaw_area_name"
    assert governing["code_attribute"] == "bylaw_area_code"


def test_backfill_is_idempotent(tmp_path: Path):
    db_url = _seed(tmp_path)
    _run(db_url)
    second = _run(db_url)

    assert second.features_updated == 0
    assert second.links_to_refreshed == 0
    assert second.features_skipped == 2


def test_an_unmapped_area_code_is_reported_not_silently_skipped(tmp_path: Path):
    """The dangerous case. A code with no lookup row resolves to no by-law
    name, so that feature keeps falling back to the dataset-level link — the
    mis-attribution itself. It must be loud."""
    db_url = _seed(tmp_path)
    with session_scope(db_url) as session:
        feature = (
            session.query(ExternalDatasetFeature)
            .filter_by(feature_key="height-sha")
            .one()
        )
        feature.attributes_json = {"GlobalID": "height-sha", "BYLAW_AREA": 99}

    report = _run(db_url)
    assert report.unknown_area_codes == {"halifax_height_precincts": {99}}
    assert "bylaw_area_name" not in _attrs(db_url)["height-sha"]


def test_string_typed_area_codes_still_resolve(tmp_path: Path):
    """A JSON round-trip can leave the publisher's integer as a string; the
    YAML keys it as an integer. A type mismatch here would resolve to no
    name, which fails the same silent way as a missing row."""
    db_url = _seed(tmp_path)
    with session_scope(db_url) as session:
        feature = (
            session.query(ExternalDatasetFeature)
            .filter_by(feature_key="height-sha")
            .one()
        )
        feature.attributes_json = {"GlobalID": "height-sha", "BYLAW_AREA": "24"}

    _run(db_url)
    assert _attrs(db_url)["height-sha"]["bylaw_area_name"] == SHA_BYLAW


def test_it_covers_every_attributed_layer_not_just_zoning(tmp_path: Path):
    """ABS-473's reason for generalising it. Both HRM layers that span by-law
    areas must be picked up off the configs — hardcoding the layer name is
    how the second one was missed for a release."""
    names = {config.name for config in attributed_configs()}
    assert {"halifax_zoning_boundaries", "halifax_height_precincts"} <= names


def test_a_layer_without_attribution_is_left_alone(tmp_path: Path):
    """A genuinely single-by-law layer has nothing to backfill, and the
    script must not invent a declaration for it."""
    assert all(
        config.links_to.governing_bylaw_from is not None
        for config in attributed_configs()
    )
    unattributed = {
        "halifax_heritage_districts",
        "halifax_far_precincts",
        "halifax_shadow_impact_areas",
    }
    assert not unattributed & {config.name for config in attributed_configs()}
