from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from layer1.datasets.canonical import CANONICAL_FIELDS, SUPPORTED_TYPES


class CanonicalFieldMapping(BaseModel):
    """How a single canonical field is sourced from a dataset's raw properties.

    Exactly one of ``from_field`` or ``synthesize`` must be set.

    When ``lookup`` is set, the raw value pulled from ``from_field`` is used
    as a key into the named lookup table on the parent ``DatasetConfig``
    (``DatasetConfig.lookups[lookup_name]``). ``lookup_field`` then picks
    which column of the lookup row becomes the canonical value. This lets a
    single integer source field (e.g. HRM's ``BYLAW_ID``) drive several
    canonical fields — code, name, and any other denormalised attributes —
    without re-fetching the source data.
    """

    from_field: str | None = Field(default=None, alias="from")
    type: str | None = None
    optional: bool = False
    null_when: list[Any] = Field(default_factory=list)
    synthesize: str | None = None
    lookup: str | None = None
    lookup_field: str | None = None

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def _validate(self) -> "CanonicalFieldMapping":
        if self.from_field and self.synthesize:
            raise ValueError("a canonical field mapping cannot set both 'from' and 'synthesize'")
        if not self.from_field and not self.synthesize:
            raise ValueError("a canonical field mapping must set either 'from' or 'synthesize'")
        if self.from_field and not self.type:
            raise ValueError(f"canonical mapping for '{self.from_field}' requires 'type'")
        if self.type and self.type not in SUPPORTED_TYPES:
            raise ValueError(f"unsupported canonical type '{self.type}'")
        if self.lookup is not None:
            if self.synthesize is not None:
                raise ValueError("'lookup' cannot be combined with 'synthesize'")
            if not self.from_field:
                raise ValueError("'lookup' requires 'from' to specify the key field")
            if not self.lookup_field:
                raise ValueError("'lookup' requires 'lookup_field' to pick a column from the row")
        elif self.lookup_field is not None:
            raise ValueError("'lookup_field' is only valid alongside 'lookup'")
        return self


class AttributesConfig(BaseModel):
    feature_key: str
    canonical: dict[str, CanonicalFieldMapping] = Field(default_factory=dict)
    ignore: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_canonical_keys(self) -> "AttributesConfig":
        unknown = set(self.canonical) - set(CANONICAL_FIELDS)
        if unknown:
            raise ValueError(
                f"unknown canonical field(s) {sorted(unknown)} — add to "
                "layer1.datasets.canonical.CANONICAL_FIELDS first"
            )
        return self


class DocumentMatch(BaseModel):
    municipality: str
    bylaw_name: str

    model_config = {"extra": "forbid"}


# Canonical fields that record which by-law area a single feature sits in.
# Mapping any of them means the layer knows, per feature, that it may span
# more than one by-law — see
# ``DatasetConfig._require_governing_bylaw_when_features_carry_one``.
BYLAW_AREA_FIELDS = frozenset(
    {"bylaw_area", "bylaw_area_id", "bylaw_area_code", "bylaw_area_name"}
)


class GoverningBylawFrom(BaseModel):
    """How to read a feature's *own* governing by-law off its attributes (ABS-472).

    ``links_to`` binds a whole dataset to one document. That is right for a
    layer published under a single by-law, and wrong for a municipality-wide
    layer: ``halifax_zoning_boundaries`` carries 11,069 features spanning 22
    by-law areas, so linking the dataset to the Regional Centre LUB cites a
    Downtown Halifax zone to a by-law that does not govern it.

    When a dataset declares this block, the canonical attribute named by
    ``name_attribute`` carries the by-law that governs *that feature*, and
    retrieval resolves the citing document per feature instead of trusting
    the dataset-level link. The attributes are the ones the ABS-66 lookup
    table already resolves from the publisher's internal area id — the
    mapping exists, this is what finally uses it for citation.
    """

    name_attribute: str
    code_attribute: str | None = None

    model_config = {"extra": "forbid"}


class LinksTo(BaseModel):
    document_match: DocumentMatch
    fragment_citation: str
    governing_bylaw_from: GoverningBylawFrom | None = None

    model_config = {"extra": "forbid"}


DatasetRole = Literal["civic_address", "property_parcels", "road_centerlines"]


class DatasetConfig(BaseModel):
    """Per-dataset YAML configuration.

    Declarative description of a companion geo dataset: where to load it from,
    which bylaw fragment it implements, and how its raw attributes map into
    the canonical retrieval-API vocabulary.

    ``role`` is an optional marker that lets other components find datasets
    with a special semantic — e.g. ``civic_address`` datasets are queried by
    the geocoder. Datasets without a role are treated as plain reference data
    (height precincts, FAR precincts, zone overlays, etc.).

    ``links_to`` is required for plain datasets but optional for role-bearing
    datasets like civic_address that don't implement a specific bylaw clause.
    """

    name: str
    publisher: str | None = None
    format: Literal["geojson"] = "geojson"
    source_path: str | None = None
    source_url: str | None = None
    crs: str = "EPSG:4326"
    role: DatasetRole | None = None
    links_to: LinksTo | None = None
    attributes: AttributesConfig
    # Named lookup tables keyed by raw source-field value. A canonical field
    # mapping with ``lookup: <name>`` consults the matching table here; the
    # outer key is whatever the upstream API publishes (e.g. integer BYLAW_ID
    # codes), and each inner row is an arbitrary dict of denormalised columns
    # the field mapping selects via ``lookup_field``. Per-dataset by design
    # so upstream codes from different jurisdictions (HRM's BYLAW_ID 9 vs.
    # Toronto's 9) never collide in a global namespace.
    #
    # ABS-473: a table shared by several layers of the SAME publisher is
    # written once and pulled in with ``lookups_from`` (see
    # :func:`load_dataset_config`), which merges the named files into this
    # dict at load time. Opt-in per dataset, so the no-global-namespace
    # property above survives — a config still says exactly which tables it
    # reads.
    lookups: dict[str, dict[Any, dict[str, Any]]] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_source(self) -> "DatasetConfig":
        if not self.source_path and not self.source_url:
            raise ValueError("dataset config must specify either 'source_path' or 'source_url'")
        if self.role is None and self.links_to is None:
            raise ValueError(
                "non-role datasets must declare 'links_to' to bind them to a bylaw fragment"
            )
        return self

    @model_validator(mode="after")
    def _validate_governing_bylaw_attributes(self) -> "DatasetConfig":
        """A per-feature governing by-law must name attributes we actually map.

        Retrieval reads these off ``canonical_attributes_json``; a typo would
        degrade silently back to the dataset-level link — the exact
        mis-attribution ABS-472 exists to stop — so it fails at load instead.
        """
        governing = self.links_to.governing_bylaw_from if self.links_to else None
        if governing is None:
            return self
        declared = set(self.attributes.canonical)
        for label, attribute in (
            ("name_attribute", governing.name_attribute),
            ("code_attribute", governing.code_attribute),
        ):
            if attribute is not None and attribute not in declared:
                raise ValueError(
                    f"links_to.governing_bylaw_from.{label} references canonical "
                    f"field {attribute!r}, which this dataset does not map"
                )
        return self

    @model_validator(mode="after")
    def _require_governing_bylaw_when_features_carry_one(self) -> "DatasetConfig":
        """Knowing a feature's by-law area obliges the config to cite from it (ABS-473).

        This is the audit ABS-473 ran, turned into a load-time guard.
        ``halifax_height_precincts`` mapped HRM's ``BYLAW_AREA`` into a
        canonical attribute and then ignored it, so 48 Suburban Housing
        Accelerator LUB precincts were served as Schedule 15 of the Regional
        Centre LUB — a by-law that does not govern them. The information
        needed to catch that was already in the config; nothing required it
        to be used.

        So: any layer that maps a per-feature by-law-area attribute must also
        declare ``links_to.governing_bylaw_from``. A layer genuinely scoped to
        one by-law is unaffected — it does not map these fields at all. Role
        datasets (civic addresses, parcels) are exempt because they bind to no
        fragment and so make no citation to misattribute.
        """
        if self.links_to is None:
            return self
        carried = sorted(set(self.attributes.canonical) & BYLAW_AREA_FIELDS)
        if not carried or self.links_to.governing_bylaw_from is not None:
            return self
        raise ValueError(
            f"dataset {self.name!r} maps per-feature by-law attribution "
            f"{carried} but does not declare 'links_to.governing_bylaw_from'. "
            "Every feature would be cited to the dataset-level by-law even "
            "where its own attribute names a different one (ABS-473). Resolve "
            "a 'bylaw_area_name' (see the shared HRM subtype lookup) and point "
            "'governing_bylaw_from.name_attribute' at it."
        )

    @model_validator(mode="after")
    def _validate_lookup_references(self) -> "DatasetConfig":
        for canonical_name, mapping in self.attributes.canonical.items():
            if mapping.lookup is None:
                continue
            if mapping.lookup not in self.lookups:
                raise ValueError(
                    f"canonical field {canonical_name!r} references unknown lookup table "
                    f"{mapping.lookup!r}; add it to the top-level 'lookups' block"
                )
        return self


def load_dataset_config(path: str | Path) -> DatasetConfig:
    """Load and validate a dataset YAML config from disk.

    A top-level ``lookups_from`` list names YAML files of shared lookup
    tables, resolved relative to the config's own directory and merged into
    ``lookups`` before validation. It is a loader concern rather than a model
    field because only the loader knows where the config came from, and
    because everything downstream — ``metadata_json``, the coherence audit —
    should see resolved tables, not a path it would have to resolve again.
    """
    config_path = Path(path)
    raw = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"dataset config at {path} must be a YAML mapping at top level")
    includes = data.pop("lookups_from", None)
    if includes is not None:
        data["lookups"] = _merge_shared_lookups(
            config_path, includes, data.get("lookups") or {}
        )
    return DatasetConfig.model_validate(data)


def _merge_shared_lookups(
    config_path: Path, includes: Any, inline: dict[str, Any]
) -> dict[str, Any]:
    """Merge the tables named by ``lookups_from`` into a config's inline ones.

    A name collision is an error rather than an override: two definitions of
    the same table means the reader cannot tell which one a feature resolved
    through, and a lookup that resolves differently than it reads is how a
    feature ends up attributed to the wrong by-law.
    """
    if isinstance(includes, str):
        includes = [includes]
    if not isinstance(includes, list) or not all(isinstance(i, str) for i in includes):
        raise ValueError(
            f"'lookups_from' in {config_path} must be a path or list of paths"
        )
    merged: dict[str, Any] = dict(inline)
    for include in includes:
        include_path = (config_path.parent / include).resolve()
        if not include_path.is_file():
            raise ValueError(
                f"'lookups_from' entry {include!r} in {config_path} does not "
                f"resolve to a file (looked in {include_path})"
            )
        tables = yaml.safe_load(include_path.read_text(encoding="utf-8"))
        if not isinstance(tables, dict):
            raise ValueError(
                f"shared lookup file {include_path} must be a YAML mapping of "
                "table name -> table"
            )
        for table_name, table in tables.items():
            if table_name in merged:
                raise ValueError(
                    f"lookup table {table_name!r} from {include!r} is already "
                    f"defined in {config_path}; a table must have one definition"
                )
            merged[table_name] = table
    return merged
