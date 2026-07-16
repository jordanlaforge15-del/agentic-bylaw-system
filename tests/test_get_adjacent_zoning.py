"""ABS-375 — the ``get_adjacent_zoning`` thick tool.

Development-standards and variance reports repeatedly punted rear/side
setback verdicts to the customer ("UNCERTAIN — depends on adjacent zoning")
because the governing setback is conditional on the *abutting* parcel's zone
and the agent had no mid-run way to resolve it. ``get_adjacent_zoning``
closes that gap: it finds the subject parcel, enumerates the parcels touching
it, and resolves each neighbour's zone by intersecting the neighbour centroid
against the zoning overlay — the same spatial join ``get_address_profile``
uses for the subject parcel.

These tests build a synthetic 3×3 parcel grid on sqlite. The centre cell is
the subject parcel; the eight surrounding cells abut it. Two zoning polygons
split the grid so the eastern parcels resolve to a Downtown zone (DH-1) and
the western parcels to a residential zone (ER-3), giving the lookup distinct
neighbour zones and directions to report.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalService
from bylaw_retrieval.retrieval.schemas import AdjacentZoningProfile
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


# 3×3 grid geometry. ``D`` is a cell edge in degrees (~30 m E-W / ~45 m N-S at
# Halifax latitude). The subject parcel is the centre cell (col 1, row 1); its
# centroid is the geocode target for "1250 Robie Street".
_X0 = -63.5920
_Y0 = 44.6480
_D = 0.0004


def _cell_polygon(col: int, row: int) -> dict:
    x = _X0 + col * _D
    y = _Y0 + row * _D
    ring = [
        [x, y],
        [x + _D, y],
        [x + _D, y + _D],
        [x, y + _D],
        [x, y],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _bbox(poly: dict) -> dict:
    xs = [pt[0] for pt in poly["coordinates"][0]]
    ys = [pt[1] for pt in poly["coordinates"][0]]
    return {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}


def _cell_centre(col: int, row: int) -> list[float]:
    return [_X0 + (col + 0.5) * _D, _Y0 + (row + 0.5) * _D]


def _zone_rect(x_min: float, x_max: float) -> dict:
    # A tall rectangle spanning the whole grid vertically; the column range is
    # chosen so the parcel centroids fall on the intended side of the split.
    y_lo = _Y0 - _D
    y_hi = _Y0 + 4 * _D
    ring = [
        [x_min, y_lo],
        [x_max, y_lo],
        [x_max, y_hi],
        [x_min, y_hi],
        [x_min, y_lo],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _add_zoning_fragment(session, *, document_id: int) -> int:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SCHEDULE,
        citation_label="Zoning Schedule",
        citation_path="zoning_schedule",
        page_start=500,
        page_end=500,
        text="Zoning Schedule.",
        parse_status=ParseStatus.PARSED,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment.id


@pytest.fixture()
def seeded_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'adjacent_zoning.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/synthetic.pdf",
            file_hash="r" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            page_count=500,
        )
        session.add(document)
        session.flush()
        zoning_frag = _add_zoning_fragment(session, document_id=document.id)

        # Zoning overlay: DH-1 covers columns 1-2 (x >= _X0 + _D); ER-3 covers
        # column 0 (x < _X0 + _D). A linked_fragment binds it so the overlay is
        # visible to _scoped_linked_datasets and classified role 'zone' by name.
        zoning = ExternalDataset(
            name="halifax_zoning_boundaries",
            publisher="Test",
            format="geojson",
            content_hash="hash-zoning",
            crs="EPSG:4326",
            feature_count=2,
            linked_fragment_id=zoning_frag,
            linked_fragment_citation="Zoning Schedule",
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(zoning)
        session.flush()
        dh_poly = _zone_rect(_X0 + _D, _X0 + 4 * _D)
        er_poly = _zone_rect(_X0 - _D, _X0 + _D)
        session.add(
            ExternalDatasetFeature(
                external_dataset_id=zoning.id,
                feature_key="zone-dh1",
                attributes_json={},
                canonical_attributes_json={"zone_code": "DH-1"},
                geometry_geojson=dh_poly,
                geometry_bbox_json=_bbox(dh_poly),
                parse_status=ParseStatus.PARSED,
                metadata_json={},
            )
        )
        session.add(
            ExternalDatasetFeature(
                external_dataset_id=zoning.id,
                feature_key="zone-er3",
                attributes_json={},
                canonical_attributes_json={"zone_code": "ER-3"},
                geometry_geojson=er_poly,
                geometry_bbox_json=_bbox(er_poly),
                parse_status=ParseStatus.PARSED,
                metadata_json={},
            )
        )

        # Parcels dataset — role-tagged base geography (no linked fragment). A
        # 3×3 grid; centre cell is the subject, the other eight abut it.
        parcels = ExternalDataset(
            name="halifax_property_parcels",
            publisher="Test",
            format="geojson",
            content_hash="hash-parcels",
            crs="EPSG:4326",
            feature_count=9,
            linked_fragment_id=None,
            linked_fragment_citation=None,
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={"role": "property_parcels"},
        )
        session.add(parcels)
        session.flush()
        for col in range(3):
            for row in range(3):
                poly = _cell_polygon(col, row)
                is_subject = col == 1 and row == 1
                session.add(
                    ExternalDatasetFeature(
                        external_dataset_id=parcels.id,
                        feature_key=f"parcel-{col}-{row}",
                        attributes_json={},
                        canonical_attributes_json={
                            "parcel_id": f"0000{col}{row}"
                        },
                        geometry_geojson=poly,
                        geometry_bbox_json=_bbox(poly),
                        parse_status=ParseStatus.PARSED,
                        metadata_json={"subject": is_subject},
                    )
                )

        # Geocode cache: "1250 Robie Street" -> the centre of the subject cell.
        session.add(
            GeocodeCache(
                normalized_text="civic:1250 robie st",
                raw_text="1250 Robie Street",
                kind="civic_address",
                status="linked",
                resolver="test_seed",
                geometry_geojson={
                    "type": "Point",
                    "coordinates": _cell_centre(1, 1),
                },
                confidence=1.0,
                detail=None,
                metadata_json={},
            )
        )

    return db_url


def test_resolves_abutting_parcel_zones(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        service = RetrievalService(session)
        profile = service.get_adjacent_zoning("1250 Robie Street")

    assert isinstance(profile, AdjacentZoningProfile)
    assert profile.unresolvable is False
    assert profile.subject_zone == "DH-1"
    # All eight surrounding cells abut the subject.
    assert len(profile.neighbours) == 8
    # East column (col 2) is DH-1; west column (col 0) is ER-3.
    assert profile.distinct_neighbour_zones == ["DH-1", "ER-3"]
    # Every neighbour resolved a zone (no null centroids in this clean grid).
    assert all(n.zone in {"DH-1", "ER-3"} for n in profile.neighbours)
    # Directions are populated so a report can pin a setback to a lot line.
    assert all(n.direction is not None for n in profile.neighbours)
    # The zone read is cited back to the zoning schedule.
    assert profile.citation is not None
    assert profile.citation.citation_path == "zoning_schedule"


def test_east_neighbour_is_downtown_west_is_residential(seeded_db: str) -> None:
    """The setback that depends on the abutting zone must be pinnable to the
    correct lot line — so the direction↔zone mapping has to be right."""
    with session_scope(seeded_db) as session:
        service = RetrievalService(session)
        profile = service.get_adjacent_zoning("1250 Robie Street")

    by_pid = {n.pid: n for n in profile.neighbours}
    # Parcel (col 2, row 1) is due east of the subject and Downtown.
    east = by_pid["000021"]
    assert east.zone == "DH-1"
    assert east.direction == "E"
    # Parcel (col 0, row 1) is due west of the subject and residential.
    west = by_pid["000001"]
    assert west.zone == "ER-3"
    assert west.direction == "W"


def test_unresolvable_address_is_typed_not_an_error(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        service = RetrievalService(session)
        profile = service.get_adjacent_zoning("not a real place 99999")

    assert profile.unresolvable is True
    assert profile.neighbours == []


def test_missing_parcels_dataset_returns_note(tmp_path: Path) -> None:
    """With no parcels dataset ingested the lookup can't enumerate neighbours;
    it must say so in a note rather than raise or silently return empty."""
    db_url = f"sqlite:///{tmp_path / 'no_parcels.db'}"
    create_layer1(db_url)
    create_layer2(db_url)
    with session_scope(db_url) as session:
        session.add(
            GeocodeCache(
                normalized_text="civic:1250 robie st",
                raw_text="1250 Robie Street",
                kind="civic_address",
                status="linked",
                resolver="test_seed",
                geometry_geojson={
                    "type": "Point",
                    "coordinates": _cell_centre(1, 1),
                },
                confidence=1.0,
                detail=None,
                metadata_json={},
            )
        )
    with session_scope(db_url) as session:
        service = RetrievalService(session)
        profile = service.get_adjacent_zoning("1250 Robie Street")

    assert profile.unresolvable is False
    assert profile.neighbours == []
    assert profile.note is not None
    assert "parcel" in profile.note.lower()
