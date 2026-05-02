from __future__ import annotations

import json
import os

from openai import OpenAI

from bylaw_retrieval.openai_tools import (
    OpenAIToolExecutor,
    build_openai_responses_tool_specs,
)
from layer1.db.session import session_scope


def main() -> None:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = (
        "Use the bylaw retrieval tools to gather citation-grounded evidence about front yard setbacks "
        "for the Sampleton zoning bylaw. Do not make up citations."
    )

    with session_scope() as session:
        executor = OpenAIToolExecutor(session)
        response = client.responses.create(
            model="gpt-5",
            input=prompt,
            tools=build_openai_responses_tool_specs(),
        )

        tool_outputs = []
        for item in response.output:
            if item.type != "function_call":
                continue
            result = executor.execute(item.name, item.arguments)
            tool_outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(result),
                }
            )

        if not tool_outputs:
            print(response.output_text)
            return

        final = client.responses.create(
            model="gpt-5",
            previous_response_id=response.id,
            input=tool_outputs,
        )
        print(final.output_text)


if __name__ == "__main__":
    main()
