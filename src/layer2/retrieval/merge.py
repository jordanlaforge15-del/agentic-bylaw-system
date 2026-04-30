from __future__ import annotations

from layer2.models.schemas import CandidateFragment


def merge_and_dedupe_candidates(*candidate_groups: list[CandidateFragment]) -> list[CandidateFragment]:
    merged: dict[tuple[int | None, int | None, int | None, str], CandidateFragment] = {}
    for group in candidate_groups:
        for candidate in group:
            key = (
                candidate.source_fragment_id,
                candidate.source_table_id,
                candidate.source_table_cell_id,
                candidate.source_type,
                candidate.metadata.get("page_block_id") if not any([candidate.source_fragment_id, candidate.source_table_id, candidate.source_table_cell_id]) else None,
            )
            existing = merged.get(key)
            if existing is None:
                merged[key] = candidate
                continue
            existing.base_score = max(existing.base_score, candidate.base_score)
            if candidate.retrieval_channel not in existing.reason.get("channels", []):
                existing.reason.setdefault("channels", []).append(candidate.retrieval_channel)
            existing.reason.update(candidate.reason)
    return sorted(merged.values(), key=lambda item: item.base_score, reverse=True)
