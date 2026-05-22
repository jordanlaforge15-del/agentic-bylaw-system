"""ABS-50 tests — APS extractor against mocked Model Derivative payloads.

Two layers:

1. **Mapping** — call `_PropertyMapper` directly on canned payloads
   and check the resulting attribute list. Exercises every mapper
   branch (height, storeys, GFA, use class, room counts).
2. **End-to-end orchestration** — call `extract_aps` with a stub
   `APSClient` that returns the canned payload, verifying the
   register-with-factory side-effect, the order of operations
   (ensure_bucket → upload → translate → poll → fetch metadata →
   fetch properties), and the result shape.

No real HTTP calls. An operator with real APS credentials can run
`APSClient.from_env().poll_translation(real_urn, ...)` separately to
prove the contract on a real .rvt; that's not in this issue's scope.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Import for the factory-registration side-effect.
from layer1.parsers.aps_submission import (
    APSClient,
    _PropertyMapper,
    _object_id_to_urn,
    extract_aps,
)
from layer1.parsers.submission_factory import extract_submission, get_extractor
from layer1.models.submission_schemas import SubmissionIngestConfig
from layer2.compliance.db.models import (
    SubmissionAttributeSource,
    SubmissionSourceType,
)

from fixtures.submissions.aps_property_payloads import (
    happy_path_payload,
    no_height_payload,
    no_use_class_payload,
    parking_and_bikes_payload,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture()
def fake_rvt(tmp_path: Path) -> Path:
    """A tiny placeholder .rvt — our tests never read its bytes,
    so the contents don't matter; only the path needs to exist."""
    p = tmp_path / "demo.rvt"
    p.write_bytes(b"not a real Revit file")
    return p


class _StubAPSClient:
    """Records call order and returns a canned properties payload."""

    def __init__(self, properties_payload: dict[str, Any]):
        self.properties_payload = properties_payload
        self.calls: list[str] = []

    def derive_bucket_key(self) -> str:
        return "stub-bucket"

    def ensure_bucket(self, bucket_key: str) -> None:
        self.calls.append(f"ensure_bucket:{bucket_key}")

    def upload_object(self, bucket_key, object_key, path) -> str:
        self.calls.append(f"upload_object:{bucket_key}/{object_key}")
        return f"urn:adsk.objects:os.object:{bucket_key}/{object_key}"

    def start_translation(self, urn: str) -> None:
        self.calls.append(f"start_translation:{urn[:24]}…")

    def poll_translation(self, urn: str, *, timeout_s, interval_s) -> None:
        self.calls.append(f"poll_translation:{urn[:24]}…")

    def fetch_metadata_guids(self, urn: str) -> list[dict[str, Any]]:
        self.calls.append(f"fetch_metadata_guids:{urn[:24]}…")
        return [
            {"guid": "view-2d-1", "role": "2d", "name": "Floor Plan"},
            {"guid": "view-3d-1", "role": "3d", "name": "{3D}"},
        ]

    def fetch_properties(self, urn: str, guid: str) -> dict[str, Any]:
        self.calls.append(f"fetch_properties:{guid}")
        return self.properties_payload


# ----------------------------------------------------------------------
# Factory registration
# ----------------------------------------------------------------------


def test_factory_dispatches_rvt_aps_via_registration():
    extractor = get_extractor(SubmissionSourceType.RVT_APS)
    assert extractor is extract_aps


# ----------------------------------------------------------------------
# Mapping
# ----------------------------------------------------------------------


def test_happy_path_mapping_emits_expected_attributes():
    mapper = _PropertyMapper(happy_path_payload(), source_path=Path("demo.rvt"))
    attrs, warnings = mapper.map_all()
    by_key = {a.attribute_key: a for a in attrs}

    assert by_key["building_height_m"].value == pytest.approx(9.5)
    assert by_key["building_height_m"].confidence == 1.0
    assert by_key["building_height_storeys"].value == 2
    # GFA: 2 × 180 m² = 360.
    assert by_key["gross_floor_area_m2"].value == pytest.approx(360.0)
    assert by_key["primary_use_class"].value == "residential"
    assert by_key["primary_use_class"].confidence == 0.4  # heuristic for free-text
    # Two Apartment-named rooms; one Parking Bay.
    assert by_key["residential_unit_count"].value == 2
    assert by_key["parking_stalls_count"].value == 1
    assert by_key["bicycle_stalls_count"].value == 0
    assert warnings == []
    for a in attrs:
        assert a.source == SubmissionAttributeSource.EXTRACTED


def test_missing_height_produces_warning_no_attribute():
    mapper = _PropertyMapper(no_height_payload(), source_path=Path("demo.rvt"))
    attrs, warnings = mapper.map_all()
    keys = {a.attribute_key for a in attrs}
    assert "building_height_m" not in keys
    assert any("BUILDING_HEIGHT" in w for w in warnings)


def test_missing_use_class_produces_warning():
    mapper = _PropertyMapper(no_use_class_payload(), source_path=Path("demo.rvt"))
    attrs, warnings = mapper.map_all()
    keys = {a.attribute_key for a in attrs}
    assert "primary_use_class" not in keys
    assert any("Project Building Type" in w for w in warnings)


def test_room_keyword_buckets_count_correctly():
    mapper = _PropertyMapper(
        parking_and_bikes_payload(), source_path=Path("demo.rvt")
    )
    attrs, warnings = mapper.map_all()
    by_key = {a.attribute_key: a for a in attrs}
    assert by_key["residential_unit_count"].value == 2  # Apartment 1 + Dwelling 2
    assert by_key["parking_stalls_count"].value == 3
    assert by_key["bicycle_stalls_count"].value == 1


# ----------------------------------------------------------------------
# Orchestration (extract_aps with stub client)
# ----------------------------------------------------------------------


def test_extract_aps_calls_client_in_expected_order(fake_rvt: Path):
    client = _StubAPSClient(happy_path_payload())
    result = extract_aps(
        fake_rvt, SubmissionIngestConfig(), aps_client=client
    )
    assert result.source_type == SubmissionSourceType.RVT_APS
    # The order matters: bucket → upload → translate → poll → metadata → properties.
    op_names = [c.split(":", 1)[0] for c in client.calls]
    assert op_names == [
        "ensure_bucket",
        "upload_object",
        "start_translation",
        "poll_translation",
        "fetch_metadata_guids",
        "fetch_properties",
    ]


def test_extract_aps_picks_3d_view_over_2d(fake_rvt: Path):
    client = _StubAPSClient(happy_path_payload())
    extract_aps(fake_rvt, SubmissionIngestConfig(), aps_client=client)
    # The last call is fetch_properties; the GUID should be the 3D view.
    assert any("fetch_properties:view-3d-1" in c for c in client.calls)


def test_extract_aps_raw_metadata_records_provenance(fake_rvt: Path):
    client = _StubAPSClient(happy_path_payload())
    result = extract_aps(
        fake_rvt, SubmissionIngestConfig(), aps_client=client
    )
    extractor_meta = result.raw_metadata["extractor"]
    assert extractor_meta["name"] == "aps-submission"
    assert extractor_meta["bucket_key"] == "stub-bucket"
    assert extractor_meta["metadata_guid"] == "view-3d-1"
    assert "aps_urn" in extractor_meta


def test_extract_aps_factory_dispatch_end_to_end(fake_rvt: Path):
    client = _StubAPSClient(happy_path_payload())
    # The factory path doesn't expose aps_client= directly; call
    # `extract_aps` directly here. The factory dispatch is covered
    # by `test_factory_dispatches_rvt_aps_via_registration`.
    result = extract_aps(
        fake_rvt, SubmissionIngestConfig(), aps_client=client
    )
    keys = {a.attribute_key for a in result.attributes}
    # Verify all six expected mappings are present.
    assert {
        "building_height_m",
        "building_height_storeys",
        "gross_floor_area_m2",
        "primary_use_class",
        "residential_unit_count",
        "parking_stalls_count",
        "bicycle_stalls_count",
    }.issubset(keys)


# ----------------------------------------------------------------------
# Credential handling
# ----------------------------------------------------------------------


def test_apsclient_requires_credentials():
    with pytest.raises(RuntimeError, match="client_id"):
        APSClient(client_id="", client_secret="")


def test_apsclient_from_env_without_credentials_raises(monkeypatch):
    monkeypatch.delenv("APS_CLIENT_ID", raising=False)
    monkeypatch.delenv("APS_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="client_id"):
        APSClient.from_env()


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def test_object_id_to_urn_is_urlsafe_base64_unpadded():
    object_id = "urn:adsk.objects:os.object:bucket/key.rvt"
    urn = _object_id_to_urn(object_id)
    # Real APS URNs are base64-encoded objectIds with `=` padding stripped.
    assert "=" not in urn
    assert "/" not in urn  # urlsafe means / → _, + → -
