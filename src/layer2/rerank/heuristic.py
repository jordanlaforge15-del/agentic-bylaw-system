from __future__ import annotations

from layer2.models.schemas import CandidateFragment, QueryUnderstanding


def rerank_candidates(candidates: list[CandidateFragment], understanding: QueryUnderstanding) -> list[CandidateFragment]:
    terms = set(understanding.normalized_question.split())
    for candidate in candidates:
        score = candidate.base_score
        text = f"{candidate.citation_label or ''} {candidate.text}".lower()
        overlap = sum(1 for term in terms if term and term in text)
        score += min(overlap * 0.08, 0.4)
        if understanding.needs_definitions and "definition" in text:
            score += 0.2
        if any(zone.lower() in text for zone in understanding.zone_keywords):
            score += 0.15
        if any(use_keyword.lower() in text for use_keyword in understanding.use_keywords):
            score += 0.15
        if "height" in understanding.topics and any(token in text for token in ["height", "storey", "storeys", "story", "stories"]):
            score += 0.25
        if any(
            concept in understanding.legal_concepts
            for concept in {"minimum lot area", "maximum height", "lot frontage", "lot coverage"}
        ) and any(
            marker in text
            for marker in [
                "minimum lot area",
                "lot area minimum",
                "maximum height",
                "height maximum",
                "minimum frontage",
                "lot frontage minimum",
                "maximum coverage",
                "lot coverage maximum",
            ]
        ):
            score += 0.3
        if candidate.retrieval_channel == "table" and candidate.reason.get("pattern") == "dimensional_pair":
            score += 0.35
        if candidate.retrieval_channel == "cross_reference":
            score += 0.1
        candidate.rerank_score = score
    return sorted(candidates, key=lambda item: item.rerank_score, reverse=True)
