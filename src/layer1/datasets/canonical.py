from __future__ import annotations

from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

# Canonical attribute schema — the small, deliberately-curated vocabulary the
# retrieval API exposes. Datasets map their raw fields into these names. Adding
# a field here is the trigger for a corresponding API capability; do not add
# speculatively.
CANONICAL_FIELDS: dict[str, str] = {
    "max_height_m": "float",
    "display_label": "string",
    "effective_date": "date",
    "source_case": "string",
}


SUPPORTED_TYPES = {"float", "int", "string", "bool", "date", "rfc2822_date"}


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
    except CoercionError:
        raise
    except Exception as exc:
        raise CoercionError(f"cannot coerce {raw!r} to {type_name}: {exc}") from exc
    raise CoercionError(f"unhandled type: {type_name}")  # pragma: no cover


def is_canonical_field(name: str) -> bool:
    return name in CANONICAL_FIELDS
