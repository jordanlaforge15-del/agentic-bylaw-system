"""Adapter from City Intake Manifest (Phase 2 City Learning Pipeline) to a Layer 1
:class:`ParsingProfile`.

The City Learning Pipeline lives in ``abs-learning/`` and produces a
``CityIntakeManifest`` JSON artifact under
``abs-learning/output/{jurisdiction_code}/manifest.json``. Until ABS-74 nothing
read it — Halifax was ingested via a hand-authored
:class:`~layer1.profiles.ParsingProfile`. This module is the bridge.

Design notes:

- We deliberately mirror the manifest JSON schema with a local Pydantic model
  (``_ManifestEnvelope`` below) rather than importing the abs-learning models
  directly. abs-learning is not a published package; reaching across its
  ``src/`` directory just to use the same types couples release cadence
  unnecessarily. The contract is the JSON, not the Python classes.
- Citation hierarchy mapping is conservative: every manifest level maps to a
  Layer 1 ``FragmentType`` based on the level's free-form name (e.g. "part",
  "section", "subclause"). Unrecognised level names fall back to
  ``FragmentType.SECTION`` at a heuristic depth so the hierarchy reconstruction
  doesn't blow up — that's the right failure mode while we're still learning
  what real cities call their levels.
- ``profile_from_manifest`` refuses to emit a profile when ``pipeline_ready`` is
  ``False`` unless the caller explicitly opts out. That gate is the third
  acceptance criterion on the issue.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from layer1.models.enums import FragmentType
from layer1.profiles import ManifestCitationLevel, ParsingProfile, get_parsing_profile


# ---------------------------------------------------------------------------
# Local mirror of the abs-learning manifest JSON schema.
# Permissive on optional fields so partial / draft manifests still parse —
# the ``profile_from_manifest`` gate is what enforces production-readiness.
# ---------------------------------------------------------------------------


class _CitationLevel(BaseModel):
    level: str
    pattern: str
    label_format: str = "{n}"


class _CitationScheme(BaseModel):
    full_citation_example: str = ""
    separator: str = ", "
    hierarchy: list[_CitationLevel] = Field(default_factory=list)


class _ParserConfig(BaseModel):
    parser_version: str
    citation_scheme: _CitationScheme
    schedule_patterns: list[str] = Field(default_factory=list)
    table_caption_pattern: Optional[str] = None
    confidence: float
    flags: list[str] = Field(default_factory=list)


class _ZoneDesignation(BaseModel):
    code: str
    full_name: Optional[str] = None
    canonical_type: str
    description: Optional[str] = None


class _TaxonomyMap(BaseModel):
    zone_designations: list[_ZoneDesignation] = Field(default_factory=list)
    use_class_map: dict[str, str] = Field(default_factory=dict)
    standards_categories: list[str] = Field(default_factory=list)
    companion_bylaws_required: list[str] = Field(default_factory=list)
    confidence: float
    flags: list[str] = Field(default_factory=list)


class _EnrichmentConfig(BaseModel):
    """Optional manifest block driving enrichment *classification* (ABS-284).

    Mirrors the knobs on
    :class:`layer1.semantic.conventions.EnrichmentConventions`. Codepoints may be
    given as integers or ``"U+F098"`` / ``"0xF098"`` hex strings so a
    human-authored manifest can name the glyph. Omitting the block (or any field)
    leaves the Regional-Centre default in force.
    """

    permission_encoding: Optional[str] = None
    permitted_marker_codepoints: list[str | int] = Field(default_factory=list)
    ignored_marker_codepoints: list[str | int] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class _Municipality(BaseModel):
    name: str
    jurisdiction_code: str
    province: str
    governing_body: Optional[str] = None


class _SourceDocument(BaseModel):
    document_name: str
    document_type: str
    document_role: str
    in_scope: bool
    # Other fields exist in the full schema but the adapter doesn't read them;
    # keep the model permissive so we don't break on schema evolution.

    model_config = {"extra": "ignore"}


class _ManifestEnvelope(BaseModel):
    municipality: _Municipality
    sources: list[_SourceDocument]
    parser_config: Optional[_ParserConfig] = None
    taxonomy: Optional[_TaxonomyMap] = None
    enrichment: Optional[_EnrichmentConfig] = None
    manifest_version: str
    status: Literal["draft", "approved", "active", "deprecated"]
    pipeline_ready: bool

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


DEFAULT_MANIFEST_ROOT = Path("abs-learning/output")


class ManifestNotReadyError(RuntimeError):
    """Raised when an ingest is attempted from a manifest whose ``pipeline_ready``
    flag is ``False``. The caller can override with
    ``profile_from_manifest(..., require_pipeline_ready=False)`` — the test
    suite uses that escape hatch; production callers should fix the upstream
    manifest instead."""


def manifest_path_for(jurisdiction_code: str, root: Path | str | None = None) -> Path:
    """Return the conventional on-disk path for a jurisdiction's manifest."""
    base = Path(root) if root is not None else DEFAULT_MANIFEST_ROOT
    return base / jurisdiction_code / "manifest.json"


def load_manifest(path: Path | str) -> _ManifestEnvelope:
    """Load and validate a manifest JSON file at ``path``."""
    return _ManifestEnvelope.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_manifest_for_jurisdiction(
    jurisdiction_code: str,
    root: Path | str | None = None,
) -> _ManifestEnvelope:
    """Load the manifest for ``jurisdiction_code`` from the conventional location."""
    return load_manifest(manifest_path_for(jurisdiction_code, root))


def profile_from_manifest(
    manifest: _ManifestEnvelope,
    *,
    base: ParsingProfile | str | None = None,
    require_pipeline_ready: bool = True,
    name: str | None = None,
) -> ParsingProfile:
    """Build a :class:`ParsingProfile` driven by a city intake manifest.

    The returned profile inherits behavior switches (e.g. compound section
    handling, definition regex) from ``base`` — defaulting to the project's
    current ``HALIFAX_PROFILE`` because nearly every real bylaw we've seen so
    far uses Halifax-style numbering. The manifest-derived overlays
    (``manifest_citation_levels``, ``known_zone_codes``, ``zone_pattern``,
    ``use_class_map``) layer on top.

    ``require_pipeline_ready=True`` (default) refuses to emit a profile when the
    upstream Validation agent didn't pass the manifest. That's the
    "pipeline_ready=True is a meaningful precondition" acceptance criterion on
    ABS-74.
    """
    if require_pipeline_ready and not manifest.pipeline_ready:
        raise ManifestNotReadyError(
            f"Manifest for {manifest.municipality.jurisdiction_code} has "
            f"pipeline_ready=False — refusing to drive ingestion. Re-run the "
            f"learning pipeline or override with require_pipeline_ready=False."
        )

    base_profile = get_parsing_profile(base)
    citation_levels = _compile_citation_levels(manifest.parser_config)
    zone_codes, zone_pattern = _compile_zone_overlay(manifest.taxonomy)
    use_class_map = _compile_use_class_map(manifest.taxonomy)
    permission_encoding, permitted_codepoints, ignored_codepoints = (
        _compile_enrichment(manifest.enrichment)
    )

    profile_name = name or f"manifest:{manifest.municipality.jurisdiction_code.lower()}"

    # Re-create the profile with the manifest overlay. We can't mutate a frozen
    # dataclass, so build a fresh ParsingProfile that inherits ``base_profile``'s
    # behavioral switches but carries the new manifest fields. RegionalCentre
    # subclass behavior (custom postprocess_parse_result) is intentionally NOT
    # inherited here — the manifest-driven path is meant to be data-driven, not
    # a vehicle for arbitrary Python overrides. Callers who need that should
    # name the subclass profile directly with ``--profile`` instead.
    return ParsingProfile(
        name=profile_name,
        allow_compound_section_labels=base_profile.allow_compound_section_labels,
        allow_docling_section_labels_from_list_blocks=base_profile.allow_docling_section_labels_from_list_blocks,
        protect_docling_headings_from_boilerplate=base_profile.protect_docling_headings_from_boilerplate,
        promote_roman_subclauses_after_heading=base_profile.promote_roman_subclauses_after_heading,
        use_full_docling=base_profile.use_full_docling,
        use_docling_table_structure=base_profile.use_docling_table_structure,
        definition_start_re=base_profile.definition_start_re,
        term_definition_re=base_profile.term_definition_re,
        term_reference_re=base_profile.term_reference_re,
        docling_definition_like_re=base_profile.docling_definition_like_re,
        jurisdiction_code=manifest.municipality.jurisdiction_code,
        manifest_citation_levels=citation_levels,
        known_zone_codes=zone_codes,
        zone_pattern=zone_pattern,
        use_class_map=use_class_map,
        permission_encoding=permission_encoding,
        permitted_marker_codepoints=permitted_codepoints,
        ignored_marker_codepoints=ignored_codepoints,
    )


def profile_for_jurisdiction(
    jurisdiction_code: str,
    *,
    root: Path | str | None = None,
    base: ParsingProfile | str | None = None,
    require_pipeline_ready: bool = True,
) -> ParsingProfile:
    """Convenience: load + adapt in one call."""
    manifest = load_manifest_for_jurisdiction(jurisdiction_code, root=root)
    return profile_from_manifest(
        manifest, base=base, require_pipeline_ready=require_pipeline_ready
    )


# ---------------------------------------------------------------------------
# Mapping helpers.
# ---------------------------------------------------------------------------


# Mapping from manifest level-name (lowercased, normalized) to Layer 1
# (FragmentType, default depth integer). The depth aligns with the stack levels
# that ``layer1.pipeline.citations.parse_citation_label`` assigns to its own
# hardcoded matches (PART=1, SECTION=2, SUBSECTION=3, …), so a manifest-driven
# match plugs into the same hierarchy reconstruction stack with no special
# casing downstream.
_LEVEL_NAME_MAP: dict[str, tuple[FragmentType, int]] = {
    "part": (FragmentType.PART, 1),
    "schedule": (FragmentType.SCHEDULE, 1),
    "appendix": (FragmentType.APPENDIX, 1),
    "chapter": (FragmentType.PART, 1),
    "division": (FragmentType.PART, 1),
    "article": (FragmentType.PART, 1),
    "section": (FragmentType.SECTION, 2),
    "subsection": (FragmentType.SUBSECTION, 3),
    "paragraph": (FragmentType.SUBSECTION, 4),
    "subparagraph": (FragmentType.CLAUSE, 5),
    "clause": (FragmentType.CLAUSE, 5),
    "subclause": (FragmentType.SUBCLAUSE, 6),
    "item": (FragmentType.CLAUSE, 5),
}


def _classify_level(raw_level: str) -> tuple[FragmentType, int]:
    """Map a manifest's free-form level name onto a (FragmentType, depth).

    Unknown names fall back to SECTION at depth 2 — a reasonable default that
    keeps a partly-correct manifest usable while we surface the issue via
    ``raw_level`` in diagnostics.
    """
    key = raw_level.strip().lower()
    if key in _LEVEL_NAME_MAP:
        return _LEVEL_NAME_MAP[key]
    # Tolerate "level_1" / "L1" style names by extracting a trailing digit.
    match = re.search(r"(\d+)\s*$", key)
    if match:
        depth = int(match.group(1))
        if depth == 1:
            return FragmentType.PART, 1
        if depth == 2:
            return FragmentType.SECTION, 2
        if depth == 3:
            return FragmentType.SUBSECTION, 3
        if depth == 4:
            return FragmentType.SUBSECTION, 4
        if depth >= 5:
            return FragmentType.SUBCLAUSE, min(depth, 6)
    return FragmentType.SECTION, 2


def _compile_citation_levels(
    parser_config: _ParserConfig | None,
) -> tuple[ManifestCitationLevel, ...]:
    if parser_config is None or not parser_config.citation_scheme.hierarchy:
        return ()
    levels: list[ManifestCitationLevel] = []
    for entry in parser_config.citation_scheme.hierarchy:
        fragment_type, depth = _classify_level(entry.level)
        try:
            compiled = re.compile(entry.pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(
                f"Manifest citation pattern for level '{entry.level}' is not a "
                f"valid regex ({exc}); refusing to silently drop it."
            ) from exc
        levels.append(
            ManifestCitationLevel(
                raw_level=entry.level,
                pattern=compiled,
                fragment_type=fragment_type,
                level=depth,
                label_format=entry.label_format,
            )
        )
    return tuple(levels)


def _compile_zone_overlay(
    taxonomy: _TaxonomyMap | None,
) -> tuple[frozenset[str] | None, re.Pattern[str] | None]:
    if taxonomy is None or not taxonomy.zone_designations:
        return None, None
    codes = frozenset(z.code for z in taxonomy.zone_designations if z.code)
    if not codes:
        return None, None
    # Build a regex that matches any known zone code as a whole token. We sort
    # by length so the longer "CDD-2" wins over the prefix "CDD" when both are
    # listed. ``re.escape`` defends against punctuation in the codes themselves.
    sorted_codes = sorted(codes, key=lambda code: (-len(code), code))
    pattern_source = r"\b(?:" + "|".join(re.escape(code) for code in sorted_codes) + r")\b"
    return codes, re.compile(pattern_source, re.IGNORECASE)


def _parse_codepoint(value: str | int) -> int:
    """Parse a codepoint given as an int or a ``"U+F098"`` / ``"0xF098"`` string."""
    if isinstance(value, int):
        return value
    token = value.strip()
    if token.upper().startswith("U+"):
        token = token[2:]
    elif token.lower().startswith("0x"):
        token = token[2:]
    try:
        return int(token, 16)
    except ValueError as exc:
        raise ValueError(
            f"Manifest enrichment codepoint {value!r} is not a valid hex "
            f"codepoint (expected an int or a 'U+XXXX' string)."
        ) from exc


def _compile_enrichment(
    enrichment: _EnrichmentConfig | None,
) -> tuple[str | None, frozenset[int] | None, frozenset[int] | None]:
    """Map the manifest's enrichment block to ``ParsingProfile`` fields (ABS-284).

    Returns ``(permission_encoding, permitted_codepoints, ignored_codepoints)``
    with ``None`` for any field the manifest doesn't declare, so the profile
    falls back to the Regional-Centre default for that field.
    """
    if enrichment is None:
        return None, None, None
    permitted = (
        frozenset(_parse_codepoint(cp) for cp in enrichment.permitted_marker_codepoints)
        if enrichment.permitted_marker_codepoints
        else None
    )
    ignored = (
        frozenset(_parse_codepoint(cp) for cp in enrichment.ignored_marker_codepoints)
        if enrichment.ignored_marker_codepoints
        else None
    )
    encoding = enrichment.permission_encoding or None
    return encoding, permitted, ignored


def _compile_use_class_map(
    taxonomy: _TaxonomyMap | None,
) -> dict[str, str] | None:
    if taxonomy is None or not taxonomy.use_class_map:
        return None
    # Normalize keys to lowercase whitespace-collapsed form so a manifest with
    # human-readable surface forms ("Single-Detached Dwelling") still matches
    # the lowercased input ``layer1.semantic.extractors.normalize_use`` produces.
    return {
        re.sub(r"\s+", " ", key.strip().lower()): value
        for key, value in taxonomy.use_class_map.items()
    }
