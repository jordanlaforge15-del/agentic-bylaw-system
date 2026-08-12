"""ABS-472 — a municipality-wide geo layer must cite the by-law that governs
each feature, not the one the layer happens to be published alongside.

``halifax_zoning_boundaries`` is HRM-wide: 11,069 features across 22 by-law
areas, all linked wholesale to the Regional Centre LUB. So a DH-1 parcel —
Downtown Halifax LUB, a document the corpus does not hold at all — came back
with a confident zone and a citation attributing it to the Regional Centre
LUB, which does not govern that ground.

These tests build a sqlite corpus with that exact shape: one zoning layer
whose features name three different governing by-laws, and documents for only
two of them. They pin the three outcomes that must stay distinct:

  * governed by the layer's own linked by-law  -> cite it, as before;
  * governed by another by-law we DO hold      -> cite THAT document;
  * governed by a by-law we do NOT hold        -> no citation at all, a typed
    ``not_held`` status, and a caveat the answer path can refuse on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from shapely.geometry import Point

from bylaw_retrieval.retrieval import (
    RetrievalService,
    audit_governing_bylaw_coverage,
    retrieval_enabled_resolver,
)
from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    GeocodeCache,
    SourceFragment,
)
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer2.db.init_db import create_all as create_layer2


RC_BYLAW = "Regional Centre Land Use By-Law"
MAINLAND_BYLAW = "Halifax Mainland Land Use By-law"
DOWNTOWN_BYLAW = "Downtown Halifax Land Use By-law"

# Three side-by-side boxes, one per by-law area. HRM's real areas tile
# complementary ground (ABS-472 measured 8.3 m² of total intersection across
# 22 touching pairs), so no overlap is modelled here either.
_AREAS: dict[str, tuple[float, float]] = {
    "rc": (-63.60, -63.58),
    "mainland": (-63.58, -63.56),
    "downtown": (-63.56, -63.54),
}
_ADDRESSES: dict[str, tuple[str, str, float]] = {
    "rc": ("100 Robie Street", "civic:100 robie st", -63.59),
    "mainland": ("200 Mainland Street", "civic:200 mainland st", -63.57),
    "downtown": ("1657 Barrington Street", "civic:1657 barrington st", -63.55),
}


def _box(area: str) -> dict:
    west, east = _AREAS[area]
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, 44.64],
                [east, 44.64],
                [east, 44.66],
                [west, 44.66],
                [west, 44.64],
            ]
        ],
    }


def _bbox(area: str) -> dict:
    west, east = _AREAS[area]
    return {"minx": west, "miny": 44.64, "maxx": east, "maxy": 44.66}


def _add_document(session, bylaw_name: str, *, file_hash_seed: str) -> Document:
    document = Document(
        municipality="HRM",
        bylaw_name=bylaw_name,
        source_path=f"/{file_hash_seed}.pdf",
        file_hash=file_hash_seed * 64,
        mime_type="application/pdf",
        ingestion_timestamp=datetime.now(timezone.utc),
        page_count=100,
        retrieval_enabled=True,
    )
    session.add(document)
    session.flush()
    return document


def _add_fragment(session, *, document_id: int, label: str, path: str) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SCHEDULE,
        citation_label=label,
        citation_path=path,
        page_start=10,
        page_end=10,
        text=f"{label}.",
        parse_status=ParseStatus.PARSED,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment


def _seed(
    tmp_path: Path,
    *,
    governing_declared: bool = True,
    hold_mainland: bool = True,
    mainland_has_schedule: bool = True,
    extra_documents: tuple[str, ...] = (),
) -> str:
    """A sqlite corpus with one HRM-wide zoning layer spanning three by-laws."""
    db_url = f"sqlite:///{tmp_path / 'governing_bylaw.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        rc = _add_document(session, RC_BYLAW, file_hash_seed="a")
        rc_zoning = _add_fragment(
            session,
            document_id=rc.id,
            label="Zoning Schedule",
            path="rc.zoning_schedule",
        )
        if hold_mainland:
            mainland = _add_document(session, MAINLAND_BYLAW, file_hash_seed="b")
            if mainland_has_schedule:
                _add_fragment(
                    session,
                    document_id=mainland.id,
                    label="Zoning Schedule",
                    path="mainland.zoning_schedule",
                )
        for index, name in enumerate(extra_documents):
            _add_document(session, name, file_hash_seed=chr(ord("d") + index))

        links_to: dict = {
            "document_match": {"municipality": "HRM", "bylaw_name": RC_BYLAW},
            "fragment_citation": "Zoning Schedule",
        }
        if governing_declared:
            links_to["governing_bylaw_from"] = {
                "name_attribute": "bylaw_area_name",
                "code_attribute": "bylaw_area_code",
            }
        dataset = ExternalDataset(
            name="halifax_zoning_boundaries",
            publisher="Halifax Regional Municipality",
            format="geojson",
            content_hash="hash-zoning",
            crs="EPSG:4326",
            feature_count=3,
            linked_document_id=rc.id,
            linked_fragment_id=rc_zoning.id,
            linked_fragment_citation="Zoning Schedule",
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={"links_to": links_to},
        )
        session.add(dataset)
        session.flush()

        for area, zone_code, bylaw_name, code in (
            ("rc", "HR-2", "Regional Centre Land Use By-law", "hrm:RC"),
            ("mainland", "R-2", MAINLAND_BYLAW, "hrm:HMAIN"),
            ("downtown", "DH-1", DOWNTOWN_BYLAW, "hrm:DHFX"),
        ):
            session.add(
                ExternalDatasetFeature(
                    external_dataset_id=dataset.id,
                    feature_key=f"zoning-{area}",
                    attributes_json={},
                    canonical_attributes_json={
                        "zone_code": zone_code,
                        "bylaw_area_name": bylaw_name,
                        "bylaw_area_code": code,
                    },
                    geometry_geojson=_box(area),
                    geometry_bbox_json=_bbox(area),
                    parse_status=ParseStatus.PARSED,
                    metadata_json={},
                )
            )

        for area, (raw, normalized, lon) in _ADDRESSES.items():
            session.add(
                GeocodeCache(
                    normalized_text=normalized,
                    raw_text=raw,
                    kind="civic_address",
                    status="linked",
                    resolver="test_seed",
                    geometry_geojson={"type": "Point", "coordinates": [lon, 44.65]},
                    confidence=1.0,
                    detail=None,
                    metadata_json={"location_type": "ROOFTOP"},
                )
            )
    return db_url


def _profile(db_url: str, address: str):
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        return service.get_address_profile(address)


# ---------------------------------------------------------------------------
# The demonstrated defect: 1657 Barrington Street.
# ---------------------------------------------------------------------------


def test_zone_governed_by_an_unheld_bylaw_is_not_cited_to_the_linked_document(
    tmp_path: Path,
) -> None:
    """The issue's measured case. DH-1 is a Downtown Halifax LUB zone; the
    corpus holds no such document, so nothing may cite it — least of all the
    Regional Centre LUB the layer is linked to."""
    profile = _profile(_seed(tmp_path), "1657 Barrington Street")

    assert profile.zone == "DH-1"
    assert profile.governing_bylaw == DOWNTOWN_BYLAW
    assert profile.governing_bylaw_code == "hrm:DHFX"
    assert profile.governing_bylaw_status == "not_held"
    # No citation at all beats one naming a by-law that does not govern.
    assert [c for c in profile.citations if "zone" in c.backs] == []
    assert all(c.bylaw_name != RC_BYLAW for c in profile.citations)


def test_unheld_governing_bylaw_yields_an_overlay_without_a_citation(
    tmp_path: Path,
) -> None:
    """The overlay still reports the zone — HRM's mapping is real — but says
    which by-law governs it and that we do not hold it."""
    profile = _profile(_seed(tmp_path), "1657 Barrington Street")

    overlay = next(o for o in profile.overlays if o.kind == "zone")
    assert overlay.label == "DH-1"
    assert overlay.citation is None
    assert overlay.governing_bylaw == DOWNTOWN_BYLAW
    assert overlay.governing_bylaw_held is False


def test_unheld_governing_bylaw_caveat_names_the_bylaw_and_refuses_standards(
    tmp_path: Path,
) -> None:
    """The caveat has to be actionable: name the by-law, and say no standard
    from any other one applies."""
    profile = _profile(_seed(tmp_path), "1657 Barrington Street")

    assert profile.caveats
    caveat = profile.caveats[0]
    assert DOWNTOWN_BYLAW in caveat
    assert "DH-1" in caveat
    assert "not in this corpus" in caveat.lower()
    # A perfect geocode must not suppress it — this is not a precision problem.
    assert profile.resolution_quality == "rooftop"


# ---------------------------------------------------------------------------
# The by-law IS held, just not the one the layer is linked to.
# ---------------------------------------------------------------------------


def test_zone_governed_by_another_held_bylaw_cites_that_document(
    tmp_path: Path,
) -> None:
    """1,209 zoning features are governed by the Halifax Mainland LUB, which
    the corpus holds as its own document. They must cite it, not the Regional
    Centre LUB."""
    db_url = _seed(tmp_path)
    profile = _profile(db_url, "200 Mainland Street")

    assert profile.zone == "R-2"
    assert profile.governing_bylaw == MAINLAND_BYLAW
    assert profile.governing_bylaw_status == "held"
    zone_citation = next(c for c in profile.citations if "zone" in c.backs)
    assert zone_citation.bylaw_name == MAINLAND_BYLAW
    assert zone_citation.citation_path == "mainland.zoning_schedule"
    assert profile.caveats == []


def test_held_governing_bylaw_without_the_schedule_degrades_to_a_document_citation(
    tmp_path: Path,
) -> None:
    """When the governing document carries no fragment under the declared
    citation label, cite the document and stop — borrowing the linked
    document's fragment id would put a real fragment behind a claim that
    document never made."""
    db_url = _seed(tmp_path, mainland_has_schedule=False)
    profile = _profile(db_url, "200 Mainland Street")

    zone_citation = next(c for c in profile.citations if "zone" in c.backs)
    assert zone_citation.bylaw_name == MAINLAND_BYLAW
    assert zone_citation.citation_path is None
    assert zone_citation.citation_label is None
    assert profile.governing_bylaw_status == "held"


def test_zone_governed_by_the_linked_bylaw_is_unchanged(tmp_path: Path) -> None:
    """The 1,910 Regional Centre features keep the citation they always had."""
    profile = _profile(_seed(tmp_path), "100 Robie Street")

    assert profile.zone == "HR-2"
    assert profile.governing_bylaw_status == "held"
    zone_citation = next(c for c in profile.citations if "zone" in c.backs)
    assert zone_citation.bylaw_name == RC_BYLAW
    assert zone_citation.citation_path == "rc.zoning_schedule"
    assert profile.caveats == []


def test_layer_without_per_feature_attribution_reports_unknown(
    tmp_path: Path,
) -> None:
    """A layer that declares no per-feature governing by-law keeps the
    dataset-level link — the correct answer for a layer that genuinely belongs
    to one by-law — and says the attribution is unknown rather than held."""
    db_url = _seed(tmp_path, governing_declared=False)
    profile = _profile(db_url, "1657 Barrington Street")

    assert profile.zone == "DH-1"
    assert profile.governing_bylaw is None
    assert profile.governing_bylaw_status == "unknown"
    zone_citation = next(c for c in profile.citations if "zone" in c.backs)
    assert zone_citation.bylaw_name == RC_BYLAW


def test_unpublished_governing_document_does_not_count_as_held(
    tmp_path: Path,
) -> None:
    """Held means visible in the active retrieval scope. A Mainland document
    that exists but was never published to retrieval cannot back a citation,
    so its features must refuse exactly like an unheld by-law."""
    db_url = _seed(tmp_path)
    with session_scope(db_url) as session:
        mainland = (
            session.query(Document).filter(Document.bylaw_name == MAINLAND_BYLAW).one()
        )
        mainland.retrieval_enabled = False

    profile = _profile(db_url, "200 Mainland Street")
    assert profile.governing_bylaw_status == "not_held"
    assert [c for c in profile.citations if "zone" in c.backs] == []


# ---------------------------------------------------------------------------
# Name matching — the place a mis-attribution would sneak back in.
# ---------------------------------------------------------------------------


def test_case_and_hyphen_drift_still_matches_the_document(tmp_path: Path) -> None:
    """The publisher writes "By-law" on its geography and "By-Law" on the
    document title. Same by-law."""
    profile = _profile(_seed(tmp_path), "100 Robie Street")
    # The feature says "Regional Centre Land Use By-law"; the document is
    # titled "...By-Law". A literal comparison would have refused this.
    assert profile.governing_bylaw == "Regional Centre Land Use By-law"
    assert profile.governing_bylaw_status == "held"


def test_a_longer_bylaw_name_does_not_swallow_a_shorter_one(
    tmp_path: Path,
) -> None:
    """"Dartmouth Land Use By-law" is a *substring* of "Downtown Dartmouth
    Land Use By-law" and they govern different ground. Matching is
    prefix-anchored so the Downtown document never answers for Dartmouth."""
    db_url = _seed(
        tmp_path,
        hold_mainland=False,
        extra_documents=("Downtown Dartmouth Land Use By-law",),
    )
    with session_scope(db_url) as session:
        feature = (
            session.query(ExternalDatasetFeature)
            .filter(ExternalDatasetFeature.feature_key == "zoning-mainland")
            .one()
        )
        feature.canonical_attributes_json = {
            "zone_code": "GC",
            "bylaw_area_name": "Dartmouth Land Use By-law",
            "bylaw_area_code": "hrm:DART",
        }

    profile = _profile(db_url, "200 Mainland Street")
    assert profile.governing_bylaw == "Dartmouth Land Use By-law"
    assert profile.governing_bylaw_status == "not_held"


def test_a_title_qualifier_on_the_document_still_matches(tmp_path: Path) -> None:
    """Document titles pick up qualifiers the publisher's geography never
    carries ("... (Consolidated to 2024)"). A prefix match absorbs them."""
    db_url = _seed(tmp_path, hold_mainland=False)
    with session_scope(db_url) as session:
        rc = session.query(Document).filter(Document.bylaw_name == RC_BYLAW).one()
        rc.bylaw_name = f"{RC_BYLAW} (Consolidated to 2024)"

    profile = _profile(db_url, "100 Robie Street")
    assert profile.governing_bylaw_status == "held"


# ---------------------------------------------------------------------------
# The corpus-level view: how much ground can we not answer for?
# ---------------------------------------------------------------------------


def test_coverage_audit_counts_the_unheld_ground(tmp_path: Path) -> None:
    db_url = _seed(tmp_path)
    with session_scope(db_url) as session:
        report = audit_governing_bylaw_coverage(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )

    assert report.datasets_checked == 1
    assert report.features_checked == 3
    assert report.covered_features == 2
    assert report.unheld_features == 1
    assert report.complete is False
    assert [row.governing_bylaw for row in report.unheld] == [DOWNTOWN_BYLAW]
    gap = report.unheld[0]
    assert gap.dataset_name == "halifax_zoning_boundaries"
    assert gap.governing_bylaw_code == "hrm:DHFX"
    assert gap.feature_count == 1


def test_coverage_audit_is_complete_when_every_bylaw_is_held(
    tmp_path: Path,
) -> None:
    db_url = _seed(tmp_path, extra_documents=(DOWNTOWN_BYLAW,))
    with session_scope(db_url) as session:
        report = audit_governing_bylaw_coverage(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )

    assert report.complete is True
    assert report.unheld == []
    assert report.covered_features == 3


def test_coverage_audit_ignores_layers_without_attribution(tmp_path: Path) -> None:
    db_url = _seed(tmp_path, governing_declared=False)
    with session_scope(db_url) as session:
        report = audit_governing_bylaw_coverage(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )

    assert report.datasets_checked == 0
    assert report.features_checked == 0
    assert report.complete is True


# ---------------------------------------------------------------------------
# get_adjacent_zoning shares the zoning layer, so it shares the defect.
# ---------------------------------------------------------------------------


def test_adjacent_zoning_drops_the_citation_when_the_bylaw_is_not_held(
    tmp_path: Path,
) -> None:
    """The abutting-zone profile feeds conditional setbacks. A citation naming
    a by-law that does not govern the neighbour's land would send the report
    writer to the wrong document for the number."""
    db_url = _seed(tmp_path)
    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        zone, dataset, governing = service._resolve_zone_at_point(
            Point(-63.55, 44.65)
        )

    assert zone == "DH-1"
    assert dataset is not None
    assert governing is not None
    assert governing.held is False


@pytest.mark.parametrize(
    "address,expected",
    [
        ("100 Robie Street", "held"),
        ("200 Mainland Street", "held"),
        ("1657 Barrington Street", "not_held"),
    ],
)
def test_every_seeded_area_reports_a_status(
    tmp_path: Path, address: str, expected: str
) -> None:
    """No feature may come back with a zone and no statement about which
    by-law governs it — that silence is the whole defect."""
    profile = _profile(_seed(tmp_path), address)
    assert profile.zone is not None
    assert profile.governing_bylaw_status == expected
