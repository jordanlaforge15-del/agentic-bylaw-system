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
                    f"source_type: {fragment.source_type}",
                    f"fragment_id: {fragment.source_fragment_id if fragment.source_fragment_id is not None else 'n/a'}",
                    f"table_id: {fragment.source_table_id if fragment.source_table_id is not None else 'n/a'}",
                    f"table_cell_id: {fragment.source_table_cell_id if fragment.source_table_cell_id is not None else 'n/a'}",
                    f"citation_label: {fragment.citation_label or 'n/a'}",
                    f"citation_path: {fragment.citation_path or 'n/a'}",
                    f"channel: {fragment.retrieval_channel}",
                    f"reason: {fragment.reason}",
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
        "Return one JSON object only. Do not wrap it in markdown fences.\n"
        "Use this exact shape:\n"
        "{\n"
        '  "answer_text": "string",\n'
        '  "assumptions": ["string"],\n'
        '  "insufficient_source": true,\n'
        '  "cited_fragment_ids": [123],\n'
        '  "cited_citation_labels": ["28"],\n'
        '  "claims": [\n'
        "    {\n"
        '      "claim_type": "definition|use_permission|dimensional_standard|parking_requirement|applicability_condition|exception|cross_reference_dependency|general_regulation|procedure_requirement",\n'
        '      "topic": "string",\n'
        '      "canonical_subject": "string or null",\n'
        '      "canonical_predicate": "string or null",\n'
        '      "canonical_object_text": "string or null",\n'
        '      "numeric_value": 35,\n'
        '      "normalized_value_text": "35 ft.",\n'
        '      "unit": "ft.",\n'
        '      "operator": "<=|>=|=|null",\n'
        '      "zone_code": "R1 or null",\n'
        '      "use_name": "string or null",\n'
        '      "applicability_text": "string or null",\n'
        '      "condition_text": "string or null",\n'
        '      "exception_text": "string or null",\n'
        '      "source_fragment_ids": [123],\n'
        '      "source_table_cell_ids": [456],\n'
        '      "citation_text": "28 / Height maximum",\n'
        '      "confidence": 0.8\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "If the source does not explicitly answer the exact question, say so in answer_text and set insufficient_source=true. "
        "Still emit claims for any directly relevant sourced facts you relied on, such as a height limit, parking number, setback, definition, or use permission."
    )
    return system_prompt, user_prompt, assembled_context
