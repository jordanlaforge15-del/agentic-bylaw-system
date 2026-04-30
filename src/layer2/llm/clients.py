from __future__ import annotations

import json
import re

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from layer2.llm.base import BaseLLMClient


class OpenAICompatibleLLMClient(BaseLLMClient):
    def __init__(self, base_url: str, model_name: str, api_key: str | None = None):
        self.model_name = model_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self.model_name,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if "api.openai.com" in self._base_url:
            payload["response_format"] = {"type": "json_object"}
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"]


class MockLLMClient(BaseLLMClient):
    def __init__(self):
        self.model_name = "mock-layer2"

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        question_match = re.search(r"Question:\s*(.+?)\n", user_prompt)
        question = question_match.group(1).strip().lower() if question_match else ""
        fragment_ids = [int(match) for match in re.findall(r"fragment_id:\s*(\d+)", user_prompt)]
        citation_labels = re.findall(r"citation_label:\s*([^\n]+)", user_prompt)
        context_lines = re.findall(r"text:\s*(.+)", user_prompt)
        context_text = " ".join(context_lines).lower()
        answer_lines = []
        claims = []
        if "minimum lot area" in question or "400 m2" in context_text or "table 1" in question:
            answer_lines.append(
                "The table context shows an R1 minimum lot area of 400 m2 [fragment 10, 2.1]."
            )
            claims.append(
                {
                    "claim_type": "dimensional_standard",
                    "topic": "minimum lot area",
                    "canonical_subject": "R1 zone",
                    "canonical_predicate": "minimum lot area",
                    "canonical_object_text": "400 m2",
                    "numeric_value": 400,
                    "normalized_value_text": "400 m2",
                    "unit": "m2",
                    "operator": ">=",
                    "zone_code": "R1",
                    "source_fragment_ids": [10] if 10 in fragment_ids else fragment_ids[:1],
                    "source_table_cell_ids": [],
                    "citation_text": "2.1 / Table 1",
                    "confidence": 0.8,
                }
            )
        elif "purpose" in question and "purpose of this bylaw" in context_text:
            answer_lines.append("The bylaw purpose is to regulate land use, subject to section 2.1 [fragment 3, 1.1].")
            claims.append(
                {
                    "claim_type": "general_regulation",
                    "topic": "bylaw purpose",
                    "canonical_subject": "bylaw",
                    "canonical_predicate": "purpose",
                    "canonical_object_text": "regulate land use subject to section 2.1",
                    "source_fragment_ids": [3],
                    "source_table_cell_ids": [],
                    "citation_text": "1.1",
                    "confidence": 0.8,
                }
            )
        elif "apply" in question and "applies to all lands within the municipality" in context_text:
            answer_lines.append("The supplied source says the bylaw applies to all lands within the municipality [fragment 5, 1.2].")
            claims.append(
                {
                    "claim_type": "applicability_condition",
                    "topic": "bylaw applicability",
                    "canonical_subject": "bylaw",
                    "canonical_predicate": "applies to",
                    "canonical_object_text": "all lands within the municipality",
                    "source_fragment_ids": [5],
                    "source_table_cell_ids": [],
                    "citation_text": "1.2",
                    "confidence": 0.82,
                }
            )
        elif "schedule b" in question and "schedule b" in context_text:
            answer_lines.append("The source cross-references Schedule B for residential zones [fragment 11, Schedule B].")
            claims.append(
                {
                    "claim_type": "cross_reference_dependency",
                    "topic": "schedule b cross-reference",
                    "canonical_subject": "residential zones",
                    "canonical_predicate": "listed in",
                    "canonical_object_text": "Schedule B",
                    "source_fragment_ids": [11, 12],
                    "source_table_cell_ids": [],
                    "citation_text": "Schedule B",
                    "confidence": 0.78,
                }
            )
        elif "section 2.1" in question or "2.1" in question:
            answer_lines.append("Section 2.1 identifies residential zones and points to Schedule B [fragment 10, 2.1].")
            claims.append(
                {
                    "claim_type": "general_regulation",
                    "topic": "section 2.1",
                    "canonical_subject": "section 2.1",
                    "canonical_predicate": "identifies",
                    "canonical_object_text": "residential zones listed in Schedule B",
                    "source_fragment_ids": [10, 11],
                    "source_table_cell_ids": [],
                    "citation_text": "2.1",
                    "confidence": 0.77,
                }
            )
        elif "footnote" in question and "footnote preserved for citation audit" in context_text:
            answer_lines.append("The preserved footnote says: This is a footnote preserved for citation audit [fragment 8, 1].")
            claims.append(
                {
                    "claim_type": "procedure_requirement",
                    "topic": "citation audit footnote",
                    "canonical_object_text": "This is a footnote preserved for citation audit.",
                    "source_fragment_ids": [8],
                    "source_table_cell_ids": [],
                    "citation_text": "1",
                    "confidence": 0.7,
                }
            )
        elif "exception" in question and "except as provided" in context_text:
            answer_lines.append("Clause (a) states that no person shall use land except as provided in 6.1.4 [fragment 6, (a)].")
            claims.append(
                {
                    "claim_type": "exception",
                    "topic": "land use exception",
                    "canonical_subject": "land use",
                    "canonical_predicate": "except as provided in",
                    "canonical_object_text": "6.1.4",
                    "source_fragment_ids": [6],
                    "source_table_cell_ids": [],
                    "citation_text": "(a)",
                    "confidence": 0.76,
                }
            )
        elif "temporary use" in question or "temporary use" in context_text:
            answer_lines.append(
                "The supplied source says a temporary use may be permitted under subsection 1.2 [fragment 7, (i)]."
            )
            claims.append(
                {
                    "claim_type": "use_permission",
                    "topic": "temporary use",
                    "canonical_subject": "temporary use",
                    "canonical_predicate": "may be permitted under",
                    "canonical_object_text": "subsection 1.2",
                    "source_fragment_ids": [7] if 7 in fragment_ids else fragment_ids[:1],
                    "source_table_cell_ids": [],
                    "citation_text": "(i)",
                    "confidence": 0.85,
                }
            )
        else:
            lead_fragment = fragment_ids[0] if fragment_ids else None
            lead_label = next((label for label in citation_labels if label != "n/a"), "n/a")
            if lead_fragment is None:
                answer_lines.append("The supplied source is insufficient for a grounded answer.")
            else:
                answer_lines.append(
                    f"The most relevant supplied source is fragment {lead_fragment} at {lead_label}."
                )
            claims.append(
                {
                    "claim_type": "general_regulation",
                    "topic": "retrieved source summary",
                    "canonical_object_text": answer_lines[0],
                    "source_fragment_ids": fragment_ids[:1],
                    "source_table_cell_ids": [],
                    "citation_text": lead_label,
                    "confidence": 0.6,
                }
            )
        payload = {
            "answer_text": " ".join(answer_lines),
            "assumptions": ["Only the supplied context was considered."],
            "insufficient_source": not fragment_ids,
            "cited_fragment_ids": fragment_ids[:4],
            "cited_citation_labels": [label for label in citation_labels if label != "n/a"][:4],
            "claims": claims,
        }
        return json.dumps(payload)
