"""Unit tests for the main automate_function entry point.

All Speckle SDK calls are mocked so these run without a real Speckle
workspace.  The tests focus on the branching logic: healthy API /
unhealthy API, IFC download success / failure, evaluator pass / fail.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import httpx
import pytest
import respx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from abs_client import ABSClientError
from inputs import FunctionInputs


BASE_URL = "http://abs.test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inputs(**overrides) -> FunctionInputs:
    defaults = dict(
        abs_api_url=BASE_URL,
        abs_api_key="secret",
        parcel_id=1,
        parcel_address=None,
        run_evaluator=True,
    )
    defaults.update(overrides)
    return FunctionInputs(**defaults)


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.automation_run_data.version_id = "ver123"
    ctx.automation_run_data.project_id = "proj456"
    ctx.speckle_client.server.get_version_blobs.return_value = []
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@respx.mock
def test_marks_failed_when_api_unreachable() -> None:
    from main import automate_function

    respx.get(f"{BASE_URL}/healthz").mock(side_effect=httpx.ConnectError("refused"))

    ctx = _make_context()
    automate_function(ctx, _make_inputs())

    ctx.mark_run_failed.assert_called_once()
    assert BASE_URL in ctx.mark_run_failed.call_args[0][0]


@respx.mock
def test_marks_failed_when_ifc_download_errors() -> None:
    from main import automate_function

    respx.get(f"{BASE_URL}/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    ctx = _make_context()
    # Receiving the Speckle object raises
    with patch("main._download_ifc", side_effect=RuntimeError("network error")):
        automate_function(ctx, _make_inputs())

    ctx.mark_run_failed.assert_called_once()
    assert "network error" in ctx.mark_run_failed.call_args[0][0]


@respx.mock
def test_marks_failed_on_upload_401(tmp_path: Path) -> None:
    from main import automate_function

    respx.get(f"{BASE_URL}/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    respx.post(f"{BASE_URL}/v1/integrations/submissions").mock(
        return_value=httpx.Response(401, json={"detail": "invalid_api_key"})
    )

    fake_ifc = tmp_path / "model.ifc"
    fake_ifc.write_bytes(b"IFC")
    ctx = _make_context()

    with patch("main._download_ifc", return_value=fake_ifc):
        automate_function(ctx, _make_inputs())

    ctx.mark_run_failed.assert_called_once()
    assert "401" in ctx.mark_run_failed.call_args[0][0]


@respx.mock
def test_success_no_evaluator(tmp_path: Path) -> None:
    from main import automate_function

    respx.get(f"{BASE_URL}/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    respx.post(f"{BASE_URL}/v1/integrations/submissions").mock(
        return_value=httpx.Response(
            200,
            json={"id": 10, "status": "DRAFT", "attributes": [{"attribute_key": "floor_area_m2", "value": 120}], "warnings": []},
        )
    )

    fake_ifc = tmp_path / "model.ifc"
    fake_ifc.write_bytes(b"IFC")
    ctx = _make_context()

    with patch("main._download_ifc", return_value=fake_ifc):
        automate_function(ctx, _make_inputs(run_evaluator=False))

    ctx.mark_run_success.assert_called_once()
    assert "10" in ctx.mark_run_success.call_args[0][0]


@respx.mock
def test_compliance_pass(tmp_path: Path) -> None:
    from main import automate_function

    respx.get(f"{BASE_URL}/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    respx.post(f"{BASE_URL}/v1/integrations/submissions").mock(
        return_value=httpx.Response(
            200,
            json={"id": 10, "status": "DRAFT", "attributes": [], "warnings": []},
        )
    )
    respx.post(f"{BASE_URL}/v1/integrations/submissions/10/evaluate").mock(
        return_value=httpx.Response(
            200,
            json={
                "submission_id": 10,
                "decision": {
                    "clause_results": [
                        {"clause_ref": "4.1", "passed": True, "message": "OK"},
                    ]
                },
            },
        )
    )

    fake_ifc = tmp_path / "model.ifc"
    fake_ifc.write_bytes(b"IFC")
    ctx = _make_context()

    with patch("main._download_ifc", return_value=fake_ifc):
        automate_function(ctx, _make_inputs())

    ctx.mark_run_success.assert_called_once()
    assert "PASSED" in ctx.mark_run_success.call_args[0][0]


@respx.mock
def test_compliance_fail(tmp_path: Path) -> None:
    from main import automate_function

    respx.get(f"{BASE_URL}/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    respx.post(f"{BASE_URL}/v1/integrations/submissions").mock(
        return_value=httpx.Response(
            200,
            json={"id": 10, "status": "DRAFT", "attributes": [], "warnings": []},
        )
    )
    respx.post(f"{BASE_URL}/v1/integrations/submissions/10/evaluate").mock(
        return_value=httpx.Response(
            200,
            json={
                "submission_id": 10,
                "decision": {
                    "clause_results": [
                        {"clause_ref": "4.1", "passed": True, "message": "OK"},
                        {
                            "clause_ref": "5.2",
                            "passed": False,
                            "message": "Maximum height exceeded",
                            "element_ids": ["elem-guid-1"],
                        },
                    ]
                },
            },
        )
    )

    fake_ifc = tmp_path / "model.ifc"
    fake_ifc.write_bytes(b"IFC")
    ctx = _make_context()

    with patch("main._download_ifc", return_value=fake_ifc):
        automate_function(ctx, _make_inputs())

    ctx.mark_run_failed.assert_called_once()
    assert "FAILED" in ctx.mark_run_failed.call_args[0][0]
    # attach_error_to_objects called for the failing clause
    ctx.attach_error_to_objects.assert_called_once()
    kwargs = ctx.attach_error_to_objects.call_args[1]
    assert "5.2" in kwargs["message"]
    assert "elem-guid-1" in kwargs["object_ids"]
