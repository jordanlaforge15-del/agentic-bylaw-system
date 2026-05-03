from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from layer1.datasets.canonical import CANONICAL_FIELDS, SUPPORTED_TYPES


class CanonicalFieldMapping(BaseModel):
    """How a single canonical field is sourced from a dataset's raw properties.

    Exactly one of ``from_field`` or ``synthesize`` must be set.
    """

    from_field: str | None = Field(default=None, alias="from")
    type: str | None = None
    optional: bool = False
    null_when: list[Any] = Field(default_factory=list)
    synthesize: str | None = None

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


DatasetRole = Literal["civic_address"]


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


def load_dataset_config(path: str | Path) -> DatasetConfig:
    """Load and validate a dataset YAML config from disk."""
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"dataset config at {path} must be a YAML mapping at top level")
    return DatasetConfig.model_validate(data)
