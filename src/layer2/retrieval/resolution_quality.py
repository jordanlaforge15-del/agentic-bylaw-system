"""How good is the point an address resolved to? (ABS-466)

A user's address becomes a point, the point selects a zoning polygon, and
the zone drives every setback, height and FAR answer downstream. When the
point is an *estimate* — Google interpolated it along the street from the
surrounding civic numbering, or handed back the centre of a block — the
zone can come back from the neighbouring parcel and every number after it
inherits the error.

This module is the single vocabulary for that quality, so the geocoder,
the cache round-trip, the ``AddressProfile`` DTO and the advisor's
answer-time qualifier all classify a resolution the same way instead of
each re-deriving it from a bare float.

``location_type`` is Google's own enum and is authoritative when present.
Confidence is the fallback for resolutions that never went through Google
(the in-database civic-address dataset, seeded rows, older cache entries
written before the type was persisted).
"""
from __future__ import annotations

from typing import Literal

ResolutionQuality = Literal[
    "rooftop", "interpolated", "centroid", "approximate", "unknown"
]

# Google's location_type -> our quality vocabulary. ROOFTOP is a precise
# match on the building; RANGE_INTERPOLATED is an estimate along the
# street; GEOMETRIC_CENTER is the centre of a result (a block, a route);
# APPROXIMATE is coarser still.
QUALITY_BY_LOCATION_TYPE: dict[str, ResolutionQuality] = {
    "ROOFTOP": "rooftop",
    "RANGE_INTERPOLATED": "interpolated",
    "GEOMETRIC_CENTER": "centroid",
    "APPROXIMATE": "approximate",
}

# The confidence we report for each location_type. Kept here (rather than in
# the geocoder) because the classifier below has to invert it for rows that
# carry a confidence but no type.
CONFIDENCE_BY_LOCATION_TYPE: dict[str, float] = {
    "ROOFTOP": 0.95,
    "RANGE_INTERPOLATED": 0.85,
    "GEOMETRIC_CENTER": 0.6,
    "APPROXIMATE": 0.4,
}

# Everything at or above this is treated as a precise, parcel-level match.
# The in-database civic-address resolver reports 0.95+ and is an authoritative
# municipal point, so it classifies as rooftop with no Google type present.
_ROOFTOP_CONFIDENCE = 0.95
_INTERPOLATED_CONFIDENCE = 0.85
_CENTROID_CONFIDENCE = 0.6

# Ready-to-quote caveats, one per below-rooftop quality. Written so the
# advisor can paste them into an answer verbatim: they name what was
# estimated, why it matters (the zone can come from the wrong parcel), and
# what the user should do about it.
_CAVEATS: dict[str, str] = {
    "interpolated": (
        "This address was not matched to a specific building. The geocoder "
        "estimated its position along the street from the surrounding civic "
        "numbering, so near a zone boundary the point can land on the "
        "neighbouring parcel. Treat the zone — and every setback, height and "
        "floor-area figure derived from it — as provisional, and confirm the "
        "parcel's zoning with HRM before relying on it."
    ),
    "centroid": (
        "This address resolved only to the approximate centre of a block or "
        "street segment, not to the property itself. The zone shown may "
        "belong to a neighbouring parcel. Confirm the parcel's zoning with "
        "HRM before relying on any figure derived from it."
    ),
    "approximate": (
        "This address resolved only to an approximate area (a locality, not a "
        "property). Any zone shown is unreliable. Confirm the address and the "
        "parcel's zoning with HRM before relying on any figure derived from it."
    ),
    "unknown": (
        "The precision of this address match is unknown, so the zone shown "
        "may not be the property's. Confirm the parcel's zoning with HRM "
        "before relying on any figure derived from it."
    ),
}

# Said when the address geocoded fine but the point fell outside every
# mapped overlay — distinct from "we could not find the address at all".
OUTSIDE_MAPPED_AREA_CAVEAT = (
    "The address resolved to a point, but that point falls outside every "
    "mapped zoning and overlay boundary in the ingested corpus, so no zone "
    "could be assigned. This is not a zoning determination: the address may "
    "sit outside the plan area, or the geocoded point may be off the parcel. "
    "Do not state a zone for this address — confirm it with HRM."
)


def classify_resolution(
    location_type: str | None, confidence: float | None
) -> ResolutionQuality:
    """Classify a resolved location's precision.

    Google's ``location_type`` wins when present. Otherwise we invert the
    confidence mapping, which covers in-database resolutions and cache rows
    written before the type was persisted. A missing confidence is
    ``"unknown"`` — not ``"rooftop"``; an absent signal must never read as
    the strongest one.
    """
    if location_type:
        mapped = QUALITY_BY_LOCATION_TYPE.get(location_type.upper())
        if mapped is not None:
            return mapped
        return "unknown"
    if confidence is None:
        return "unknown"
    if confidence >= _ROOFTOP_CONFIDENCE:
        return "rooftop"
    if confidence >= _INTERPOLATED_CONFIDENCE:
        return "interpolated"
    if confidence >= _CENTROID_CONFIDENCE:
        return "centroid"
    if confidence > 0:
        return "approximate"
    return "unknown"


def is_precise(quality: ResolutionQuality) -> bool:
    """True only for a parcel-level match — the one case needing no caveat."""
    return quality == "rooftop"


def resolution_caveat(quality: ResolutionQuality) -> str | None:
    """The sentence an answer must carry for a below-rooftop resolution.

    ``None`` for ``rooftop``: a precise match needs no qualification, and
    returning ``None`` lets callers use the result as the "should I qualify
    this answer?" predicate directly.
    """
    if is_precise(quality):
        return None
    return _CAVEATS.get(quality, _CAVEATS["unknown"])
