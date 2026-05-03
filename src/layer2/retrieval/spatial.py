from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from shapely.geometry import shape as shapely_shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment


# A resolved location is whatever the geocoder gave us back. Stage D supports
# the three primitive shapes; Phase E layers civic-address resolution on top
# without changing the spatial query interface.
ResolvedLocationKind = Literal["point", "shape", "parcel"]


@dataclass(frozen=True)
class ResolvedLocation:
    """A location ready to intersect against precinct features.

    ``geometry`` is a GeoJSON geometry dict in EPSG:4326 (the only CRS our
    datasets currently store, asserted at ingest). ``confidence`` is the
    geocoder's reported confidence; ``source`` names the resolver so
    citations can attribute the lookup.
    """

    kind: ResolvedLocationKind
    geometry: dict[str, Any]
    confidence: float = 1.0
    source: str = "direct"
    reference_text: str | None = None


@dataclass(frozen=True)
class FeatureMatch:
    feature: ExternalDatasetFeature
    overlap_area: float  # square degrees in 4326 — coarse, fine for ordering
    contains_input: bool


def query_features(
    session: Session,
    *,
    dataset_id: int,
    location: ResolvedLocation,
) -> list[FeatureMatch]:
    """Intersect a resolved location against an external dataset's features.

    v1 implementation: bbox-prefilter on geometry_bbox_json, then exact
    intersection in shapely. The bbox column makes this fine at thousands of
    features; PostGIS becomes attractive past that. Returns matches in
    descending overlap order so the most-likely-governing precinct is first.
    """
    try:
        location_geom = shapely_shape(location.geometry)
    except (ValueError, TypeError, KeyError):
        return []
    if not location_geom.is_valid or location_geom.is_empty:
        return []
    minx, miny, maxx, maxy = location_geom.bounds

    # SQLite-portable bbox prefilter using JSON1 functions would be nicer
    # but SQLAlchemy can't express it portably; pull the dataset's features
    # and filter in Python. At Halifax scale (62 polygons) this is trivial;
    # the bbox column is precomputed for a future PostGIS port.
    features = (
        session.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id == dataset_id
            )
        )
        .scalars()
        .all()
    )

    matches: list[FeatureMatch] = []
    for feature in features:
        bbox = feature.geometry_bbox_json or {}
        if (
            bbox.get("maxx", float("inf")) < minx
            or bbox.get("minx", float("-inf")) > maxx
            or bbox.get("maxy", float("inf")) < miny
            or bbox.get("miny", float("-inf")) > maxy
        ):
            continue
        feature_geom = shapely_shape(feature.geometry_geojson)
        if not feature_geom.is_valid or not feature_geom.intersects(location_geom):
            continue
        overlap = feature_geom.intersection(location_geom)
        contains = feature_geom.contains(location_geom)
        # Use length for line/point inputs, area for polygon inputs:
        if location_geom.geom_type in {"Point", "MultiPoint"}:
            overlap_metric = 1.0 if not overlap.is_empty else 0.0
        elif location_geom.geom_type in {"LineString", "MultiLineString"}:
            overlap_metric = overlap.length
        else:
            overlap_metric = overlap.area
        matches.append(
            FeatureMatch(
                feature=feature,
                overlap_area=float(overlap_metric),
                contains_input=contains,
            )
        )
    matches.sort(key=lambda m: (-m.overlap_area, not m.contains_input))
    return matches


def expand_spatial(
    session: Session,
    candidates: list[CandidateFragment],
    *,
    location: ResolvedLocation | None,
) -> list[CandidateFragment]:
    """Emit DATASET_FEATURE candidates whenever a DATASET candidate is in the
    stream and a location is active. The location parameter survives across
    multiple datasets in the same query (height + FAR + zone overlay), so we
    don't consume it after one match.
    """
    if location is None:
        return list(candidates)

    seen_feature_ids = {
        c.external_dataset_feature_id
        for c in candidates
        if c.external_dataset_feature_id is not None
    }
    expanded = list(candidates)
    for candidate in list(candidates):
        if candidate.source_type != SourceType.DATASET.value:
            continue
        if candidate.external_dataset_id is None:
            continue
        matches = query_features(session, dataset_id=candidate.external_dataset_id, location=location)
        if not matches:
            continue
        dataset = session.get(ExternalDataset, candidate.external_dataset_id)
        for match in matches:
            if match.feature.id in seen_feature_ids:
                continue
            expanded.append(_feature_to_candidate(match, dataset, candidate, location))
            seen_feature_ids.add(match.feature.id)
    return expanded


def _feature_to_candidate(
    match: FeatureMatch,
    dataset: ExternalDataset | None,
    parent_candidate: CandidateFragment,
    location: ResolvedLocation,
) -> CandidateFragment:
    canonical = match.feature.canonical_attributes_json or {}
    parts = []
    label = canonical.get("display_label")
    if label:
        parts.append(label)
    height = canonical.get("max_height_m")
    if height is not None:
        parts.append(f"max_height_m={height:g}")
    case = canonical.get("source_case")
    if case:
        parts.append(f"source_case={case}")
    citation = (
        parent_candidate.citation_label
        or (dataset.linked_fragment_citation if dataset else None)
        or "(unlinked)"
    )
    text = f"{citation} feature: " + ", ".join(parts) if parts else f"{citation} feature {match.feature.feature_key}"
    return CandidateFragment(
        source_fragment_id=parent_candidate.source_fragment_id,
        external_dataset_id=match.feature.external_dataset_id,
        external_dataset_feature_id=match.feature.id,
        source_type=SourceType.DATASET_FEATURE.value,
        retrieval_channel=RetrievalChannel.SPATIAL.value,
        base_score=0.7 + (0.1 if match.contains_input else 0.0),
        text=text,
        citation_label=parent_candidate.citation_label,
        citation_path=parent_candidate.citation_path,
        reason={
            "expansion": "spatial",
            "feature_key": match.feature.feature_key,
            "overlap_area": match.overlap_area,
            "contains_input": match.contains_input,
            "location_source": location.source,
            "location_kind": location.kind,
            "location_reference_text": location.reference_text,
        },
        metadata={
            "canonical_attributes": canonical,
            "geometry_bbox": match.feature.geometry_bbox_json,
        },
    )
