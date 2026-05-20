"""Loader contract for the Phase-1 attribute taxonomy.

Two surfaces are checked:

* The shipped ``taxonomy.yaml`` parses cleanly and covers every
  attribute listed in the Linear issue (ABS-41). Catches drift between
  the doc and the file.
* The loader is strict — malformed YAML raises ``TaxonomyError`` at
  parse time, not at first attribute lookup. We exercise each
  validation branch with synthetic fixtures so the failure mode is
  pinned in tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from layer2.compliance.taxonomy import (
    DEFAULT_TAXONOMY_PATH,
    AttributeCategory,
    AttributeValueType,
    Taxonomy,
    TaxonomyError,
    load_taxonomy,
)

# IDs the Linear issue declares as required Phase-1 coverage. Kept in
# the test so a future taxonomy edit that drops one of these is caught.
REQUIRED_IDS = {
    # Geometric
    "building_height_m",
    "building_height_storeys",
    "gross_floor_area_m2",
    "floor_area_ratio",
    "lot_coverage_percent",
    "building_footprint_area_m2",
    "front_setback_m",
    "rear_setback_m",
    "side_setback_left_m",
    "side_setback_right_m",
    "height_to_eaves_m",
    "height_to_ridge_m",
    # Categorical
    "primary_use_class",
    "secondary_use_classes",
    "occupancy_type",
    "construction_type",
    "residential_unit_count",
    "residential_unit_mix",
    # Site / parking
    "parking_stalls_count",
    "parking_stalls_accessible_count",
    "bicycle_stalls_count",
    "loading_bays_count",
    # Contextual
    "zone_code",
    "height_precinct_code",
    "heritage_overlay",
    "flood_overlay",
    "transit_catchment",
    "lot_area_m2",
    "corner_lot_boolean",
    "arterial_frontage_boolean",
}


def test_default_taxonomy_loads_and_covers_required_ids() -> None:
    taxonomy = load_taxonomy(reload=True)
    assert isinstance(taxonomy, Taxonomy)
    assert taxonomy.version
    ids = set(taxonomy.ids)
    missing = REQUIRED_IDS - ids
    assert not missing, f"taxonomy missing required ids: {sorted(missing)}"
    # The issue caps the *target* size around ~30; allow some headroom
    # but flag an explosion that would suggest someone over-enumerated.
    assert 25 <= len(taxonomy.attributes) <= 60, (
        f"unexpected taxonomy size: {len(taxonomy.attributes)}"
    )


def test_every_attribute_has_required_fields() -> None:
    taxonomy = load_taxonomy(reload=True)
    for attr in taxonomy.attributes:
        assert isinstance(attr.id, str) and attr.id
        assert isinstance(attr.category, AttributeCategory)
        assert isinstance(attr.value_type, AttributeValueType)
        assert attr.description.strip(), f"{attr.id} missing description"


def test_geometric_attributes_carry_unit() -> None:
    taxonomy = load_taxonomy(reload=True)
    # The MCP tool callers rely on units being present for geometric
    # attributes so they don't have to guess from the id suffix.
    for attr in taxonomy.attributes:
        if attr.category in {
            AttributeCategory.GEOMETRIC_DIRECT,
            AttributeCategory.GEOMETRIC_DERIVED,
        }:
            if attr.value_type in {AttributeValueType.NUMBER, AttributeValueType.INTEGER}:
                assert attr.unit, f"{attr.id} (geometric) is missing 'unit'"


def test_by_id_and_keyword_lookup_helpers() -> None:
    taxonomy = load_taxonomy(reload=True)
    front_setback = taxonomy.by_id("front_setback_m")
    assert "front yard" in front_setback.bylaw_tag_keywords

    matches = taxonomy.find_by_keywords(
        "The minimum front yard setback shall not be less than 4.5 metres."
    )
    matched_ids = {a.id for a in matches}
    assert "front_setback_m" in matched_ids


def test_load_taxonomy_caches_by_path(tmp_path: Path) -> None:
    """A second load against the same path returns the cached instance.

    The enrichment pass and the evaluator both call ``load_taxonomy``
    on the hot path; we don't want to re-parse the file every time.
    """
    cached = load_taxonomy()
    again = load_taxonomy()
    assert cached is again
    fresh = load_taxonomy(reload=True)
    assert fresh is not cached


def _write_taxonomy(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "taxonomy.yaml"
    target.write_text(body, encoding="utf-8")
    return target


def test_loader_rejects_missing_version(tmp_path: Path) -> None:
    path = _write_taxonomy(
        tmp_path,
        """
attributes:
  - id: x
    category: site
    value_type: integer
    description: x
""",
    )
    with pytest.raises(TaxonomyError, match="version"):
        load_taxonomy(path, reload=True)


def test_loader_rejects_unknown_category(tmp_path: Path) -> None:
    path = _write_taxonomy(
        tmp_path,
        """
version: test
attributes:
  - id: x
    category: nonsense
    value_type: integer
    description: x
""",
    )
    with pytest.raises(TaxonomyError, match="invalid category"):
        load_taxonomy(path, reload=True)


def test_loader_rejects_duplicate_id(tmp_path: Path) -> None:
    path = _write_taxonomy(
        tmp_path,
        """
version: test
attributes:
  - id: x
    category: site
    value_type: integer
    description: x
  - id: x
    category: site
    value_type: integer
    description: x again
""",
    )
    with pytest.raises(TaxonomyError, match="duplicate"):
        load_taxonomy(path, reload=True)


def test_loader_rejects_bad_difficulty_level(tmp_path: Path) -> None:
    path = _write_taxonomy(
        tmp_path,
        """
version: test
attributes:
  - id: x
    category: site
    value_type: integer
    description: x
    extraction_difficulty:
      bim: trivial
""",
    )
    with pytest.raises(TaxonomyError, match="extraction_difficulty.bim"):
        load_taxonomy(path, reload=True)


def test_default_taxonomy_file_exists() -> None:
    # Sanity check — the loader caches by resolved path, so a typo in
    # the shipped path would only surface here.
    assert DEFAULT_TAXONOMY_PATH.exists()
