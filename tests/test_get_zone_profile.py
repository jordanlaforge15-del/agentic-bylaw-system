"""Unit tests for ABS-272: get_zone_profile thick tool.

Covers AC-2.2 through AC-2.9. The tests seed a sqlite database with
per-zone, table-row-shaped fragments mirroring the Regional Centre LUB
fixture (``tests/fixtures/halifax_regional_centre_lub.txt``) — height +
coverage (Table 5), setbacks (Table 3), use permissions (Table 1A/1B),
zone establishment (Part II), and the general off-street-parking rule
(Part V). ``get_zone_profile`` composes semantic searches over this
corpus exactly as it would over a real ingest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import (
    EVIDENCE_CLASS_CONFIDENCE,
    MIN_GATED_EVIDENCE_CONFIDENCE,
    CitationLookupRequest,
    EvidenceClass,
    RetrievalMatch,
    RetrievalRequest,
    RetrievalService,
    ZoneProfile,
)
from bylaw_retrieval.retrieval.service import _classify_evidence, _extract_height_m
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _add_document(session) -> Document:
    doc = Document(
        municipality="Halifax Regional Municipality",
        bylaw_name="Regional Centre Land Use By-Law",
        source_path="halifax_regional_centre_lub.txt",
        source_url=None,
        file_hash="abs272-zone-profile-fixture",
        version_label=None,
        consolidation_date=None,
        mime_type="text/plain",
        page_count=1,
        parser_version="test",
    )
    session.add(doc)
    session.flush()
    return doc


def _add_fragment(
    session,
    document_id: int,
    *,
    text: str,
    citation_path: str,
    citation_label: str,
    page: int,
    order: int,
) -> SourceFragment:
    frag = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SECTION,
        citation_label=citation_label,
        citation_path=citation_path,
        parent_fragment_id=None,
        page_start=page,
        page_end=page,
        reading_order_start=order,
        reading_order_end=order,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(frag)
    return frag


# Per-zone source rows. Values lifted from the Regional Centre LUB
# fixture: Table 5 (height/coverage), Table 3/4 (setbacks), Table 1A/1B
# (uses). COR and CEN-2 height is governed by Schedule 15 — no inline
# number — which is the correct ``None`` outcome for ``max_height_m``.
_ZONE_ROWS = {
    "HR-2": {
        "full_name": "Higher Order Residential 2",
        "height": "HR-2 Maximum Height 25.0 m Maximum Lot Coverage 65%",
        "setbacks": "HR-2 Front Setback 3.0 m Side Setback 3.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions HR-2 single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
        "use_table": "Table 1A",
    },
    "HR-1": {
        "full_name": "Higher Order Residential 1",
        "height": "HR-1 Maximum Height 20.0 m Maximum Lot Coverage 60%",
        "setbacks": "HR-1 Front Setback 3.0 m Side Setback 3.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions HR-1 single-unit dwelling P secondary suite P "
            "multi-unit dwelling P home occupation P daycare P"
        ),
        "use_table": "Table 1A",
    },
    "COR": {
        "full_name": "Corridor",
        "height": "COR Maximum Height as per Schedule 15 Maximum Lot Coverage 70%",
        "setbacks": "COR Front Setback 3.0 m Side Setback 0.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions COR single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
        "use_table": "Table 1B",
    },
    "CEN-2": {
        "full_name": "Centre 2",
        "height": "CEN-2 Maximum Height as per Schedule 15 Maximum Lot Coverage 80%",
        "setbacks": "CEN-2 Front Setback 0.0 m Side Setback 0.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions CEN-2 single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
        "use_table": "Table 1B",
    },
}

_PARKING_TEXT = (
    "Off-Street Parking Requirements. A residential development shall "
    "provide a minimum of 1 parking space per dwelling unit. Despite "
    "that, no off-street parking is required for a development in the "
    "CEN-1, CEN-2, DH, or DD zone. A non-residential development shall "
    "provide parking at the ratios in Table 8."
)


def _seed_regional_centre(db_url: str) -> int:
    """Seed the multi-zone Regional Centre corpus. Returns document_id."""
    create_all(db_url)
    with session_scope(db_url) as session:
        doc = _add_document(session)
        order = 0
        for zone, row in _ZONE_ROWS.items():
            order += 1
            _add_fragment(
                session,
                doc.id,
                text=f"{zone} {row['full_name']} Zone",
                citation_path=f"Part II > 30 > {zone}",
                citation_label=zone,
                page=2,
                order=order,
            )
            order += 1
            _add_fragment(
                session,
                doc.id,
                text=row["height"],
                citation_path=f"Table 5 > {zone}",
                citation_label=zone,
                page=4,
                order=order,
            )
            order += 1
            _add_fragment(
                session,
                doc.id,
                text=row["setbacks"],
                citation_path=f"Table 3 > {zone}",
                citation_label=zone,
                page=4,
                order=order,
            )
            order += 1
            _add_fragment(
                session,
                doc.id,
                text=row["uses"],
                citation_path=f"{row['use_table']} > {zone}",
                citation_label=zone,
                page=3,
                order=order,
            )
        order += 1
        _add_fragment(
            session,
            doc.id,
            text=_PARKING_TEXT,
            citation_path="Part V > 120",
            citation_label="Section 120",
            page=6,
            order=order,
        )
        return doc.id


def _service(db_url: str, session) -> RetrievalService:
    return RetrievalService(session)


# ---------------------------------------------------------------------------
# AC-2.2 — full DTO for HR-2
# ---------------------------------------------------------------------------


def test_get_zone_profile_returns_full_dto_for_HR_2(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'hr2.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        profile = RetrievalService(session).get_zone_profile("HR-2")

    assert isinstance(profile, ZoneProfile)
    assert profile.zone == "HR-2"
    assert profile.unknown_zone is False

    assert profile.dimensions is not None
    assert profile.dimensions.max_height_m == 25.0
    assert profile.dimensions.max_lot_coverage_pct == 65.0

    assert profile.uses is not None
    assert "multi-unit dwelling" in profile.uses.permitted
    assert "home occupation" in profile.uses.not_permitted

    assert profile.citations, "citations must be non-empty for a known zone"


# ---------------------------------------------------------------------------
# AC-2.3 — representative Regional Centre zones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("zone", ["HR-2", "COR", "HR-1", "CEN-2"])
def test_get_zone_profile_returns_dto_for_representative_zones(tmp_path: Path, zone: str):
    db_url = f"sqlite:///{tmp_path / 'rep.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        profile = RetrievalService(session).get_zone_profile(zone)

    assert profile.zone == zone
    assert profile.unknown_zone is False
    assert profile.citations, f"expected non-empty citations for {zone}"
    # Every representative zone has use permissions in the fixture.
    assert profile.uses is not None
    assert profile.uses.permitted or profile.uses.not_permitted


# ---------------------------------------------------------------------------
# AC-2.4 — citations resolve via lookup_citation
# ---------------------------------------------------------------------------


def test_get_zone_profile_citations_are_resolvable(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'cit.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        profile = service.get_zone_profile("HR-2")

        assert profile.citations
        for citation in profile.citations:
            response = service.lookup_citation(
                CitationLookupRequest(citation_path=citation.citation_path)
            )
            assert response.match is not None, (
                f"citation {citation.citation_path!r} did not resolve to a fragment"
            )
            assert response.match.citation_path == citation.citation_path


# ---------------------------------------------------------------------------
# AC-2.5 — unknown zone returns null DTO, no exception
# ---------------------------------------------------------------------------


def test_get_zone_profile_unknown_zone_returns_null_dto(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'unknown.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        # Must not raise (FR-2.5 / ABS-261 pattern).
        profile = RetrievalService(session).get_zone_profile("XYZ-99")

    assert profile.zone == "XYZ-99"
    assert profile.unknown_zone is True
    assert profile.citations == []
    assert profile.dimensions is None
    assert profile.uses is None
    assert profile.parking is None


# ---------------------------------------------------------------------------
# AC-2.6 — include filter omits sections
# ---------------------------------------------------------------------------


def test_get_zone_profile_include_filter_omits_sections(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'include.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        profile = RetrievalService(session).get_zone_profile(
            "HR-2", include=["dimensions"]
        )

    assert profile.dimensions is not None
    assert profile.uses is None
    assert profile.parking is None
    # Citations are always populated regardless of the include filter.
    assert profile.citations


# ---------------------------------------------------------------------------
# AC-2.7 — dimensions match the equivalent thin-tool sequence
# ---------------------------------------------------------------------------


def test_get_zone_profile_dimensions_match_thin_retrieval(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'thin.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        service = RetrievalService(session)

        # Thick tool.
        profile = service.get_zone_profile("HR-2", include=["dimensions"])

        # Equivalent thin sequence: search, then lookup_citation on the
        # top result, then extract the height from the fragment text.
        search = service.search(
            RetrievalRequest(query="HR-2 maximum height", limit=5)
        )
        assert search.matches
        top = search.matches[0]
        looked_up = service.lookup_citation(
            CitationLookupRequest(citation_path=top.citation_path)
        )
        assert looked_up.match is not None
        thin_height = _extract_height_m(looked_up.match.text)

    assert thin_height == 25.0
    assert profile.dimensions is not None
    assert profile.dimensions.max_height_m == thin_height


# ---------------------------------------------------------------------------
# AC-2.9 — low-confidence field returns null and is not cited
# ---------------------------------------------------------------------------


def test_get_zone_profile_low_confidence_returns_null_field(tmp_path: Path):
    """A zone mentioned only in prose body text (no table-row anchor for
    the dimension keywords) scores below the confidence threshold, so the
    height — though present in the text — is dropped to None and emits no
    citation.
    """
    db_url = f"sqlite:///{tmp_path / 'lowconf.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        doc = _add_document(session)
        # Zone code lives only in the fragment body, and the citation
        # path/label carry no dimension keywords — a weak (low-score)
        # match for "LOWC maximum height lot coverage". The height
        # value IS extractable from the prose, so this isolates the
        # confidence gate rather than a missing value.
        _add_fragment(
            session,
            doc.id,
            text="In LOWC the maximum permitted height is 12.0 metres.",
            # Digit-free zone code and path/label, so nothing can anchor
            # the score via a citation_path match — the only signal is the
            # weak body-text overlap, keeping confidence below threshold.
            # (Before ABS-478 this fixture used "LOWC-1"; the zone's '1'
            # token then substring-matched the "12.0" in its own body text,
            # so the gate was being tested against a token that only fired
            # by accident. A digit-free code isolates the gate for real.)
            citation_path="General Standards",
            citation_label="General Standards",
            page=5,
            order=1,
        )

    with session_scope(db_url) as session:
        profile = RetrievalService(session).get_zone_profile("LOWC")

    # Zone is found (not unknown) but the height field was gated out.
    assert profile.unknown_zone is False
    assert profile.dimensions is not None
    assert profile.dimensions.max_height_m is None
    assert "max_height_m" not in profile.confidence
    cited_fields = [field for c in profile.citations for field in c.backs]
    assert "max_height_m" not in cited_fields


# ---------------------------------------------------------------------------
# ABS-409 — matrix enumeration path (symbol-dot bylaws have no P/N prose)
# ---------------------------------------------------------------------------

DOT = ""  # symbol-font ● "permitted as-of-right"

MATRIX_409 = [
    ["Use", "DD", "DH", "COR"],
    ["Restaurant use", DOT, "③", ""],
    ["Office use", DOT, DOT, DOT],
    ["Multi-unit dwelling use", DOT, DOT, ""],
]

CONDITION_409 = (
    "③ Use is permitted subject to a maximum gross floor area of 100 "
    "square metres."
)


def _seed_matrix_corpus(db_url: str, *, with_prose: bool = True) -> int:
    """A symbol-dot permission matrix bound by enrichment (no P/N prose)."""
    from layer1.db.base import SourceTable, SourceTableCell
    from layer1.semantic.enrichment import enrich_document_semantics

    create_all(db_url)
    with session_scope(db_url) as session:
        doc = _add_document(session)
        if with_prose:
            _add_fragment(
                session,
                doc.id,
                text="The DH (Downtown Halifax) Zone is established in Part II.",
                citation_path="Part II > 14",
                citation_label="14",
                page=20,
                order=1,
            )
        table = SourceTable(
            document_id=doc.id,
            caption="Table 1A: Permitted uses by zone (DD, DH, and COR)",
            page_start=45,
            page_end=45,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(table)
        session.flush()
        for row_index, row in enumerate(MATRIX_409):
            for col_index, text in enumerate(row):
                session.add(
                    SourceTableCell(
                        table_id=table.id,
                        row_index=row_index,
                        col_index=col_index,
                        row_header_path=row[0] if row_index else None,
                        col_header_path=MATRIX_409[0][col_index] if row_index else None,
                        text=text,
                        metadata_json={},
                    )
                )
        session.add(
            SourceFragment(
                document_id=doc.id,
                fragment_type=FragmentType.FOOTNOTE,
                page_start=47,
                page_end=47,
                reading_order_start=900,
                reading_order_end=900,
                text=CONDITION_409,
                parse_status=ParseStatus.PARSED,
                confidence=0.9,
            )
        )
        session.flush()
        enrich_document_semantics(session, document_id=doc.id)
        return doc.id


def test_abs409_matrix_enumeration_populates_uses(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'matrix.db'}"
    _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        profile = _service(db_url, session).get_zone_profile("DH")
        assert profile.unknown_zone is False
        assert profile.uses is not None
        assert "Office use" in profile.uses.permitted
        assert "Multi-unit dwelling use" in profile.uses.permitted
        conditional = {item.use: item for item in profile.uses.conditional}
        assert "Restaurant use" in conditional
        assert conditional["Restaurant use"].footnote_ordinal == 3
        assert conditional["Restaurant use"].condition is not None
        assert "100" in conditional["Restaurant use"].condition
        assert profile.confidence.get("uses") is not None
        # Pre-backfill the table has no caption-linked citation_path, but the
        # citation must still surface (add_ref path) with label + pages.
        uses_refs = [c for c in profile.citations if "uses" in c.backs]
        assert uses_refs
        assert uses_refs[0].page_start == 45


def test_abs409_blank_cells_enumerate_as_not_permitted(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'matrix2.db'}"
    _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        profile = _service(db_url, session).get_zone_profile("COR")
        assert profile.uses is not None
        assert "Office use" in profile.uses.permitted
        assert "Restaurant use" in profile.uses.not_permitted
        assert "Multi-unit dwelling use" in profile.uses.not_permitted


def test_abs409_matrix_only_zone_is_known(tmp_path: Path):
    """A zone named ONLY in matrix column headers (no prose fragment) must not
    return unknown_zone — the ABS-409 gate probes the bound columns."""
    db_url = f"sqlite:///{tmp_path / 'matrix3.db'}"
    _seed_matrix_corpus(db_url, with_prose=False)
    with session_scope(db_url) as session:
        profile = _service(db_url, session).get_zone_profile("DD")
        assert profile.unknown_zone is False
        assert profile.uses is not None
        assert "Restaurant use" in profile.uses.permitted


# ---------------------------------------------------------------------------
# ABS-484 — UNKNOWN absorbs: a cell we could not read is undetermined, never
# a prohibition, and it is never cited
# ---------------------------------------------------------------------------


# Row index of each MATRIX_409 use row, and the column index of each zone.
_MATRIX_ROW = {label: idx for idx, (label, *_rest) in enumerate(MATRIX_409)}
_MATRIX_COL = {zone: idx for idx, zone in enumerate(MATRIX_409[0])}


def _punch_hole(session, *, use: str, zone: str) -> None:
    """Delete a cell so the bound row has nothing to read in ``zone``'s column.

    This is the extraction failure ABS-483 made producible: the row binding
    survives (the bylaw HAS this use row), the cell does not.
    """
    from layer1.db.base import SourceTable, SourceTableCell

    table_id = session.query(SourceTable).first().id
    session.query(SourceTableCell).filter(
        SourceTableCell.table_id == table_id,
        SourceTableCell.row_index == _MATRIX_ROW[use],
        SourceTableCell.col_index == _MATRIX_COL[zone],
    ).delete(synchronize_session=False)
    session.flush()


def _add_use_prose(session, document_id: int, text: str, *, zone: str) -> None:
    """A P/N prose use row — the other reading of the same permissions."""
    _add_fragment(
        session,
        document_id,
        text=text,
        citation_path=f"Table 1B > {zone}",
        citation_label=zone,
        page=3,
        order=500,
    )
    session.flush()


def test_abs484_missing_cell_is_undetermined_not_not_permitted(tmp_path: Path):
    """The headline bug: a hole in the COR column used to be served as an
    authoritative prohibition. It must land in ``undetermined`` instead, while
    the genuinely blank cell beside it keeps meaning not-permitted."""
    db_url = f"sqlite:///{tmp_path / 'hole.db'}"
    _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        _punch_hole(session, use="Multi-unit dwelling use", zone="COR")
        profile = _service(db_url, session).get_zone_profile("COR")

    assert profile.uses is not None
    assert "Multi-unit dwelling use" in profile.uses.undetermined
    assert "Multi-unit dwelling use" not in profile.uses.not_permitted
    assert "Multi-unit dwelling use" not in profile.uses.permitted
    # The blank-cell convention is real bylaw content and survives untouched.
    assert "Restaurant use" in profile.uses.not_permitted
    assert "Office use" in profile.uses.permitted


def test_abs484_all_unknown_column_carries_no_citation_and_no_confidence(
    tmp_path: Path,
):
    """When every cell in the column is a hole the profile asserts nothing, so
    it must claim nothing: no 0.9 confidence, and no citation backing ``uses``
    (a citation beside an UNKNOWN reads as evidence for a verdict)."""
    db_url = f"sqlite:///{tmp_path / 'all_holes.db'}"
    _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        for use in ("Restaurant use", "Office use", "Multi-unit dwelling use"):
            _punch_hole(session, use=use, zone="COR")
        profile = _service(db_url, session).get_zone_profile("COR")

    assert profile.uses is not None
    assert sorted(profile.uses.undetermined) == [
        "Multi-unit dwelling use",
        "Office use",
        "Restaurant use",
    ]
    assert profile.uses.permitted == []
    assert profile.uses.not_permitted == []
    assert profile.uses.conditional == []
    assert "uses" not in profile.confidence
    assert [c for c in profile.citations if "uses" in c.backs] == []


def test_abs484_prose_fallback_wins_for_an_undetermined_use(tmp_path: Path):
    """The matrix path may no longer short-circuit while it holds UNKNOWNs: the
    prose row states what the lost cell would have, so the use moves into
    ``permitted`` and carries the prose fragment's citation."""
    db_url = f"sqlite:///{tmp_path / 'prose_wins.db'}"
    doc_id = _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        _punch_hole(session, use="Multi-unit dwelling use", zone="COR")
        _add_use_prose(
            session,
            doc_id,
            "Use Permissions COR multi-unit dwelling P daycare P",
            zone="COR",
        )
        profile = _service(db_url, session).get_zone_profile("COR")

    assert profile.uses is not None
    assert "Multi-unit dwelling use" in profile.uses.permitted
    assert profile.uses.undetermined == []
    uses_paths = {c.citation_path for c in profile.citations if "uses" in c.backs}
    assert "Table 1B > COR" in uses_paths
    # The block now mixes a matrix reading with a weaker prose one, so the
    # matrix's 0.9 no longer stands for the whole thing.
    assert profile.confidence["uses"] < 0.9


def test_abs484_prose_fallback_can_resolve_to_not_permitted(tmp_path: Path):
    """Prose wins in both directions — an explicit 'N' is a real prohibition,
    unlike the hole it replaces."""
    db_url = f"sqlite:///{tmp_path / 'prose_n.db'}"
    doc_id = _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        _punch_hole(session, use="Multi-unit dwelling use", zone="COR")
        _add_use_prose(
            session,
            doc_id,
            "Use Permissions COR multi-unit dwelling N daycare P",
            zone="COR",
        )
        profile = _service(db_url, session).get_zone_profile("COR")

    assert profile.uses is not None
    assert "Multi-unit dwelling use" in profile.uses.not_permitted
    assert profile.uses.undetermined == []


def test_abs484_prose_silence_leaves_the_use_undetermined(tmp_path: Path):
    """A prose row that simply doesn't mention the use resolves nothing — the
    gap stays a gap rather than being read as an omission-is-prohibition."""
    db_url = f"sqlite:///{tmp_path / 'prose_silent.db'}"
    doc_id = _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        _punch_hole(session, use="Multi-unit dwelling use", zone="COR")
        _add_use_prose(
            session,
            doc_id,
            "Use Permissions COR daycare P single-unit dwelling N",
            zone="COR",
        )
        profile = _service(db_url, session).get_zone_profile("COR")

    assert profile.uses is not None
    assert profile.uses.undetermined == ["Multi-unit dwelling use"]
    assert "Multi-unit dwelling use" not in profile.uses.not_permitted
    # Nothing prose-derived entered the block, so the matrix claim stands.
    assert profile.confidence["uses"] == 0.9


def test_abs484_determinate_zone_reports_no_undetermined(tmp_path: Path):
    """Regression guard: the ABS-409 happy path must not grow a phantom
    undetermined list."""
    db_url = f"sqlite:///{tmp_path / 'clean.db'}"
    _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        profile = _service(db_url, session).get_zone_profile("DH")

    assert profile.uses is not None
    assert profile.uses.undetermined == []
    assert profile.confidence["uses"] == 0.9


def test_abs409_caption_linked_citation_carries_path(tmp_path: Path):
    """After the ABS-409 caption linking pass, the uses citation resolves to
    the caption fragment's citation_path."""
    from layer1.db.base import SourceTable
    from layer1.pipeline.table_captions import link_table_captions

    db_url = f"sqlite:///{tmp_path / 'matrix4.db'}"
    doc_id = _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        # Stage the caption fragment as ingest would (unaddressed PROSE), and
        # clear the table's inline caption so linking is what supplies it.
        table = session.query(SourceTable).one()
        caption_text = table.caption
        table.caption = None
        table.parent_fragment_id = None
        session.add(
            SourceFragment(
                document_id=doc_id,
                fragment_type=FragmentType.PROSE,
                page_start=45,
                page_end=45,
                reading_order_start=100,
                reading_order_end=100,
                text=caption_text,
                parse_status=ParseStatus.PARSED,
                confidence=1.0,
            )
        )
        session.flush()
        link_table_captions(session, document_id=doc_id, profile="halifax")
        profile = _service(db_url, session).get_zone_profile("DH")
        uses_refs = [c for c in profile.citations if "uses" in c.backs]
        assert uses_refs
        assert uses_refs[0].citation_path is not None
        assert uses_refs[0].citation_path.endswith("[Table 1A]")


# ---------------------------------------------------------------------------
# ABS-493 — confidence is an ordinal evidence class, not a function of how
# many words the internal query happened to contain.
#
# See docs/decisions/ABS-493-CONFIDENCE-DEFINITION.md. The gate's *instinct*
# (drop the value AND its citation below threshold) is unchanged; what these
# tests pin down is that the threshold now reads WHERE the query's terms land
# rather than HOW MANY of them land.
# ---------------------------------------------------------------------------


def _row_match(text: str, *, citation_path: str, citation_label: str) -> RetrievalMatch:
    """A minimal match standing in for one retrieved fragment.

    ``_classify_evidence`` reads only text / citation_path / citation_label, so
    the rest is filler — deliberately including a ``score`` the classifier must
    ignore.
    """
    return RetrievalMatch(
        fragment_id=1,
        document_id=1,
        municipality="Halifax Regional Municipality",
        bylaw_name="Regional Centre Land Use By-Law",
        fragment_type="section",
        citation_label=citation_label,
        citation_path=citation_path,
        page_start=4,
        page_end=4,
        parse_status="parsed",
        text=text,
        score=999.0,
    )


# The headline invariant (ABS-493 DoD #1): equal evidence, different query
# word counts, same class — therefore the same gating outcome. "HR-2 setback"
# tokenizes to 4 terms and "HR-2 floor area ratio" to 6, which is exactly the
# spread that used to flip the old score/40 gate from 0.325 to 0.525 on one
# and the same fragment.
@pytest.mark.parametrize(
    "path, label, expected_class, gated_in",
    [
        # Structurally addressed by the corpus: the row is FILED under HR-2.
        ("Table 3 > HR-2", "HR-2", EvidenceClass.PATH_ANCHORED, True),
        # Addressed only by its label.
        ("Part V > 120", "HR-2", EvidenceClass.LABELLED_ROW, True),
        # Not addressed at all — the zone is a word in the prose, nothing more.
        ("General Standards", "General Standards", EvidenceClass.BODY_TERMS, False),
    ],
)
def test_abs493_equal_evidence_gates_the_same_at_any_query_length(
    path: str, label: str, expected_class: EvidenceClass, gated_in: bool
):
    body = (
        "HR-2 Front Setback 3.0 m Side Setback 3.0 m Rear Setback 3.0 m "
        "Floor Area Ratio 2.0"
    )
    match = _row_match(body, citation_path=path, citation_label=label)

    short = _classify_evidence("HR-2 setback", match)
    long = _classify_evidence("HR-2 floor area ratio", match)

    assert short == long == expected_class
    rung = EVIDENCE_CLASS_CONFIDENCE[expected_class]
    assert (rung >= MIN_GATED_EVIDENCE_CONFIDENCE) is gated_in


def test_abs493_evidence_class_ignores_the_match_score():
    """The class is a property of (query, fragment), not of the score.

    Two fragments with the same locus classify identically no matter what the
    scorer summed — which is the whole point of taking the score out of the
    gate.
    """
    strong = _row_match("HR-2 Front Setback 3.0 m", citation_path="Table 3 > HR-2", citation_label="HR-2")
    weak = strong.model_copy(update={"score": 0.5})

    assert _classify_evidence("HR-2 setback", strong) == EvidenceClass.PATH_ANCHORED
    assert _classify_evidence("HR-2 setback", weak) == EvidenceClass.PATH_ANCHORED


def test_abs493_ladder_is_walked_strongest_rung_first():
    """Each rung, and the strict ordering between them."""
    body = "off-street parking requirements apply to every development"

    exact = _row_match(body, citation_path="Part V > 120", citation_label="Section 120")
    assert _classify_evidence("Part V > 120", exact) == EvidenceClass.EXACT_PATH
    # Path beats label beats body: the same query, three fragments.
    assert (
        _classify_evidence(
            "off-street parking", _row_match(body, citation_path="Part V > Parking", citation_label="Section 120")
        )
        == EvidenceClass.PATH_ANCHORED
    )
    assert (
        _classify_evidence(
            "off-street parking", _row_match(body, citation_path="Part V > 120", citation_label="Parking rules")
        )
        == EvidenceClass.LABELLED_ROW
    )
    # Verbatim phrase in the body outranks scattered terms in the body.
    assert _classify_evidence("off-street parking", exact) == EvidenceClass.BODY_PHRASE
    assert _classify_evidence("off-street bicycle storage", exact) == EvidenceClass.BODY_TERMS
    assert _classify_evidence("heritage conservation district", exact) == EvidenceClass.NO_MATCH

    rungs = [
        EVIDENCE_CLASS_CONFIDENCE[c]
        for c in (
            EvidenceClass.EXACT_PATH,
            EvidenceClass.BOUND_TABLE_CELL,
            EvidenceClass.PATH_ANCHORED,
            EvidenceClass.LABELLED_ROW,
            EvidenceClass.BODY_PHRASE,
            EvidenceClass.BODY_TERMS,
            EvidenceClass.NO_MATCH,
        )
    ]
    assert rungs == sorted(rungs, reverse=True)
    assert len(set(rungs)) == len(rungs), "rungs must be distinguishable"


def test_abs493_cor_keeps_the_setbacks_its_two_token_query_used_to_lose(
    tmp_path: Path,
):
    """The regression this issue was raised on, end to end.

    Every zone in the fixture carries the identical ``Table 3 > <zone>``
    setback row. Under ``score / 40.0`` the gate read query length instead of
    evidence, so COR — whose code has no hyphen to split into extra tokens —
    silently lost all three setbacks while CEN-2 kept them off the same row
    shape. Same evidence must now mean same outcome AND the same rung.
    """
    db_url = f"sqlite:///{tmp_path / 'verbosity.db'}"
    _seed_regional_centre(db_url)

    setback_fields = ("front_setback_m", "side_setback_m", "rear_setback_m")
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        profiles = {
            zone: service.get_zone_profile(zone, include=["dimensions"])
            for zone in ("HR-2", "HR-1", "COR", "CEN-2")
        }

    for zone, profile in profiles.items():
        assert profile.dimensions is not None
        for field in setback_fields:
            assert getattr(profile.dimensions, field) is not None, (
                f"{zone}.{field} was gated out despite a Table 3 > {zone} row"
            )
            assert profile.confidence[field] == EVIDENCE_CLASS_CONFIDENCE[
                EvidenceClass.PATH_ANCHORED
            ]

    # And the rungs agree across zones — not merely "all above threshold".
    per_zone = [
        tuple(profile.confidence[field] for field in setback_fields)
        for profile in profiles.values()
    ]
    assert len(set(per_zone)) == 1, f"identical evidence produced differing rungs: {per_zone}"


def test_abs493_body_text_prose_still_clears_the_gate_when_it_states_the_query(
    tmp_path: Path,
):
    """Not every real answer is a table row.

    The Part V parking rule is prose whose citation path ("Part V > 120") says
    nothing about parking — it clears the gate on ``body_phrase`` because the
    section states the query verbatim. Tightening the gate to structural
    anchors alone would have silently dropped it, so this pins the rung.
    """
    db_url = f"sqlite:///{tmp_path / 'prose_gate.db'}"
    _seed_regional_centre(db_url)

    with session_scope(db_url) as session:
        profile = RetrievalService(session).get_zone_profile("HR-2", include=["parking"])

    assert profile.parking is not None
    assert profile.parking.min_spaces_per_dwelling_unit == 1.0
    assert profile.confidence["parking"] == EVIDENCE_CLASS_CONFIDENCE[
        EvidenceClass.BODY_PHRASE
    ]
    assert "parking" in [field for c in profile.citations for field in c.backs]


def test_abs493_matrix_uses_report_the_bound_table_cell_rung(tmp_path: Path):
    """The ABS-409 matrix path's 0.9 is now a named rung on the same ladder,
    not a free-floating constant — and it outranks every keyword-derived rung
    below an outright citation-path identity match."""
    db_url = f"sqlite:///{tmp_path / 'matrix_rung.db'}"
    _seed_matrix_corpus(db_url)
    with session_scope(db_url) as session:
        profile = _service(db_url, session).get_zone_profile("DH")

    assert profile.confidence["uses"] == EVIDENCE_CLASS_CONFIDENCE[
        EvidenceClass.BOUND_TABLE_CELL
    ]
    assert (
        EVIDENCE_CLASS_CONFIDENCE[EvidenceClass.BOUND_TABLE_CELL]
        > EVIDENCE_CLASS_CONFIDENCE[EvidenceClass.PATH_ANCHORED]
    )
