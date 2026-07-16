"""Unit tests for abs_client.ABSClient.

Uses respx to intercept httpx requests — no network calls.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
import pytest
import respx

# Add parent dir to path so abs_client is importable without installing.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from abs_client import ABSClient, ABSClientError

BASE_URL = "http://abs.test"


@pytest.fixture()
def client() -> ABSClient:
    return ABSClient(BASE_URL, api_key="test-key-abc")


@respx.mock
def test_upload_ifc_posts_multipart(client: ABSClient, tmp_path: Path) -> None:
    """upload_ifc sends a multipart POST with the IFC file and parcel_id."""
    ifc_file = tmp_path / "model.ifc"
    ifc_file.write_bytes(b"IFC dummy")

    route = respx.post(f"{BASE_URL}/v1/integrations/submissions").mock(
        return_value=httpx.Response(
            200,
            json={"id": 42, "status": "DRAFT", "attributes": [], "warnings": []},
        )
    )

    result = client.upload_ifc(ifc_file, parcel_id=7)

    assert route.called
    assert result["id"] == 42
    req = route.calls[0].request
    assert req.headers["X-ABS-API-Key"] == "test-key-abc"
    assert b"model.ifc" in req.content


@respx.mock
def test_upload_ifc_requires_parcel(client: ABSClient, tmp_path: Path) -> None:
    ifc_file = tmp_path / "model.ifc"
    ifc_file.write_bytes(b"IFC")
    with pytest.raises(ValueError, match="parcel"):
        client.upload_ifc(ifc_file)


@respx.mock
def test_upload_ifc_raises_on_http_error(client: ABSClient, tmp_path: Path) -> None:
    ifc_file = tmp_path / "model.ifc"
    ifc_file.write_bytes(b"IFC")
    respx.post(f"{BASE_URL}/v1/integrations/submissions").mock(
        return_value=httpx.Response(401, json={"detail": "invalid_api_key"})
    )
    with pytest.raises(ABSClientError) as exc_info:
        client.upload_ifc(ifc_file, parcel_id=1)
    assert exc_info.value.status_code == 401


@respx.mock
def test_evaluate_submission(client: ABSClient) -> None:
    respx.post(f"{BASE_URL}/v1/integrations/submissions/42/evaluate").mock(
        return_value=httpx.Response(
            200,
            json={"submission_id": 42, "decision": {"clause_results": []}},
        )
    )
    result = client.evaluate_submission(42)
    assert result["submission_id"] == 42


@respx.mock
def test_get_matrix(client: ABSClient) -> None:
    matrix = {"clause_results": [{"clause_ref": "4.1", "passed": True}]}
    respx.get(f"{BASE_URL}/v1/integrations/submissions/42/matrix").mock(
        return_value=httpx.Response(200, json=matrix)
    )
    result = client.get_matrix(42)
    assert result["clause_results"][0]["clause_ref"] == "4.1"


@respx.mock
def test_health_check_returns_true_on_200(client: ABSClient) -> None:
    respx.get(f"{BASE_URL}/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    assert client.health_check() is True


@respx.mock
def test_health_check_returns_false_on_error(client: ABSClient) -> None:
    respx.get(f"{BASE_URL}/healthz").mock(side_effect=httpx.ConnectError("refused"))
    assert client.health_check() is False
