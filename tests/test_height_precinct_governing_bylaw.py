"""ABS-473 — a Suburban Housing Accelerator precinct is not Schedule 15.

Split out of ABS-472. That issue fixed ``halifax_zoning_boundaries``, which
is HRM-wide but was linked wholesale to the Regional Centre LUB. Auditing the
sibling geo layers found five clean and one not: ``halifax_height_precincts``
carries its own ``BYLAW_AREA`` attribute, and 48 of its 1,822 precincts say
24 — the Suburban Housing Accelerator LUB, a by-law this corpus does not
hold. All 1,822 were served as Schedule 15 of the Regional Centre LUB.

The failure is quieter than ABS-472's, and that is the point of testing it
separately. There the zone itself came from an unheld by-law, so the parcel
was refused outright. Here the *zone* is Regional Centre and perfectly well
held — only the height precinct over it is not. Nothing about the zone is
wrong, the address resolves rooftop, and a max-height answer still comes out
of the wrong by-law.

The corpus below has that exact shape: one held Regional Centre LUB with both
a Zoning Schedule and a Schedule 15, a wholly-Regional-Centre zoning layer,
and a height-precinct layer spanning two by-law areas.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bylaw_retrieval.retrieval import (
    RetrievalService,
    audit_governing_bylaw_coverage,
    retrieval_enabled_resolver,
)

from advisor.chat.compact import compact_address_profile
from advisor.chat.resolution_qualifier import governing_bylaw_suffix
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
SHA_BYLAW = "Suburban Housing Accelerator Land Use By-law"

# Two side-by-side boxes. Both are Regional Centre *zoning*; they differ only
# in which by-law the height precinct over them belongs to — which is the
# whole distinction this issue is about.
_AREAS: dict[str, tuple[float, float]] = {
    "rc": (-63.60, -63.58),
    "sha": (-63.58, -63.56),
}
_ADDRESSES: dict[str, tuple[str, str, float]] = {
    "rc": ("100 Robie Street", "civic:100 robie st", -63.59),
    "sha": ("50 Accelerator Way", "civic:50 accelerator way", -63.57),
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


def _seed(tmp_path: Path, *, hold_sha: bool = False) -> str:
    """A corpus holding the Regional Centre LUB, with a mixed height layer."""
    db_url = f"sqlite:///{tmp_path / 'height_precincts.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        rc = Document(
            municipality="HRM",
            bylaw_name=RC_BYLAW,
            source_path="/rc.pdf",
            file_hash="a" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            page_count=400,
            retrieval_enabled=True,
        )
        session.add(rc)
        session.flush()
        zoning_schedule = _add_fragment(
            session, document_id=rc.id, label="Zoning Schedule", path="rc.zoning"
        )
        schedule_15 = _add_fragment(
            session, document_id=rc.id, label="Schedule 15", path="rc.schedule_15"
        )
        if hold_sha:
            sha_doc = Document(
                municipality="HRM",
                bylaw_name=SHA_BYLAW,
                source_path="/sha.pdf",
                file_hash="b" * 64,
                mime_type="application/pdf",
                ingestion_timestamp=datetime.now(timezone.utc),
                page_count=80,
                retrieval_enabled=True,
            )
            session.add(sha_doc)
            session.flush()
            _add_fragment(
                session,
                document_id=sha_doc.id,
                label="Schedule 15",
                path="sha.schedule_15",
            )

        # The zoning layer: both boxes are Regional Centre. Post-ABS-472 it
        # declares per-feature attribution, and both features resolve to the
        # by-law we hold — so the zone side of every profile below is clean.
        zoning = ExternalDataset(
            name="halifax_zoning_boundaries",
            publisher="Halifax Regional Municipality",
            format="geojson",
            content_hash="hash-zoning",
            crs="EPSG:4326",
            feature_count=2,
            linked_document_id=rc.id,
            linked_fragment_id=zoning_schedule.id,
            linked_fragment_citation="Zoning Schedule",
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={
                "links_to": {
                    "document_match": {"municipality": "HRM", "bylaw_name": RC_BYLAW},
                    "fragment_citation": "Zoning Schedule",
                    "governing_bylaw_from": {
                        "name_attribute": "bylaw_area_name",
                        "code_attribute": "bylaw_area_code",
                    },
                }
            },
        )
        # The height-precinct layer: same two boxes, two different by-laws.
        heights = ExternalDataset(
            name="halifax_height_precincts",
            publisher="Halifax Regional Municipality",
            format="geojson",
            content_hash="hash-heights",
            crs="EPSG:4326",
            feature_count=2,
            linked_document_id=rc.id,
            linked_fragment_id=schedule_15.id,
            linked_fragment_citation="Schedule 15",
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={
                "links_to": {
                    "document_match": {"municipality": "HRM", "bylaw_name": RC_BYLAW},
                    "fragment_citation": "Schedule 15",
                    "governing_bylaw_from": {
                        "name_attribute": "bylaw_area_name",
                        "code_attribute": "bylaw_area_code",
                    },
                }
            },
        )
        session.add_all([zoning, heights])
        session.flush()

        for area in ("rc", "sha"):
            session.add(
                ExternalDatasetFeature(
                    external_dataset_id=zoning.id,
                    feature_key=f"zoning-{area}",
                    attributes_json={},
                    canonical_attributes_json={
                        "zone_code": "HR-2",
                        "bylaw_area_id": 23,
                        "bylaw_area_name": "Regional Centre Land Use By-law",
                        "bylaw_area_code": "hrm:RC",
                    },
                    geometry_geojson=_box(area),
                    geometry_bbox_json=_bbox(area),
                    parse_status=ParseStatus.PARSED,
                    metadata_json={},
                )
            )

        for area, storeys, bylaw_id, bylaw_name, code in (
            ("rc", 20, 23, "Regional Centre Land Use By-law", "hrm:RC"),
            ("sha", 5, 24, SHA_BYLAW, "hrm:SHA"),
        ):
            session.add(
                ExternalDatasetFeature(
                    external_dataset_id=heights.id,
                    feature_key=f"height-{area}",
                    attributes_json={},
                    canonical_attributes_json={
                        "max_height_storeys": storeys,
                        "bylaw_area_id": bylaw_id,
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


def _height_overlay(profile):
    return next(o for o in profile.overlays if o.kind == "height_precinct")


# ---------------------------------------------------------------------------
# The defect: 48 SHA precincts served as Schedule 15 of the RCLUB.
# ---------------------------------------------------------------------------


def test_sha_precinct_is_not_cited_to_schedule_15(tmp_path: Path) -> None:
    """The measured case. The precinct is real and HRM published it, but it
    belongs to a by-law we do not hold — so no citation, and above all not
    Schedule 15 of the Regional Centre LUB, which does not govern it."""
    profile = _profile(_seed(tmp_path), "50 Accelerator Way")

    assert profile.height_precinct == "HP-5st"
    overlay = _height_overlay(profile)
    assert overlay.citation is None
    assert overlay.governing_bylaw == SHA_BYLAW
    assert overlay.governing_bylaw_held is False
    assert [c for c in profile.citations if "height_precinct" in c.backs] == []
    assert all(c.citation_label != "Schedule 15" for c in profile.citations)


def test_the_zone_stays_held_and_cited_while_its_height_precinct_does_not(
    tmp_path: Path,
) -> None:
    """What makes this distinct from ABS-472, and easier to miss: nothing is
    wrong with the zone. It is Regional Centre, held, and correctly cited.
    Only the overlay over it comes from a by-law we do not have — so a
    zone-level check reports a clean profile."""
    profile = _profile(_seed(tmp_path), "50 Accelerator Way")

    assert profile.zone == "HR-2"
    assert profile.governing_bylaw_status == "held"
    zone_citation = next(c for c in profile.citations if "zone" in c.backs)
    assert zone_citation.bylaw_name == RC_BYLAW
    # ...and yet the profile is not clean.
    assert profile.caveats


def test_sha_precinct_caveat_names_the_bylaw_and_refuses_schedule_15(
    tmp_path: Path,
) -> None:
    """The caveat has to do two things the zone caveat does not: name which
    overlay is affected, and refuse the *equivalent held schedule* by name.
    'Schedule 15 says 26 m' is the wrong answer available closest to hand."""
    profile = _profile(_seed(tmp_path), "50 Accelerator Way")

    caveat = next(c for c in profile.caveats if SHA_BYLAW in c)
    assert "height precinct" in caveat
    assert "HP-5st" in caveat
    assert "Schedule 15" in caveat
    assert "NOT in this corpus" in caveat
    # A rooftop geocode must not suppress it: this is not a precision problem.
    assert profile.resolution_quality == "rooftop"


def test_regional_centre_precinct_is_unchanged(tmp_path: Path) -> None:
    """The other 1,774 precincts keep the Schedule 15 citation they had."""
    profile = _profile(_seed(tmp_path), "100 Robie Street")

    assert profile.height_precinct == "HP-20st"
    overlay = _height_overlay(profile)
    assert overlay.citation == "Schedule 15"
    assert overlay.governing_bylaw_held is True
    height_citation = next(c for c in profile.citations if "height_precinct" in c.backs)
    assert height_citation.citation_path == "rc.schedule_15"
    assert profile.caveats == []


def test_ingesting_the_sha_bylaw_restores_the_citation(tmp_path: Path) -> None:
    """The refusal is about coverage, not about the precinct. Hold the
    Suburban Housing Accelerator LUB and its precincts cite it — its own
    Schedule 15, not the Regional Centre's."""
    profile = _profile(_seed(tmp_path, hold_sha=True), "50 Accelerator Way")

    overlay = _height_overlay(profile)
    assert overlay.governing_bylaw_held is True
    height_citation = next(c for c in profile.citations if "height_precinct" in c.backs)
    assert height_citation.bylaw_name == SHA_BYLAW
    assert height_citation.citation_path == "sha.schedule_15"
    assert profile.caveats == []


# ---------------------------------------------------------------------------
# What the model actually sees. The refusal is worth nothing if it is
# compacted away before it reaches the agent.
# ---------------------------------------------------------------------------


def test_compact_profile_keeps_the_unheld_attribution(tmp_path: Path) -> None:
    """The overlay's citation is already stripped by the time it is
    compacted, and a missing citation on its own reads as 'none handy'
    rather than 'wrong by-law'. The attribution has to survive too."""
    profile = _profile(_seed(tmp_path), "50 Accelerator Way")
    payload = compact_address_profile(profile)

    overlay = next(o for o in payload["overlays"] if o["kind"] == "height_precinct")
    assert overlay["governing_bylaw"] == SHA_BYLAW
    assert overlay["governing_bylaw_held"] is False
    assert "citation" not in overlay
    # The zone-level status is 'held' here, so the instruction must come from
    # the overlay — this is the branch ABS-472's zone-only check never took.
    assert SHA_BYLAW in payload["instruction"]
    assert "do NOT substitute" in payload["instruction"]


def test_compact_profile_of_a_held_precinct_adds_nothing(tmp_path: Path) -> None:
    """No attribution noise on the 1,774 features that were always fine."""
    profile = _profile(_seed(tmp_path), "100 Robie Street")
    payload = compact_address_profile(profile)

    overlay = next(o for o in payload["overlays"] if o["kind"] == "height_precinct")
    assert "governing_bylaw" not in overlay
    assert "governing_bylaw_held" not in overlay
    assert "instruction" not in payload


# ---------------------------------------------------------------------------
# The turn-level disclosure.
# ---------------------------------------------------------------------------


class _Call:
    """Minimal stand-in for a recorded tool call."""

    def __init__(self, payload: dict) -> None:
        self.tool_name = "get_address_profile"
        self.error = None
        self.output = json.dumps(payload)


def test_turn_discloses_the_unheld_overlay_bylaw(tmp_path: Path) -> None:
    """ABS-472's suffix keys off the zone-level status, which is 'held' here.
    Without an overlay-aware check the turn said nothing at all."""
    profile = _profile(_seed(tmp_path), "50 Accelerator Way")
    payload = compact_address_profile(profile)

    suffix = governing_bylaw_suffix([_Call(payload)])
    assert suffix is not None
    assert SHA_BYLAW in suffix
    assert "height precinct" in suffix
    # It must not overclaim: the parcel is NOT governed by the SHA LUB, only
    # its height precinct is. That wording belongs to the zone-level suffix.
    assert "this parcel is governed by" not in suffix.lower()


def test_turn_says_nothing_for_a_held_precinct(tmp_path: Path) -> None:
    profile = _profile(_seed(tmp_path), "100 Robie Street")
    payload = compact_address_profile(profile)

    assert governing_bylaw_suffix([_Call(payload)]) is None


# ---------------------------------------------------------------------------
# The corpus-level view. ABS-472 built this over one layer; the point of the
# ABS-473 audit is that it has to answer for every layer that carries
# attribution, or the next one added reintroduces the defect silently.
# ---------------------------------------------------------------------------


def test_coverage_audit_reports_the_height_layer_too(tmp_path: Path) -> None:
    db_url = _seed(tmp_path)
    with session_scope(db_url) as session:
        report = audit_governing_bylaw_coverage(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )

    assert report.datasets_checked == 2
    assert report.features_checked == 4
    assert report.unheld_features == 1
    gap = next(row for row in report.unheld if row.governing_bylaw == SHA_BYLAW)
    assert gap.dataset_name == "halifax_height_precincts"
    assert gap.governing_bylaw_code == "hrm:SHA"
    assert gap.feature_count == 1


@pytest.mark.parametrize(
    "address,held",
    [("100 Robie Street", True), ("50 Accelerator Way", False)],
)
def test_every_matched_precinct_states_its_governing_bylaw(
    tmp_path: Path, address: str, held: bool
) -> None:
    """No precinct may come back with a label and no statement about which
    by-law it belongs to — that silence is the defect."""
    overlay = _height_overlay(_profile(_seed(tmp_path), address))
    assert overlay.label is not None
    assert overlay.governing_bylaw is not None
    assert overlay.governing_bylaw_held is held
