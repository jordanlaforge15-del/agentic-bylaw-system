from __future__ import annotations

from pathlib import Path

from layer2.models.schemas import PromptContext

SYSTEM_PROMPT_PATH = Path(__file__).with_suffix("").parent / "assets" / "system_v1.txt"


def load_system_prompt(version: str = "v1") -> str:
    if version != "v1":
        raise ValueError(f"Unsupported prompt version: {version}")
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_prompt(context: PromptContext, prompt_version: str = "v1") -> tuple[str, str, str]:
    system_prompt = load_system_prompt(prompt_version)
    fragment_blocks = []
    for fragment in context.fragments:
        fragment_blocks.append(
            "\n".join(
                [
                    f"fragment_id: {fragment.source_fragment_id}",
                    f"citation_label: {fragment.citation_label or 'n/a'}",
                    f"citation_path: {fragment.citation_path or 'n/a'}",
                    f"channel: {fragment.retrieval_channel}",
                    f"text: {fragment.text}",
                ]
            )
        )
    claim_blocks = []
    for claim in context.cached_claims:
        claim_blocks.append(
            f"claim_id: {claim.claim_id} | type: {claim.claim_type} | status: {claim.verification_status} | text: {claim.text}"
        )
    assembled_context = "\n\n".join(
        [
            "Known facts:",
            str(context.known_facts or {}),
            "Selected fragments:",
            "\n\n".join(fragment_blocks) if fragment_blocks else "(none)",
            "Verified cached claims:",
            "\n".join(claim_blocks) if claim_blocks else "(none)",
        ]
    )
    user_prompt = (
        f"Question: {context.question_text}\n"
        f"{assembled_context}\n"
        "Respond using only the supplied context."
    )
    return system_prompt, user_prompt, assembled_context

