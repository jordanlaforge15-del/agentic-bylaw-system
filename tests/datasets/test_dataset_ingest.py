from pathlib import Path

import pytest

from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset


CONFIG_PATH = Path("src/layer1/datasets/halifax_height_precincts.yaml")
MINI_FIXTURE_CONFIG = """
name: mini_height_precincts
publisher: Test
format: geojson
source_path: tests/fixtures/geo/mini_height_precincts.geojson
crs: EPSG:4326
links_to:
  document_match:
    municipality: Halifax Regional Municipality
    bylaw_name: Regional Centre Land Use By-law
  fragment_citation: Schedule 15
attributes:
  feature_key: GlobalID
  canonical:
    max_height_m: { from: MAXBLDHGT, type: float, optional: true }
    max_height_storeys: { from: MAXBLDSTRY, type: int, optional: true }
    effective_date: { from: SDATE, type: rfc2822_date, optional: true }
    source_case: { from: SOURCE, type: string, optional: true }
  ignore: [OBJECTID, BHTMAX_ID, FCODE, BYLAW_AREA, SACC]
"""


def _setup_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    return db_url


def test_ingests_mini_fixture_end_to_end(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    cfg_path = tmp_path / "mini.yaml"
    cfg_path.write_text(MINI_FIXTURE_CONFIG)

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.dataset.feature_count == 3
        assert result.dataset.parse_status == ParseStatus.PARSED
        assert result.dataset.linked_fragment_citation == "Schedule 15"
        assert result.dataset.linked_fragment_id is None  # Phase B populates this
        assert result.dataset.crs == "EPSG:4326"
        dataset_id = result.dataset.id

    with session_scope(db_url) as session:
        dataset = session.get(ExternalDataset, dataset_id)
        assert dataset is not None
        assert dataset.name == "mini_height_precincts"
        assert dataset.content_hash and len(dataset.content_hash) == 64

        features = (
            session.query(ExternalDatasetFeature)
            .filter_by(external_dataset_id=dataset_id)
            .order_by(ExternalDatasetFeature.feature_key)
            .all()
        )
        assert len(features) == 3
        # Mini fixture has two metres-based features (25, 35) and one
        # storeys-based feature (9). MAXBLDHGT and MAXBLDSTRY are mutually
        # exclusive in the published Halifax data, so one feature has
        # max_height_storeys but no max_height_m.
        heights_m = sorted(
            f.canonical_attributes_json["max_height_m"]
            for f in features
            if f.canonical_attributes_json.get("max_height_m") is not None
        )
        storeys = sorted(
            f.canonical_attributes_json["max_height_storeys"]
            for f in features
            if f.canonical_attributes_json.get("max_height_storeys") is not None
        )
        assert heights_m == [25.0, 35.0]
        assert storeys == [9]

        first = features[0]
        # Raw passthrough preserves untouched source fields:
        assert first.attributes_json["SACC"] == "IN"
        # Canonical mapping kept its small declared set, plus optional fields populated:
        assert "effective_date" in first.canonical_attributes_json
        assert "source_case" in first.canonical_attributes_json
        # Geometry round-trips as a GeoJSON object:
        assert first.geometry_geojson["type"] == "Polygon"
        # Bbox is precomputed for spatial prefilter (Phase D):
        assert set(first.geometry_bbox_json) == {"minx", "miny", "maxx", "maxy"}


def test_ingests_real_halifax_dataset_when_present(tmp_path: Path):
    real = Path("data/geo-datasets/Height_Precincts_3210696484251958940.geojson")
    if not real.exists():
        pytest.skip("real Halifax dataset not present in this checkout")

    db_url = _setup_db(tmp_path)
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, CONFIG_PATH)
        # The current Halifax Maximum Building Heights export (1822 features)
        # parses clean — no invalid geometry, no required-field misses.
        # MAXBLDHGT and MAXBLDSTRY are both optional canonical fields since
        # they're mutually exclusive per feature; missing one is expected,
        # not a warning.
        assert result.dataset.feature_count == 1822
        assert result.dataset.parse_status == ParseStatus.PARSED
        assert result.feature_warnings == 0


def test_unique_dataset_name_constraint(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    cfg_path = tmp_path / "mini.yaml"
    cfg_path.write_text(MINI_FIXTURE_CONFIG)

    with session_scope(db_url) as session:
        ingest_geo_dataset(session, cfg_path)

    with pytest.raises(Exception):
        with session_scope(db_url) as session:
            ingest_geo_dataset(session, cfg_path)


def test_missing_source_file_raises(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    bad_config = MINI_FIXTURE_CONFIG.replace(
        "tests/fixtures/geo/mini_height_precincts.geojson",
        "tests/fixtures/geo/does_not_exist.geojson",
    )
    cfg_path = tmp_path / "missing.yaml"
    cfg_path.write_text(bad_config)
    with session_scope(db_url) as session:
        with pytest.raises(FileNotFoundError):
            ingest_geo_dataset(session, cfg_path)
