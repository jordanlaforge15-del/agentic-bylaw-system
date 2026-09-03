from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import os
import re
from typing import Iterable

from sqlalchemy.orm import Session

from layer1.db.base import (
    CrossReference,
    SemanticEdge,
    SemanticEntity,
    SemanticFact,
    SemanticFactParticipant,
    SemanticProvenance,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
)
from layer1.profiles import ParsingProfile
from layer1.semantic.conventions import (
    DEFAULT_IGNORED_CODEPOINTS,
    DEFAULT_PERMITTED_CODEPOINTS,
    SYMBOL_MATRIX,
    EnrichmentConventions,
)
from layer1.semantic.extractors import (
    ProfileOverlay,
    current_enrichment_conventions,
    extract_condition_refs,
    extract_development_contexts,
    extract_numeric_values,
    extract_section_refs,
    extract_standards,
    extract_table_refs,
    extract_uses,
    extract_zones,
    looks_like_section_label,
    looks_like_use_label,
    normalize_use,
    normalize_zone,
    reset_profile_overlay,
    use_profile_overlay,
)
from layer1.semantic.permission_grid import densify_permission_matrix
from layer1.semantic.permission_markers import (
    UNKNOWN,
    annotate_value_cells,
    cell_footnotes,
    classify_permission_marker,
)
from layer1.semantic.use_matching import match_use

EXTRACTOR_VERSION = "semantic-v1"
REVIEW_AUTO = "auto_accepted"
REVIEW_NEEDS = "needs_review"

# ABS-278: axis bindings whose confidence is at or below this threshold are
# flagged for human review (``metadata_json.review = "needs_review"``) rather
# than trusted for automated (use, zone) → cell resolution. Recovered header-
# bleed columns and ambiguous labels land here. Configurable via env so a
# jurisdiction with noisier source tables can loosen/tighten the gate without a
# code change.
AXIS_REVIEW_CONFIDENCE_THRESHOLD = float(
    os.environ.get("ABS_AXIS_REVIEW_THRESHOLD", "0.6")
)
# Confidence assigned to a zone column recovered from header-bleed (a bare zone
# code found in the data region rather than the header row). Deliberately at/
# below the review threshold — these are corrections, not first-class headers.
HEADER_BLEED_CONFIDENCE = 0.55
PERMISSION_MARKERS = {"●", "", "•", "■", "x", "X"}


@dataclass
class SemanticEnrichmentReport:
    document_id: int
    entities: int = 0
    facts: int = 0
    fact_participants: int = 0
    edges: int = 0
    table_profiles: int = 0
    axis_bindings: int = 0
    provenance: int = 0
    # ABS-520: blank permission-matrix cells the parser dropped and this run
    # materialized, and the ones its geometry refused to vouch for.
    permission_cells_filled: int = 0
    permission_cells_unfilled: int = 0
    warnings: list[str] = field(default_factory=list)

    def model_dump(self) -> dict:
        return {
            "document_id": self.document_id,
            "entities": self.entities,
            "facts": self.facts,
            "fact_participants": self.fact_participants,
            "edges": self.edges,
            "table_profiles": self.table_profiles,
            "axis_bindings": self.axis_bindings,
            "provenance": self.provenance,
            "permission_cells_filled": self.permission_cells_filled,
            "permission_cells_unfilled": self.permission_cells_unfilled,
            "warnings": self.warnings,
        }


def enrich_document_semantics(
    session: Session,
    *,
    document_id: int,
    profile: ParsingProfile | None = None,
) -> SemanticEnrichmentReport:
    """Run semantic enrichment for a single document.

    Pass ``profile`` (typically derived via
    :func:`layer1.manifest_adapter.profile_from_manifest`) to override the
    Halifax-flavoured hardcoded zone codes / use map for the duration of this
    call. Without a profile the historical behavior is preserved exactly.
    """
    overlay = _overlay_from_profile(profile)
    token = use_profile_overlay(overlay) if overlay is not None else None
    try:
        _clear_existing_semantics(session, document_id=document_id)
        report = SemanticEnrichmentReport(document_id=document_id)
        cache: dict[tuple[str, str], SemanticEntity] = {}

        fragments = (
            session.query(SourceFragment)
            .filter(SourceFragment.document_id == document_id)
            .order_by(SourceFragment.page_start, SourceFragment.id)
            .all()
        )
        tables = (
            session.query(SourceTable)
            .filter(SourceTable.document_id == document_id)
            .order_by(SourceTable.page_start, SourceTable.id)
            .all()
        )
        for fragment in fragments:
            _extract_fragment_entities(session, report, cache, fragment)
            _extract_definition_fact(session, report, cache, fragment)
            _extract_condition_definition(session, report, cache, fragment)
            _extract_mainland_permitted_uses(session, report, cache, fragment)
        for table in tables:
            _enrich_table(session, report, cache, table)
        _inherit_sibling_column_bindings(session, report, tables)
        _enrich_cross_references(session, report, cache, document_id=document_id)
        session.flush()
        _refresh_counts(session, report)
        return report
    finally:
        if token is not None:
            reset_profile_overlay(token)


def _overlay_from_profile(profile: ParsingProfile | None) -> ProfileOverlay | None:
    if profile is None:
        return None
    conventions = _conventions_from_profile(profile)
    if (
        profile.zone_pattern is None
        and profile.known_zone_codes is None
        and profile.use_class_map is None
        and conventions is None
    ):
        return None
    return ProfileOverlay(
        zone_pattern=profile.zone_pattern,
        known_zone_codes=profile.known_zone_codes,
        use_class_map=profile.use_class_map,
        enrichment=conventions,
    )


def _conventions_from_profile(
    profile: ParsingProfile | None,
) -> EnrichmentConventions | None:
    """Build per-bylaw :class:`EnrichmentConventions` from a parsing profile.

    Returns ``None`` when the profile declares no enrichment fields, so the
    overlay stays minimal and enrichment classification falls back to the
    Regional-Centre default (ABS-284 FR3).
    """
    if profile is None:
        return None
    if (
        profile.permission_encoding is None
        and profile.permitted_marker_codepoints is None
        and profile.ignored_marker_codepoints is None
    ):
        return None
    return EnrichmentConventions(
        permission_encoding=profile.permission_encoding or SYMBOL_MATRIX,
        permitted_codepoints=(
            profile.permitted_marker_codepoints
            if profile.permitted_marker_codepoints is not None
            else DEFAULT_PERMITTED_CODEPOINTS
        ),
        ignored_codepoints=(
            profile.ignored_marker_codepoints
            if profile.ignored_marker_codepoints is not None
            else DEFAULT_IGNORED_CODEPOINTS
        ),
    )


def validate_document_semantics(session: Session, *, document_id: int) -> dict:
    tables = session.query(SourceTable).filter(SourceTable.document_id == document_id).count()
    profiles = (
        session.query(TableSemanticProfile)
        .join(SourceTable, SourceTable.id == TableSemanticProfile.table_id)
        .filter(SourceTable.document_id == document_id)
        .count()
    )
    facts_without_provenance = 0
    for fact in session.query(SemanticFact).filter(SemanticFact.document_id == document_id).all():
        has_provenance = (
            session.query(SemanticProvenance)
            .filter(
                SemanticProvenance.document_id == document_id,
                SemanticProvenance.object_type == "semantic_fact",
                SemanticProvenance.object_id == fact.id,
            )
            .first()
            is not None
        )
        facts_without_provenance += int(not has_provenance)
    warnings = []
    if tables and not profiles:
        warnings.append("Document has source tables but no semantic table profiles")
    if facts_without_provenance:
        warnings.append(f"{facts_without_provenance} semantic facts lack provenance")
    return {
        "document_id": document_id,
        "ok": not warnings,
        "tables": tables,
        "table_profiles": profiles,
        "semantic_entities": session.query(SemanticEntity).filter(SemanticEntity.document_id == document_id).count(),
        "semantic_facts": session.query(SemanticFact).filter(SemanticFact.document_id == document_id).count(),
        "warnings": warnings,
    }


def _clear_existing_semantics(session: Session, *, document_id: int) -> None:
    table_ids = [row.id for row in session.query(SourceTable.id).filter(SourceTable.document_id == document_id).all()]
    fact_ids = [row.id for row in session.query(SemanticFact.id).filter(SemanticFact.document_id == document_id).all()]
    if fact_ids:
        session.query(SemanticFactParticipant).filter(SemanticFactParticipant.fact_id.in_(fact_ids)).delete(synchronize_session=False)
    if table_ids:
        session.query(TableAxisBinding).filter(TableAxisBinding.table_id.in_(table_ids)).delete(synchronize_session=False)
        session.query(TableSemanticProfile).filter(TableSemanticProfile.table_id.in_(table_ids)).delete(synchronize_session=False)
    for model in [SemanticEdge, SemanticProvenance, SemanticFact, SemanticEntity]:
        session.query(model).filter(model.document_id == document_id).delete(synchronize_session=False)
    session.flush()


def _extract_fragment_entities(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    fragment: SourceFragment,
) -> None:
    text = fragment.text
    for entity_type, values in [
        ("zone", extract_zones(text)),
        ("use", extract_uses(text)),
        ("standard", extract_standards(text)),
        ("development_context", extract_development_contexts(text)),
        ("condition_ref", extract_condition_refs(text)),
        ("section_ref", extract_section_refs(text)),
        ("table_ref", extract_table_refs(text)),
        ("numeric_value", extract_numeric_values(text)),
    ]:
        for value in values:
            entity = _get_or_create_entity(
                session,
                report,
                cache,
                document_id=fragment.document_id,
                entity_type=entity_type,
                canonical_name=value,
                source_text=value,
                confidence=0.85,
                metadata={"source_fragment_id": fragment.id},
            )
            _add_provenance(
                session,
                report,
                document_id=fragment.document_id,
                object_type="semantic_entity",
                object_id=entity.id,
                source_type="source_fragment",
                source_id=fragment.id,
                method="fragment_entity_extractor",
                confidence=0.85,
            )


# ABS-106: Bylaws that don't use circled-number / [N] markers (e.g.
# Halifax Mainland LUB) still have conditions expressed in plain prose.
# This heuristic fires on fragments whose text contains a conditional
# discourse marker (subject to, provided that, only if, where the,
# notwithstanding, in addition to) paired with permission/restriction
# language. Returns a synthetic marker derived from citation_path so
# downstream consumers can still address the fact.
_PROSE_CONDITION_RE = re.compile(
    r"\b(subject\s+to|provided\s+that|provided\s+however|only\s+if|"
    r"only\s+where|except\s+(?:where|when)|notwithstanding|"
    r"in\s+addition\s+to|where\s+the)\b",
    re.IGNORECASE,
)
_PERMISSION_LANGUAGE_RE = re.compile(
    r"\b(permitted|prohibited|allowed|shall\s+not|may\s+not|"
    r"required|approval|approved)\b",
    re.IGNORECASE,
)


def _detect_prose_condition_markers(fragment: SourceFragment) -> list[str]:
    """ABS-106: heuristic detection of prose-only condition fragments.

    Returns a list of synthetic markers (typically one per fragment) if
    the fragment expresses a condition via prose rather than a circled
    number or [N] marker. Empty list if not a condition.
    """
    text = fragment.text or ""
    if len(text) < 30:
        return []
    if not _PROSE_CONDITION_RE.search(text):
        return []
    if not _PERMISSION_LANGUAGE_RE.search(text):
        return []
    # Synthetic marker derived from citation_path so multiple synthetic
    # conditions from the same bylaw are still distinguishable downstream.
    citation = (fragment.citation_path or f"fragment:{fragment.id}").strip()
    return [f"prose:{citation}"]


def _extract_condition_definition(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    fragment: SourceFragment,
) -> None:
    markers = extract_condition_refs(fragment.text)
    if not markers:
        # ABS-106: fall back to prose-heuristic detection for bylaws
        # that don't use circled-number / [N] marker conventions.
        markers = _detect_prose_condition_markers(fragment)
    if not markers:
        return
    for marker in markers:
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=fragment.document_id,
            entity_type="condition_ref",
            canonical_name=marker,
            source_text=marker,
            confidence=0.95,
            metadata={"source_fragment_id": fragment.id},
        )
        fact = _create_fact(
            session,
            report,
            document_id=fragment.document_id,
            relation_type="condition_definition",
            subject=entity,
            value_text=fragment.text,
            normalized_value={"condition_ref": marker, "text": fragment.text},
            assertion_type="explicit",
            confidence=0.9,
            metadata={"source_fragment_ids": [fragment.id]},
        )
        _add_participant(session, report, fact, entity, "condition", 0.95)
        for section_ref in extract_section_refs(fragment.text):
            section_entity = _get_or_create_entity(
                session,
                report,
                cache,
                document_id=fragment.document_id,
                entity_type="section_ref",
                canonical_name=section_ref,
                source_text=section_ref,
                confidence=0.8,
                metadata={"source_fragment_id": fragment.id},
            )
            _add_participant(session, report, fact, section_entity, "section_ref", 0.8)
            _add_edge(session, report, fragment.document_id, fact, "references", section_entity, [fragment.id], 0.8)
        _add_provenance(
            session,
            report,
            document_id=fragment.document_id,
            object_type="semantic_fact",
            object_id=fact.id,
            source_type="source_fragment",
            source_id=fragment.id,
            method="condition_definition_extractor",
            confidence=0.9,
        )


# --------------------------------------------------------------------- ABS-283
# Mainland permitted-use extraction. The Regional Centre LUB encodes permitted
# uses in a symbol-font ●/circled-number matrix (Table 1A). The Halifax
# Mainland LUB does NOT use that convention (0 U+F098 cells) — it lists permitted
# uses as prose under each zone's section, e.g.:
#
#     "62EA(1) The following uses shall be permitted in any ICH Zone:
#      (1) Single Unit Dwellings
#      (2) Open Space Uses"
#
# plus an exhaustiveness clause ("No person shall ... carry out ... any
# development for any purpose other than ... the uses set out in subsection
# (1)"), which makes the list closed: a use absent from the list is *not
# permitted*, not merely unknown. We detect the intro, parse the enumerated /
# comma-separated use list, and emit one ``permitted_use_list`` fact per
# (zone, fragment) carrying the permitted uses. :func:`resolve_mainland_
# permitted_use` reads those facts to return a grounded (use, zone) verdict.
_MAINLAND_PERMITTED_INTRO_RE = re.compile(
    r"following\s+uses?\s+(?:shall\s+be\s+|are\s+|may\s+be\s+|will\s+be\s+)?"
    r"permitted\s+in\s+(?:any|the|an|all|each)\s+"
    r"([A-Za-z0-9][A-Za-z0-9\-]{0,11})\s+zones?\b\s*:?",
    re.IGNORECASE,
)
# Split the use list into items: enumeration markers "(1)"/"(a)", bullets,
# newlines, semicolons, or commas.
_MAINLAND_LIST_SPLIT_RE = re.compile(
    r"\(\s*\d{1,3}\s*\)|\(\s*[a-z]{1,3}\s*\)|[\n;,•]|(?<!\w)-\s+", re.IGNORECASE
)
# A trailing exhaustiveness/restriction clause marks the end of the use list.
_MAINLAND_LIST_STOP_RE = re.compile(
    r"\bno\s+person\s+shall\b|\bno\s+development\b|\bsubsection\b|"
    r"\bexcept\s+as\b|\bprovided\s+that\b",
    re.IGNORECASE,
)


def _loose_use_key(name: str) -> str:
    """Plural/qualifier-insensitive match key for a Mainland use phrase.

    Lists spell uses as title-case plurals ("Single Unit Dwellings", "Open
    Space Uses") while a query arrives singular and may carry a trailing
    "use" ("single unit dwelling", "single unit dwelling use"). Collapse both
    sides to a common key: lowercase + alias-normalize, neutralize hyphenation
    (``normalize_use`` aliases the singular "single unit dwelling" to the
    hyphenated "single-unit dwelling" but leaves the plural list form spaced),
    drop a trailing "use"/"uses" qualifier, then a single trailing plural "s".
    """
    key = normalize_use(name).replace("-", " ")
    key = re.sub(r"\s+", " ", key).strip()
    key = re.sub(r"\s+uses?$", "", key).strip()
    key = re.sub(r"s$", "", key)
    return key


def _parse_mainland_use_list(tail: str) -> list[str]:
    """Parse the enumerated/comma-separated use list following the intro."""
    stop = _MAINLAND_LIST_STOP_RE.search(tail)
    if stop is not None:
        tail = tail[: stop.start()]
    items: list[str] = []
    for raw in _MAINLAND_LIST_SPLIT_RE.split(tail):
        if raw is None:
            continue
        item = re.sub(r"\s+", " ", raw).strip().strip(".,;:()")
        if len(item) < 3 or len(item) > 60:
            continue
        if not re.search(r"[A-Za-z]", item):
            continue
        # Drop bare section labels and connective fragments that aren't uses.
        if looks_like_section_label(item):
            continue
        if item.lower() in {"and", "or", "and the following", "the following"}:
            continue
        items.append(item)
    # De-duplicate, preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _extract_mainland_permitted_uses(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    fragment: SourceFragment,
) -> None:
    """ABS-283: emit a ``permitted_use_list`` fact from Mainland section prose.

    Detects "The following uses shall be permitted in any <ZONE> Zone: ..."
    style fragments, parses the enumerated use list, and records one fact per
    (zone, fragment) so a (use, zone) query can be resolved without the
    symbol-dot matrix the Mainland LUB doesn't use.
    """
    text = fragment.text or ""
    match = _MAINLAND_PERMITTED_INTRO_RE.search(text)
    if not match:
        return
    zone_raw = match.group(1)
    zone_norm = normalize_zone(zone_raw)
    if not zone_norm:
        return
    uses = _parse_mainland_use_list(text[match.end():])
    if not uses:
        return
    zone_entity = _get_or_create_entity(
        session,
        report,
        cache,
        document_id=fragment.document_id,
        entity_type="zone",
        canonical_name=zone_norm,
        source_text=zone_raw,
        confidence=0.85,
        metadata={"source_fragment_id": fragment.id},
    )
    use_keys: list[str] = []
    use_entities: list[SemanticEntity] = []
    for use_label in uses:
        use_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=fragment.document_id,
            entity_type="use",
            canonical_name=normalize_use(use_label),
            source_text=use_label,
            confidence=0.8,
            metadata={"source_fragment_id": fragment.id},
        )
        use_entities.append(use_entity)
        use_keys.append(_loose_use_key(use_label))
    fact = _create_fact(
        session,
        report,
        document_id=fragment.document_id,
        relation_type="permitted_use_list",
        subject=zone_entity,
        value_text=fragment.text,
        normalized_value={
            "permission_convention": "mainland_section_prose",
            "zone": zone_norm,
            "permitted_uses": uses,
            "permitted_use_keys": sorted(set(use_keys)),
        },
        assertion_type="explicit",
        confidence=0.8,
        metadata={"source_fragment_ids": [fragment.id]},
    )
    _add_participant(session, report, fact, zone_entity, "zone", 0.85)
    for use_entity in use_entities:
        _add_participant(session, report, fact, use_entity, "use", 0.8)
    _add_provenance(
        session,
        report,
        document_id=fragment.document_id,
        object_type="semantic_fact",
        object_id=fact.id,
        source_type="source_fragment",
        source_id=fragment.id,
        method="mainland_permitted_use_extractor",
        confidence=0.8,
    )


def _enrich_table(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
) -> None:
    cells = (
        session.query(SourceTableCell)
        .filter(SourceTableCell.table_id == table.id)
        .order_by(SourceTableCell.row_index, SourceTableCell.col_index)
        .all()
    )
    if not cells:
        return
    rows = _rows_by_index(cells)
    conventions = current_enrichment_conventions()
    profile_type, row_axis_type, column_axis_type, value_type, confidence = _classify_table(
        table, rows, conventions
    )
    profile = TableSemanticProfile(
        table_id=table.id,
        profile_type=profile_type,
        row_axis_type=row_axis_type,
        column_axis_type=column_axis_type,
        value_type=value_type,
        confidence=confidence,
        review_status=REVIEW_AUTO,
        metadata_json={"caption": table.caption},
    )
    session.add(profile)
    session.flush()
    report.table_profiles += 1
    _add_provenance(
        session,
        report,
        document_id=table.document_id,
        object_type="table_semantic_profile",
        object_id=profile.id,
        source_type="source_table",
        source_id=table.id,
        method="table_profiler",
        confidence=confidence,
    )
    if profile_type == "permission_matrix":
        # ABS-281: recover the ●/circled-number permission markers into
        # ``metadata_json.permission_marker`` now that this table is confirmed a
        # permission matrix. This is the single annotation trigger — the old
        # ingest-time hook (which ran before this profile existed and gated on
        # captions that the corpus doesn't carry) is removed.
        annotate_value_cells(cells, apply=True, conventions=conventions)
        # ABS-520: the parser emits a ragged grid — a cell with no glyph is
        # dropped rather than stored — so the by-law's "blank means not
        # permitted" convention arrives as a missing cell, which retrieval can
        # only report as ``unknown``. Materialize the blanks the geometry can
        # vouch for, before the axis bindings make those intersections
        # addressable.
        audit = densify_permission_matrix(
            session, table, cells, apply=True, conventions=conventions
        )
        if audit.gaps:
            # Keep ``rows`` faithful to the grid now on disk. The fact
            # extractor below skips empty-text cells, so the materialized
            # blanks add no ``permission`` facts either way — retrieval reads
            # the cells, not the facts — but a stale ``rows`` is a trap for
            # whoever changes that pass next.
            session.flush()
            cells = (
                session.query(SourceTableCell)
                .filter(SourceTableCell.table_id == table.id)
                .order_by(SourceTableCell.row_index, SourceTableCell.col_index)
                .all()
            )
            rows = _rows_by_index(cells)
        report.permission_cells_filled += len(audit.gaps)
        report.permission_cells_unfilled += len(audit.refused)
        _extract_permission_table_facts(session, report, cache, table, rows)
    elif profile_type == "parking_matrix":
        _extract_parking_table_facts(session, report, cache, table, rows)
    elif profile_type == "requirement_matrix":
        _extract_requirement_table_facts(session, report, cache, table, rows)
    elif profile_type == "dimensional_matrix":
        _extract_dimensional_table_facts(session, report, cache, table, rows)


_QUOTE_CHARS = "'\"‘’“”`"
_LEADING_QUOTE_RE = re.compile(rf"^[{_QUOTE_CHARS}]\s*")
# Strip qualifying clauses that intervene between the quoted term and "means".
# Real examples from Halifax Mainland LUB:
#   "'X' hereinafter referred to as Y, means ..."
#   '"Height" when applied to a building, means ...'
_QUALIFIER_CLAUSE_RE = re.compile(
    rf"[{_QUOTE_CHARS}]?\s*"
    r"(?:hereinafter\s+referred\s+to\s+as\s+[^,]+"
    r"|when\s+applied\s+to\s+[^,]+)"
    r"\s*,\s*",
    re.IGNORECASE,
)
# Strip a trailing quote that closes the captured term, just before "means".
_TRAILING_QUOTE_BEFORE_MEANS_RE = re.compile(
    rf"[{_QUOTE_CHARS}]\s+means\b"
)


def _normalize_definition_text(text: str) -> str:
    """Strip quoted-term wrapping + qualifying clauses so a "TERM means ..."
    regex can match Halifax Mainland-style definitions (ABS-103).
    """
    out = text.lstrip()
    out = _LEADING_QUOTE_RE.sub("", out, count=1)
    out = _QUALIFIER_CLAUSE_RE.sub(" ", out, count=1)
    out = _TRAILING_QUOTE_BEFORE_MEANS_RE.sub(" means", out, count=1)
    return out


def _extract_definition_fact(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    fragment: SourceFragment,
) -> None:
    normalized_text = _normalize_definition_text(fragment.text)
    match = re.match(r"\s*([A-Z][A-Za-z0-9 /'()-]{1,80}?)\s+means\s+(.+)", normalized_text)
    if not match:
        return
    term = normalize_use(match.group(1))
    if not term:
        return
    entity = _get_or_create_entity(
        session,
        report,
        cache,
        document_id=fragment.document_id,
        entity_type="defined_term",
        canonical_name=term,
        source_text=match.group(1),
        confidence=0.9,
        metadata={"source_fragment_id": fragment.id},
    )
    fact = _create_fact(
        session,
        report,
        document_id=fragment.document_id,
        relation_type="definition",
        subject=entity,
        value_text=fragment.text,
        normalized_value={"term": term, "definition": match.group(2).strip()},
        assertion_type="explicit",
        confidence=0.9,
        metadata={"source_fragment_ids": [fragment.id]},
    )
    _add_participant(session, report, fact, entity, "defined_term", 0.9)
    _add_provenance(
        session,
        report,
        document_id=fragment.document_id,
        object_type="semantic_fact",
        object_id=fact.id,
        source_type="source_fragment",
        source_id=fragment.id,
        method="definition_fragment_extractor",
        confidence=0.9,
    )


def _inherit_sibling_column_bindings(
    session: Session,
    report: SemanticEnrichmentReport,
    tables: list[SourceTable],
) -> None:
    """Propagate zone-column bindings across caption-linked sibling slices.

    ABS-409: a multi-page permission matrix is persisted as several
    ``source_table`` rows sharing one caption parent. Continuation slices
    sometimes carry no repeated header row (Table 1D p56), so per-table
    enrichment can bind their use rows but never their zone columns — the
    slice's cells become unreachable. When siblings share
    ``parent_fragment_id`` and column count, the headered slice's column
    bindings apply verbatim to the headerless one; copy them (at slightly
    reduced confidence, provenance noted).
    """
    by_parent: dict[int, list[SourceTable]] = {}
    for table in tables:
        if table.parent_fragment_id is not None:
            by_parent.setdefault(table.parent_fragment_id, []).append(table)

    for siblings in by_parent.values():
        if len(siblings) < 2:
            continue
        bindings_by_table: dict[int, list[TableAxisBinding]] = {}
        for table in siblings:
            bindings_by_table[table.id] = (
                session.query(TableAxisBinding)
                .filter(
                    TableAxisBinding.table_id == table.id,
                    TableAxisBinding.axis == "column",
                )
                .all()
            )
        donor = next(
            (table for table in siblings if bindings_by_table[table.id]), None
        )
        if donor is None:
            continue
        donor_width = _table_width(session, donor.id)
        for table in siblings:
            if bindings_by_table[table.id]:
                continue
            if _table_width(session, table.id) != donor_width:
                continue
            for source in bindings_by_table[donor.id]:
                binding = TableAxisBinding(
                    table_id=table.id,
                    axis="column",
                    index=source.index,
                    entity_id=source.entity_id,
                    raw_label=source.raw_label,
                    confidence=round(source.confidence * 0.9, 3),
                    metadata_json={"inherited_from_table_id": donor.id},
                )
                session.add(binding)
                report.axis_bindings += 1
            session.flush()


def _table_width(session: Session, table_id: int) -> int:
    from sqlalchemy import func

    return (
        session.query(func.max(SourceTableCell.col_index))
        .filter(SourceTableCell.table_id == table_id)
        .scalar()
        or 0
    )


def _classify_table(
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
    conventions: EnrichmentConventions | None = None,
) -> tuple[str, str | None, str | None, str | None, float]:
    # ABS-284: permission-matrix detection is now profile-driven. ``conventions``
    # carries the active bylaw's encoding (``symbol_matrix`` vs
    # ``section_indexed``) and its amendment-table disqualifier; ``None`` selects
    # the Regional-Centre default so the no-profile path is byte-for-byte
    # unchanged.
    if conventions is None:
        conventions = current_enrichment_conventions()
    caption = (table.caption or "").lower()
    row_labels = [_row_label(cells) for idx, cells in rows.items() if idx > 0]
    headers = [_cell_text(cell) for cell in rows.get(_header_row_index(rows), [])]
    zone_density = _zone_density(headers)
    # ABS-283: a genuine permission/zone matrix carries *several* zone columns.
    # An amendment/section-history table that happens to spill one stray zone
    # code into its header would still clear the density gate when it has few
    # columns, so additionally require an absolute count of zone-bearing data
    # columns (header row, excluding the corner/row-label cell) before the
    # section-indexed branch may fire.
    zone_header_count = sum(1 for header in headers[1:] if extract_zones(header))
    # ABS-283: section/amendment-history tables (e.g. Mainland LUB table 1117
    # header "62(1)", table 1135 "14QB(2) | Sep 1/20 | Nov 7/20 | Case 21162")
    # carry amendment-date and case-number columns that a permission matrix
    # never has. Detecting them vetoes the false-positive permission_matrix
    # classification that the marker backfill would otherwise stamp as garbage.
    amendment_signal = _looks_like_amendment_table(headers, row_labels)
    use_density = sum(1 for label in row_labels if looks_like_use_label(label)) / max(len(row_labels), 1)
    # ABS-105: bylaws that index permission/parking matrices by section
    # number (e.g. Halifax Mainland LUB row labels: "11(1)", "5A", "28AO(1)")
    # don't trigger the use_density signal — the rows aren't use-class
    # named, they're section refs. Track section-label density separately
    # so the classifier can recognize the same "rows × zones → permission"
    # shape on that convention.
    section_label_density = sum(
        1 for label in row_labels if looks_like_section_label(label)
    ) / max(len(row_labels), 1)
    standard_density = sum(1 for text in headers + row_labels if extract_standards(text)) / max(len(headers) + len(row_labels), 1)
    context_density = sum(1 for text in headers + row_labels if extract_development_contexts(text)) / max(
        len(headers) + len(row_labels), 1
    )
    parking_signal = "parking" in caption or any("parking" in text.lower() for text in headers + row_labels)
    # ABS-284: a bylaw that doesn't encode permissions in a symbol-dot matrix
    # (``section_indexed`` — e.g. Halifax Mainland LUB, whose permissions live in
    # section prose) must never classify a table as a permission matrix. Under
    # that encoding the section×zone-shaped tables are amendment/section-history
    # grids, and the prose extractor supplies the permitted uses instead. The
    # amendment-table disqualifier is likewise profile-configurable (default on).
    amendment_disqualifies = (
        conventions.disqualify_amendment_tables and amendment_signal
    )
    # ABS-409: an explicit "permitted uses by zone" caption (supplied by the
    # profile-gated caption-linking pass) is the strongest classification
    # evidence and overrides the density-branch vetoes below. The real
    # Regional Centre matrices trip BOTH vetoes legitimately: their cells
    # carry amendment annotations ("RC-Dec 10/19…") that fire the
    # amendment-table disqualifier, and their use rows include parking-named
    # USES ("Parking structure use") that fire the parking signal — neither
    # makes the table anything other than what its caption declares. A
    # parking-requirements table (Table 15) is excluded because its own
    # caption says "parking".
    caption_declares_permitted_uses = (
        conventions.detects_symbol_matrix
        and "permitted uses by zone" in caption
        and "parking" not in caption
    )
    if caption_declares_permitted_uses or (
        conventions.detects_symbol_matrix
        and (
            (zone_density >= 0.4 and use_density >= 0.35)
            or (
                zone_density >= 0.3
                and section_label_density >= 0.5
                and zone_header_count >= 2
            )
        )
        and not parking_signal
        and not amendment_disqualifies
    ):
        return "permission_matrix", "use", "zone", "permission_marker", 0.9
    if parking_signal and (zone_density >= 0.2 or standard_density >= 0.2):
        return "parking_matrix", "use", "standard", "requirement", 0.75
    if standard_density >= 0.2 and context_density >= 0.1:
        return "requirement_matrix", "standard", "development_context", "requirement", 0.78
    if standard_density >= 0.2 and (zone_density >= 0.2 or any(extract_zones(label) for label in row_labels)):
        return "dimensional_matrix", "zone", "standard", "numeric_or_text", 0.8
    if len(rows) <= 8 and all(len(cells) <= 2 for cells in rows.values()):
        return "key_value_table", "key", "value", "text", 0.55
    return "unknown", None, None, None, 0.4


def _extract_permission_table_facts(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> None:
    header_idx = _header_row_index(rows)
    header_cells = rows.get(header_idx, [])
    column_entities: dict[int, SemanticEntity] = {}

    def _bind_column_zone(
        col_index: int,
        zone_code: str,
        raw_label: str,
        cell: SourceTableCell,
        confidence: float,
    ) -> None:
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="zone",
            canonical_name=zone_code,
            source_text=raw_label,
            confidence=confidence,
            metadata={"source_table_id": table.id, "source_table_cell_id": cell.id},
        )
        column_entities[col_index] = entity
        _add_axis_binding(
            session, report, table, "column", col_index, entity, raw_label, confidence
        )

    # 1. Primary: zone codes sitting in the detected header row.
    for cell in header_cells[1:]:
        zones = extract_zones(cell.text)
        if not zones:
            # FR5: a populated header column we couldn't map to a zone is logged
            # rather than silently dropped, so it surfaces for later review.
            if _cell_text(cell):
                report.warnings.append(
                    f"table {table.id}: unmapped column header "
                    f"{_cell_text(cell)!r} at col {cell.col_index} "
                    "(no zone code resolved)"
                )
            continue
        _bind_column_zone(cell.col_index, zones[0], cell.text, cell, 0.95)

    # 2. ABS-278 Defect C: header-bleed correction. Some source tables spill a
    # zone-header band into the body (e.g. table 1057 rows 8–9), so bare zone
    # codes appear as data-cell values. Promote those to column bindings for any
    # column still missing a zone, and flag the offending cells so they are not
    # read as permission markers.
    _correct_header_bleed(
        session, report, table, rows, header_idx, column_entities, _bind_column_zone
    )

    for row_index, row_cells in rows.items():
        if row_index == header_idx or _is_repeated_header_row(row_cells):
            continue
        row_label = _row_label(row_cells)
        is_use_label = looks_like_use_label(row_label)
        is_section_label = (not is_use_label) and looks_like_section_label(row_label)
        if not (is_use_label or is_section_label):
            # FR5: a non-empty row label that maps to neither a use class nor a
            # section reference is logged for review instead of dropped.
            if row_label:
                report.warnings.append(
                    f"table {table.id}: unmapped row label {row_label!r} at "
                    f"row {row_index} (neither use nor section label)"
                )
            continue
        # ABS-105: when the row is a section-number label (no use-class
        # wording), still create a "use" entity but mark it section-keyed
        # so downstream consumers know this is a placeholder pending
        # resolution to the section's defined_term.
        if is_section_label:
            canonical_use_name = f"section:{row_label.strip()}"
            row_confidence = 0.7
        else:
            canonical_use_name = normalize_use(row_label)
            row_confidence = 0.9
        use_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="use",
            canonical_name=canonical_use_name,
            source_text=row_label,
            confidence=row_confidence,
            metadata={
                "source_table_id": table.id,
                "row_index": row_index,
                "section_keyed": is_section_label,
            },
        )
        _add_axis_binding(session, report, table, "row", row_index, use_entity, row_label, row_confidence)
        for cell in row_cells[1:]:
            # ABS-278 Defect C: a bare zone code sitting in a use row's data
            # region is bled header content, not a permission value. Re-attribute
            # it to the column axis and flag the cell so it no longer reports the
            # zone code as its value.
            if (cell.metadata_json or {}).get("header_bleed") or _is_pure_zone_token(cell.text):
                _flag_header_bleed_cell(
                    session, report, table, cell, column_entities, _bind_column_zone
                )
                continue
            marker = cell.text.strip()
            if not marker:
                continue
            zone_entity = column_entities.get(cell.col_index)
            if zone_entity is None:
                continue
            conditions = [
                _get_or_create_entity(
                    session,
                    report,
                    cache,
                    document_id=table.document_id,
                    entity_type="condition_ref",
                    canonical_name=condition,
                    source_text=condition,
                    confidence=0.9,
                    metadata={"source_table_cell_id": cell.id},
                )
                for condition in extract_condition_refs(marker)
            ]
            normalized = _normalize_permission_marker(marker)
            fact = _create_fact(
                session,
                report,
                document_id=table.document_id,
                relation_type="permission",
                subject=use_entity,
                scope=zone_entity,
                value_text=marker,
                normalized_value=normalized,
                assertion_type="inferred_from_legend" if normalized["permission"] != "unknown" else "explicit",
                confidence=0.88,
                metadata={
                    "source_table_id": table.id,
                    "source_table_cell_ids": [cell.id],
                    "row_index": row_index,
                    "col_index": cell.col_index,
                },
            )
            _add_participant(session, report, fact, use_entity, "use", 0.9)
            _add_participant(session, report, fact, zone_entity, "zone", 0.95)
            for condition in conditions:
                _add_participant(session, report, fact, condition, "condition", 0.9)
                _add_edge(session, report, table.document_id, fact, "conditioned_by", condition, [cell.id], 0.85)
            _add_provenance(
                session,
                report,
                document_id=table.document_id,
                object_type="semantic_fact",
                object_id=fact.id,
                source_type="source_table_cell",
                source_id=cell.id,
                method="permission_matrix_fact_extractor",
                confidence=0.88,
            )


def _extract_dimensional_table_facts(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> None:
    header_idx = _header_row_index(rows)
    standard_entities: dict[int, SemanticEntity] = {}
    for cell in rows.get(header_idx, [])[1:]:
        standards = extract_standards(cell.text)
        if not standards:
            continue
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="standard",
            canonical_name=standards[0],
            source_text=cell.text,
            confidence=0.85,
            metadata={"source_table_cell_id": cell.id},
        )
        standard_entities[cell.col_index] = entity
        _add_axis_binding(session, report, table, "column", cell.col_index, entity, cell.text, 0.85)
    for row_index, row_cells in rows.items():
        if row_index == header_idx:
            continue
        row_label = _row_label(row_cells)
        zones = extract_zones(row_label)
        if not zones:
            continue
        zone_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="zone",
            canonical_name=zones[0],
            source_text=row_label,
            confidence=0.9,
            metadata={"source_table_id": table.id, "row_index": row_index},
        )
        _add_axis_binding(session, report, table, "row", row_index, zone_entity, row_label, 0.9)
        for cell in row_cells[1:]:
            value = cell.text.strip()
            standard_entity = standard_entities.get(cell.col_index)
            if not value or standard_entity is None:
                continue
            fact = _create_fact(
                session,
                report,
                document_id=table.document_id,
                relation_type="dimensional_standard",
                subject=standard_entity,
                scope=zone_entity,
                value_text=value,
                normalized_value={"values": extract_numeric_values(value), "raw": value},
                assertion_type="explicit",
                confidence=0.82,
                metadata={"source_table_id": table.id, "source_table_cell_ids": [cell.id]},
            )
            _add_participant(session, report, fact, standard_entity, "standard", 0.85)
            _add_participant(session, report, fact, zone_entity, "zone", 0.9)
            _add_provenance(
                session,
                report,
                document_id=table.document_id,
                object_type="semantic_fact",
                object_id=fact.id,
                source_type="source_table_cell",
                source_id=cell.id,
                method="dimensional_matrix_fact_extractor",
                confidence=0.82,
            )


def _extract_parking_table_facts(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> None:
    header_idx = _header_row_index(rows)
    header_cells = rows.get(header_idx, [])
    zone_entities: dict[int, SemanticEntity] = {}
    for cell in header_cells[1:]:
        zones = extract_zones(cell.text)
        if not zones:
            continue
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="zone",
            canonical_name=zones[0],
            source_text=cell.text,
            confidence=0.9,
            metadata={"source_table_id": table.id, "source_table_cell_id": cell.id},
        )
        zone_entities[cell.col_index] = entity
        _add_axis_binding(session, report, table, "column", cell.col_index, entity, cell.text, 0.9)
    parking_standard = _get_or_create_entity(
        session,
        report,
        cache,
        document_id=table.document_id,
        entity_type="standard",
        canonical_name="parking spaces",
        source_text=table.caption or "parking spaces",
        confidence=0.85,
        metadata={"source_table_id": table.id},
    )
    for row_index, row_cells in rows.items():
        if row_index == header_idx or _is_repeated_header_row(row_cells):
            continue
        row_label = _row_label(row_cells)
        is_use_label = looks_like_use_label(row_label)
        is_section_label = (not is_use_label) and looks_like_section_label(row_label)
        if not (is_use_label or is_section_label):
            continue
        # ABS-105: same section-keyed placeholder treatment as in the
        # permission_matrix extractor — parking tables in Mainland Halifax
        # LUB are indexed by section number, not use-class name.
        if is_section_label:
            canonical_use_name = f"section:{row_label.strip()}"
            row_confidence = 0.7
        else:
            canonical_use_name = normalize_use(row_label)
            row_confidence = 0.88
        use_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="use",
            canonical_name=canonical_use_name,
            source_text=row_label,
            confidence=row_confidence,
            metadata={
                "source_table_id": table.id,
                "row_index": row_index,
                "section_keyed": is_section_label,
            },
        )
        _add_axis_binding(session, report, table, "row", row_index, use_entity, row_label, row_confidence)
        for cell in row_cells[1:]:
            value = cell.text.strip()
            zone_entity = zone_entities.get(cell.col_index)
            if not value or zone_entity is None:
                continue
            fact = _create_fact(
                session,
                report,
                document_id=table.document_id,
                relation_type="parking_standard",
                subject=use_entity,
                scope=zone_entity,
                value_text=value,
                normalized_value={"values": extract_numeric_values(value), "raw": value},
                assertion_type="explicit",
                confidence=0.84,
                metadata={
                    "source_table_id": table.id,
                    "source_table_cell_ids": [cell.id],
                    "row_index": row_index,
                    "col_index": cell.col_index,
                },
            )
            _add_participant(session, report, fact, use_entity, "use", 0.88)
            _add_participant(session, report, fact, zone_entity, "zone", 0.9)
            _add_participant(session, report, fact, parking_standard, "standard", 0.85)
            _add_provenance(
                session,
                report,
                document_id=table.document_id,
                object_type="semantic_fact",
                object_id=fact.id,
                source_type="source_table_cell",
                source_id=cell.id,
                method="parking_matrix_fact_extractor",
                confidence=0.84,
            )


def _extract_requirement_table_facts(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
) -> None:
    header_idx = _header_row_index(rows)
    header_cells = rows.get(header_idx, [])
    context_entities: dict[int, SemanticEntity] = {}
    for cell in header_cells[1:]:
        contexts = extract_development_contexts(cell.text)
        if not contexts:
            contexts = [re.sub(r"\s+", " ", cell.text.strip().lower())] if cell.text.strip() else []
        if not contexts:
            continue
        entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="development_context",
            canonical_name=contexts[0],
            source_text=cell.text,
            confidence=0.82,
            metadata={"source_table_id": table.id, "source_table_cell_id": cell.id},
        )
        context_entities[cell.col_index] = entity
        _add_axis_binding(session, report, table, "column", cell.col_index, entity, cell.text, 0.82)
    for row_index, row_cells in rows.items():
        if row_index == header_idx:
            continue
        row_label = _row_label(row_cells)
        standards = extract_standards(row_label)
        contexts = extract_development_contexts(row_label)
        if not standards and not contexts:
            continue
        standard_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=table.document_id,
            entity_type="standard",
            canonical_name=standards[0] if standards else contexts[0],
            source_text=row_label,
            confidence=0.82,
            metadata={"source_table_id": table.id, "row_index": row_index},
        )
        _add_axis_binding(session, report, table, "row", row_index, standard_entity, row_label, 0.82)
        for row_context in contexts:
            context_entity = _get_or_create_entity(
                session,
                report,
                cache,
                document_id=table.document_id,
                entity_type="development_context",
                canonical_name=row_context,
                source_text=row_label,
                confidence=0.76,
                metadata={"source_table_id": table.id, "row_index": row_index},
            )
            _add_axis_binding(session, report, table, "row", row_index, context_entity, row_label, 0.76)
        for cell in row_cells[1:]:
            value = cell.text.strip()
            if not value:
                continue
            context_entity = context_entities.get(cell.col_index)
            if context_entity is None:
                continue
            condition_entities = [
                _get_or_create_entity(
                    session,
                    report,
                    cache,
                    document_id=table.document_id,
                    entity_type="condition_ref",
                    canonical_name=condition,
                    source_text=condition,
                    confidence=0.86,
                    metadata={"source_table_cell_id": cell.id},
                )
                for condition in extract_condition_refs(value)
            ]
            fact = _create_fact(
                session,
                report,
                document_id=table.document_id,
                relation_type="requirement",
                subject=standard_entity,
                scope=context_entity,
                value_text=value,
                normalized_value={"values": extract_numeric_values(value), "raw": value},
                assertion_type="explicit",
                confidence=0.8,
                metadata={
                    "source_table_id": table.id,
                    "source_table_cell_ids": [cell.id],
                    "row_index": row_index,
                    "col_index": cell.col_index,
                },
            )
            _add_participant(session, report, fact, standard_entity, "standard", 0.82)
            _add_participant(session, report, fact, context_entity, "development_context", 0.82)
            for row_context in contexts:
                row_context_entity = _get_or_create_entity(
                    session,
                    report,
                    cache,
                    document_id=table.document_id,
                    entity_type="development_context",
                    canonical_name=row_context,
                    source_text=row_label,
                    confidence=0.76,
                    metadata={"source_table_id": table.id, "row_index": row_index},
                )
                _add_participant(session, report, fact, row_context_entity, "development_context", 0.76)
            for condition in condition_entities:
                _add_participant(session, report, fact, condition, "condition", 0.86)
                _add_edge(session, report, table.document_id, fact, "conditioned_by", condition, [cell.id], 0.8)
            _add_provenance(
                session,
                report,
                document_id=table.document_id,
                object_type="semantic_fact",
                object_id=fact.id,
                source_type="source_table_cell",
                source_id=cell.id,
                method="requirement_matrix_fact_extractor",
                confidence=0.8,
            )


def _enrich_cross_references(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    *,
    document_id: int,
) -> None:
    for ref in session.query(CrossReference).filter(CrossReference.document_id == document_id).all():
        source_entity = _get_or_create_entity(
            session,
            report,
            cache,
            document_id=document_id,
            entity_type="section_ref",
            canonical_name=ref.raw_reference_text,
            source_text=ref.raw_reference_text,
            confidence=0.75,
            metadata={"cross_reference_id": ref.id},
        )
        target_entity = None
        if ref.target_citation_guess:
            target_entity = _get_or_create_entity(
                session,
                report,
                cache,
                document_id=document_id,
                entity_type="section_ref",
                canonical_name=ref.target_citation_guess,
                source_text=ref.target_citation_guess,
                confidence=0.7,
                metadata={"cross_reference_id": ref.id},
            )
        edge = SemanticEdge(
            document_id=document_id,
            source_entity_id=source_entity.id,
            edge_type="references",
            target_entity_id=target_entity.id if target_entity else None,
            source_fragment_ids_json=[ref.source_fragment_id],
            confidence=ref.confidence,
            metadata_json={"cross_reference_id": ref.id},
        )
        session.add(edge)
        session.flush()
        report.edges += 1
        _add_provenance(
            session,
            report,
            document_id=document_id,
            object_type="semantic_edge",
            object_id=edge.id,
            source_type="cross_reference",
            source_id=ref.id,
            method="cross_reference_edge_extractor",
            confidence=ref.confidence,
        )


def _get_or_create_entity(
    session: Session,
    report: SemanticEnrichmentReport,
    cache: dict[tuple[str, str], SemanticEntity],
    *,
    document_id: int,
    entity_type: str,
    canonical_name: str,
    source_text: str | None,
    confidence: float,
    metadata: dict,
) -> SemanticEntity:
    canonical_name = canonical_name.strip()
    key = (entity_type, canonical_name.lower())
    if key in cache:
        return cache[key]
    existing = (
        session.query(SemanticEntity)
        .filter(
            SemanticEntity.document_id == document_id,
            SemanticEntity.entity_type == entity_type,
            SemanticEntity.canonical_name == canonical_name,
        )
        .first()
    )
    if existing:
        cache[key] = existing
        return existing
    entity = SemanticEntity(
        document_id=document_id,
        entity_type=entity_type,
        canonical_name=canonical_name,
        aliases_json=[],
        source_text=source_text,
        confidence=confidence,
        review_status=REVIEW_AUTO,
        metadata_json=metadata,
    )
    session.add(entity)
    session.flush()
    report.entities += 1
    cache[key] = entity
    return entity


def _create_fact(
    session: Session,
    report: SemanticEnrichmentReport,
    *,
    document_id: int,
    relation_type: str,
    subject: SemanticEntity | None = None,
    object_: SemanticEntity | None = None,
    scope: SemanticEntity | None = None,
    value_text: str | None,
    normalized_value: dict,
    assertion_type: str,
    confidence: float,
    metadata: dict,
) -> SemanticFact:
    fact = SemanticFact(
        document_id=document_id,
        relation_type=relation_type,
        primary_subject_entity_id=subject.id if subject else None,
        primary_object_entity_id=object_.id if object_ else None,
        primary_scope_entity_id=scope.id if scope else None,
        value_text=value_text,
        normalized_value_json=normalized_value,
        assertion_type=assertion_type,
        confidence=confidence,
        review_status=REVIEW_AUTO,
        metadata_json=metadata,
    )
    session.add(fact)
    session.flush()
    report.facts += 1
    return fact


def _add_participant(
    session: Session,
    report: SemanticEnrichmentReport,
    fact: SemanticFact,
    entity: SemanticEntity,
    role: str,
    confidence: float,
) -> None:
    session.add(
        SemanticFactParticipant(
            fact_id=fact.id,
            entity_id=entity.id,
            role=role,
            confidence=confidence,
            metadata_json={},
        )
    )
    report.fact_participants += 1


def _add_axis_binding(
    session: Session,
    report: SemanticEnrichmentReport,
    table: SourceTable,
    axis: str,
    index: int,
    entity: SemanticEntity,
    raw_label: str,
    confidence: float,
) -> None:
    # FR5: bindings at/below the review threshold are flagged (not dropped) so a
    # reviewer can confirm or correct the low-confidence resolution later.
    metadata: dict = {}
    if confidence <= AXIS_REVIEW_CONFIDENCE_THRESHOLD:
        metadata["review"] = REVIEW_NEEDS
    binding = TableAxisBinding(
        table_id=table.id,
        axis=axis,
        index=index,
        entity_id=entity.id,
        raw_label=raw_label,
        confidence=confidence,
        metadata_json=metadata,
    )
    session.add(binding)
    session.flush()
    report.axis_bindings += 1
    _add_provenance(
        session,
        report,
        document_id=table.document_id,
        object_type="table_axis_binding",
        object_id=binding.id,
        source_type="source_table",
        source_id=table.id,
        method="table_axis_binder",
        confidence=confidence,
    )


def _add_edge(
    session: Session,
    report: SemanticEnrichmentReport,
    document_id: int,
    source_fact: SemanticFact,
    edge_type: str,
    target_entity: SemanticEntity,
    source_ids: list[int],
    confidence: float,
) -> None:
    edge = SemanticEdge(
        document_id=document_id,
        source_fact_id=source_fact.id,
        edge_type=edge_type,
        target_entity_id=target_entity.id,
        source_fragment_ids_json=[],
        confidence=confidence,
        metadata_json={"source_table_cell_ids": source_ids},
    )
    session.add(edge)
    report.edges += 1


def _add_provenance(
    session: Session,
    report: SemanticEnrichmentReport,
    *,
    document_id: int,
    object_type: str,
    object_id: int,
    source_type: str,
    source_id: int | None,
    method: str,
    confidence: float | None,
) -> None:
    session.add(
        SemanticProvenance(
            document_id=document_id,
            object_type=object_type,
            object_id=object_id,
            source_type=source_type,
            source_id=source_id,
            extraction_method=method,
            extractor_version=EXTRACTOR_VERSION,
            confidence=confidence,
            metadata_json={},
        )
    )
    report.provenance += 1


def _rows_by_index(cells: Iterable[SourceTableCell]) -> dict[int, list[SourceTableCell]]:
    rows: dict[int, list[SourceTableCell]] = defaultdict(list)
    for cell in cells:
        rows[cell.row_index].append(cell)
    return {idx: sorted(row, key=lambda item: item.col_index) for idx, row in sorted(rows.items())}


def _header_row_index(rows: dict[int, list[SourceTableCell]]) -> int:
    scores = {}
    for row_index, row_cells in rows.items():
        labels = [_cell_text(cell) for cell in row_cells]
        scores[row_index] = len(
            [label for label in labels if extract_zones(label) or extract_standards(label) or extract_development_contexts(label)]
        )
    return max(scores, key=scores.get) if scores else 0


def _row_label(row_cells: list[SourceTableCell]) -> str:
    if not row_cells:
        return ""
    return _cell_text(sorted(row_cells, key=lambda item: item.col_index)[0])


def _cell_text(cell: SourceTableCell) -> str:
    return re.sub(r"\s+", " ", cell.text or "").strip()


def _zone_density(labels: list[str]) -> float:
    return sum(1 for label in labels if extract_zones(label)) / max(len(labels), 1)


# ABS-283: amendment/section-history table detection. Mainland LUB carries
# tables that track when a section was amended — their columns are amendment
# dates ("Sep 1/20", "Nov 7/20") and council case numbers ("Case 21162"),
# their rows section numbers ("62(1)", "14QB(2)"). The structural classifier
# previously mistook these for section-indexed permission matrices; these
# patterns let it veto that. A real permission matrix never carries an
# amendment-date or case-number column, so even a couple of hits is decisive.
_AMENDMENT_DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?\s*\d{1,2}\s*/\s*\d{2,4}\b",
    re.IGNORECASE,
)
_AMENDMENT_CASE_RE = re.compile(r"\bCase\s*(?:No\.?\s*)?\d{3,}\b", re.IGNORECASE)


def _looks_like_amendment_cell(text: str) -> bool:
    """True when a cell is an amendment-date or council-case-number column."""
    if not text:
        return False
    return bool(_AMENDMENT_DATE_RE.search(text) or _AMENDMENT_CASE_RE.search(text))


def _looks_like_amendment_table(
    headers: list[str], row_labels: list[str]
) -> bool:
    """True when a table's labels expose it as an amendment/section-history grid.

    Fires when at least two label cells carry amendment markers, or when a
    single marker accounts for a meaningful share of a small label set — either
    way a signal a permission matrix would never produce.
    """
    cells = [text for text in headers + row_labels if text]
    if not cells:
        return False
    hits = sum(1 for text in cells if _looks_like_amendment_cell(text))
    return hits >= 2 or (hits >= 1 and hits / len(cells) >= 0.25)


def _is_repeated_header_row(row_cells: list[SourceTableCell]) -> bool:
    labels = [_cell_text(cell) for cell in row_cells]
    return len(labels) > 2 and _zone_density(labels[1:]) >= 0.5


def _is_pure_zone_token(text: str | None) -> bool:
    """True when ``text`` is *only* a single zone code (e.g. ``"DH"``, ``"CEN-2"``).

    A permission-matrix data cell should hold a marker (●, a circled number,
    blank, a condition ref) — never a bare zone code. When it does, that's
    header-bleed: a zone header spilled into the body (ABS-278 Defect C).
    """
    stripped = re.sub(r"\s+", " ", (text or "")).strip()
    if not stripped:
        return False
    zones = extract_zones(stripped)
    return len(zones) == 1 and normalize_zone(stripped) == zones[0]


def _row_is_bled_zone_header(row_cells: list[SourceTableCell]) -> bool:
    """True when a body row is really a mis-attributed zone-header band.

    Fires when the majority of the row's non-empty data cells (everything past
    the row-label column) are bare zone codes — the signature of a header that
    spilled into the table body.
    """
    data = sorted(row_cells, key=lambda item: item.col_index)[1:]
    nonempty = [cell for cell in data if _cell_text(cell)]
    if len(nonempty) < 2:
        return False
    pure = [cell for cell in nonempty if _is_pure_zone_token(cell.text)]
    return len(pure) / len(nonempty) >= 0.6


def _flag_header_bleed_cell(
    session: Session,
    report: SemanticEnrichmentReport,
    table: SourceTable,
    cell: SourceTableCell,
    column_entities: dict[int, SemanticEntity],
    bind_column_zone,
) -> None:
    """Re-attribute a bled zone-code cell to its column axis and flag it.

    Marks ``metadata_json.header_bleed`` so downstream consumers (retrieval,
    fact extraction) skip the cell as a value, records the recovered zone, and —
    if the column has no zone yet — promotes the code to a low-confidence column
    binding flagged for review.
    """
    zones = extract_zones(cell.text or "")
    if not zones:
        return
    zone_code = zones[0]
    meta = dict(cell.metadata_json or {})
    if not meta.get("header_bleed") or meta.get("recovered_zone") != zone_code:
        meta["header_bleed"] = True
        meta["recovered_zone"] = zone_code
        cell.metadata_json = meta
    if cell.col_index not in column_entities:
        bind_column_zone(
            cell.col_index, zone_code, cell.text, cell, HEADER_BLEED_CONFIDENCE
        )
        report.warnings.append(
            f"table {table.id}: recovered header-bleed zone {zone_code!r} for "
            f"column {cell.col_index} (cell row {cell.row_index})"
        )


def _correct_header_bleed(
    session: Session,
    report: SemanticEnrichmentReport,
    table: SourceTable,
    rows: dict[int, list[SourceTableCell]],
    header_idx: int,
    column_entities: dict[int, SemanticEntity],
    bind_column_zone,
) -> None:
    """Promote zone codes from fully-bled body rows to column bindings.

    Per-cell bleed inside genuine use rows is handled inline in the marker loop;
    this pass catches whole rows that are really repeated/mis-placed zone-header
    bands (which the use-row loop skips via :func:`_is_repeated_header_row`), so
    their zone codes would otherwise be lost entirely.
    """
    for row_index, row_cells in rows.items():
        if row_index == header_idx:
            continue
        if not _row_is_bled_zone_header(row_cells):
            continue
        for cell in sorted(row_cells, key=lambda item: item.col_index)[1:]:
            if not _is_pure_zone_token(cell.text):
                continue
            _flag_header_bleed_cell(
                session, report, table, cell, column_entities, bind_column_zone
            )


def _use_row_bindings(
    session: Session, table_id: int
) -> list[tuple[TableAxisBinding, str]]:
    """Return ``(binding, canonical_name)`` for every use-row axis binding.

    Section-keyed placeholder rows (ABS-105 — canonical ``"section:..."``) are
    excluded: they aren't real use classes, so they must never be a fuzzy-match
    target for a permitted-use query.
    """
    rows = (
        session.query(TableAxisBinding, SemanticEntity.canonical_name)
        .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
        .filter(
            TableAxisBinding.table_id == table_id,
            TableAxisBinding.axis == "row",
            SemanticEntity.entity_type == "use",
        )
        .order_by(TableAxisBinding.confidence.desc())
        .all()
    )
    return [
        (binding, name)
        for binding, name in rows
        if name and not name.startswith("section:")
    ]


def _resolve_use_row_by_key(
    session: Session, table_id: int, use_name: str
) -> TableAxisBinding | None:
    """Resolve a use row by normalized-key equivalence (ABS-351).

    Returns the highest-confidence row binding whose canonical name is a
    :func:`~layer1.semantic.use_matching.use_match_key` equivalent of
    ``use_name``, or ``None`` when the match is absent or ambiguous. Only the
    unambiguous, deterministic key match resolves here — near misses that merely
    *overlap* several rows are left for the caller to surface as suggestions.
    """
    candidates = _use_row_bindings(session, table_id)
    if not candidates:
        return None
    match = match_use(use_name, [name for _binding, name in candidates])
    if match.resolved is None:
        return None
    # Bindings are pre-sorted by descending confidence, so the first match wins.
    for binding, name in candidates:
        if name == match.resolved:
            return binding
    return None


def use_row_labels(session: Session, table_ids: list[int]) -> list[str]:
    """Distinct human-readable use-row labels across ``table_ids`` (ABS-351).

    Feeds the advisory suggestion ranking when a ``(use, zone)`` query lands on
    an unknown use: the caller ranks these labels against the query so a failed
    lookup carries the closest real row names back. Prefers the row's
    ``raw_label`` (what the bylaw actually prints, e.g. "Multi-unit dwelling
    use") over the lowercased canonical name, and skips section-keyed
    placeholders.
    """
    if not table_ids:
        return []
    rows = (
        session.query(TableAxisBinding.raw_label, SemanticEntity.canonical_name)
        .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
        .filter(
            TableAxisBinding.table_id.in_(table_ids),
            TableAxisBinding.axis == "row",
            SemanticEntity.entity_type == "use",
        )
        .all()
    )
    labels: list[str] = []
    for raw_label, canonical_name in rows:
        if not canonical_name or canonical_name.startswith("section:"):
            continue
        label = (raw_label or "").strip() or canonical_name
        labels.append(label)
    return list(dict.fromkeys(labels))


def resolve_permission_cell(
    session: Session,
    *,
    table_id: int,
    use_name: str,
    zone: str,
) -> dict | None:
    """Resolve a (use, zone) pair to the addressed permission-matrix cell.

    Uses the bound axes (ABS-278): the use resolves a ``row`` binding, the zone
    resolves a ``column`` binding, and their indices address the cell. Returns
    ``None`` when either axis can't be resolved. The returned dict carries the
    resolved indices plus the cell's recovered ``permission_marker`` so callers
    don't need a second query.

    ABS-483: when both axes bind but the grid has **no cell** at their
    intersection, the marker is ``unknown`` — the row was lost in extraction,
    which is not the same claim as "the bylaw prohibits this". A cell that
    exists but carries no annotation still reports ``None`` so the caller's
    on-the-fly classification of ``cell_text`` stays in charge.
    """
    use_norm = normalize_use(use_name)
    zone_norm = normalize_zone(zone)
    # ABS-282: bound use entities carry whatever ``normalize_use`` produced for
    # the raw row label — e.g. "Home occupation use" -> "home occupation use" —
    # but a query may arrive bare ("home occupation"). ``normalize_use`` only
    # appends "use" for aliased single-word uses, so a bare multi-word query
    # misses the binding. Match the canonical name against both the given form
    # and its "<...> use" / de-suffixed counterpart so either phrasing resolves.
    use_candidates = {use_norm}
    if use_norm.endswith(" use"):
        use_candidates.add(use_norm[: -len(" use")])
    else:
        use_candidates.add(f"{use_norm} use")
    row_binding = (
        session.query(TableAxisBinding)
        .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
        .filter(
            TableAxisBinding.table_id == table_id,
            TableAxisBinding.axis == "row",
            SemanticEntity.entity_type == "use",
            SemanticEntity.canonical_name.in_(use_candidates),
        )
        .order_by(TableAxisBinding.confidence.desc())
        .first()
    )
    # ABS-351: an exact/aliased miss falls back to a normalized-key match against
    # this matrix's bound use rows, so near-miss phrasings ("Multiple-unit
    # dwelling", "multi unit dwelling") resolve to their canonical row
    # ("Multi-unit dwelling use") without the caller burning a tool iteration
    # guessing the spelling. This resolves ONLY on a deterministic key
    # equivalence (see ``use_match_key``); genuinely ambiguous terms are left for
    # the caller to surface as advisory suggestions rather than picked here.
    if row_binding is None:
        row_binding = _resolve_use_row_by_key(session, table_id, use_name)
    col_binding = (
        session.query(TableAxisBinding)
        .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
        .filter(
            TableAxisBinding.table_id == table_id,
            TableAxisBinding.axis == "column",
            SemanticEntity.entity_type == "zone",
            SemanticEntity.canonical_name == zone_norm,
        )
        .order_by(TableAxisBinding.confidence.desc())
        .first()
    )
    if row_binding is None or col_binding is None:
        return None
    cell = (
        session.query(SourceTableCell)
        .filter(
            SourceTableCell.table_id == table_id,
            SourceTableCell.row_index == row_binding.index,
            SourceTableCell.col_index == col_binding.index,
        )
        .first()
    )
    meta = (cell.metadata_json or {}) if cell is not None else {}
    cell_text = cell.text if cell is not None else None
    return {
        "table_id": table_id,
        "row_index": row_binding.index,
        "col_index": col_binding.index,
        "use": use_norm,
        "zone": zone_norm,
        "cell_text": cell_text,
        "permission_marker": (
            meta.get("permission_marker") if cell is not None else UNKNOWN
        ),
        "footnote": meta.get("footnote"),
        # ABS-523: a cell may print several markers ("⑮ ㉒") and every one of
        # them binds. ``footnote`` is the first and is kept only for callers
        # that predate the list.
        "footnotes": cell_footnotes(meta, cell_text),
    }


def enumerate_permission_column(
    session: Session,
    *,
    table_id: int,
    zone: str,
) -> list[dict] | None:
    """Enumerate every (use, permission) pair in a zone's matrix column (ABS-409).

    The per-cell resolver (:func:`resolve_permission_cell`) answers "is THIS
    use permitted?"; this walks the whole column so callers can answer "what
    uses are permitted in this zone?" without one round-trip per row.

    Returns ``None`` when the zone doesn't bind a column on ``table_id``
    (caller falls through to the next table), else a list of
    ``{"use_label", "permission", "footnote_ordinal", "footnote_ordinals"}``
    dicts in row order. ``footnote_ordinals`` is every marker in the cell
    (ABS-523); ``footnote_ordinal`` is the first of them, lossy, retained for
    callers that predate the list.
    ``permission`` is ``permitted`` / ``conditional`` / ``not_permitted``, or
    ``unknown`` when the bound row has no cell in this column at all (ABS-483 —
    an extraction gap, reported as such instead of as a prohibition).
    Skips ``section:``-keyed placeholder rows (as :func:`use_row_labels`
    does). Zone matching is hardened for the corpus as ingested: bound
    ``raw_label`` values may carry trailing whitespace ("CEN-1  ") and
    grouped columns bind one canonical entity for a comma-separated label
    ("DD, DH, CEN-2, …") — a comma-token match catches the zones the single
    canonical binding misses.
    """
    zone_norm = normalize_zone(zone)
    column_bindings = (
        session.query(TableAxisBinding)
        .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
        .filter(
            TableAxisBinding.table_id == table_id,
            TableAxisBinding.axis == "column",
            SemanticEntity.entity_type == "zone",
        )
        .order_by(TableAxisBinding.confidence.desc())
        .all()
    )
    col_binding = None
    for binding in column_bindings:
        entity = session.get(SemanticEntity, binding.entity_id)
        canonical = (entity.canonical_name or "") if entity is not None else ""
        if canonical == zone_norm:
            col_binding = binding
            break
        raw_tokens = {
            normalize_zone(token.strip())
            for token in (binding.raw_label or "").split(",")
            if token.strip()
        }
        if zone_norm in raw_tokens:
            col_binding = binding
            break
    if col_binding is None:
        return None

    row_bindings = (
        session.query(TableAxisBinding, SemanticEntity.canonical_name)
        .join(SemanticEntity, SemanticEntity.id == TableAxisBinding.entity_id)
        .filter(
            TableAxisBinding.table_id == table_id,
            TableAxisBinding.axis == "row",
            SemanticEntity.entity_type == "use",
        )
        .order_by(TableAxisBinding.index)
        .all()
    )
    rows = [
        (binding, (binding.raw_label or "").strip() or canonical_name)
        for binding, canonical_name in row_bindings
        if canonical_name and not canonical_name.startswith("section:")
    ]
    if not rows:
        return []

    row_indices = [binding.index for binding, _label in rows]
    cells_by_row = {
        cell.row_index: cell
        for cell in session.query(SourceTableCell)
        .filter(
            SourceTableCell.table_id == table_id,
            SourceTableCell.col_index == col_binding.index,
            SourceTableCell.row_index.in_(row_indices),
        )
        .all()
    }

    results: list[dict] = []
    seen_labels: set[str] = set()
    for binding, label in rows:
        if label in seen_labels:
            continue
        seen_labels.add(label)
        cell = cells_by_row.get(binding.index)
        meta = (cell.metadata_json or {}) if cell is not None else {}
        cell_text = cell.text if cell is not None else None
        marker = meta.get("permission_marker")
        footnote = meta.get("footnote")
        if marker is None:
            # ABS-483: a bound row with NO cell in the grid classifies as
            # ``unknown`` (pass None, not ""), so a row extraction dropped is
            # never reported as the bylaw prohibiting the use. A cell that is
            # present and blank still classifies as ``not_permitted``.
            classified = classify_permission_marker(cell_text)
            marker = classified.get("permission_marker")
            footnote = classified.get("footnote", footnote)
        # ABS-523: every marker in the cell, not just the one that happened to
        # print first — see ``cell_footnotes``.
        footnotes = cell_footnotes(meta, cell_text) if marker == "conditional" else []
        results.append(
            {
                "use_label": label,
                "permission": marker,
                "footnote_ordinal": footnote,
                "footnote_ordinals": footnotes,
            }
        )
    return results


def resolve_mainland_permitted_use(
    session: Session,
    *,
    use_name: str,
    zone: str,
    document_id: int | None = None,
) -> dict | None:
    """Resolve a ``(use, zone)`` pair against Mainland section-prose use lists.

    ABS-283. The Halifax Mainland LUB lists permitted uses as prose under each
    zone's section rather than in a ●/circled-number matrix, so the symbol-dot
    :func:`resolve_permission_cell` resolver can't address it. Enrichment records
    those lists as ``permitted_use_list`` facts (subject = zone entity); this
    reads them back.

    Returns ``None`` when no permitted-use list exists for ``zone`` in scope (so
    the caller can fall through to other resolvers). When a list *is* present the
    verdict is grounded and closed: ``permitted`` if the use is in the list,
    otherwise ``not_permitted`` (the Mainland exhaustiveness clause makes the
    list closed). ``document_id`` pins the search to one bylaw; ``None`` searches
    every document.
    """
    zone_norm = normalize_zone(zone)
    query = (
        session.query(SemanticFact)
        .join(
            SemanticEntity,
            SemanticEntity.id == SemanticFact.primary_subject_entity_id,
        )
        .filter(
            SemanticFact.relation_type == "permitted_use_list",
            SemanticEntity.entity_type == "zone",
            SemanticEntity.canonical_name == zone_norm,
        )
    )
    if document_id is not None:
        query = query.filter(SemanticFact.document_id == document_id)
    facts = query.all()
    if not facts:
        return None
    keys: set[str] = set()
    display: list[str] = []
    fragment_ids: list[int] = []
    for fact in facts:
        normalized = fact.normalized_value_json or {}
        keys.update(normalized.get("permitted_use_keys", []))
        display.extend(normalized.get("permitted_uses", []))
        fragment_ids.extend((fact.metadata_json or {}).get("source_fragment_ids", []))
    permitted = _loose_use_key(use_name) in keys
    return {
        "zone": zone_norm,
        "use": normalize_use(use_name),
        "permission": "permitted" if permitted else "not_permitted",
        "convention": "mainland_section_prose",
        "permitted_uses": sorted(set(display)),
        "document_id": facts[0].document_id,
        "source_fragment_ids": fragment_ids,
    }


def _normalize_permission_marker(marker: str) -> dict:
    stripped = marker.strip()
    conditions = extract_condition_refs(stripped)
    if stripped in PERMISSION_MARKERS:
        return {"permission": "permitted", "markers": []}
    if conditions:
        return {"permission": "conditional", "markers": conditions}
    return {"permission": "unknown", "raw": stripped}


def _refresh_counts(session: Session, report: SemanticEnrichmentReport) -> None:
    report.entities = session.query(SemanticEntity).filter(SemanticEntity.document_id == report.document_id).count()
    report.facts = session.query(SemanticFact).filter(SemanticFact.document_id == report.document_id).count()
    report.edges = session.query(SemanticEdge).filter(SemanticEdge.document_id == report.document_id).count()
    table_ids = [row.id for row in session.query(SourceTable.id).filter(SourceTable.document_id == report.document_id).all()]
    if table_ids:
        report.table_profiles = session.query(TableSemanticProfile).filter(TableSemanticProfile.table_id.in_(table_ids)).count()
        report.axis_bindings = session.query(TableAxisBinding).filter(TableAxisBinding.table_id.in_(table_ids)).count()
    fact_ids = [row.id for row in session.query(SemanticFact.id).filter(SemanticFact.document_id == report.document_id).all()]
    if fact_ids:
        report.fact_participants = (
            session.query(SemanticFactParticipant).filter(SemanticFactParticipant.fact_id.in_(fact_ids)).count()
        )
    report.provenance = session.query(SemanticProvenance).filter(SemanticProvenance.document_id == report.document_id).count()
