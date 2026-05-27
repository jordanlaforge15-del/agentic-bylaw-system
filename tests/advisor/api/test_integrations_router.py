"""Unit tests for advisor.api.integrations_router (ABS-59).

Mounts the integrations router on a minimal FastAPI + TestClient.
Uses an in-memory SQLite DB and an issued API key for auth.
Parallels test_submissions_router.py but exercises the API-key path.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from advisor.api.api_key_auth import issue_api_key
from advisor.api.integrations_router import build_integrations_router
from advisor.db.models import AdvisorApiKey, User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer2.compliance.db.models import (
    ApprovalDecision,
    Parcel,
)

import layer1.parsers.ifc_submission  # noqa: F401 — registers IFC extractor

from fixtures.submissions.synthetic_ifc import (
    SyntheticBuildingSpec,
    SyntheticSpace,
    write_synthetic_ifc,
)

_HALIFAX_BASE_EASTING = 454500.0
_HALIFAX_BASE_NORTHING = 4946000.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _halifax_parcel_polygon_4326() -> dict[str, Any]:
    from pyproj import Transformer

    t = Transformer.from_crs(2961, 4326, always_xy=True)
    corners = [
        (_HALIFAX_BASE_EASTING - 15, _HALIFAX_BASE_NORTHING - 20),
        (_HALIFAX_BASE_EASTING + 15, _HALIFAX_BASE_NORTHING - 20),
        (_HALIFAX_BASE_EASTING + 15, _HALIFAX_BASE_NORTHING + 20),
        (_HALIFAX_BASE_EASTING - 15, _HALIFAX_BASE_NORTHING + 20),
    ]
    pts = [list(t.transform(e, n)) for e, n in corners]
    pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


def _make_ifc(tmp_path: Path, name: str = "test.ifc") -> Path:
    spec = SyntheticBuildingSpec(
        overall_height_m=9.0,
        storey_gross_planned_area_m2=[200.0, 200.0],
        spaces=[SyntheticSpace(name="Office", occupancy_type="Office")],
        footprint_coords=[(-5.0, -4.0), (5.0, -4.0), (5.0, 4.0), (-5.0, 4.0)],
        world_origin=(_HALIFAX_BASE_EASTING, _HALIFAX_BASE_NORTHING, 0.0),
    )
    return write_synthetic_ifc(spec, tmp_path / name)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@dataclass
class _Wired:
    client: TestClient
    raw_key: str
    parcel_id: int
    user_id: int


@pytest.fixture()
def wired(tmp_path: Path) -> Iterator[_Wired]:
    db_url = f"sqlite:///{tmp_path / 'integ.db'}"
    create_all(db_url)

    with session_scope(db_url) as db:
        user = User(
            clerk_user_id="speckle-bot",
            email="speckle@test.example",
            full_name="Speckle Bot",
        )
        db.add(user)
        db.flush()
        user_id = user.id

        parcel = Parcel(
            jurisdiction="HRM",
            parcel_identifier="INTEG-001",
            geometry_geojson=_halifax_parcel_polygon_4326(),
            area_m2=1200.0,
        )
        db.add(parcel)
        db.flush()
        parcel_id = parcel.id

        _, raw_key = issue_api_key(db, user_id=user_id, name="speckle-automate")
        db.commit()

    @contextmanager
    def _db_factory():
        with session_scope(db_url) as session:
            yield session

    def _evaluator_factory(db):
        def _evaluate(request: Any):
            from layer2.compliance.evaluator import EvaluationResponse

            response = EvaluationResponse(
                overall_status="compliant",
                attribute_results=[],
                unevaluated_attributes=[],
                notes=["stub"],
            )
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

    router = build_integrations_router(
        db_session_factory=_db_factory,
        evaluator_factory=_evaluator_factory,
        storage_dir=tmp_path / "uploads",
    )
    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as tc:
        yield _Wired(client=tc, raw_key=raw_key, parcel_id=parcel_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_upload_happy_path(wired: _Wired, tmp_path: Path) -> None:
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        resp = wired.client.post(
            "/v1/integrations/submissions",
            files={"file": ("test.ifc", f, "application/octet-stream")},
            data={"parcel_address": "INTEG-001"},
            headers={"X-ABS-API-Key": wired.raw_key},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert "building_height_m" in {a["attribute_key"] for a in body["attributes"]}


def test_upload_rejects_missing_api_key(wired: _Wired, tmp_path: Path) -> None:
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        resp = wired.client.post(
            "/v1/integrations/submissions",
            files={"file": ("test.ifc", f, "application/octet-stream")},
            data={"parcel_address": "INTEG-001"},
        )
    assert resp.status_code == 401


def test_upload_rejects_bad_api_key(wired: _Wired, tmp_path: Path) -> None:
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        resp = wired.client.post(
            "/v1/integrations/submissions",
            files={"file": ("test.ifc", f, "application/octet-stream")},
            data={"parcel_address": "INTEG-001"},
            headers={"X-ABS-API-Key": "invalid-key"},
        )
    assert resp.status_code == 401


def test_upload_requires_parcel(wired: _Wired, tmp_path: Path) -> None:
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        resp = wired.client.post(
            "/v1/integrations/submissions",
            files={"file": ("test.ifc", f, "application/octet-stream")},
            headers={"X-ABS-API-Key": wired.raw_key},
        )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "missing_parcel"


def test_evaluate_and_matrix(wired: _Wired, tmp_path: Path) -> None:
    ifc = _make_ifc(tmp_path)
    with ifc.open("rb") as f:
        upload_resp = wired.client.post(
            "/v1/integrations/submissions",
            files={"file": ("test.ifc", f, "application/octet-stream")},
            data={"parcel_address": "INTEG-001"},
            headers={"X-ABS-API-Key": wired.raw_key},
        )
    assert upload_resp.status_code == 200
    submission_id = upload_resp.json()["id"]

    eval_resp = wired.client.post(
        f"/v1/integrations/submissions/{submission_id}/evaluate",
        headers={"X-ABS-API-Key": wired.raw_key},
    )
    assert eval_resp.status_code == 200
    assert eval_resp.json()["submission_id"] == submission_id

    matrix_resp = wired.client.get(
        f"/v1/integrations/submissions/{submission_id}/matrix",
        headers={"X-ABS-API-Key": wired.raw_key},
    )
    assert matrix_resp.status_code == 200
