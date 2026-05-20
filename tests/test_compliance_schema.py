"""Smoke + ORM coverage for the Phase-1 compliance schema.

The migration itself is exercised in production via ``alembic upgrade``;
this suite exercises the resulting tables through the ORM so a future
schema drift (forgotten relationship, mismatched enum) trips a unit
test rather than a deploy. Everything runs on sqlite via ``create_all``
to keep the suite cheap.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from layer1.db.base import Document, ExternalDataset, ExternalDatasetFeature, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer2.compliance.db.models import (
    ApprovalDecision,
    Parcel,
    Submission,
    SubmissionAttribute,
    SubmissionAttributeSource,
    SubmissionSourceType,
    SubmissionStatus,
)


def _make_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'compliance.db'}"
    create_all(db_url)
    return db_url


def _seed_document(session) -> int:
    doc = Document(
        municipality="HRM",
        bylaw_name="Test Bylaw",
        source_path="test.pdf",
        file_hash="abc",
        mime_type="application/pdf",
        page_count=1,
        parser_version="test",
    )
    session.add(doc)
    session.flush()
    return doc.id


def test_source_fragment_carries_attribute_tags(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id = _seed_document(session)
        fragment = SourceFragment(
            document_id=document_id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.2.1",
            page_start=1,
            page_end=1,
            text="A front yard of not less than 4.5 m shall be provided.",
            parse_status=ParseStatus.PARSED,
            confidence=0.9,
            attribute_tags=["front_setback_m"],
        )
        session.add(fragment)
        session.flush()
        fragment_id = fragment.id

    with session_scope(db_url) as session:
        reloaded = session.get(SourceFragment, fragment_id)
        assert reloaded is not None
        # MutableList round-trips: ORM hands back a list-like object the
        # caller can both read and mutate; assert both halves.
        assert list(reloaded.attribute_tags) == ["front_setback_m"]
        reloaded.attribute_tags.append("side_setback_left_m")

    with session_scope(db_url) as session:
        reloaded = session.get(SourceFragment, fragment_id)
        assert reloaded is not None
        assert sorted(reloaded.attribute_tags) == [
            "front_setback_m",
            "side_setback_left_m",
        ]


def test_parcel_unique_per_jurisdiction(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        session.add(
            Parcel(
                jurisdiction="HRM",
                parcel_identifier="00012345",
                zone_code="ER-1",
                area_m2=812.5,
                metadata_json={"source": "halifax_parcels"},
            )
        )
        # Same identifier under a different jurisdiction is fine.
        session.add(
            Parcel(
                jurisdiction="Burnaby",
                parcel_identifier="00012345",
            )
        )

    # Fresh session: we expect the flush to raise, and we cannot use
    # session_scope's auto-commit on a session that's already in a
    # rollback-required state.
    from layer1.db.session import make_session_factory

    session = make_session_factory(db_url)()
    try:
        session.add(
            Parcel(
                jurisdiction="HRM",
                parcel_identifier="00012345",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
    finally:
        session.rollback()
        session.close()


def test_external_dataset_feature_links_to_parcel(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        document_id = _seed_document(session)
        parcel = Parcel(jurisdiction="HRM", parcel_identifier="00099999")
        session.add(parcel)
        dataset = ExternalDataset(
            name="halifax_property_parcels",
            publisher="HRM",
            format="geojson",
            content_hash="hash",
            crs="EPSG:4326",
            feature_count=1,
            linked_document_id=document_id,
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(dataset)
        session.flush()
        feature = ExternalDatasetFeature(
            external_dataset_id=dataset.id,
            feature_key="PID-00099999",
            attributes_json={"PID": "00099999"},
            canonical_attributes_json={"parcel_id": "00099999"},
            geometry_geojson={"type": "Point", "coordinates": [-63.6, 44.65]},
            geometry_bbox_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={},
            parcel_id=parcel.id,
        )
        session.add(feature)
        session.flush()
        feature_id = feature.id

    with session_scope(db_url) as session:
        reloaded = session.get(ExternalDatasetFeature, feature_id)
        assert reloaded is not None
        assert reloaded.parcel_id is not None
        parcel = session.get(Parcel, reloaded.parcel_id)
        assert parcel is not None
        assert parcel.parcel_identifier == "00099999"


def test_submission_roundtrip_with_attributes_and_decision(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        parcel = Parcel(
            jurisdiction="HRM",
            parcel_identifier="00012345",
            zone_code="ER-1",
        )
        session.add(parcel)
        session.flush()

        submission = Submission(
            parcel_id=parcel.id,
            status=SubmissionStatus.DRAFT,
            source_type=SubmissionSourceType.MANUAL,
            metadata_json={"created_via": "advisor_ui"},
        )
        submission.attributes = [
            SubmissionAttribute(
                attribute_key="front_setback_m",
                value_json={"value": 4.5, "unit": "m"},
                source=SubmissionAttributeSource.MANUAL,
            ),
            SubmissionAttribute(
                attribute_key="building_height_m",
                value_json={"value": 9.2, "unit": "m"},
                source=SubmissionAttributeSource.MANUAL,
            ),
        ]
        session.add(submission)
        session.flush()

        decision = ApprovalDecision(
            submission_id=submission.id,
            evaluator_version="phase1-rc1",
            decision_summary_json={
                "overall_status": "compliant",
                "attribute_results": [],
            },
        )
        session.add(decision)
        session.flush()
        submission_id = submission.id

    with session_scope(db_url) as session:
        reloaded = session.get(Submission, submission_id)
        assert reloaded is not None
        assert reloaded.parcel is not None
        assert reloaded.parcel.zone_code == "ER-1"
        assert {a.attribute_key for a in reloaded.attributes} == {
            "front_setback_m",
            "building_height_m",
        }
        assert len(reloaded.decisions) == 1
        assert reloaded.decisions[0].evaluator_version == "phase1-rc1"


def test_submission_attribute_unique_per_key(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        submission = Submission(status=SubmissionStatus.DRAFT)
        session.add(submission)
        session.flush()
        submission_id = submission.id
        session.add(
            SubmissionAttribute(
                submission_id=submission_id,
                attribute_key="front_setback_m",
                value_json={"value": 4.5, "unit": "m"},
            )
        )

    from layer1.db.session import make_session_factory

    session = make_session_factory(db_url)()
    try:
        session.add(
            SubmissionAttribute(
                submission_id=submission_id,
                attribute_key="front_setback_m",
                value_json={"value": 3.0, "unit": "m"},
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
    finally:
        session.rollback()
        session.close()


# NOTE: a full alembic-upgrade-on-sqlite check was tried here and
# removed. Earlier migrations (0012_case_based_billing.py) use
# ``batch_alter_table`` against unnamed constraints which sqlite
# reflection rejects with "Constraint must have a name". That's a
# pre-existing limitation independent of this migration — the
# production target is postgres, where the full chain applies cleanly,
# and the ORM round-trip tests above already cover the new tables.
