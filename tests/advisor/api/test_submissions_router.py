"""ABS-53: submissions router tests.

Mount the router on a minimal FastAPI app + TestClient. DB is a
fresh sqlite per test. The evaluator is a no-op stub that returns a
canned `EvaluationResponse` shape so the /evaluate path exercises
the persistence + response wiring without standing up the retrieval
stack.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from advisor.api.submissions_router import build_submissions_router
from advisor.db.models import User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer2.compliance.db.models import (
    ApprovalDecision,
    Parcel,
    Submission,
    SubmissionAttributeSource,
)

# Force the IFC extractor to be registered (the router does this on import,
# but we also need access to the synthetic IFC builder).
import layer1.parsers.ifc_submission  # noqa: F401
from fixtures.submissions.synthetic_ifc import (
    SyntheticBuildingSpec,
    SyntheticSpace,
    write_synthetic_ifc,
)


# Halifax UTM 20N base point — the synthetic IFC and parcel both
# anchor here so ABS-52's centroid sanity check passes during the
# derived-attribute computation in the upload path.
_HALIFAX_BASE_EASTING = 454500.0
_HALIFAX_BASE_NORTHING = 4946000.0


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@dataclass
class _Wired:
    app: FastAPI
    client: TestClient
    db_url: str
    user_id: int


@pytest.fixture()
def wired(tmp_path: Path) -> Iterator[_Wired]:
    db_url = f"sqlite:///{tmp_path / 'sub.db'}"
    create_all(db_url)

    # Seed a user + a Halifax parcel up-front.
    with session_scope(db_url) as session:
        user = User(
            clerk_user_id="test-user-1",
            email="test@example.com",
            full_name="Test User",
        )
        session.add(user)
        session.flush()
        user_id = user.id

        parcel = Parcel(
            jurisdiction="HRM",
            parcel_identifier="TEST-001",
            geometry_geojson=_halifax_parcel_polygon_4326(),
            area_m2=1200.0,
        )
        session.add(parcel)
        session.flush()
        parcel_id = parcel.id

    @contextmanager
    def _db_factory():
        with session_scope(db_url) as session:
            yield session

    def _user_dep() -> User:
        # Resolve by id — tests pass headers but we cheat here since
        # we control the wiring. Production wires require_user which
        # honours X-Test-User-Id; for unit tests we just return the user.
        with session_scope(db_url) as session:
            return session.get(User, user_id)

    def _user_resolver(auth_session: Any, db) -> User:
        if isinstance(auth_session, User):
            return db.get(User, auth_session.id)
        return db.get(User, user_id)

    # Stub evaluator: returns a canned response with persisted decision.
    def _evaluator_factory(db):
        def _evaluate(request: Any):
            from layer2.compliance.evaluator import EvaluationResponse

            response = EvaluationResponse(
                overall_status="compliant",
                attribute_results=[],
                unevaluated_attributes=[],
                notes=["stub evaluator"],
            )
            # Persist an approval_decision row so /matrix can read it.
            db.add(
                ApprovalDecision(
                    submission_id=request.submission_id,
                    evaluator_version="test-stub",
                    decision_summary_json=response.to_json(),
                )
            )
            db.flush()
            return response

        return _evaluate

    router = build_submissions_router(
        db_session_factory=_db_factory,
        user_dependency=_user_dep,
        user_resolver=_user_resolver,
        evaluator_factory=_evaluator_factory,
        storage_dir=tmp_path / "uploads",
    )
    app = FastAPI()
    app.include_router(router)

    # Expose the seeded parcel_id on the user dep result so tests can use it.
    with TestClient(app) as client:
        yield _Wired(app=app, client=client, db_url=db_url, user_id=user_id)


def _halifax_parcel_polygon_4326() -> dict[str, Any]:
    from pyproj import Transformer

    transformer = Transformer.from_crs(2961, 4326, always_xy=True)
    corners = [
        (_HALIFAX_BASE_EASTING - 15, _HALIFAX_BASE_NORTHING - 20),
        (_HALIFAX_BASE_EASTING + 15, _HALIFAX_BASE_NORTHING - 20),
        (_HALIFAX_BASE_EASTING + 15, _HALIFAX_BASE_NORTHING + 20),
        (_HALIFAX_BASE_EASTING - 15, _HALIFAX_BASE_NORTHING + 20),
    ]
    pts = [list(transformer.transform(e, n)) for e, n in corners]
    pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


def _make_ifc(tmp_path: Path, name: str = "demo.ifc") -> Path:
    spec = SyntheticBuildingSpec(
        overall_height_m=9.0,
        storey_gross_planned_area_m2=[200.0, 200.0],
        spaces=[
            SyntheticSpace(name="Apt 1", occupancy_type="Residential Unit"),
        ],
        footprint_coords=[(-5.0, -4.0), (5.0, -4.0), (5.0, 4.0), (-5.0, 4.0)],
        world_origin=(_HALIFAX_BASE_EASTING, _HALIFAX_BASE_NORTHING, 0.0),
    )
    return write_synthetic_ifc(spec, tmp_path / name)


# ----------------------------------------------------------------------
# Upload
# ----------------------------------------------------------------------


def test_upload_happy_path_returns_submission_and_attributes(
    wired: _Wired, tmp_path: Path
):
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        response = wired.client.post(
            "/v1/submissions",
            files={"file": ("demo.ifc", f, "application/octet-stream")},
            data={"parcel_address": "TEST-001"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "draft"  # evaluator not auto-run
    assert body["source_type"] == "ifc"
    keys = {a["attribute_key"] for a in body["attributes"]}
    # Extracted (from IFC) + derived (from ABS-51) both present.
    assert "building_height_m" in keys
    assert "lot_coverage_percent" in keys
    assert "front_setback_m" not in keys  # no centerlines seeded → skipped


def test_upload_requires_parcel(wired: _Wired, tmp_path: Path):
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        response = wired.client.post(
            "/v1/submissions",
            files={"file": ("demo.ifc", f, "application/octet-stream")},
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_parcel"


def test_upload_rejects_unknown_parcel(wired: _Wired, tmp_path: Path):
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        response = wired.client.post(
            "/v1/submissions",
            files={"file": ("demo.ifc", f, "application/octet-stream")},
            data={"parcel_address": "DOES-NOT-EXIST"},
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "parcel_not_found"


def test_upload_rejects_non_ifc_file(wired: _Wired, tmp_path: Path):
    other = tmp_path / "not-ifc.rvt"
    other.write_bytes(b"placeholder")
    with other.open("rb") as f:
        response = wired.client.post(
            "/v1/submissions",
            files={"file": ("not-ifc.rvt", f, "application/octet-stream")},
            data={"parcel_address": "TEST-001"},
        )
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "unsupported_file_type"


# ----------------------------------------------------------------------
# Fetch / override / evaluate / matrix
# ----------------------------------------------------------------------


def _upload_one(wired: _Wired, tmp_path: Path) -> int:
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        response = wired.client.post(
            "/v1/submissions",
            files={"file": ("demo.ifc", f, "application/octet-stream")},
            data={"parcel_address": "TEST-001"},
        )
    assert response.status_code == 200
    return response.json()["id"]


def test_get_submission_returns_attributes(wired: _Wired, tmp_path: Path):
    sub_id = _upload_one(wired, tmp_path)
    response = wired.client.get(f"/v1/submissions/{sub_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == sub_id
    assert any(
        a["attribute_key"] == "building_height_m" for a in body["attributes"]
    )


def test_get_submission_404_for_unknown_id(wired: _Wired):
    response = wired.client.get("/v1/submissions/99999")
    assert response.status_code == 404


def test_override_attribute_writes_override_row(wired: _Wired, tmp_path: Path):
    sub_id = _upload_one(wired, tmp_path)
    response = wired.client.patch(
        f"/v1/submissions/{sub_id}/attributes/building_height_m",
        json={"value": 11.5, "unit": "m"},
    )
    assert response.status_code == 200
    body = response.json()
    by_key = {a["attribute_key"]: a for a in body["attributes"]}
    overridden = by_key["building_height_m"]
    assert overridden["value"] == 11.5
    assert overridden["source"] == "override"
    assert overridden["confidence"] == 1.0
    # Audit trail captured.
    assert "overridden_from" in (overridden["evidence"] or {})


def test_override_then_evaluate_uses_override_value(
    wired: _Wired, tmp_path: Path
):
    sub_id = _upload_one(wired, tmp_path)
    wired.client.patch(
        f"/v1/submissions/{sub_id}/attributes/building_height_m",
        json={"value": 11.5, "unit": "m"},
    )
    response = wired.client.post(f"/v1/submissions/{sub_id}/evaluate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"]["overall_status"] == "compliant"
    # Submission status transitions to EVALUATED.
    fetch = wired.client.get(f"/v1/submissions/{sub_id}")
    assert fetch.json()["status"] == "evaluated"


def test_matrix_404_until_evaluator_runs(wired: _Wired, tmp_path: Path):
    sub_id = _upload_one(wired, tmp_path)
    response = wired.client.get(f"/v1/submissions/{sub_id}/matrix")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "no_decision"


def test_matrix_returns_decision_after_evaluator(wired: _Wired, tmp_path: Path):
    sub_id = _upload_one(wired, tmp_path)
    wired.client.post(f"/v1/submissions/{sub_id}/evaluate")
    response = wired.client.get(f"/v1/submissions/{sub_id}/matrix")
    assert response.status_code == 200
    assert response.json()["overall_status"] == "compliant"
