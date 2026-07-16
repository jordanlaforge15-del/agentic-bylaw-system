from __future__ import annotations

import json
import re

from layer2.llm.base import BaseLLMClient


class HalifaxMockLLMClient(BaseLLMClient):
    """Deterministic LLM mock for Halifax Regional Centre LUB ground-truth eval."""

    model_name = "mock-halifax-groundtruth"

    def generate(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        question_match = re.search(r"Question:\s*(.+?)\n", user_prompt)
        question = question_match.group(1).strip().lower() if question_match else ""
        fragment_ids = [int(m) for m in re.findall(r"fragment_id:\s*(\d+)", user_prompt)]
        citation_labels = re.findall(r"citation_label:\s*([^\n]+)", user_prompt)
        context_text = " ".join(re.findall(r"text:\s*(.+)", user_prompt)).lower()

        answer_lines: list[str] = []
        claims: list[dict] = []

        if "front setback" in question and "er-1" in question:
            answer_lines.append("The front setback for the ER-1 zone is 6.0 m per Table 3.")
            claims.append(self._dimensional("front setback", "ER-1", 6.0, "m", ">=", fragment_ids))

        elif "side setback" in question and "er-2" in question:
            answer_lines.append("The side setback for the ER-2 zone is 1.5 m per Table 3.")
            claims.append(self._dimensional("side setback", "ER-2", 1.5, "m", ">=", fragment_ids))

        elif "rear setback" in question and "hr-1" in question:
            answer_lines.append("The rear setback for the HR-1 zone is 3.0 m per Table 3.")
            claims.append(self._dimensional("rear setback", "HR-1", 3.0, "m", ">=", fragment_ids))

        elif "setback" in question and "cen-1" in question:
            answer_lines.append(
                "In the CEN-1 zone the front setback is 0.0 m, side setback is 0.0 m, "
                "and rear setback is 3.0 m per Table 4."
            )
            claims.append(self._dimensional("front setback", "CEN-1", 0.0, "m", ">=", fragment_ids))

        elif "setback" in question and "er-3" in question:
            answer_lines.append(
                "In the ER-3 zone the front setback is 3.0 m, side setback is 1.2 m, "
                "and rear setback is 6.0 m per Table 3."
            )
            claims.append(self._dimensional("front setback", "ER-3", 3.0, "m", ">=", fragment_ids))

        elif "maximum height" in question and "er-1" in question:
            answer_lines.append("The maximum height in the ER-1 zone is 10.0 m per Table 5.")
            claims.append(self._dimensional("maximum height", "ER-1", 10.0, "m", "<=", fragment_ids))

        elif "maximum height" in question and "hr-1" in question:
            answer_lines.append("The maximum height in the HR-1 zone is 20.0 m per Table 5.")
            claims.append(self._dimensional("maximum height", "HR-1", 20.0, "m", "<=", fragment_ids))

        elif ("height" in question or "maximum height" in question) and "cen-1" in question:
            answer_lines.append(
                "The maximum height in the CEN-1 zone is governed by the height precinct "
                "identified on Schedule 15, per section 111."
            )
            claims.append({
                "claim_type": "cross_reference_dependency",
                "topic": "height precinct",
                "canonical_subject": "CEN-1 zone",
                "canonical_predicate": "maximum height governed by",
                "canonical_object_text": "Schedule 15 height precinct",
                "zone_code": "CEN-1",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "111(a)",
                "confidence": 0.85,
            })

        elif "lot coverage" in question and "er-1" in question:
            answer_lines.append("The maximum lot coverage in the ER-1 zone is 35% per Table 5.")
            claims.append(self._dimensional("lot coverage", "ER-1", 35.0, "%", "<=", fragment_ids))

        elif "lot coverage" in question and "hr-1" in question:
            answer_lines.append("The maximum lot coverage in the HR-1 zone is 60% per Table 5.")
            claims.append(self._dimensional("lot coverage", "HR-1", 60.0, "%", "<=", fragment_ids))

        elif "single-unit dwelling" in question and "er-1" in question:
            answer_lines.append(
                "A single-unit dwelling use is permitted (P) in the ER-1 zone per Table 1A."
            )
            claims.append(self._use_permission("single-unit dwelling", "ER-1", "P", fragment_ids))

        elif "multi-unit dwelling" in question and "er-1" in question:
            answer_lines.append(
                "A multi-unit dwelling use is not permitted (N) in the ER-1 zone per Table 1A."
            )
            claims.append(self._use_permission("multi-unit dwelling", "ER-1", "N", fragment_ids))

        elif "secondary suite" in question and "cen-1" in question:
            answer_lines.append(
                "A secondary suite use is not permitted (N) in the CEN-1 zone per Table 1B."
            )
            claims.append(self._use_permission("secondary suite", "CEN-1", "N", fragment_ids))

        elif "multi-unit dwelling" in question and "cen-1" in question:
            answer_lines.append(
                "A multi-unit dwelling use is permitted (P) in the CEN-1 zone per Table 1B."
            )
            claims.append(self._use_permission("multi-unit dwelling", "CEN-1", "P", fragment_ids))

        elif "daycare" in question and "hr-1" in question:
            answer_lines.append(
                "A daycare use is permitted (P) in the HR-1 zone per Table 1A."
            )
            claims.append(self._use_permission("daycare", "HR-1", "P", fragment_ids))

        elif "heritage" in question and ("demolition" in question or "conservation" in question):
            answer_lines.append(
                "Within a heritage conservation district identified on Schedule 22, "
                "no development permit shall be issued for the demolition of a registered "
                "heritage property without the approval of the Heritage Advisory Committee, "
                "per section 110."
            )
            claims.append({
                "claim_type": "procedure_requirement",
                "topic": "heritage demolition approval",
                "canonical_subject": "heritage conservation district",
                "canonical_predicate": "demolition requires",
                "canonical_object_text": "approval of the Heritage Advisory Committee",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "110(b)",
                "confidence": 0.9,
            })

        elif "parking" in question and ("cen-1" in question or "exempt" in question or "downtown" in question):
            answer_lines.append(
                "No off-street parking is required for a development in the CEN-1, CEN-2, "
                "DH, or DD zone, per section 120(b)."
            )
            claims.append({
                "claim_type": "parking_requirement",
                "topic": "parking exemption",
                "canonical_subject": "CEN-1, CEN-2, DH, DD zones",
                "canonical_predicate": "parking requirement",
                "canonical_object_text": "no off-street parking required",
                "numeric_value": 0,
                "unit": "spaces",
                "zone_code": "CEN-1",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "120(b)",
                "confidence": 0.88,
            })

        elif "parking" in question and "residential" in question:
            answer_lines.append(
                "A residential development shall provide a minimum of 1 parking space "
                "per dwelling unit, per section 120(a)."
            )
            claims.append({
                "claim_type": "parking_requirement",
                "topic": "residential parking",
                "canonical_subject": "residential development",
                "canonical_predicate": "parking requirement",
                "canonical_object_text": "1 parking space per dwelling unit",
                "numeric_value": 1,
                "unit": "spaces per dwelling unit",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "120(a)",
                "confidence": 0.9,
            })

        elif "development permit" in question and "exemption" in question:
            answer_lines.append(
                "Development permit exemptions include interior renovations that do not "
                "change the use, exterior maintenance, fences under 2 metres, and accessory "
                "buildings under 20 square metres, per section 9."
            )
            claims.append({
                "claim_type": "exception",
                "topic": "development permit exemptions",
                "canonical_subject": "development permit",
                "canonical_predicate": "exempt activities",
                "canonical_object_text": "interior renovations, exterior maintenance, fences under 2 metres, accessory buildings under 20 m2",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "9",
                "confidence": 0.88,
            })

        elif "combination of uses" in question or ("home occupation" in question and "er-" in question):
            answer_lines.append(
                "In the ER-1, ER-2, and ER-3 zones, home occupation use and secondary "
                "suite use may be combined on a single lot, per section 49."
            )
            claims.append({
                "claim_type": "use_permission",
                "topic": "combination of uses",
                "canonical_subject": "ER-1, ER-2, ER-3 zones",
                "canonical_predicate": "combined uses permitted",
                "canonical_object_text": "home occupation use and secondary suite use",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "49",
                "confidence": 0.87,
            })

        elif "dwelling unit" in question and "definition" in question:
            answer_lines.append(
                "A dwelling unit means a self-contained living quarters with a "
                "private entrance, per section 3(a)."
            )
            claims.append({
                "claim_type": "definition",
                "topic": "dwelling unit definition",
                "canonical_subject": "dwelling unit",
                "canonical_predicate": "means",
                "canonical_object_text": "self-contained living quarters with a private entrance",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "3(a)",
                "confidence": 0.92,
            })

        elif "schedule 15" in question or "height precinct" in question:
            answer_lines.append(
                "Schedule 15 is the Height Precinct Map showing maximum building heights. "
                "Where Schedule 15 specifies a maximum height, it prevails over Table 5, "
                "per section 111."
            )
            claims.append({
                "claim_type": "cross_reference_dependency",
                "topic": "height precinct overlay",
                "canonical_subject": "Schedule 15",
                "canonical_predicate": "governs",
                "canonical_object_text": "maximum building heights for CEN-1, CEN-2, COR, DD, DH zones",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "111",
                "confidence": 0.85,
            })

        elif "floor area ratio" in question or "schedule 17" in question:
            answer_lines.append(
                "Maximum floor area ratios in the CEN-1, CEN-2, COR, DD, and DH zones "
                "are governed by Schedule 17, per section 112."
            )
            claims.append({
                "claim_type": "cross_reference_dependency",
                "topic": "floor area ratio overlay",
                "canonical_subject": "Schedule 17",
                "canonical_predicate": "governs",
                "canonical_object_text": "maximum floor area ratios for CEN-1, CEN-2, COR, DD, DH zones",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "112",
                "confidence": 0.85,
            })

        elif "bicycle" in question and "parking" in question:
            answer_lines.append(
                "Bicycle parking shall be provided at a minimum of 1 space per "
                "4 dwelling units, per section 120(d)."
            )
            claims.append({
                "claim_type": "parking_requirement",
                "topic": "bicycle parking",
                "canonical_subject": "bicycle parking",
                "canonical_predicate": "minimum requirement",
                "canonical_object_text": "1 space per 4 dwelling units",
                "numeric_value": 0.25,
                "unit": "spaces per dwelling unit",
                "source_fragment_ids": fragment_ids[:2],
                "source_table_cell_ids": [],
                "citation_text": "120(d)",
                "confidence": 0.88,
            })

        else:
            lead_frag = fragment_ids[0] if fragment_ids else None
            lead_label = next((l for l in citation_labels if l != "n/a"), "n/a")
            if lead_frag is None:
                answer_lines.append("The supplied source is insufficient for a grounded answer.")
            else:
                answer_lines.append(
                    f"The most relevant supplied source is fragment {lead_frag} at {lead_label}."
                )
            claims.append({
                "claim_type": "general_regulation",
                "topic": "retrieved source summary",
                "canonical_object_text": answer_lines[0],
                "source_fragment_ids": fragment_ids[:1],
                "source_table_cell_ids": [],
                "citation_text": lead_label,
                "confidence": 0.6,
            })

        payload = {
            "answer_text": " ".join(answer_lines),
            "assumptions": ["Only the supplied context was considered."],
            "insufficient_source": not fragment_ids,
            "cited_fragment_ids": fragment_ids[:4],
            "cited_citation_labels": [l for l in citation_labels if l != "n/a"][:4],
            "claims": claims,
        }
        return json.dumps(payload)

    @staticmethod
    def _dimensional(
        topic: str, zone: str, value: float, unit: str, operator: str, fragment_ids: list[int],
    ) -> dict:
        return {
            "claim_type": "dimensional_standard",
            "topic": topic,
            "canonical_subject": f"{zone} zone",
            "canonical_predicate": topic,
            "canonical_object_text": f"{value} {unit}",
            "numeric_value": value,
            "normalized_value_text": f"{value} {unit}",
            "unit": unit,
            "operator": operator,
            "zone_code": zone,
            "source_fragment_ids": fragment_ids[:2],
            "source_table_cell_ids": [],
            "citation_text": f"Table 3 / {zone}",
            "confidence": 0.85,
        }

    @staticmethod
    def _use_permission(
        use: str, zone: str, status: str, fragment_ids: list[int],
    ) -> dict:
        return {
            "claim_type": "use_permission",
            "topic": f"{use} in {zone}",
            "canonical_subject": use,
            "canonical_predicate": "permission status",
            "canonical_object_text": "permitted" if status == "P" else "not permitted",
            "zone_code": zone,
            "source_fragment_ids": fragment_ids[:2],
            "source_table_cell_ids": [],
            "citation_text": f"Table 1A / {zone}" if zone.startswith("ER") or zone.startswith("HR") else f"Table 1B / {zone}",
            "confidence": 0.85,
        }
