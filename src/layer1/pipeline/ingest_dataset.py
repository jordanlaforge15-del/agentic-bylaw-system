from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from layer1.datasets.config import DatasetConfig, load_dataset_config
from layer1.datasets.linker import LinkResult, link_dataset_to_bylaw
from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer1.models.enums import ParseStatus
from layer1.parsers.geo_dataset import GeoDatasetParseResult, parse_geojson


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
) -> DatasetIngestResult:
    """Ingest a companion geo dataset described by a YAML config.

    ``base_path`` is the working directory used to resolve relative
    ``source_path`` entries in the config. Defaults to ``Path.cwd()`` so the
    repo-root-relative path in ``halifax_height_precincts.yaml`` resolves
    naturally when invoked from the project root.

    Linkage to the bylaw fragment (Schedule 15 → fragment_id) lands in
    Phase B; here we only persist the dataset and its features.
    """
    config = load_dataset_config(config_path)
    fs_path = _resolve_source_path(config, base_path or Path.cwd())
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
        linked_fragment_citation=config.links_to.fragment_citation,
        linked_fragment_id=None,
        schema_mapping_json=config.attributes.model_dump(by_alias=True),
        parse_status=dataset_status,
        ingestion_timestamp=datetime.now(timezone.utc),
        metadata_json={
            "publisher": config.publisher,
            "links_to": config.links_to.model_dump(),
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
    link_result = link_dataset_to_bylaw(session, dataset.id)
    return DatasetIngestResult(
        dataset=dataset,
        warnings=parsed.warnings,
        feature_warnings=feature_warning_count,
        link_result=link_result,
    )


def _resolve_source_path(config: DatasetConfig, base_path: Path) -> Path:
    if not config.source_path:
        raise ValueError(
            f"dataset {config.name!r} has no source_path; URL fetch is not yet supported"
        )
    candidate = Path(config.source_path)
    if not candidate.is_absolute():
        candidate = (base_path / candidate).resolve()
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate
