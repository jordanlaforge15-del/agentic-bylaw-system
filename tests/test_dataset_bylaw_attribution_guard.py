"""ABS-473 — the sibling-layer audit, kept as a test.

ABS-472 fixed one HRM-wide layer. The question this issue asked is whether
its five siblings had the same defect, because every one of them carries the
same dataset-level ``links_to.document_match``. Answering it meant counting,
per layer, how many features fall outside the by-law the layer is linked to.
Five were clean; ``halifax_height_precincts`` was not.

That audit was a one-off script. These tests are the part of it that can run
on every commit — the structural invariant behind the count, checked over
the real configs in ``src/layer1/datasets/`` rather than a fixture:

  a layer that knows which by-law area each feature sits in must cite from
  that knowledge, and the table it resolves through must be able to name
  every area the publisher can stamp.

A new overlay layer added tomorrow either carries no per-feature attribution
(and is genuinely single-by-law, like the four RC_-prefixed overlays) or it
declares ``governing_bylaw_from``. There is no third option in which it
silently inherits the wrong by-law, which is what both ABS-472 and ABS-473
were.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from layer1.datasets.config import (
    BYLAW_AREA_FIELDS,
    DatasetConfig,
    load_dataset_config,
)


CONFIG_DIR = Path("src/layer1/datasets")
SHARED_LOOKUPS = CONFIG_DIR / "lookups" / "hrm_bylaw_areas.yaml"


def _config_paths() -> list[Path]:
    # Non-recursive on purpose: lookups/ holds shared tables, not configs —
    # the same glob shape the coherence audit uses to find overlay configs.
    return sorted(CONFIG_DIR.glob("*.yaml"))


def _configs() -> list[tuple[str, DatasetConfig]]:
    return [(p.name, load_dataset_config(p)) for p in _config_paths()]


def test_every_real_dataset_config_loads():
    """The guards below are load-time validators, so this is what proves they
    are actually reached for the shipped configs and not just for fixtures."""
    configs = _configs()
    assert configs, "no dataset configs found — did the config dir move?"
    names = {cfg.name for _, cfg in configs}
    # The six layers the ABS-473 audit covered, so a rename cannot quietly
    # drop one out of every check in this module.
    assert {
        "halifax_zoning_boundaries",
        "halifax_height_precincts",
        "halifax_heritage_districts",
        "halifax_far_precincts",
        "halifax_bonus_zoning_districts",
        "halifax_shadow_impact_areas",
        "halifax_pedestrian_oriented_commercial_streets",
    } <= names


@pytest.mark.parametrize("filename,config", _configs(), ids=[p.stem for p in _config_paths()])
def test_a_layer_that_knows_its_bylaw_area_cites_from_it(
    filename: str, config: DatasetConfig
):
    """The invariant. A layer mapping a per-feature by-law area must resolve
    a *name* from it and cite through that name.

    ``halifax_height_precincts`` failed this before ABS-473: it mapped
    BYLAW_AREA into a bare ``bylaw_area`` string and cited all 1,822 features
    to Schedule 15 of the Regional Centre LUB, including the 48 that said
    they belonged to the Suburban Housing Accelerator LUB.
    """
    carried = set(config.attributes.canonical) & BYLAW_AREA_FIELDS
    if not carried or config.links_to is None:
        return
    governing = config.links_to.governing_bylaw_from
    assert governing is not None, (
        f"{filename} maps {sorted(carried)} but cites from the dataset-level "
        "by-law"
    )
    name_mapping = config.attributes.canonical[governing.name_attribute]
    # A raw area *code* cannot be matched against a document title, so the
    # cited attribute has to be the resolved human-readable name.
    assert name_mapping.lookup is not None, (
        f"{filename} cites from {governing.name_attribute!r}, which is not "
        "resolved through a lookup — a bare publisher code names no document"
    )
    assert name_mapping.lookup_field == "name"


@pytest.mark.parametrize("filename,config", _configs(), ids=[p.stem for p in _config_paths()])
def test_every_lookup_row_can_name_a_bylaw(filename: str, config: DatasetConfig):
    """A row missing its ``name`` resolves to nothing, and a feature that
    resolves to nothing falls back to the dataset-level link — the defect,
    reintroduced one table row at a time."""
    governing = config.links_to.governing_bylaw_from if config.links_to else None
    if governing is None:
        return
    table_name = config.attributes.canonical[governing.name_attribute].lookup
    table = config.lookups[table_name]
    assert table, f"{filename} resolves through an empty {table_name!r}"
    for key, row in table.items():
        assert isinstance(row.get("name"), str) and row["name"].strip(), (
            f"{filename}: {table_name}[{key}] has no by-law name"
        )
        if governing.code_attribute is not None:
            assert isinstance(row.get("code"), str) and row["code"].strip(), (
                f"{filename}: {table_name}[{key}] has no by-law code"
            )


def test_the_hrm_subtype_table_has_exactly_one_definition():
    """ABS-473's other half. The zoning layer and the height-precinct layer
    stamp the same publisher codes, and the table used to live inline in
    zoning's config. Two copies drift: a code added to one and not the other
    resolves to no name in the layer that missed it, which degrades that
    layer straight back to its dataset-level link."""
    inline = [
        path.name
        for path in _config_paths()
        if "bylaw_area_subtypes" in (yaml.safe_load(path.read_text()).get("lookups") or {})
    ]
    assert inline == [], (
        f"{inline} define bylaw_area_subtypes inline; include "
        f"{SHARED_LOOKUPS.name} through 'lookups_from' instead"
    )

    tables = {
        name: cfg.lookups["bylaw_area_subtypes"]
        for name, cfg in _configs()
        if "bylaw_area_subtypes" in cfg.lookups
    }
    # Both HRM layers that span by-law areas read it...
    assert {"halifax_zoning.yaml", "halifax_height_precincts.yaml"} <= set(tables)
    # ...and every reader sees the identical table, which is what the shared
    # file buys and what two inline copies could not guarantee.
    resolved = {name: sorted(table.items()) for name, table in tables.items()}
    assert len({str(rows) for rows in resolved.values()}) == 1, (
        f"bylaw_area_subtypes differs between {sorted(resolved)}"
    )


def test_the_suburban_housing_accelerator_resolves_in_both_layers():
    """The specific ground this issue is about. HRM code 24 appears in the
    height-precinct layer (48 features) and the zoning layer (47), so both
    must name the same by-law for it — otherwise the same parcel is refused
    on one surface and cited on the other."""
    named = {
        name: cfg.lookups["bylaw_area_subtypes"][24]["name"]
        for name, cfg in _configs()
        if "bylaw_area_subtypes" in cfg.lookups
    }
    assert set(named.values()) == {"Suburban Housing Accelerator Land Use By-law"}


def test_a_layer_scoped_to_one_bylaw_needs_no_attribution():
    """The audit's other finding, and why the guard is conditional rather
    than universal. Four of the overlay layers come from ``RC_``-prefixed
    ArcGIS services and are genuinely Regional Centre-scoped; heritage
    districts are not RC_-prefixed but all 20 fall inside it. None carry a
    per-feature by-law area, and their wholesale link is correct."""
    clean = {
        "halifax_heritage_districts",
        "halifax_far_precincts",
        "halifax_bonus_zoning_districts",
        "halifax_shadow_impact_areas",
        "halifax_pedestrian_oriented_commercial_streets",
    }
    for _, cfg in _configs():
        if cfg.name not in clean:
            continue
        assert not (set(cfg.attributes.canonical) & BYLAW_AREA_FIELDS)
        assert cfg.links_to is not None
        assert cfg.links_to.governing_bylaw_from is None


# ---------------------------------------------------------------------------
# The rule itself, exercised against the shape it exists to reject.
# ---------------------------------------------------------------------------


_PRE_ABS_473_HEIGHT_CONFIG = """
name: halifax_height_precincts
publisher: Halifax Regional Municipality
format: geojson
source_path: data/geo-datasets/heights.geojson
crs: EPSG:4326
links_to:
  document_match: { municipality: HRM, bylaw_name: Regional Centre Land Use By-Law }
  fragment_citation: Schedule 15
attributes:
  feature_key: GlobalID
  canonical:
    max_height_m: { from: MAXBLDHGT, type: float, optional: true }
    bylaw_area: { from: BYLAW_AREA, type: string, optional: true }
"""


def test_the_defective_config_is_rejected_at_load(tmp_path: Path):
    """Verbatim the config that shipped the defect: BYLAW_AREA mapped to a
    canonical string, and every feature cited to Schedule 15 regardless."""
    path = tmp_path / "heights.yaml"
    path.write_text(_PRE_ABS_473_HEIGHT_CONFIG)

    with pytest.raises(ValueError, match="governing_bylaw_from"):
        load_dataset_config(path)


def test_a_role_dataset_carrying_an_area_is_exempt(tmp_path: Path):
    """Role datasets (civic addresses, parcels) bind to no fragment, so they
    make no citation there is anything to misattribute."""
    path = tmp_path / "addresses.yaml"
    path.write_text(
        "name: test_addresses\n"
        "publisher: Test\n"
        "format: geojson\n"
        "source_path: x.geojson\n"
        "role: civic_address\n"
        "attributes:\n"
        "  feature_key: ID\n"
        "  canonical:\n"
        "    bylaw_area_id: { from: BYLAW_AREA, type: int, optional: true }\n"
    )

    assert load_dataset_config(path).links_to is None
