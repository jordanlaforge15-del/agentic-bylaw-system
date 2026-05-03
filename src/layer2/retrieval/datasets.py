from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature, SourceFragment
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment


def expand_datasets(
    session: Session,
    candidates: list[CandidateFragment],
) -> list[CandidateFragment]:
    """Emit DATASET candidates for any fragment-bearing candidate whose
    fragment is the linked entry point of an external dataset.

    Bulk-mode only: returns one summary candidate per dataset, not feature-
    level results. The spatial channel (Phase D) emits DATASET_FEATURE
    candidates when a location parameter is active.
    """
    fragment_ids = {
        candidate.source_fragment_id
        for candidate in candidates
        if candidate.source_fragment_id is not None
    }
    if not fragment_ids:
        return list(candidates)

    rows = (
        session.execute(
            select(ExternalDataset).where(ExternalDataset.linked_fragment_id.in_(fragment_ids))
        )
        .scalars()
        .all()
    )
    if not rows:
        return list(candidates)

    seen_dataset_ids = {
        candidate.external_dataset_id
        for candidate in candidates
        if candidate.external_dataset_id is not None
    }

    expanded = list(candidates)
    for dataset in rows:
        if dataset.id in seen_dataset_ids:
            continue
        fragment = (
            session.get(SourceFragment, dataset.linked_fragment_id)
            if dataset.linked_fragment_id is not None
            else None
        )
        summary = _summarize_dataset(session, dataset)
        expanded.append(
            CandidateFragment(
                source_fragment_id=dataset.linked_fragment_id,
                external_dataset_id=dataset.id,
                source_type=SourceType.DATASET.value,
                retrieval_channel=RetrievalChannel.DATASET.value,
                base_score=0.55,
                text=summary,
                citation_label=fragment.citation_label if fragment else dataset.linked_fragment_citation,
                citation_path=fragment.citation_path if fragment else None,
                reason={
                    "expansion": "linked_dataset",
                    "dataset_name": dataset.name,
                    "feature_count": dataset.feature_count,
                },
            )
        )
        seen_dataset_ids.add(dataset.id)
    return expanded


def _summarize_dataset(session: Session, dataset: ExternalDataset) -> str:
    """Produce a compact, prompt-friendly description of a dataset.

    The summary is what the LLM sees as evidence when the question doesn't
    carry a location parameter. It must convey: what the dataset represents,
    its scope, and the distribution of its key canonical attribute(s) so the
    LLM can answer "what does Schedule 15 cover?" type questions without
    enumerating every feature.
    """
    feature_keys = (
        session.execute(
            select(ExternalDatasetFeature.canonical_attributes_json).where(
                ExternalDatasetFeature.external_dataset_id == dataset.id
            )
        )
        .scalars()
        .all()
    )
    citation = dataset.linked_fragment_citation or "(unlinked)"
    parts = [
        f"{citation} is backed by the '{dataset.name}' dataset",
        f"published by {dataset.publisher}" if dataset.publisher else None,
        f"({dataset.feature_count} feature(s), CRS {dataset.crs}).",
    ]
    summary = " ".join(p for p in parts if p)

    height_values = sorted({
        attrs.get("max_height_m")
        for attrs in feature_keys
        if isinstance(attrs, dict) and attrs.get("max_height_m") is not None
    })
    if height_values:
        summary += (
            f" Maximum heights range from {height_values[0]:g} m to "
            f"{height_values[-1]:g} m across {len(height_values)} distinct value(s)."
        )

    distinct_labels = _distinct_labels(feature_keys, key="display_label", limit=8)
    if distinct_labels:
        summary += " Examples of precinct labels: " + ", ".join(distinct_labels) + "."
    return summary


def _distinct_labels(
    canonical_dicts: Iterable[dict | None], *, key: str, limit: int
) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for d in canonical_dicts:
        if not isinstance(d, dict):
            continue
        label = d.get(key)
        if not isinstance(label, str) or label in seen_set:
            continue
        seen.append(label)
        seen_set.add(label)
        if len(seen) >= limit:
            break
    return seen
