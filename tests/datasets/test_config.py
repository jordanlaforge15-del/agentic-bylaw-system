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


# --- ABS-472: per-feature governing by-law --------------------------------


GOVERNING_BLOCK = "  fragment_citation: Schedule 15\n"
GOVERNING_YAML = VALID_YAML.replace(
    GOVERNING_BLOCK,
    GOVERNING_BLOCK
    + "  governing_bylaw_from:\n"
    + "    name_attribute: bylaw_area_name\n"
    + "    code_attribute: bylaw_area_code\n",
).replace(
    "    display_label: { synthesize: \"{HEIGHT}m precinct\" }",
    "    display_label: { synthesize: \"{HEIGHT}m precinct\" }\n"
    "    bylaw_area_name: { from: BYLAW_ID, type: string }\n"
    "    bylaw_area_code: { from: BYLAW_ID, type: string }",
)


def test_loads_governing_bylaw_from(tmp_path: Path):
    cfg = load_dataset_config(_write(tmp_path, GOVERNING_YAML))
    governing = cfg.links_to.governing_bylaw_from
    assert governing is not None
    assert governing.name_attribute == "bylaw_area_name"
    assert governing.code_attribute == "bylaw_area_code"


def test_rejects_governing_bylaw_from_naming_an_unmapped_attribute(tmp_path: Path):
    """A typo here would degrade silently back to the dataset-level link —
    the exact mis-attribution ABS-472 exists to stop — so it fails at load."""
    body = GOVERNING_YAML.replace(
        "    name_attribute: bylaw_area_name", "    name_attribute: bylaw_area_nmae"
    )
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_rejects_governing_bylaw_code_attribute_that_is_not_mapped(tmp_path: Path):
    body = GOVERNING_YAML.replace(
        "    bylaw_area_code: { from: BYLAW_ID, type: string }", ""
    )
    with pytest.raises(Exception):
        load_dataset_config(_write(tmp_path, body))


def test_real_zoning_config_resolves_its_governing_bylaw_per_feature():
    """The HRM-wide zoning layer must not rely on its dataset-level link for
    attribution: 20 of its 22 by-law areas are governed by documents this
    corpus does not hold (ABS-472)."""
    cfg = load_dataset_config(Path("src/layer1/datasets/halifax_zoning.yaml"))
    governing = cfg.links_to.governing_bylaw_from
    assert governing is not None
    assert governing.name_attribute == "bylaw_area_name"
    assert governing.code_attribute == "bylaw_area_code"
    # Both are resolved from BYLAW_ID through the ABS-66 subtype lookup.
    assert cfg.attributes.canonical["bylaw_area_name"].lookup == "bylaw_area_subtypes"
    assert cfg.attributes.canonical["bylaw_area_code"].lookup == "bylaw_area_subtypes"


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
