from __future__ import annotations

import json
import re

from pydantic import ValidationError

from layer2.models.schemas import LLMAnswerPayload


def parse_answer_payload(raw_output: str) -> LLMAnswerPayload:
    candidate = _extract_json_object(raw_output)
    try:
        return LLMAnswerPayload.model_validate(json.loads(candidate))
    except (json.JSONDecodeError, ValidationError) as exc:
        return LLMAnswerPayload(
            answer_text=f"Model output could not be parsed as structured JSON: {exc}",
            assumptions=["Structured parsing failed."],
            insufficient_source=True,
            claims=[],
        )


def _extract_json_object(raw_output: str) -> str:
    stripped = raw_output.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped
