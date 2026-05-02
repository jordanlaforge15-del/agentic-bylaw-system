from layer2.models.schemas import CandidateFragment
from layer2.retrieval.merge import merge_and_dedupe_candidates


def test_merge_and_dedupe_prefers_highest_score():
    left = CandidateFragment(
        source_fragment_id=7,
        source_type="fragment",
        retrieval_channel="full_text",
        base_score=0.3,
        text="temporary use",
    )
    right = CandidateFragment(
        source_fragment_id=7,
        source_type="fragment",
        retrieval_channel="vector",
        base_score=0.7,
        text="temporary use",
        reason={"origin": "vector"},
    )
    merged = merge_and_dedupe_candidates([left], [right])
    assert len(merged) == 1
    assert merged[0].base_score == 0.7
    assert "vector" in merged[0].reason["channels"]
