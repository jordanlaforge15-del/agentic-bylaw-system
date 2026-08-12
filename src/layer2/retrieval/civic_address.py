"""Does this civic address exist? (ABS-469)

ABS-466 made a weak resolution *visible*: the profile now says the point was
estimated rather than matched. It did not make resolution *correct*. Google
answers "100 Robie Street" with a confident coordinate produced by
interpolating along the street from the surrounding civic numbering — the
civic number was never found, and near a zone boundary that estimate lands on
the neighbouring parcel. Hedging on that is better than silence, but the
answer is still built on a guess about an address that does not exist.

This module checks the civic number against the municipality's own data
before anything downstream trusts the point, so a fabricated address becomes
a *correct refusal with a suggestion* — "there is no 100 Robie Street;
even-numbered addresses on Robie Street run 820–3898" — instead of a
confidently wrong setback.

Two sources, in precedence order:

``civic_address_points`` (authoritative)
    A ``role: civic_address`` point dataset — HRM publishes one
    (``CivicAddresses``, 158,523 points, the source E-911 and Property Online
    consume). One row per real civic address, so presence/absence is a fact,
    not an inference. When such a dataset is in scope its verdict is final.

``street_centerline_ranges`` (inference, but a good one)
    A ``role: road_centerlines`` dataset carrying per-segment address ranges
    (HRM: ``FROM_LEFT`` / ``TO_LEFT`` / ``FROM_RIGHT`` / ``TO_RIGHT``). A
    civic number past both ends of everything a street publishes almost
    certainly does not exist.

    Measured against 4,000 real HRM civic-address points inside the Regional
    Centre: refusing *any* uncovered number would wrongly reject 15 of them
    (0.38%); refusing only numbers outside the street's whole addressed
    extent wrongly rejects 6 (0.15%), all on one street whose published
    ranges stop at 81 while its real addresses run to 355. The difference is
    that a number sitting BETWEEN two published ranges is usually a range
    that has not caught up with the street (Nora Bernard Street's 5440–5549
    stretch after the rename from Cornwallis), whereas a number past both
    ends is one nothing on that street has ever carried. So an in-gap number
    is ``unverifiable``, not ``not_found`` — see ``_falls_in_a_gap``.

    Under that rule the check still rejects every fabricated address in the
    ABS-469 table (100 Robie, 567 Windsor, 2563 Maitland, 200 Bayers) and
    89 Jubilee Road besides.

The check is *per segment*. Aggregating a street's min/max makes it useless:
Robie Street spans 0–3899 that way and swallows every number in between.

Safety rules, because a false "this address does not exist" is worse than the
hedge it replaces:

* A street the data has never heard of yields ``unverifiable``, never
  ``not_found``. Renamed streets (Cornwallis → Nora Bernard), typos and
  out-of-municipality addresses land here.
* Segments are filtered by street *type* only when at least one segment of
  that type exists (HRM writes CRT for Court and LANE for Lane, so an exact
  abbreviation match cannot be required); otherwise every segment of the name
  is considered, which can only widen coverage.
* Ranges of ``0/0`` are placeholders for a segment with no addressing, not a
  range covering zero, and are ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer2.retrieval.geocode import normalize_street

CivicAddressStatus = Literal["confirmed", "not_found", "unverifiable"]

# How the verdict was reached. Named (rather than described by a float) for
# the same reason ABS-466 carried Google's ``location_type`` forward: only
# the word says whether "not found" is a municipal fact or an inference from
# a published address range.
CivicAddressMethod = Literal["civic_address_points", "street_centerline_ranges"]

CIVIC_ADDRESS_ROLE = "civic_address"
ROAD_CENTERLINES_ROLE = "road_centerlines"

# Raw HRM StreetNetwork field names, used when a centerlines dataset was
# ingested before the canonical mapping below existed (the live dev corpus).
# The canonical names are preferred when present — see ``_segment_ranges``.
_RAW_STREET_NAME_FIELDS = ("STR_NAME",)
_RAW_STREET_TYPE_FIELDS = ("STR_TYPE",)
_RAW_RANGE_FIELDS = (
    ("FROM_LEFT", "TO_LEFT", "left"),
    ("FROM_RIGHT", "TO_RIGHT", "right"),
)
_CANONICAL_RANGE_FIELDS = (
    ("civic_from_left", "civic_to_left", "left"),
    ("civic_from_right", "civic_to_right", "right"),
)

# Street-type tokens as HRM writes them, keyed by the normalized suffix
# ``normalize_street`` produces. A missing entry just means no type filter is
# applied, which is safe (it widens the segment set).
_HRM_STREET_TYPES: dict[str, tuple[str, ...]] = {
    "st": ("ST",),
    "ave": ("AVE", "AV"),
    "rd": ("RD",),
    "blvd": ("BLVD",),
    "dr": ("DR",),
    "ln": ("LANE", "LN"),
    "cres": ("CRES",),
    "ct": ("CRT", "CT"),
    "pl": ("PL",),
    "terr": ("TERR",),
    "hwy": ("HWY",),
    "pkwy": ("PKWY",),
    "way": ("WAY",),
}

# Cap on how many ranges we quote back. A long street can publish 40+; the
# response is replayed on every subsequent turn, and the two or three ranges
# nearest the number the user asked about are the ones that answer "what
# should I have said instead".
_MAX_QUOTED_RANGES = 3


@dataclass(frozen=True)
class CivicAddressVerdict:
    """Whether a civic number exists, on what evidence, and what does.

    ``status`` is the only field a caller must branch on:

    * ``confirmed``   — the municipality publishes this address (or a street
      segment whose range covers it).
    * ``not_found``   — the street is known and no published address or range
      covers the number. The address does not exist; do not report a zone for
      it.
    * ``unverifiable`` — no dataset in scope can answer the question (no
      civic-address or centerline dataset ingested, an unknown street, a
      non-numeric civic number). Says nothing about the address.
    """

    status: CivicAddressStatus
    method: CivicAddressMethod | None = None
    dataset_name: str | None = None
    # Why the verdict could not be reached — populated for ``unverifiable``
    # so an operator can tell a missing dataset from an unknown street.
    reason: str | None = None
    # The segment that covered the number, for a centerline ``confirmed``.
    # This is the auditable half of "tier 2": the resolver can say which
    # segment, which range and which side of the street produced the answer.
    matched_segment: str | None = None
    matched_range: tuple[int, int] | None = None
    matched_side: Literal["left", "right"] | None = None
    # Valid civic-number ranges on the street, same parity as the number
    # asked about, nearest first. Populated for ``not_found``.
    valid_ranges: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    # Nearest valid civic numbers of the same parity, nearest first.
    suggestions: tuple[int, ...] = field(default_factory=tuple)
    street_label: str | None = None

    @property
    def exists(self) -> bool:
        return self.status == "confirmed"


def verify_civic_address(
    session: Session, *, civic_number: str | None, street: str | None
) -> CivicAddressVerdict:
    """Check ``civic_number`` on ``street`` against the municipal datasets.

    Never raises and never guesses: anything the ingested data cannot settle
    comes back ``unverifiable`` with the reason.
    """
    number = _parse_civic_number(civic_number)
    if number is None:
        return CivicAddressVerdict(
            status="unverifiable",
            reason=f"civic number {civic_number!r} is not a whole number",
        )
    normalized = normalize_street(street)
    if not normalized:
        return CivicAddressVerdict(
            status="unverifiable", reason="no street name was parsed from the address"
        )
    name, suffix = _split_street(normalized)
    label = _street_label(street, normalized)

    points_verdict = _verify_against_civic_points(
        session, number=number, name=name, label=label
    )
    if points_verdict is not None:
        return points_verdict

    centerline_verdict = _verify_against_centerlines(
        session, number=number, name=name, suffix=suffix, label=label
    )
    if centerline_verdict is not None:
        return centerline_verdict

    return CivicAddressVerdict(
        status="unverifiable",
        street_label=label,
        reason=(
            "no civic-address or street-centerline dataset is in scope, so the "
            "civic number could not be checked against municipal data"
        ),
    )


# ---------------------------------------------------------------------------
# Tier 3 — authoritative civic-address points
# ---------------------------------------------------------------------------


def _verify_against_civic_points(
    session: Session, *, number: int, name: str, label: str
) -> CivicAddressVerdict | None:
    """Verdict from a ``role: civic_address`` point dataset, or None.

    None means "this source cannot answer" — either no such dataset is
    ingested or it has never heard of the street — and the caller falls
    through to the centerline ranges.
    """
    for dataset in _datasets_with_role(session, CIVIC_ADDRESS_ROLE):
        numbers: set[int] = set()
        matched = False
        for feature in _features_on_street(session, dataset.id, name):
            canonical = feature.canonical_attributes_json or {}
            feature_street = normalize_street(canonical.get("street_name"))
            if not feature_street:
                continue
            if _split_street(feature_street)[0] != name:
                continue
            feature_number = _parse_civic_number(canonical.get("civic_number"))
            if feature_number is None:
                continue
            numbers.add(feature_number)
            if feature_number == number:
                matched = True
        if matched:
            return CivicAddressVerdict(
                status="confirmed",
                method="civic_address_points",
                dataset_name=dataset.name,
                street_label=label,
            )
        if numbers:
            same_parity = sorted(n for n in numbers if n % 2 == number % 2) or sorted(numbers)
            merged = _merge_ranges([(n, n) for n in same_parity])
            return CivicAddressVerdict(
                status="not_found",
                method="civic_address_points",
                dataset_name=dataset.name,
                street_label=label,
                valid_ranges=_nearest_ranges(merged, number),
                suggestions=_nearest_valid_numbers(merged, number),
            )
    return None


# ---------------------------------------------------------------------------
# Tier 1 — per-segment street-centerline address ranges
# ---------------------------------------------------------------------------


def _verify_against_centerlines(
    session: Session, *, number: int, name: str, suffix: str | None, label: str
) -> CivicAddressVerdict | None:
    for dataset in _datasets_with_role(session, ROAD_CENTERLINES_ROLE):
        segments = [
            segment
            for segment in (
                _segment_ranges(feature)
                for feature in _features_on_street(session, dataset.id, name)
            )
            if segment is not None and segment.name == name
        ]
        if not segments:
            continue
        typed = _filter_by_type(segments, suffix)
        for segment in typed:
            for low, high, side in segment.ranges:
                if low <= number <= high:
                    return CivicAddressVerdict(
                        status="confirmed",
                        method="street_centerline_ranges",
                        dataset_name=dataset.name,
                        matched_segment=segment.key,
                        matched_range=(low, high),
                        matched_side=side,
                        street_label=label,
                    )
        every_range = [
            (low, high) for segment in typed for low, high, _side in segment.ranges
        ]
        spans = [
            (low, high) for low, high in every_range if low % 2 == number % 2
        ] or every_range
        merged = _merge_ranges(spans)
        if _falls_in_a_gap(number, every_range):
            # Inside the street's addressed extent but between two published
            # ranges. Measured over 2,000 real HRM civic addresses in the
            # Regional Centre, every centerline false negative but one was
            # this shape — a segment whose published range had not caught up
            # with the street (Nora Bernard Street's 5440-5549 stretch after
            # the rename from Cornwallis). Numbers BEYOND the street's whole
            # extent are a different population: nothing on the street has
            # ever carried them. Refusing only those cuts the measured false
            # refusal rate from 0.15% to 0.05% and still rejects every
            # fabricated address in the issue's table, all of which sit past
            # one end of their street.
            return CivicAddressVerdict(
                status="unverifiable",
                method="street_centerline_ranges",
                dataset_name=dataset.name,
                street_label=label,
                valid_ranges=_nearest_ranges(merged, number),
                reason=(
                    "the civic number falls between two published address "
                    "ranges on this street rather than outside them, which is "
                    "where the centerline layer's own gaps are — it cannot be "
                    "called non-existent on this evidence"
                ),
            )
        return CivicAddressVerdict(
            status="not_found",
            method="street_centerline_ranges",
            dataset_name=dataset.name,
            street_label=label,
            valid_ranges=_nearest_ranges(merged, number),
            suggestions=_nearest_valid_numbers(merged, number),
        )
    return None


def _falls_in_a_gap(number: int, ranges: list[tuple[int, int]]) -> bool:
    """True when ``number`` sits inside the street's extent but in no range."""
    if not ranges:
        return False
    return min(low for low, _ in ranges) < number < max(high for _, high in ranges)


@dataclass(frozen=True)
class _Segment:
    key: str
    name: str
    type: str | None
    ranges: tuple[tuple[int, int, Literal["left", "right"]], ...]


def _segment_ranges(feature: ExternalDatasetFeature) -> _Segment | None:
    """Street name, type and per-side address ranges for one centerline row.

    Reads the canonical mapping when the dataset declares one and falls back
    to HRM's raw field names. The fallback is load-bearing: the live corpus
    ingested 18,468 centerline segments before the canonical mapping existed,
    and re-ingesting the layer is not a precondition for this check.
    """
    canonical = feature.canonical_attributes_json or {}
    raw = feature.attributes_json or {}

    name = normalize_street(_first(canonical, ("street_name",)) or _first(raw, _RAW_STREET_NAME_FIELDS))
    if not name:
        return None
    street_type = _first(canonical, ("street_type",)) or _first(raw, _RAW_STREET_TYPE_FIELDS)

    ranges: list[tuple[int, int, Literal["left", "right"]]] = []
    for source, fields in ((canonical, _CANONICAL_RANGE_FIELDS), (raw, _RAW_RANGE_FIELDS)):
        for from_field, to_field, side in fields:
            low = _parse_civic_number(source.get(from_field))
            high = _parse_civic_number(source.get(to_field))
            if low is None or high is None:
                continue
            # 0/0 marks a segment with no addressing at all (a ramp, a lane
            # behind a block). Treated as a range it would cover the number 0
            # and, worse, read as evidence the street is addressed.
            if low == 0 and high == 0:
                continue
            ranges.append((min(low, high), max(low, high), side))  # type: ignore[arg-type]
        if ranges:
            break

    return _Segment(
        key=feature.feature_key,
        name=_split_street(name)[0],
        type=str(street_type).strip().upper() if street_type else None,
        ranges=tuple(ranges),
    )


def _filter_by_type(segments: list[_Segment], suffix: str | None) -> list[_Segment]:
    """Keep only segments whose street type matches, when any of them do.

    Jubilee Road, Jubilee Court and Jubilee Lane share a ``STR_NAME``; without
    the filter, "89 Jubilee Road" is covered by Jubilee Court's 2–98 range and
    the check reports an address that does not exist as real. But HRM writes
    CRT for Court and LANE for Lane, so an exact match cannot be *required*:
    when nothing matches, every segment of the name is used, which can only
    make the check more conservative.
    """
    if not suffix:
        return segments
    accepted = _HRM_STREET_TYPES.get(suffix)
    if not accepted:
        return segments
    matching = [s for s in segments if s.type in accepted]
    return matching or segments


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _datasets_with_role(session: Session, role: str) -> list[ExternalDataset]:
    rows = session.execute(select(ExternalDataset)).scalars().all()
    return [row for row in rows if (row.metadata_json or {}).get("role") == role]


def _features_on_street(
    session: Session, dataset_id: int, name: str
) -> list[ExternalDatasetFeature]:
    """Rows of ``dataset_id`` whose street name starts with ``name``.

    Filtered in SQL rather than in Python because these datasets are large —
    18,468 centerline segments, and HRM's civic-address layer is 158,523
    points — and this runs on the per-question path. The prefix match is
    deliberately loose (it also matches a dataset that stores the street type
    in the same column, and streets like ROBIEWOOD); the exact comparison
    happens on the handful of rows that come back.

    ``JSON`` indexing renders as ``->>`` on Postgres and ``JSON_EXTRACT`` on
    SQLite, so the same query serves the production path and the test suite.
    """
    pattern = f"{name}%"
    raw_name = func.upper(
        ExternalDatasetFeature.attributes_json["STR_NAME"].as_string()
    )
    canonical_name = func.upper(
        ExternalDatasetFeature.canonical_attributes_json["street_name"].as_string()
    )
    return list(
        session.execute(
            select(ExternalDatasetFeature)
            .where(ExternalDatasetFeature.external_dataset_id == dataset_id)
            .where(or_(raw_name.like(pattern), canonical_name.like(pattern)))
        )
        .scalars()
        .all()
    )


def _first(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


_NUMBER_RE = re.compile(r"^\d+$")


def _parse_civic_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and _NUMBER_RE.match(value.strip()):
        return int(value.strip())
    return None


def _split_street(normalized: str) -> tuple[str, str | None]:
    """Split a normalized street into (name, suffix).

    ``normalize_street`` already collapses "Street"/"St."  to ``st``; HRM
    stores the two halves in separate columns, so the comparison has to as
    well. A street with no recognised suffix ("Kings Wharf Place" once
    normalized is "kings wharf pl", but "Sunnybrae" is just a name) returns
    the whole string and no suffix.
    """
    parts = normalized.split()
    if len(parts) > 1 and parts[-1] in _HRM_STREET_TYPES:
        return " ".join(parts[:-1]).upper(), parts[-1]
    return normalized.upper(), None


def _street_label(raw: str | None, normalized: str) -> str:
    """The street as it should be quoted back to the user."""
    return (raw or normalized).strip()


def _merge_ranges(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Merge ranges that are contiguous *within their parity*.

    Address ranges alternate sides of the street, so 820–838 and 840–904 are
    consecutive even numbers with nothing missing between them. Merging them
    (gap of 2 rather than 1) turns forty published segment ranges into the two
    or three the user actually needs to see.
    """
    if not spans:
        return ()
    ordered = sorted(spans)
    merged: list[list[int]] = [list(ordered[0])]
    for low, high in ordered[1:]:
        current = merged[-1]
        gap = 2 if (low % 2 == current[1] % 2) else 1
        if low <= current[1] + gap:
            current[1] = max(current[1], high)
        else:
            merged.append([low, high])
    return tuple((low, high) for low, high in merged)


def _nearest_ranges(
    merged: tuple[tuple[int, int], ...], number: int
) -> tuple[tuple[int, int], ...]:
    ordered = sorted(merged, key=lambda span: _distance_to(span, number))
    return tuple(sorted(ordered[:_MAX_QUOTED_RANGES]))


def _distance_to(span: tuple[int, int], number: int) -> int:
    low, high = span
    if low <= number <= high:
        return 0
    return min(abs(low - number), abs(high - number))


def _nearest_valid_numbers(
    merged: tuple[tuple[int, int], ...], number: int, *, limit: int = 2
) -> tuple[int, ...]:
    """The valid civic numbers closest to the one the user asked about.

    Clamps into each range rather than collecting range endpoints: the far end
    of the range the address *should* have been in is not a suggestion anyone
    can use ("there is no 100 Robie Street — did you mean 2180?"), whereas the
    near end is exactly the correction to offer.
    """
    candidates = {min(max(number, low), high) for low, high in merged}
    return tuple(sorted(sorted(candidates, key=lambda n: abs(n - number))[:limit]))


def format_ranges(ranges: tuple[tuple[int, int], ...]) -> list[str]:
    """Render ranges for the DTO: ``[(820, 3898)]`` -> ``["820-3898"]``."""
    return [f"{low}-{high}" if low != high else str(low) for low, high in ranges]
