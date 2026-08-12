import json
from pathlib import Path

import pytest

from layer1.datasets.config import load_dataset_config
from layer1.models.enums import ParseStatus
from layer1.parsers.geo_dataset import parse_geojson


CONFIG_PATH = Path("src/layer1/datasets/halifax_height_precincts.yaml")
MINI_FIXTURE = Path("tests/fixtures/geo/mini_height_precincts.geojson")


def test_parses_mini_fixture_with_all_canonical_fields():
    cfg = load_dataset_config(CONFIG_PATH)
    result = parse_geojson(MINI_FIXTURE, cfg)

    assert result.feature_count == 3
    assert result.declared_crs == "EPSG:4326"
    assert result.content_hash and len(result.content_hash) == 64
    assert result.warnings == []

    first = result.features[0]
    assert first.feature_key == "11111111-1111-1111-1111-111111111111"
    assert first.canonical_attributes == {
        "max_height_m": 25.0,
        "effective_date": "2018-11-03",
        "source_case": "Case H00045",
        # ABS-473: BYLAW_AREA used to land as a bare "23" string nothing read.
        # It now resolves through the shared HRM subtype table into the by-law
        # that governs this precinct, which is what retrieval cites from.
        "bylaw_area_id": 23,
        "bylaw_area_code": "hrm:RC",
        "bylaw_area_name": "Regional Centre Land Use By-law",
    }
    # Feature 1 is metres-typed; max_height_storeys must NOT be set.
    assert "max_height_storeys" not in first.canonical_attributes
    assert first.parse_status == ParseStatus.PARSED
    assert first.attributes["MAXBLDHGT"] == 25
    assert first.attributes["MAXBLDSTRY"] is None  # mutual exclusion preserved in raw passthrough
    assert first.attributes["SACC"] == "IN"  # raw passthrough preserves ignored fields
    assert first.geometry["type"] == "Polygon"
    assert set(first.bbox) == {"minx", "miny", "maxx", "maxy"}
    assert first.bbox["minx"] < first.bbox["maxx"]
    assert first.bbox["miny"] < first.bbox["maxy"]

    # Feature 3 is storeys-typed: max_height_storeys set, max_height_m absent.
    third = result.features[2]
    assert third.canonical_attributes.get("max_height_storeys") == 9
    assert "max_height_m" not in third.canonical_attributes
    # ...and it sits in a different by-law area than the other two. The
    # published layer is mixed the same way (48 of 1,822 precincts are
    # Suburban Housing Accelerator LUB), so the miniature is too — a fixture
    # that were uniformly Regional Centre could not fail the way ABS-473 did.
    assert third.canonical_attributes["bylaw_area_id"] == 24
    assert third.canonical_attributes["bylaw_area_code"] == "hrm:SHA"
    assert (
        third.canonical_attributes["bylaw_area_name"]
        == "Suburban Housing Accelerator Land Use By-law"
    )


def test_optional_field_missing_does_not_warn(tmp_path: Path):
    cfg = load_dataset_config(CONFIG_PATH)
    payload = json.loads(MINI_FIXTURE.read_text())
    payload["features"][0]["properties"]["SOURCE"] = None  # optional field nulled
    p = tmp_path / "no-source.geojson"
    p.write_text(json.dumps(payload))

    result = parse_geojson(p, cfg)
    assert result.warnings == []
    assert "source_case" not in result.features[0].canonical_attributes
    assert result.features[0].parse_status == ParseStatus.PARSED


def test_required_field_missing_marks_uncertain(tmp_path: Path):
    """With current Halifax YAML both height fields are optional (mutually
    exclusive in source). Use a synthetic config that *requires* MAXBLDHGT
    to verify the UNCERTAIN-on-missing-required path still works.
    """
    from layer1.datasets.config import load_dataset_config as load_cfg
    required_yaml = (tmp_path / "required.yaml")
    required_yaml.write_text(
        "name: required_height\n"
        "publisher: Test\n"
        "format: geojson\n"
        "source_path: tests/fixtures/geo/mini_height_precincts.geojson\n"
        "crs: EPSG:4326\n"
        "links_to:\n"
        "  document_match: { municipality: HRM, bylaw_name: Test }\n"
        "  fragment_citation: Schedule 15\n"
        "attributes:\n"
        "  feature_key: GlobalID\n"
        "  canonical:\n"
        "    max_height_m: { from: MAXBLDHGT, type: float }\n"
    )
    cfg = load_cfg(required_yaml)
    payload = json.loads(MINI_FIXTURE.read_text())
    payload["features"][0]["properties"]["MAXBLDHGT"] = None
    p = tmp_path / "no-height.geojson"
    p.write_text(json.dumps(payload))

    result = parse_geojson(p, cfg)
    assert result.features[0].parse_status == ParseStatus.UNCERTAIN
    assert "max_height_m" not in result.features[0].canonical_attributes
    assert any("max_height_m" in w or "MAXBLDHGT" in w for w in result.warnings)


def test_missing_feature_key_drops_feature(tmp_path: Path):
    cfg = load_dataset_config(CONFIG_PATH)
    payload = json.loads(MINI_FIXTURE.read_text())
    del payload["features"][0]["properties"]["GlobalID"]
    p = tmp_path / "no-key.geojson"
    p.write_text(json.dumps(payload))

    result = parse_geojson(p, cfg)
    assert result.feature_count == 2
    assert any("GlobalID" in w or "feature_key" in w for w in result.warnings)


def test_duplicate_feature_key_keeps_first(tmp_path: Path):
    cfg = load_dataset_config(CONFIG_PATH)
    payload = json.loads(MINI_FIXTURE.read_text())
    payload["features"][1]["properties"]["GlobalID"] = payload["features"][0]["properties"]["GlobalID"]
    p = tmp_path / "dup-key.geojson"
    p.write_text(json.dumps(payload))

    result = parse_geojson(p, cfg)
    assert result.feature_count == 2
    assert any("duplicate" in w.lower() for w in result.warnings)


def test_crs_mismatch_raises(tmp_path: Path):
    cfg = load_dataset_config(CONFIG_PATH)
    payload = json.loads(MINI_FIXTURE.read_text())
    payload["crs"] = {"type": "name", "properties": {"name": "EPSG:2961"}}
    p = tmp_path / "wrong-crs.geojson"
    p.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="CRS mismatch"):
        parse_geojson(p, cfg)


def test_crs_absent_assumes_default(tmp_path: Path):
    cfg = load_dataset_config(CONFIG_PATH)
    payload = json.loads(MINI_FIXTURE.read_text())
    payload.pop("crs", None)
    p = tmp_path / "no-crs.geojson"
    p.write_text(json.dumps(payload))
    result = parse_geojson(p, cfg)
    assert result.declared_crs == "EPSG:4326"


def test_non_featurecollection_rejected(tmp_path: Path):
    cfg = load_dataset_config(CONFIG_PATH)
    p = tmp_path / "not-fc.geojson"
    p.write_text(json.dumps({"type": "Feature", "geometry": None, "properties": {}}))
    with pytest.raises(ValueError, match="FeatureCollection"):
        parse_geojson(p, cfg)


def test_invalid_geometry_is_repaired_not_dropped(tmp_path: Path):
    cfg = load_dataset_config(CONFIG_PATH)
    payload = json.loads(MINI_FIXTURE.read_text())
    # A self-intersecting bowtie polygon — shapely.make_valid resolves to a MultiPolygon.
    payload["features"][0]["geometry"]["coordinates"] = [[
        [-63.60, 44.64], [-63.58, 44.66], [-63.60, 44.66],
        [-63.58, 44.64], [-63.60, 44.64],
    ]]
    p = tmp_path / "self-intersect.geojson"
    p.write_text(json.dumps(payload))

    result = parse_geojson(p, cfg)
    assert result.feature_count == 3  # nothing dropped
    repaired = result.features[0]
    assert repaired.parse_status == ParseStatus.UNCERTAIN
    assert repaired.metadata.get("geometry_repaired") is True
    assert any("repaired" in w for w in result.warnings)


def _real_halifax_config():
    """The real config, retyped for the checked-in static export.

    ABS-473 pointed the config at the live FeatureServer, which publishes
    SDATE as epoch milliseconds. The export in ``data/geo-datasets/`` is the
    Hub's *static* GeoJSON of the same layer and encodes SDATE as RFC 2822,
    so parsing it with the config verbatim would warn on every feature. The
    one-field retype keeps this test about the mapping the config declares —
    field names, mutual exclusion, by-law attribution — rather than about
    which of the publisher's two encodings the snapshot happens to use.
    """
    cfg = load_dataset_config(CONFIG_PATH)
    cfg.attributes.canonical["effective_date"].type = "rfc2822_date"
    return cfg


def test_parses_real_halifax_dataset_when_present():
    """Sanity-check against the real published Maximum Building Heights
    layer when it's present in the checkout. Bounds are loose because the
    open-data publication may amend over time."""
    real = Path("data/geo-datasets/Maximum_Building_Heights_6478354320888850499.geojson")
    if not real.exists():
        pytest.skip("real Halifax dataset not present in this checkout")
    cfg = _real_halifax_config()
    result = parse_geojson(real, cfg)
    # Current export profiled at 1822 features; allow drift either way.
    assert result.feature_count > 1000
    heights = [f.canonical_attributes.get("max_height_m") for f in result.features]
    storeys = [f.canonical_attributes.get("max_height_storeys") for f in result.features]
    has_height = sum(1 for h in heights if h is not None)
    has_storeys = sum(1 for s in storeys if s is not None)
    # Mutual exclusion: no feature has both populated.
    both = sum(
        1 for h, s in zip(heights, storeys) if h is not None and s is not None
    )
    assert both == 0
    assert has_height > 0
    assert has_storeys > 0


def test_real_halifax_dataset_spans_two_bylaws_and_resolves_both():
    """ABS-473, measured on the real layer rather than a miniature.

    The published Maximum Building Heights layer is not wholly Regional
    Centre: 48 of its 1,822 precincts carry BYLAW_AREA 24, the Suburban
    Housing Accelerator LUB. Serving those as Schedule 15 of the Regional
    Centre LUB is the defect. What makes it fixable is that every feature
    resolves to a named by-law — a precinct whose area code fell outside the
    subtype table would resolve to nothing, and retrieval would quietly fall
    back to the dataset-level Regional Centre link for it.
    """
    real = Path("data/geo-datasets/Maximum_Building_Heights_6478354320888850499.geojson")
    if not real.exists():
        pytest.skip("real Halifax dataset not present in this checkout")
    result = parse_geojson(real, _real_halifax_config())

    names = [f.canonical_attributes.get("bylaw_area_name") for f in result.features]
    assert None not in names, "every precinct must resolve to a named by-law"
    assert set(names) == {
        "Regional Centre Land Use By-law",
        "Suburban Housing Accelerator Land Use By-law",
    }
    sha = sum(1 for n in names if n.startswith("Suburban"))
    # Loose bound: HRM amends the layer. The point is that the non-Regional
    # Centre slice is real and non-empty, not that it is exactly 48.
    assert 0 < sha < len(names)
