from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

# Canonical attribute schema — the small, deliberately-curated vocabulary the
# retrieval API exposes. Datasets map their raw fields into these names. Adding
# a field here is the trigger for a corresponding API capability; do not add
# speculatively.
CANONICAL_FIELDS: dict[str, str] = {
    # Building-height precinct fields. ``max_height_m`` and
    # ``max_height_storeys`` are mutually exclusive in Halifax's published
    # schedule (a precinct caps building height by metres OR by stories,
    # never both), so per-feature only one is populated. Both are required
    # in the canonical schema so the API can return whichever is set.
    "max_height_m": "float",
    "max_height_storeys": "int",
    "display_label": "string",
    "effective_date": "date",
    "source_case": "string",
    "bylaw_area": "string",
    # Zoning fields — populated by datasets like the HRM Zoning Boundaries
    # layer where each polygon assigns a zone code (e.g. "ER-3", "DD") to
    # a geographic area. The bylaw_area_id is the numeric LUB identifier
    # that lets a downstream filter restrict to a single bylaw's zones.
    "zone_code": "string",
    "zone_description": "string",
    "bylaw_area_id": "int",
    # Built-form precinct fields surfaced by additional schedule datasets.
    # Each is populated only by the dataset(s) where the value is meaningful;
    # the API consumer disambiguates via the linked dataset's name.
    "max_far": "float",
    # District-style fields shared across the various RCLUB schedules that
    # carve the bylaw area into named regions (Heritage Conservation
    # Districts, Bonus Zoning Rate Districts, Shadow Impact Assessment
    # Areas, etc.). Generic names — the dataset_name in the response
    # makes it clear which "district" the field describes.
    "district_name": "string",
    "district_code": "string",
    "district_status": "string",
    "district_label": "string",
    "impact_area": "string",
    # Civic-address fields — populated by datasets with role=civic_address
    # so the geocoder (Phase E) can resolve a LocationReference to a point
    # or parcel polygon.
    "civic_number": "string",
    "street_name": "string",
    "parcel_id": "string",
}


SUPPORTED_TYPES = {"float", "int", "string", "bool", "date", "rfc2822_date", "epoch_ms_date"}


class CoercionError(ValueError):
    """Raised when a raw value cannot be coerced to the declared type."""


def coerce_value(raw: Any, type_name: str) -> Any:
    """Coerce a raw attribute value to a canonical type.

    Returns the coerced value, or raises ``CoercionError`` if conversion
    fails. Callers decide whether to record the failure as UNCERTAIN or
    propagate as ERROR.
    """
    if type_name not in SUPPORTED_TYPES:
        raise CoercionError(f"unsupported canonical type: {type_name}")

    try:
        if type_name == "float":
            return float(raw)
        if type_name == "int":
            return int(raw)
        if type_name == "string":
            return str(raw)
        if type_name == "bool":
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)):
                return bool(raw)
            text = str(raw).strip().lower()
            if text in {"true", "yes", "y", "1"}:
                return True
            if text in {"false", "no", "n", "0"}:
                return False
            raise CoercionError(f"cannot coerce {raw!r} to bool")
        if type_name == "date":
            if isinstance(raw, date) and not isinstance(raw, datetime):
                return raw.isoformat()
            if isinstance(raw, datetime):
                return raw.date().isoformat()
            return date.fromisoformat(str(raw)).isoformat()
        if type_name == "rfc2822_date":
            parsed = parsedate_to_datetime(str(raw))
            if parsed is None:
                raise CoercionError(f"cannot parse RFC 2822 date: {raw!r}")
            return parsed.date().isoformat()
        if type_name == "epoch_ms_date":
            # ArcGIS REST endpoints commonly return dates as Unix epoch
            # milliseconds (e.g. 1003968000000). Static Hub GeoJSON exports
            # of the same data convert these to RFC 2822 strings — so the
            # caller picks the right type for the source they're using.
            try:
                ms = int(raw)
            except (TypeError, ValueError) as exc:
                raise CoercionError(f"epoch_ms_date expects integer ms, got {raw!r}") from exc
            try:
                return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError) as exc:
                raise CoercionError(f"epoch_ms {ms} is out of range: {exc}") from exc
    except CoercionError:
        raise
    except Exception as exc:
        raise CoercionError(f"cannot coerce {raw!r} to {type_name}: {exc}") from exc
    raise CoercionError(f"unhandled type: {type_name}")  # pragma: no cover


def is_canonical_field(name: str) -> bool:
    return name in CANONICAL_FIELDS
