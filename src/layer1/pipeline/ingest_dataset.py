from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from layer1.datasets.config import DatasetConfig, load_dataset_config
from layer1.datasets.linker import LinkResult, link_dataset_to_bylaw
from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer1.models.enums import ParseStatus
from layer1.parsers.geo_dataset import GeoDatasetParseResult, parse_geojson

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/.cache/geo-datasets")
# 1000 is a defensive page size — many ArcGIS layers cap maxRecordCount at
# 1000 or 2000, but complex polygons with many vertices make 2000-feature
# pages large enough to time out under the default httpx 5s response window.
ARCGIS_PAGE_SIZE = 1000
ARCGIS_REQUEST_TIMEOUT_S = 120.0
ARCGIS_REST_PATH_MARKER = "/arcgis/rest/services/"
# ArcGIS public endpoints occasionally drop connections mid-payload for
# heavy queries. Retry transient failures with exponential backoff before
# giving up; final error propagates so the caller still sees a hard fail.
HTTP_RETRY_ATTEMPTS = 3
HTTP_RETRY_BASE_DELAY_S = 1.0
_TRANSIENT_HTTP_ERRORS: tuple[type[Exception], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ConnectError,
)


class DatasetIngestResult:
    """Lightweight result container — mirrors the (document, run) shape of
    the PDF pipeline without inventing a full second run-tracking table.
    Status, warnings, and the persisted dataset row are all the caller needs.
    """

    def __init__(
        self,
        dataset: ExternalDataset,
        warnings: list[str],
        feature_warnings: int,
        link_result: LinkResult,
    ) -> None:
        self.dataset = dataset
        self.warnings = warnings
        self.feature_warnings = feature_warnings
        self.link_result = link_result


def ingest_geo_dataset(
    session: Session,
    config_path: str | Path,
    *,
    base_path: Path | None = None,
    cache_dir: Path | None = None,
    http_get: Any | None = None,
) -> DatasetIngestResult:
    """Ingest a companion geo dataset described by a YAML config.

    ``base_path`` is the working directory used to resolve relative
    ``source_path`` entries in the config. Defaults to ``Path.cwd()`` so the
    repo-root-relative path in ``halifax_height_precincts.yaml`` resolves
    naturally when invoked from the project root.

    ``cache_dir`` overrides where URL-fetched datasets are written. Tests
    point this at ``tmp_path`` so the repo's real cache isn't polluted.

    ``http_get`` is injected for testability — production calls default to
    ``httpx.get`` via ``_fetch_dataset_to_cache``.

    Linkage to the bylaw fragment runs as Phase B's ``link_dataset_to_bylaw``
    immediately after persistence so this single call covers the parse +
    persist + link sequence.
    """
    config = load_dataset_config(config_path)
    fs_path = _resolve_source_path(
        config,
        base_path or Path.cwd(),
        cache_dir=cache_dir,
        http_get=http_get,
    )
    parsed: GeoDatasetParseResult = parse_geojson(fs_path, config)

    feature_warning_count = sum(
        1 for f in parsed.features if f.parse_status != ParseStatus.PARSED
    )
    dataset_status = (
        ParseStatus.UNCERTAIN
        if parsed.warnings or feature_warning_count
        else ParseStatus.PARSED
    )

    dataset = ExternalDataset(
        name=config.name,
        publisher=config.publisher,
        source_url=config.source_url,
        source_path=str(fs_path),
        format=config.format,
        version=None,
        content_hash=parsed.content_hash,
        crs=parsed.declared_crs,
        feature_count=parsed.feature_count,
        linked_document_id=None,
        linked_fragment_citation=(
            config.links_to.fragment_citation if config.links_to else None
        ),
        linked_fragment_id=None,
        schema_mapping_json=config.attributes.model_dump(by_alias=True),
        parse_status=dataset_status,
        ingestion_timestamp=datetime.now(timezone.utc),
        metadata_json={
            "publisher": config.publisher,
            "role": config.role,
            "links_to": config.links_to.model_dump() if config.links_to else None,
            "warnings": parsed.warnings,
            "feature_warning_count": feature_warning_count,
        },
    )
    session.add(dataset)
    session.flush()

    for feature in parsed.features:
        session.add(
            ExternalDatasetFeature(
                external_dataset_id=dataset.id,
                feature_key=feature.feature_key,
                attributes_json=dict(feature.attributes),
                canonical_attributes_json=dict(feature.canonical_attributes),
                geometry_geojson=dict(feature.geometry),
                geometry_bbox_json=dict(feature.bbox),
                parse_status=feature.parse_status,
                metadata_json=dict(feature.metadata),
            )
        )
    session.flush()
    # Populate the PostGIS geometry column for the rows we just inserted.
    # Migration 0009 added the column and backfilled rows present at that
    # time; subsequent ingests need to populate it themselves or every
    # ST_Intersects / ST_Contains query against the new dataset misses.
    # SQLite skips this — the spatial.py shapely fallback reads geometry_geojson.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text(
                """
                UPDATE external_dataset_feature
                   SET geometry = ST_GeomFromGeoJSON(geometry_geojson::text)
                 WHERE external_dataset_id = :ds_id AND geometry IS NULL
                """
            ),
            {"ds_id": dataset.id},
        )
        session.flush()
    if config.links_to is not None:
        link_result = link_dataset_to_bylaw(session, dataset.id)
    else:
        link_result = LinkResult(
            dataset_id=dataset.id,
            document_id=None,
            fragment_id=None,
            status="not_applicable",
            detail=f"dataset role={config.role!r} does not bind to a bylaw fragment",
        )
    return DatasetIngestResult(
        dataset=dataset,
        warnings=parsed.warnings,
        feature_warnings=feature_warning_count,
        link_result=link_result,
    )


def _resolve_source_path(
    config: DatasetConfig,
    base_path: Path,
    *,
    cache_dir: Path | None = None,
    http_get: Any | None = None,
) -> Path:
    """Resolve a config's source to a local file path.

    Accepts either ``source_path`` (relative to ``base_path`` or absolute)
    or ``source_url``. URL sources are fetched and cached on disk under
    ``data/.cache/geo-datasets/{name}_{content_hash}.geojson`` so the
    parser sees the same shape regardless of where the data came from.
    ArcGIS REST query endpoints are paginated transparently.

    ``http_get`` is injected for testability — production calls default
    to ``httpx.get``; tests pass a stub that returns canned responses.
    """
    if config.source_path:
        candidate = Path(config.source_path)
        if not candidate.is_absolute():
            candidate = (base_path / candidate).resolve()
        if not candidate.exists():
            raise FileNotFoundError(candidate)
        return candidate
    if config.source_url:
        return _fetch_dataset_to_cache(
            config.source_url,
            config.name,
            cache_dir or DEFAULT_CACHE_DIR,
            http_get=http_get,
        )
    raise ValueError(
        f"dataset {config.name!r} has neither source_path nor source_url"
    )


def _fetch_dataset_to_cache(
    url: str,
    dataset_name: str,
    cache_dir: Path,
    *,
    http_get: Any | None = None,
) -> Path:
    """Download a dataset URL to disk, with ArcGIS pagination if needed.

    The result is a single GeoJSON FeatureCollection written to
    ``cache_dir/{dataset_name}_{content_hash}.geojson``. Returns the path.
    Re-fetching emits a new file (different content_hash) so prior cached
    versions remain available; the ingester writes its own ``content_hash``
    to ``external_dataset.content_hash`` so a re-ingest detects no-change
    cleanly via the existing dedupe path.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    if ARCGIS_REST_PATH_MARKER in url:
        payload = _fetch_arcgis_paginated(url, http_get=http_get)
    else:
        response = _http_get_with_retry(http_get, url, timeout=ARCGIS_REQUEST_TIMEOUT_S)
        payload = response.json()

    raw = json.dumps(payload).encode("utf-8")
    content_hash = hashlib.sha256(raw).hexdigest()[:16]
    target = cache_dir / f"{dataset_name}_{content_hash}.geojson"
    target.write_bytes(raw)
    logger.info(
        "dataset %r fetched: %d bytes, %d features, cache=%s",
        dataset_name,
        len(raw),
        len(payload.get("features", [])),
        target,
    )
    return target


def _fetch_arcgis_paginated(
    base_url: str,
    *,
    page_size: int = ARCGIS_PAGE_SIZE,
    http_get: Any | None = None,
) -> dict[str, Any]:
    """Page through an ArcGIS REST query endpoint as GeoJSON.

    The base URL may already carry query parameters (e.g. ``where=1=1``,
    ``outFields=*``, ``outSR=4326``); we add only what's missing and step
    ``resultOffset`` until the server returns fewer features than the page
    size. CRS is taken from the first page; subsequent pages are folded in.
    """
    parsed = urlparse(base_url)
    base_query = dict(parse_qsl(parsed.query))
    base_query.setdefault("f", "geojson")
    base_query.setdefault("where", "1=1")
    base_query.setdefault("outFields", "*")
    base_query.setdefault("outSR", "4326")

    all_features: list[dict[str, Any]] = []
    crs: dict | None = None
    offset = 0
    while True:
        page_query = {
            **base_query,
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
        }
        page_url = urlunparse(parsed._replace(query=urlencode(page_query)))
        response = _http_get_with_retry(http_get, page_url, timeout=ARCGIS_REQUEST_TIMEOUT_S)
        payload = response.json()
        features = payload.get("features", []) or []
        if crs is None:
            crs = payload.get("crs")
        all_features.extend(features)
        if len(features) < page_size:
            break
        offset += page_size

    return {
        "type": "FeatureCollection",
        "crs": crs or {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": all_features,
    }


def _http_get_with_retry(
    http_get: Any | None,
    url: str,
    *,
    timeout: float,
    attempts: int = HTTP_RETRY_ATTEMPTS,
) -> Any:
    """GET ``url`` with exponential backoff on transient failures.

    Public ArcGIS endpoints occasionally drop connections mid-page for
    heavy queries. We retry on the connection / read errors httpx surfaces
    for those, but propagate non-transient errors (HTTP 4xx, JSON-parse
    issues) immediately. The injected ``http_get`` keeps tests offline.
    """
    client = http_get or httpx.get
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client(url, timeout=timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response
        except _TRANSIENT_HTTP_ERRORS as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                break
            delay = HTTP_RETRY_BASE_DELAY_S * (2**attempt)
            logger.warning(
                "transient HTTP error on %s (attempt %d/%d): %s; retrying in %.1fs",
                url,
                attempt + 1,
                attempts,
                exc.__class__.__name__,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
