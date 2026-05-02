from __future__ import annotations

from sqlalchemy.orm import Session

from layer1.db.base import CrossReference, SourceFragment
from layer1.models.enums import ResolutionStatus
from layer2.models.schemas import CandidateFragment


def expand_hierarchy(session: Session, document_id: int, candidates: list[CandidateFragment]) -> list[CandidateFragment]:
    seen_fragment_ids = {candidate.source_fragment_id for candidate in candidates if candidate.source_fragment_id}
    expanded = list(candidates)
    for candidate in list(candidates):
        if not candidate.source_fragment_id:
            continue
        fragment = session.get(SourceFragment, candidate.source_fragment_id)
        if fragment is None:
            continue
        if fragment.parent_fragment_id and fragment.parent_fragment_id not in seen_fragment_ids:
            parent = session.get(SourceFragment, fragment.parent_fragment_id)
            if parent:
                expanded.append(
                    CandidateFragment(
                        source_fragment_id=parent.id,
                        source_type="fragment",
                        retrieval_channel="hierarchy",
                        base_score=max(candidate.base_score - 0.15, 0.0),
                        text=parent.text,
                        citation_label=parent.citation_label,
                        citation_path=parent.citation_path,
                        reason={"expansion": "parent"},
                    )
                )
                seen_fragment_ids.add(parent.id)
        siblings = (
            session.query(SourceFragment)
            .filter(
                SourceFragment.document_id == document_id,
                SourceFragment.parent_fragment_id == fragment.parent_fragment_id,
                SourceFragment.id != fragment.id,
            )
            .limit(2)
            .all()
        )
        for sibling in siblings:
            if sibling.id in seen_fragment_ids:
                continue
            expanded.append(
                CandidateFragment(
                    source_fragment_id=sibling.id,
                    source_type="fragment",
                    retrieval_channel="hierarchy",
                    base_score=max(candidate.base_score - 0.25, 0.0),
                    text=sibling.text,
                    citation_label=sibling.citation_label,
                    citation_path=sibling.citation_path,
                    reason={"expansion": "sibling"},
                )
            )
            seen_fragment_ids.add(sibling.id)
    return expanded


def expand_cross_references(session: Session, candidates: list[CandidateFragment]) -> list[CandidateFragment]:
    seen_fragment_ids = {candidate.source_fragment_id for candidate in candidates if candidate.source_fragment_id}
    expanded = list(candidates)
    fragment_ids = [fragment_id for fragment_id in seen_fragment_ids if fragment_id is not None]
    if not fragment_ids:
        return expanded
    refs = (
        session.query(CrossReference)
        .filter(
            CrossReference.source_fragment_id.in_(fragment_ids),
            CrossReference.resolution_status == ResolutionStatus.RESOLVED,
            CrossReference.target_fragment_id.is_not(None),
        )
        .all()
    )
    for ref in refs:
        if ref.target_fragment_id in seen_fragment_ids:
            continue
        target = session.get(SourceFragment, ref.target_fragment_id)
        if target is None:
            continue
        expanded.append(
            CandidateFragment(
                source_fragment_id=target.id,
                source_type="fragment",
                retrieval_channel="cross_reference",
                base_score=0.55,
                text=target.text,
                citation_label=target.citation_label,
                citation_path=target.citation_path,
                reason={"expansion": "cross_reference", "reference_text": ref.raw_reference_text},
            )
        )
        seen_fragment_ids.add(target.id)
    return expanded
