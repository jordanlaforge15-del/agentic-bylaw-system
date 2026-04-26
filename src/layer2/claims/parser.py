from __future__ import annotations

import json

from pydantic import ValidationError

from layer2.models.schemas import LLMAnswerPayload


def parse_answer_payload(raw_output: str) -> LLMAnswerPayload:
    try:
        return LLMAnswerPayload.model_validate(json.loads(raw_output))
    except (json.JSONDecodeError, ValidationError) as exc:
        return LLMAnswerPayload(
            answer_text=f"Model output could not be parsed as structured JSON: {exc}",
            assumptions=["Structured parsing failed."],
            insufficient_source=True,
            claims=[],
        )
