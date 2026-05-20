"""Loader + typed view over the Phase-1 attribute taxonomy.

The YAML at :data:`DEFAULT_TAXONOMY_PATH` is the single source of truth
for attribute IDs used across the system:

* ``source_fragment.attribute_tags`` — which clauses regulate this
  attribute (populated by the semantic-enrichment pass).
* ``submission_attribute.attribute_key`` — which value the submission
  carries.

This module exposes a small typed surface so callers don't reach into
raw YAML dicts:

* :class:`AttributeCategory` — enum of valid categories.
* :class:`AttributeValueType` — enum of allowed value types.
* :class:`AttributeDefinition` — one attribute entry.
* :class:`Taxonomy` — the whole loaded file, with helpers for
  lookup-by-id and keyword search (the enrichment pass uses both).
* :func:`load_taxonomy` — entry point. Caches by path so the YAML is
  parsed exactly once per process; pass ``reload=True`` to bust the
  cache in tests.

The loader is deliberately strict: malformed entries raise
:class:`TaxonomyError` at import time so a broken YAML can't silently
poison the evaluator's filter step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parent / "attributes" / "taxonomy.yaml"


class TaxonomyError(ValueError):
    """Raised when the YAML doesn't parse into a valid taxonomy."""


class AttributeCategory(StrEnum):
    """Where the attribute comes from and how it's computed.

    Used by both the evaluator (to know whether to look the value up
    on the parcel vs. the submission) and the extraction pipelines in
    later phases (to know whether to query BIM or the title block).
    """

    GEOMETRIC_DIRECT = "geometric_direct"
    GEOMETRIC_DERIVED = "geometric_derived"
    CATEGORICAL = "categorical"
    SITE = "site"
    CONTEXTUAL = "contextual"


class AttributeValueType(StrEnum):
    """The shape of ``submission_attribute.value_json["value"]``."""

    NUMBER = "number"
    INTEGER = "integer"
    STRING = "string"
    ENUM = "enum"
    BOOLEAN = "boolean"


@dataclass(frozen=True)
class ExtractionDifficulty:
    """How hard the attribute is to obtain from each surface.

    Coarse three-level scale (easy / medium / hard); fine-grained
    confidence belongs on the submission_attribute row itself, not the
    taxonomy. Used by Phase-2/3 extraction pipelines to prioritise
    which attributes to invest LLM-vision effort on for PDF scans
    versus trusting BIM directly.
    """

    bim: str | None = None
    pdf_vector: str | None = None
    pdf_scan: str | None = None


@dataclass(frozen=True)
class AttributeDerivation:
    """How a derived attribute is computed.

    Only populated for ``geometric_derived`` and a few ``contextual``
    attributes. The string ``method`` is a tag, not executable code;
    the evaluator side maps tags to actual derivation functions.
    """

    inputs: tuple[str, ...] = ()
    method: str | None = None


@dataclass(frozen=True)
class AttributeDefinition:
    """One row of the taxonomy.

    Identity is the :attr:`id`. Equality compares ids, not the full
    definition body, so the same attribute coming from different
    taxonomy versions still compares equal — useful when the evaluator
    is asked to evaluate against a slightly newer taxonomy than the
    one a submission was originally built against.
    """

    id: str
    category: AttributeCategory
    value_type: AttributeValueType
    description: str
    unit: str | None = None
    extraction_difficulty: ExtractionDifficulty = field(default_factory=ExtractionDifficulty)
    derivation: AttributeDerivation = field(default_factory=AttributeDerivation)
    bylaw_tag_keywords: tuple[str, ...] = ()

    def matches_text(self, text: str) -> bool:
        """Does any of this attribute's keywords appear in the supplied text?

        Used by the semantic-enrichment pass's keyword pre-filter to
        cheaply skip clauses that obviously don't regulate this
        attribute. Case-insensitive substring match; not a tokenizer —
        bylaws use phrases like "front yard" and "minimum front yard"
        that should both match without word-boundary tricks.
        """
        if not self.bylaw_tag_keywords:
            return False
        lowered = text.lower()
        return any(kw.lower() in lowered for kw in self.bylaw_tag_keywords)


@dataclass(frozen=True)
class Taxonomy:
    """The whole loaded taxonomy file.

    Constructed once per process via :func:`load_taxonomy`; pass
    around as a value. Provides id lookup and a keyword-prefilter
    pass that callers (the enrichment script and the evaluator
    fallback path) reach for.
    """

    version: str
    attributes: tuple[AttributeDefinition, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(a.id for a in self.attributes)

    def by_id(self, attribute_id: str) -> AttributeDefinition:
        for attr in self.attributes:
            if attr.id == attribute_id:
                return attr
        raise KeyError(f"attribute {attribute_id!r} not in taxonomy version {self.version!r}")

    def find_by_keywords(self, text: str) -> tuple[AttributeDefinition, ...]:
        """Return every attribute whose keywords match the supplied text.

        Used by the enrichment pass's pre-filter step and by the
        evaluator when no explicit ``attribute_tags`` are populated
        yet on a fragment (fallback path).
        """
        return tuple(a for a in self.attributes if a.matches_text(text))


_CACHE: dict[Path, Taxonomy] = {}


_VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def load_taxonomy(path: Path | str | None = None, *, reload: bool = False) -> Taxonomy:
    """Load (and cache) the taxonomy YAML.

    Cache key is the resolved absolute path. ``reload=True`` busts the
    cache — useful in tests that write a fixture file then load it.
    """
    resolved = Path(path).resolve() if path is not None else DEFAULT_TAXONOMY_PATH
    if not reload and resolved in _CACHE:
        return _CACHE[resolved]
    if not resolved.exists():
        raise TaxonomyError(f"taxonomy file not found: {resolved}")
    with resolved.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    taxonomy = _parse_taxonomy(raw, source=resolved)
    _CACHE[resolved] = taxonomy
    return taxonomy


def _parse_taxonomy(raw: Any, *, source: Path) -> Taxonomy:
    if not isinstance(raw, dict):
        raise TaxonomyError(f"{source}: root must be a mapping, got {type(raw).__name__}")
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise TaxonomyError(f"{source}: missing or empty 'version'")
    attrs_raw = raw.get("attributes")
    if not isinstance(attrs_raw, list) or not attrs_raw:
        raise TaxonomyError(f"{source}: 'attributes' must be a non-empty list")

    seen_ids: set[str] = set()
    attributes: list[AttributeDefinition] = []
    for index, entry in enumerate(attrs_raw):
        if not isinstance(entry, dict):
            raise TaxonomyError(f"{source}: attribute #{index} is not a mapping")
        attr = _parse_attribute(entry, index=index, source=source)
        if attr.id in seen_ids:
            raise TaxonomyError(f"{source}: duplicate attribute id {attr.id!r}")
        seen_ids.add(attr.id)
        attributes.append(attr)
    return Taxonomy(version=version, attributes=tuple(attributes))


def _parse_attribute(entry: dict, *, index: int, source: Path) -> AttributeDefinition:
    attr_id = entry.get("id")
    if not isinstance(attr_id, str) or not attr_id:
        raise TaxonomyError(f"{source}: attribute #{index} missing 'id'")

    try:
        category = AttributeCategory(entry.get("category", ""))
    except ValueError as exc:
        raise TaxonomyError(
            f"{source}: attribute {attr_id!r} has invalid category {entry.get('category')!r}"
        ) from exc

    try:
        value_type = AttributeValueType(entry.get("value_type", ""))
    except ValueError as exc:
        raise TaxonomyError(
            f"{source}: attribute {attr_id!r} has invalid value_type {entry.get('value_type')!r}"
        ) from exc

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise TaxonomyError(f"{source}: attribute {attr_id!r} missing 'description'")

    unit = entry.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise TaxonomyError(f"{source}: attribute {attr_id!r} 'unit' must be a string or omitted")

    difficulty = _parse_difficulty(entry.get("extraction_difficulty"), attr_id=attr_id, source=source)
    derivation = _parse_derivation(entry.get("derivation"), attr_id=attr_id, source=source)
    keywords = _parse_keywords(entry.get("bylaw_tag_keywords"), attr_id=attr_id, source=source)

    return AttributeDefinition(
        id=attr_id,
        category=category,
        value_type=value_type,
        description=description,
        unit=unit,
        extraction_difficulty=difficulty,
        derivation=derivation,
        bylaw_tag_keywords=keywords,
    )


def _parse_difficulty(raw: Any, *, attr_id: str, source: Path) -> ExtractionDifficulty:
    if raw is None:
        return ExtractionDifficulty()
    if not isinstance(raw, dict):
        raise TaxonomyError(
            f"{source}: attribute {attr_id!r} 'extraction_difficulty' must be a mapping"
        )
    values: dict[str, str | None] = {"bim": None, "pdf_vector": None, "pdf_scan": None}
    for key in values:
        if key in raw:
            level = raw[key]
            if level is not None and (not isinstance(level, str) or level not in _VALID_DIFFICULTIES):
                raise TaxonomyError(
                    f"{source}: attribute {attr_id!r} extraction_difficulty.{key} "
                    f"must be one of {sorted(_VALID_DIFFICULTIES)}; got {level!r}"
                )
            values[key] = level
    return ExtractionDifficulty(**values)


def _parse_derivation(raw: Any, *, attr_id: str, source: Path) -> AttributeDerivation:
    if raw is None:
        return AttributeDerivation()
    if not isinstance(raw, dict):
        raise TaxonomyError(
            f"{source}: attribute {attr_id!r} 'derivation' must be a mapping"
        )
    inputs = raw.get("inputs", [])
    if inputs is None:
        inputs = []
    if not isinstance(inputs, list) or not all(isinstance(x, str) for x in inputs):
        raise TaxonomyError(
            f"{source}: attribute {attr_id!r} derivation.inputs must be a list of strings"
        )
    method = raw.get("method")
    if method is not None and not isinstance(method, str):
        raise TaxonomyError(f"{source}: attribute {attr_id!r} derivation.method must be a string")
    return AttributeDerivation(inputs=tuple(inputs), method=method)


def _parse_keywords(raw: Any, *, attr_id: str, source: Path) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise TaxonomyError(
            f"{source}: attribute {attr_id!r} bylaw_tag_keywords must be a list of non-empty strings"
        )
    return tuple(raw)
