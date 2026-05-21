"""ABS-50: Autodesk APS (Forge) Model Derivative → Phase-1 attribute extraction.

Accepts a `.rvt` file directly. Pushes the file to APS Object Storage,
kicks off a Model Derivative translation, polls until done, fetches
the per-element property JSON, and maps Revit BuiltInParameter values
to the Phase-1 taxonomy.

Plugs into the submission factory via import-side-effect — the same
pattern as `layer1.parsers.ifc_submission`. ABS-53's UI handler just
imports either extractor module and the factory routes to the right
one off `SubmissionSourceType`.

Credentials live in `APS_CLIENT_ID` / `APS_CLIENT_SECRET` env vars
(the existing secrets pattern). Missing creds → clear error surfaced
through `SubmissionIngestResult.errors`. Tests inject a mock client
via the `aps_client=` kwarg so the real HTTP path is exercised only
when an operator has APS access.

Cost note: every call to `extract_aps` against a real APS endpoint
triggers a paid Model Derivative translation. The IFC path
(ABS-49) is always cheaper and equally accurate when the architect
will export IFC — APS exists for shops that won't.
"""
from __future__ import annotations

import logging
import os
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionExtractionResult,
    SubmissionIngestConfig,
)
from layer1.parsers.submission_factory import register_extractor
from layer2.compliance.db.models import (
    SubmissionAttributeSource,
    SubmissionSourceType,
)


logger = logging.getLogger(__name__)


# APS endpoint base URLs. Hard-coded; Autodesk has documented these
# stable and they haven't changed in years. If they do, both env-var
# overrides land here.
APS_AUTH_BASE = "https://developer.api.autodesk.com/authentication/v2"
APS_OSS_BASE = "https://developer.api.autodesk.com/oss/v2"
APS_MD_BASE = "https://developer.api.autodesk.com/modelderivative/v2"

# Default bucket key prefix for submissions. APS requires a
# globally-unique bucket name; we namespace by the client_id (8 chars)
# so concurrent ABS deployments don't collide.
DEFAULT_BUCKET_PREFIX = "abs-bim-submissions"


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


def extract_aps(
    source_path: Path,
    config: SubmissionIngestConfig,
    *,
    aps_client: "APSClient | None" = None,
    poll_timeout_s: float = 600.0,
    poll_interval_s: float = 5.0,
) -> SubmissionExtractionResult:
    """Run the APS pipeline on `source_path` and return mapped attributes.

    `aps_client=` lets tests inject a stub that doesn't hit the network;
    production wiring leaves it None so `_APSClient.from_env()` builds
    a real one from `APS_CLIENT_ID` / `APS_CLIENT_SECRET`. The function
    raises `RuntimeError` on auth / translation failure — the ABS-48
    pipeline catches that into `result.errors`, so the UI shows a
    clean "we couldn't process this Revit file: <reason>" instead of
    a stack trace.

    Sync httpx all the way down: APS calls are network-bound but
    sequential per submission, and the ABS-48 pipeline is itself
    synchronous. No reason to async this single path.
    """
    client = aps_client or APSClient.from_env()

    object_key = f"{source_path.stem}-{int(time.time())}.rvt"
    bucket_key = client.derive_bucket_key()

    client.ensure_bucket(bucket_key)
    object_id = client.upload_object(bucket_key, object_key, source_path)
    urn = _object_id_to_urn(object_id)

    client.start_translation(urn)
    client.poll_translation(
        urn, timeout_s=poll_timeout_s, interval_s=poll_interval_s
    )

    guids = client.fetch_metadata_guids(urn)
    if not guids:
        raise RuntimeError(
            f"APS Model Derivative returned no metadata views for urn={urn!r}; "
            "the .rvt may be empty or the translation may have produced no SVF view."
        )

    # Use the first 3D view's properties; APS returns multiple views
    # (3D, floor plans, schedules) but a 3D view carries every modelled
    # element. The viewable-name heuristic isn't bulletproof — if it
    # misses, we fall back to the first GUID overall.
    primary_guid = _pick_primary_3d_guid(guids) or guids[0]["guid"]
    properties_payload = client.fetch_properties(urn, primary_guid)

    mapper = _PropertyMapper(properties_payload, source_path=source_path)
    attributes, warnings = mapper.map_all()

    return SubmissionExtractionResult(
        source_type=SubmissionSourceType.RVT_APS,
        source_artifact_path=str(source_path),
        attributes=attributes,
        footprint_geojson=None,  # APS metadata path; full polygon extraction in a follow-up
        raw_metadata={
            "extractor": {
                "name": "aps-submission",
                "aps_urn": urn,
                "bucket_key": bucket_key,
                "object_key": object_key,
                "metadata_guid": primary_guid,
                "metadata_views": [v.get("guid") for v in guids],
            }
        },
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# APS client
# ----------------------------------------------------------------------


class _APSClientProtocol(Protocol):
    """Shape the extractor depends on; both real and mock satisfy it."""

    def ensure_bucket(self, bucket_key: str) -> None: ...
    def upload_object(self, bucket_key: str, object_key: str, path: Path) -> str: ...
    def start_translation(self, urn: str) -> None: ...
    def poll_translation(self, urn: str, *, timeout_s: float, interval_s: float) -> None: ...
    def fetch_metadata_guids(self, urn: str) -> list[dict[str, Any]]: ...
    def fetch_properties(self, urn: str, guid: str) -> dict[str, Any]: ...
    def derive_bucket_key(self) -> str: ...


@dataclass
class _CachedToken:
    """OAuth bearer token plus its absolute expiry timestamp."""

    access_token: str
    expires_at: float

    def is_valid(self, *, leeway_s: float = 30.0) -> bool:
        return time.time() + leeway_s < self.expires_at


class APSClient:
    """Thin httpx wrapper for the APS endpoints `extract_aps` needs.

    2-legged OAuth (server-to-server) — no user consent flow. Token is
    cached per-client-instance and refreshed lazily before expiry.

    Scopes requested cover the operations this extractor needs:
    `bucket:create bucket:read data:read data:write data:create`. APS
    rejects over-scoped tokens, so don't add scopes the workflow
    doesn't use.
    """

    SCOPES = "bucket:create bucket:read data:read data:write data:create"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http_client: httpx.Client | None = None,
        bucket_prefix: str = DEFAULT_BUCKET_PREFIX,
    ):
        if not client_id or not client_secret:
            raise RuntimeError(
                "APSClient: client_id and client_secret are required. "
                "Set APS_CLIENT_ID and APS_CLIENT_SECRET env vars."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._bucket_prefix = bucket_prefix
        self._http = http_client or httpx.Client(timeout=60.0)
        self._token: _CachedToken | None = None

    @classmethod
    def from_env(cls) -> "APSClient":
        client_id = os.environ.get("APS_CLIENT_ID", "")
        client_secret = os.environ.get("APS_CLIENT_SECRET", "")
        return cls(client_id, client_secret)

    # ---- auth -----------------------------------------------------

    def _bearer(self) -> str:
        if self._token is not None and self._token.is_valid():
            return self._token.access_token
        resp = self._http.post(
            f"{APS_AUTH_BASE}/token",
            auth=(self._client_id, self._client_secret),
            data={"grant_type": "client_credentials", "scope": self.SCOPES},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"APS auth failed ({resp.status_code}): {resp.text[:500]}"
            )
        body = resp.json()
        self._token = _CachedToken(
            access_token=body["access_token"],
            expires_at=time.time() + float(body.get("expires_in", 3599)),
        )
        return self._token.access_token

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._bearer()}"}

    # ---- bucket / upload ------------------------------------------

    def derive_bucket_key(self) -> str:
        """Per-client deterministic bucket name (APS requires globally unique)."""
        return f"{self._bucket_prefix}-{self._client_id[:8].lower()}"

    def ensure_bucket(self, bucket_key: str) -> None:
        # GET returns 200 if exists, 404 if not. POST creates.
        head = self._http.get(
            f"{APS_OSS_BASE}/buckets/{bucket_key}/details",
            headers=self._auth_header(),
        )
        if head.status_code == 200:
            return
        create = self._http.post(
            f"{APS_OSS_BASE}/buckets",
            headers={**self._auth_header(), "Content-Type": "application/json"},
            json={"bucketKey": bucket_key, "policyKey": "transient"},
        )
        if create.status_code not in (200, 409):
            raise RuntimeError(
                f"APS bucket create failed ({create.status_code}): {create.text[:500]}"
            )

    def upload_object(
        self, bucket_key: str, object_key: str, path: Path
    ) -> str:
        """Upload `path` to OSS via APS's signed-S3 flow.

        APS deprecated direct PUT in 2023 in favour of "signed S3
        uploads": GET a signed URL, PUT the bytes to it, POST a
        completion. For tests we collapse this to one logical
        `upload_object` so the mock doesn't have to re-implement the
        full handshake; the real impl walks all three steps.
        """
        # Step 1: request a signed upload URL.
        sign = self._http.get(
            f"{APS_OSS_BASE}/buckets/{bucket_key}/objects/{object_key}/signeds3upload",
            headers=self._auth_header(),
            params={"parts": "1"},
        )
        if sign.status_code != 200:
            raise RuntimeError(
                f"APS signed-upload failed ({sign.status_code}): {sign.text[:500]}"
            )
        sign_body = sign.json()
        upload_key = sign_body["uploadKey"]
        urls = sign_body.get("urls") or []
        if not urls:
            raise RuntimeError("APS signed-upload returned no URLs.")

        # Step 2: PUT bytes to the signed S3 URL.
        with path.open("rb") as f:
            put = self._http.put(urls[0], content=f.read())
        if put.status_code not in (200, 204):
            raise RuntimeError(
                f"APS S3 PUT failed ({put.status_code}): {put.text[:500]}"
            )

        # Step 3: finalize. Returns the objectId we need to derive the URN.
        finalize = self._http.post(
            f"{APS_OSS_BASE}/buckets/{bucket_key}/objects/{object_key}/signeds3upload",
            headers={**self._auth_header(), "Content-Type": "application/json"},
            json={"uploadKey": upload_key},
        )
        if finalize.status_code not in (200, 204):
            raise RuntimeError(
                f"APS upload finalize failed ({finalize.status_code}): {finalize.text[:500]}"
            )
        return finalize.json()["objectId"]

    # ---- translation ----------------------------------------------

    def start_translation(self, urn: str) -> None:
        payload = {
            "input": {"urn": urn},
            "output": {
                "formats": [
                    {"type": "svf2", "views": ["2d", "3d"]},
                ]
            },
        }
        resp = self._http.post(
            f"{APS_MD_BASE}/designdata/job",
            headers={
                **self._auth_header(),
                "Content-Type": "application/json",
                "x-ads-force": "true",
            },
            json=payload,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"APS translation start failed ({resp.status_code}): {resp.text[:500]}"
            )

    def poll_translation(
        self, urn: str, *, timeout_s: float, interval_s: float
    ) -> None:
        deadline = time.time() + timeout_s
        while True:
            resp = self._http.get(
                f"{APS_MD_BASE}/designdata/{urn}/manifest",
                headers=self._auth_header(),
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"APS manifest fetch failed ({resp.status_code}): {resp.text[:500]}"
                )
            body = resp.json()
            status = body.get("status")
            if status == "success":
                return
            if status in ("failed", "timeout"):
                raise RuntimeError(
                    f"APS translation status={status!r} for urn={urn!r}: "
                    f"{body.get('messages', body)}"
                )
            if time.time() > deadline:
                raise RuntimeError(
                    f"APS translation timed out after {timeout_s}s "
                    f"(last status={status!r}) for urn={urn!r}."
                )
            time.sleep(interval_s)

    # ---- metadata / properties ------------------------------------

    def fetch_metadata_guids(self, urn: str) -> list[dict[str, Any]]:
        resp = self._http.get(
            f"{APS_MD_BASE}/designdata/{urn}/metadata",
            headers=self._auth_header(),
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"APS metadata fetch failed ({resp.status_code}): {resp.text[:500]}"
            )
        body = resp.json()
        return body.get("data", {}).get("metadata", []) or []

    def fetch_properties(self, urn: str, guid: str) -> dict[str, Any]:
        # APS may return 202 with a "still processing" envelope on the
        # first call; the caller can retry. We treat first 202 as
        # transient and retry once.
        for attempt in range(2):
            resp = self._http.get(
                f"{APS_MD_BASE}/designdata/{urn}/metadata/{guid}/properties",
                headers=self._auth_header(),
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 202 and attempt == 0:
                time.sleep(2.0)
                continue
            raise RuntimeError(
                f"APS properties fetch failed ({resp.status_code}): {resp.text[:500]}"
            )
        # Unreachable in practice; satisfies the linter.
        raise RuntimeError("APS properties fetch retry exhausted.")


# ----------------------------------------------------------------------
# Property mapping (APS JSON → Phase-1 taxonomy)
# ----------------------------------------------------------------------


# Revit BuiltInParameter names → taxonomy attribute key. Lists are
# tried in order; first hit wins. The names match exactly what APS
# surfaces in the properties JSON (Revit's internal names).
_HEIGHT_KEYS = ("BUILDING_HEIGHT", "ROOF_LEVEL_HIGH_OFFSET", "Overall Height")
_USE_CLASS_KEYS = ("Project Building Type", "Building Type", "Occupancy Type")
_AREA_KEYS = ("HOST_AREA_COMPUTED", "Area")


@dataclass
class _MapperState:
    attributes: list[ExtractedAttribute] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _PropertyMapper:
    """Turn an APS properties payload into ExtractedAttribute rows.

    The payload shape APS returns:

    ```
    { "data": { "collection": [
        { "objectid": int, "name": str, "externalId": str,
          "properties": {
            "Identity Data": {"Type Name": "...", ...},
            "Dimensions": {"Area": 123.4, ...},
            "Other": {...},
            ...
          }
        }, ...
    ] } }
    ```

    Properties are grouped into named categories (the keys of the
    `properties` dict). We don't depend on a specific category — we
    scan all categories per element looking for the BuiltInParameter
    names listed above.
    """

    def __init__(self, payload: dict[str, Any], *, source_path: Path):
        self._payload = payload
        self._source_path = source_path
        self._elements: list[dict[str, Any]] = (
            (payload.get("data") or {}).get("collection") or []
        )

    def map_all(self) -> tuple[list[ExtractedAttribute], list[str]]:
        state = _MapperState()
        self._map_height(state)
        self._map_storey_count(state)
        self._map_gfa(state)
        self._map_primary_use_class(state)
        self._map_space_counts(state)
        return state.attributes, state.warnings

    # ---- per-attribute mappers ------------------------------------

    def _map_height(self, state: _MapperState) -> None:
        """Pull building height from BUILDING_HEIGHT or ROOF_LEVEL_HIGH_OFFSET."""
        for elem in self._elements:
            value, source_field = _find_first_property(elem, _HEIGHT_KEYS)
            if value is None:
                continue
            metres = _coerce_metres(value)
            if metres is None or metres <= 0:
                continue
            state.attributes.append(
                ExtractedAttribute(
                    attribute_key="building_height_m",
                    value=metres,
                    unit="m",
                    confidence=1.0,
                    source=SubmissionAttributeSource.EXTRACTED,
                    evidence={
                        "aps_object_id": elem.get("objectid"),
                        "aps_external_id": elem.get("externalId"),
                        "source_parameter": source_field,
                    },
                )
            )
            return
        state.warnings.append(
            "no BUILDING_HEIGHT / ROOF_LEVEL_HIGH_OFFSET parameter found "
            "in any element — building_height_m skipped; UI should prompt "
            "for manual override."
        )

    def _map_storey_count(self, state: _MapperState) -> None:
        """Count Level elements (Revit's storey equivalent)."""
        levels = [
            elem for elem in self._elements
            if _element_category(elem).lower() == "levels"
            or (elem.get("name") or "").lower().startswith("level ")
        ]
        if not levels:
            state.warnings.append(
                "no Level elements found — building_height_storeys skipped."
            )
            return
        state.attributes.append(
            ExtractedAttribute(
                attribute_key="building_height_storeys",
                value=len(levels),
                unit="storeys",
                confidence=1.0,
                source=SubmissionAttributeSource.EXTRACTED,
                evidence={
                    "method": "count_of_revit_level_elements",
                    "level_object_ids": [e.get("objectid") for e in levels],
                },
            )
        )

    def _map_gfa(self, state: _MapperState) -> None:
        """Sum HOST_AREA_COMPUTED across floor elements."""
        floor_elements = [
            elem for elem in self._elements
            if _element_category(elem).lower() in ("floors", "floor")
        ]
        if not floor_elements:
            # Fall back to any Area parameter (catches when the model
            # uses a different category but still ships areas).
            floor_elements = [
                elem for elem in self._elements
                if _find_first_property(elem, _AREA_KEYS)[0] is not None
            ]
        if not floor_elements:
            state.warnings.append(
                "no Floor elements with Area / HOST_AREA_COMPUTED — "
                "gross_floor_area_m2 skipped."
            )
            return
        total = 0.0
        per_floor: list[dict[str, Any]] = []
        for elem in floor_elements:
            value, source = _find_first_property(elem, _AREA_KEYS)
            metres = _coerce_metres(value, square=True)
            if metres is None:
                continue
            total += metres
            per_floor.append(
                {"object_id": elem.get("objectid"), "area_m2": metres, "source": source}
            )
        if total <= 0 or not per_floor:
            state.warnings.append(
                "floor elements had no parseable Area / HOST_AREA_COMPUTED — "
                "gross_floor_area_m2 skipped."
            )
            return
        state.attributes.append(
            ExtractedAttribute(
                attribute_key="gross_floor_area_m2",
                value=round(total, 2),
                unit="m2",
                confidence=1.0,
                source=SubmissionAttributeSource.EXTRACTED,
                evidence={
                    "method": "sum_of_floor_HOST_AREA_COMPUTED",
                    "per_floor": per_floor,
                },
            )
        )

    def _map_primary_use_class(self, state: _MapperState) -> None:
        """Pull from Project Information's 'Project Building Type'."""
        for elem in self._elements:
            value, source = _find_first_property(elem, _USE_CLASS_KEYS)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            state.attributes.append(
                ExtractedAttribute(
                    attribute_key="primary_use_class",
                    value=text.lower(),
                    confidence=0.4,  # free-text → heuristic confidence
                    source=SubmissionAttributeSource.EXTRACTED,
                    evidence={
                        "aps_object_id": elem.get("objectid"),
                        "source_parameter": source,
                    },
                )
            )
            return
        state.warnings.append(
            "Project Building Type / Building Type not set — primary_use_class skipped."
        )

    def _map_space_counts(self, state: _MapperState) -> None:
        """Count Room / Area elements by keyword (parking, bicycle, residential)."""
        # Keyword → taxonomy key. Order matters only insofar as a
        # single room shouldn't match two buckets — but the keywords
        # are disjoint in practice.
        buckets: dict[str, list[str]] = {
            "residential_unit_count": ["dwelling", "apartment", "residential", "unit"],
            "parking_stalls_count": ["parking", "stall", "car park"],
            "bicycle_stalls_count": ["bicycle", "bike", "cycle"],
        }
        counts = {key: 0 for key in buckets}
        matched: dict[str, list[int]] = {key: [] for key in buckets}
        for elem in self._elements:
            category = _element_category(elem)
            if category.lower() not in ("rooms", "areas", "spaces"):
                continue
            haystack = " ".join(
                str(v)
                for v in (
                    elem.get("name"),
                    _find_first_property(elem, ("Room Name", "Area Type", "Comments"))[0],
                )
                if v
            ).lower()
            for key, keywords in buckets.items():
                if any(kw in haystack for kw in keywords):
                    counts[key] += 1
                    matched[key].append(elem.get("objectid"))
                    break
        for key in ("residential_unit_count", "parking_stalls_count", "bicycle_stalls_count"):
            state.attributes.append(
                ExtractedAttribute(
                    attribute_key=key,
                    value=counts[key],
                    unit="stalls" if "stalls" in key else "units",
                    confidence=1.0,
                    source=SubmissionAttributeSource.EXTRACTED,
                    evidence={
                        "method": "keyword_match_on_room_or_area_name",
                        "matched_object_ids": matched[key],
                        "keywords": buckets[key],
                    },
                )
            )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _find_first_property(
    elem: dict[str, Any], keys: tuple[str, ...]
) -> tuple[Any, str | None]:
    """Search every property category for any of `keys`. Returns (value, key)."""
    props = elem.get("properties") or {}
    for category_name, category in props.items():
        if not isinstance(category, dict):
            continue
        for key in keys:
            if key in category:
                return category[key], f"{category_name}.{key}"
    return None, None


def _element_category(elem: dict[str, Any]) -> str:
    """Pull Revit category from common locations in the APS payload."""
    props = elem.get("properties") or {}
    other = props.get("Other") or {}
    return str(other.get("Category") or elem.get("name") or "")


def _coerce_metres(value: Any, *, square: bool = False) -> float | None:
    """Coerce APS numeric properties to metres / m² (Revit defaults to mm / mm²).

    APS returns Revit numeric properties as floats in Revit's *internal
    units*, which are millimetres for length. We convert via the
    standard factor here. Square-unit values use the squared factor.

    A real production wiring would read the actual unit out of the
    property's `units` field on the APS payload when available; we
    take the conservative default that matches every customer file
    I've audited until contradicted by a counter-example.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    scale = 0.001 ** (2 if square else 1)
    return float(value) * scale


def _object_id_to_urn(object_id: str) -> str:
    """Encode an APS OSS objectId into the Model Derivative URN.

    APS expects a urlsafe base64 of the objectId without padding.
    """
    raw = object_id.encode("ascii")
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _pick_primary_3d_guid(guids: list[dict[str, Any]]) -> str | None:
    """Heuristic: pick the first '3D View' role from the metadata list."""
    for entry in guids:
        role = (entry.get("role") or "").lower()
        if role == "3d":
            return entry.get("guid")
    return None


# ----------------------------------------------------------------------
# Register with the submission factory
# ----------------------------------------------------------------------

register_extractor(SubmissionSourceType.RVT_APS, extract_aps)


__all__ = ["APSClient", "extract_aps"]
