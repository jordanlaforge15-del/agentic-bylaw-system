"""Thin async HTTP client for the ABS integration submissions API.

Used by the Speckle Automate function to:
1. Upload an IFC file → get a submission ID.
2. Trigger the evaluator (optional) → get a compliance decision.
3. Fetch the compliance matrix.

All requests carry the ``X-ABS-API-Key`` header; no Clerk JWT needed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120  # seconds — IFC extraction can be slow
_POLL_TIMEOUT = 300  # seconds for evaluation polling


class ABSClientError(Exception):
    """Raised when the ABS API returns an unexpected response."""

    def __init__(self, status_code: int, detail: Any) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"ABS API error {status_code}: {detail}")


class ABSClient:
    """Synchronous HTTP client for the ABS integration endpoints.

    Speckle Automate functions run synchronously (the SDK wraps them),
    so we use ``httpx`` in sync mode for simplicity.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "X-ABS-API-Key": api_key,
        }
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def upload_ifc(
        self,
        ifc_path: Path,
        *,
        parcel_id: int | None = None,
        parcel_address: str | None = None,
    ) -> dict[str, Any]:
        """POST the IFC file to ``/v1/integrations/submissions``.

        Returns the full submission response dict (id, status,
        attributes, etc.).
        """
        if parcel_id is None and not parcel_address:
            raise ValueError("Either parcel_id or parcel_address must be supplied.")

        data: dict[str, Any] = {}
        if parcel_id is not None:
            data["parcel_id"] = parcel_id
        if parcel_address:
            data["parcel_address"] = parcel_address

        with ifc_path.open("rb") as fh:
            files = {"file": (ifc_path.name, fh, "application/octet-stream")}
            response = self._request(
                "POST",
                "/v1/integrations/submissions",
                data=data,
                files=files,
            )
        return response

    def evaluate_submission(self, submission_id: int) -> dict[str, Any]:
        """POST ``/v1/integrations/submissions/{id}/evaluate``.

        Returns the evaluate response (submission_id, decision).
        """
        return self._request(
            "POST",
            f"/v1/integrations/submissions/{submission_id}/evaluate",
        )

    def get_matrix(self, submission_id: int) -> dict[str, Any]:
        """GET ``/v1/integrations/submissions/{id}/matrix``.

        Returns the compliance matrix dict.
        """
        return self._request(
            "GET",
            f"/v1/integrations/submissions/{submission_id}/matrix",
        )

    def health_check(self) -> bool:
        """Return True when the ABS API is reachable and healthy."""
        try:
            resp = httpx.get(
                f"{self._base}/healthz",
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        logger.debug("%s %s", method, url)
        resp = httpx.request(
            method,
            url,
            headers=self._headers,
            data=data,
            files=files,
            json=json,
            timeout=self._timeout,
        )
        if not resp.is_success:
            try:
                detail = resp.json()
            except Exception:  # noqa: BLE001
                detail = resp.text
            raise ABSClientError(resp.status_code, detail)
        return resp.json()


__all__ = ["ABSClient", "ABSClientError"]
