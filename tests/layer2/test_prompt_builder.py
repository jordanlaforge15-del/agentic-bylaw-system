from layer2.models.schemas import CachedClaimContext, CandidateFragment, PromptContext
from layer2.prompts.builder import build_prompt


def test_build_prompt_includes_fragments_and_claims():
    context = PromptContext(
        question_text="Is a temporary use permitted?",
        known_facts={"municipality": "Sampleton"},
        fragments=[
            CandidateFragment(
                source_fragment_id=7,
                source_type="fragment",
                retrieval_channel="full_text",
                base_score=0.9,
                text="(i) A temporary use may be permitted under subsection 1.2.",
                citation_label="(i)",
            )
        ],
        cached_claims=[
            CachedClaimContext(
                claim_id=1,
                claim_type="use_permission",
                topic="temporary use",
                text="temporary use may be permitted",
                verification_status="verified",
            )
        ],
    )
    system_prompt, user_prompt, assembled = build_prompt(context)
    assert "strict JSON" in system_prompt
    assert "fragment_id: 7" in user_prompt
    assert "claim_id: 1" in assembled

