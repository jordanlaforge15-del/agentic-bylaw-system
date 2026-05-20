from pathlib import Path

import pytest

from layer1.datasets.config import DatasetConfig, load_dataset_config


VALID_YAML = """
name: test_height
publisher: Halifax Regional Municipality
format: geojson
source_path: data/somefile.geojson
crs: EPSG:4326
links_to:
  document_match:
    municipality: Halifax Regional Municipality
    bylaw_name: Regional Centre Land Use By-law
  fragment_citation: Schedule 15
attributes:
  feature_key: GLOBALID
  canonical:
    max_height_m: { from: HEIGHT, type: float }
    display_label: { synthesize: "{HEIGHT}m precinct" }
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_loads_valid_config(tmp_path: Path):
    cfg = load_dataset_config(_write(tmp_path, VALID_YAML))
    assert isinstance(cfg, DatasetConfig)
    assert cfg.name == "test_height"
    assert cfg.attributes.feature_key == "GLOBALID"
    assert cfg.attributes.canonical["max_height_m"].from_field == "HEIGHT"
    assert cfg.attributes.canonical["display_label"].synthesize == "{HEIGHT}m precinct"


def test_rejects_unknown_canonical_field(tmp_path: Path):
    body = VALID_YAML.replace("max_height_m", "max_height_furlongs")
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_rejects_mapping_with_neither_from_nor_synthesize(tmp_path: Path):
    body = VALID_YAML.replace(
        "max_height_m: { from: HEIGHT, type: float }",
        "max_height_m: { type: float }",
    )
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_rejects_mapping_with_both_from_and_synthesize(tmp_path: Path):
    body = VALID_YAML.replace(
        "max_height_m: { from: HEIGHT, type: float }",
        'max_height_m: { from: HEIGHT, type: float, synthesize: "{HEIGHT}" }',
    )
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_rejects_mapping_without_source_path_or_url(tmp_path: Path):
    body = VALID_YAML.replace("source_path: data/somefile.geojson\n", "")
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_unsupported_type_rejected(tmp_path: Path):
    body = VALID_YAML.replace("type: float", "type: polygon")
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_rejects_lookup_without_lookup_field(tmp_path: Path):
    body = VALID_YAML.replace(
        "max_height_m: { from: HEIGHT, type: float }",
        "max_height_m: { from: HEIGHT, type: float, lookup: subs }",
    )
    body += "lookups:\n  subs:\n    1: { name: x }\n"
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_rejects_lookup_referencing_undefined_table(tmp_path: Path):
    body = VALID_YAML.replace(
        "max_height_m: { from: HEIGHT, type: float }",
        (
            "max_height_m: { from: HEIGHT, type: float, lookup: missing,"
            " lookup_field: name }"
        ),
    )
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_rejects_lookup_field_without_lookup(tmp_path: Path):
    body = VALID_YAML.replace(
        "max_height_m: { from: HEIGHT, type: float }",
        "max_height_m: { from: HEIGHT, type: float, lookup_field: name }",
    )
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_real_halifax_config_loads():
    cfg = load_dataset_config(
        Path("src/layer1/datasets/halifax_height_precincts.yaml")
    )
    assert cfg.name == "halifax_height_precincts"
    assert cfg.links_to.fragment_citation == "Schedule 15"
    # MAXBLDHGT (metres) and MAXBLDSTRY (storeys) are mutually exclusive in
    # the published Halifax data — both fields are optional in the canonical
    # schema so a feature with only one populated parses cleanly.
    assert "max_height_m" in cfg.attributes.canonical
    assert "max_height_storeys" in cfg.attributes.canonical
    assert cfg.attributes.canonical["max_height_m"].optional is True
    assert cfg.attributes.canonical["max_height_storeys"].optional is True
    assert "effective_date" in cfg.attributes.canonical
    assert cfg.attributes.canonical["effective_date"].optional is True
