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


class LinksTo(BaseModel):
    document_match: DocumentMatch
    fragment_citation: str

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
    """Load and validate a dataset YAML config from disk."""
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"dataset config at {path} must be a YAML mapping at top level")
    return DatasetConfig.model_validate(data)
